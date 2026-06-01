# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli_cache_repo import cli_cache_repo
from gain.genomic_resources.testing import (
    setup_denovo,
    setup_directories,
)


@pytest.fixture
def cache_grr_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A GRR with resources ``one`` and ``two`` plus a pipeline resource."""
    root = tmp_path / "grr_source"
    setup_directories(
        root, {
            "one": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: position_score
                    table:
                        filename: data.txt
                    scores:
                    - id: score
                      type: float
                      name: s1
                """),
            },
            "two": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: position_score
                    table:
                        filename: data.txt
                    scores:
                    - id: score
                      type: float
                      name: s1
                """),
            },
            "res_pipeline": {
                "annotation.yaml": textwrap.dedent("""
                    - position_score: one
                """),
                "genomic_resource.yaml": textwrap.dedent("""
                    type: annotation_pipeline
                    filename: annotation.yaml
                """),
            },
        },
    )
    one_content = textwrap.dedent("""
        chrom  pos_begin  s1
        chr1   23         0.1
    """)
    setup_denovo(root / "one" / "data.txt", one_content)
    setup_denovo(root / "two" / "data.txt", one_content)
    return root


@pytest.fixture
def grr_config_file(
    tmp_path: pathlib.Path,
    cache_grr_dir: pathlib.Path,
) -> pathlib.Path:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    grr_yaml = tmp_path / "grr.yaml"
    grr_yaml.write_text(textwrap.dedent(f"""
        id: cache_test
        type: dir
        directory: "{cache_grr_dir}"
        cache_dir: "{cache_dir}"
    """))
    return grr_yaml


def test_cli_cache_pipeline_file(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
) -> None:
    pipeline_yaml = tmp_path / "annotation.yaml"
    pipeline_yaml.write_text(textwrap.dedent("""
        - position_score: one
    """))

    cli_cache_repo([
        "--grr", str(grr_config_file),
        "--pipeline", str(pipeline_yaml),
        "-j", "1",
    ])

    cache_dir = tmp_path / "cache"
    assert (cache_dir / "cache_test" / "one" /
            "genomic_resource.yaml").exists()
    assert not (cache_dir / "cache_test" / "two" /
                "genomic_resource.yaml").exists()


def test_cli_cache_pipeline_grr_resource(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
) -> None:
    cli_cache_repo([
        "--grr", str(grr_config_file),
        "--pipeline", "res_pipeline",
        "-j", "1",
    ])

    cache_dir = tmp_path / "cache"
    assert (cache_dir / "cache_test" / "one" /
            "genomic_resource.yaml").exists()
    assert not (cache_dir / "cache_test" / "two" /
                "genomic_resource.yaml").exists()


def test_cli_cache_no_pipeline(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging
    caplog.set_level(logging.WARNING, logger="grr_cache_repo")

    cli_cache_repo([
        "--grr", str(grr_config_file),
        "-j", "1",
    ])

    assert "no pipeline supplied" in caplog.text
    cache_dir = tmp_path / "cache"
    assert not (cache_dir / "cache_test" / "one" /
                "genomic_resource.yaml").exists()


CACHE_LOGGER = "gain.genomic_resources.cached_repository"


def test_cli_cache_reports_progress_off_tty(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    # pytest captures stdout/stderr, so isatty() is False: the default
    # progress mode emits milestone log lines rather than a live bar.
    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)

    pipeline_yaml = tmp_path / "annotation.yaml"
    pipeline_yaml.write_text(textwrap.dedent("""
        - position_score: one
    """))

    cli_cache_repo([
        "--grr", str(grr_config_file),
        "--pipeline", str(pipeline_yaml),
        "-j", "1",
    ])

    progress_lines = [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "caching progress" in rec.message
    ]
    assert progress_lines, "expected at least one milestone progress line"
    assert any("100%" in line for line in progress_lines)


def test_cli_cache_no_progress_suppresses_milestones(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging
    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)

    pipeline_yaml = tmp_path / "annotation.yaml"
    pipeline_yaml.write_text(textwrap.dedent("""
        - position_score: one
    """))

    cli_cache_repo([
        "--grr", str(grr_config_file),
        "--pipeline", str(pipeline_yaml),
        "-j", "1",
        "--no-progress",
    ])

    progress_lines = [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "caching progress" in rec.message
    ]
    assert not progress_lines, progress_lines
    # the resource is still cached; only the progress reporting is silenced
    assert (tmp_path / "cache" / "cache_test" / "one" /
            "genomic_resource.yaml").exists()


def test_cli_cache_per_file_lines_demoted_to_debug(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging
    caplog.set_level(logging.DEBUG, logger=CACHE_LOGGER)

    pipeline_yaml = tmp_path / "annotation.yaml"
    pipeline_yaml.write_text(textwrap.dedent("""
        - position_score: one
    """))

    cli_cache_repo([
        "--grr", str(grr_config_file),
        "--pipeline", str(pipeline_yaml),
        "-j", "1",
    ])

    # The per-file "finished n/m" chatter is preserved for debugging, but
    # demoted to DEBUG so the progress bar/milestones own the INFO stream.
    per_file = [
        rec for rec in caplog.records
        if rec.name == CACHE_LOGGER and rec.message.startswith("finished ")
    ]
    assert per_file, "per-file lines should still be emitted at DEBUG"
    assert all(rec.levelno == logging.DEBUG for rec in per_file)
