# pylint: disable=W0621,C0114,C0116,W0212,W0613,C0415
import argparse
import pathlib
import textwrap
from typing import Any

import pytest
import pytest_mock
from gain.annotation.annotation_factory import (
    load_pipeline_from_file_or_resource,
)
from gain.genomic_resources import genomic_context as gc_mod
from gain.genomic_resources.cli_cache_repo import cli_cache_repo
from gain.genomic_resources.genomic_context_base import (
    GC_ANNOTATION_PIPELINE_KEY,
    GC_GRR_KEY,
    GenomicContext,
    GenomicContextProvider,
    SimpleGenomicContext,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
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


class _FakeInstanceContextProvider(GenomicContextProvider):
    """Stand-in for the gpf ``GPFInstanceContextProvider``.

    GPF is not importable from gain (strict layering), so this provider
    reproduces the relevant behaviour: when ``-i/--instance`` points at a
    grr config yaml it builds that GRR and exposes both the GRR and the
    instance's annotation pipeline (the ``res_pipeline`` GRR resource).
    Its priority is 2000, matching the real gpf provider (above
    ``CLIAnnotationContextProvider`` at 800). Because providers init in
    descending-priority order and each registers its context at the front
    of the stack, the lower-priority CLIAnnotation context is registered
    last and therefore *wins* the PriorityGenomicContext lookup -- this is
    exactly the "positional wins" relation the real gpf provider has.
    """

    def __init__(self) -> None:
        super().__init__("FakeInstanceContextProvider", 2000)

    def add_argparser_arguments(
        self, parser: argparse.ArgumentParser, **kwargs: Any,
    ) -> None:
        parser.add_argument("-i", "--instance", default=None)

    def init(self, **kwargs: Any) -> GenomicContext | None:
        instance = kwargs.get("instance")
        if not instance:
            return None
        grr = build_genomic_resource_repository(file_name=instance)
        pipeline = load_pipeline_from_file_or_resource("res_pipeline", grr)
        return SimpleGenomicContext(
            {
                GC_GRR_KEY: grr,
                GC_ANNOTATION_PIPELINE_KEY: pipeline,
            },
            source="FakeInstanceContextProvider",
        )


@pytest.fixture
def fake_instance_provider(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Register a fake gpf-instance provider alongside the real ones."""
    providers = list(gc_mod._REGISTERED_CONTEXT_PROVIDERS)
    providers.append(_FakeInstanceContextProvider())
    mocker.patch.object(
        gc_mod, "_REGISTERED_CONTEXT_PROVIDERS", providers)


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
        "-j", "1",
        str(pipeline_yaml),
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
        "-j", "1",
        "res_pipeline",
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


def test_cli_cache_pipeline_arg_removed(
    grr_config_file: pathlib.Path,
) -> None:
    # --pipeline / -p no longer exists; argparse must reject them.
    with pytest.raises(SystemExit):
        cli_cache_repo([
            "--grr", str(grr_config_file),
            "--pipeline", "res_pipeline",
        ])
    with pytest.raises(SystemExit):
        cli_cache_repo([
            "--grr", str(grr_config_file),
            "-p", "res_pipeline",
        ])


def test_cli_cache_positional_logs_source(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging
    caplog.set_level(logging.INFO, logger="grr_cache_repo")

    cli_cache_repo([
        "--grr", str(grr_config_file),
        "-j", "1",
        "res_pipeline",
    ])

    assert "caching pipeline from positional arg res_pipeline" in caplog.text


def test_cli_cache_instance_pipeline_fallback(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
    fake_instance_provider: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # No positional pipeline: falls back to the instance's pipeline.
    import logging
    caplog.set_level(logging.INFO, logger="grr_cache_repo")

    cli_cache_repo([
        "-i", str(grr_config_file),
        "-j", "1",
    ])

    cache_dir = tmp_path / "cache"
    assert (cache_dir / "cache_test" / "one" /
            "genomic_resource.yaml").exists()
    assert not (cache_dir / "cache_test" / "two" /
                "genomic_resource.yaml").exists()
    assert "caching pipeline from gpf instance / genomic context" \
        in caplog.text


def test_cli_cache_context_sentinel_resolves_to_instance(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
    fake_instance_provider: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Explicit "context" sentinel positional resolves to the instance.
    import logging
    caplog.set_level(logging.INFO, logger="grr_cache_repo")

    cli_cache_repo([
        "-i", str(grr_config_file),
        "-j", "1",
        "context",
    ])

    cache_dir = tmp_path / "cache"
    assert (cache_dir / "cache_test" / "one" /
            "genomic_resource.yaml").exists()
    assert "caching pipeline from gpf instance / genomic context" \
        in caplog.text


def test_cli_cache_positional_wins_over_instance(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
    fake_instance_provider: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Both instance and positional present: positional wins, and the
    # instance still supplies the GRR. The instance pipeline caches
    # resource "one"; we point the positional at a pipeline caching "two"
    # so we can tell which one was used.
    import logging
    caplog.set_level(logging.INFO, logger="grr_cache_repo")

    pipeline_yaml = tmp_path / "positional.yaml"
    pipeline_yaml.write_text(textwrap.dedent("""
        - position_score: two
    """))

    cli_cache_repo([
        "-i", str(grr_config_file),
        "-j", "1",
        str(pipeline_yaml),
    ])

    cache_dir = tmp_path / "cache"
    assert (cache_dir / "cache_test" / "two" /
            "genomic_resource.yaml").exists()
    assert not (cache_dir / "cache_test" / "one" /
                "genomic_resource.yaml").exists()
    assert "caching pipeline from positional arg" in caplog.text


CACHE_LOGGER = "gain.genomic_resources.cached_repository"


def test_cli_cache_reports_progress_off_tty(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import logging

    # Force the non-tty branch of _make_cache_progress so the default
    # progress mode emits milestone log lines rather than a live tqdm bar,
    # regardless of how pytest captures (under -s / a real TTY,
    # sys.stderr.isatty() is otherwise True and the bar bypasses logging).
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)

    pipeline_yaml = tmp_path / "annotation.yaml"
    pipeline_yaml.write_text(textwrap.dedent("""
        - position_score: one
    """))

    cli_cache_repo([
        "--grr", str(grr_config_file),
        "-j", "1",
        str(pipeline_yaml),
    ])

    progress_lines = [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "caching progress" in rec.message
    ]
    assert progress_lines, "expected at least one milestone progress line"
    # A genuine 0% baseline line is emitted before any file completes
    # (spec #59: 0% / every 10% / 100%).
    assert any("0/" in line and "(0%)" in line for line in progress_lines)
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
        "-j", "1",
        "--no-progress",
        str(pipeline_yaml),
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
        "-j", "1",
        str(pipeline_yaml),
    ])

    # The per-file "finished n/m" chatter is preserved for debugging, but
    # demoted to DEBUG so the progress bar/milestones own the INFO stream.
    per_file = [
        rec for rec in caplog.records
        if rec.name == CACHE_LOGGER and rec.message.startswith("finished ")
    ]
    assert per_file, "per-file lines should still be emitted at DEBUG"
    assert all(rec.levelno == logging.DEBUG for rec in per_file)


def _milestone_schedule(
    caplog: pytest.LogCaptureFixture, total: int,
) -> list[str]:
    """Run a full _MilestoneProgress over ``total`` files; return its lines."""
    import logging

    from gain.genomic_resources.cached_repository import _MilestoneProgress

    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)
    reporter = _MilestoneProgress(total)
    for _ in range(total):
        reporter.update(failed=False)
    reporter.close()
    return [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "caching progress" in rec.message
    ]


def test_milestone_progress_emits_baseline_zero_at_construction(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from gain.genomic_resources.cached_repository import _MilestoneProgress

    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)
    _MilestoneProgress(200)

    lines = [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "caching progress" in rec.message
    ]
    assert lines == ["caching progress: 0/200 files (0%)"]


def test_milestone_progress_schedule_no_duplicate_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lines = _milestone_schedule(caplog, 200)

    # Exactly one 0% baseline (at construction, before done==1).
    zero_lines = [line for line in lines if "(0%)" in line]
    assert zero_lines == ["caching progress: 0/200 files (0%)"]

    # One line per 10% bucket crossing plus the final 100%.
    pcts = [int(line.split("(")[1].split("%")[0]) for line in lines]
    assert pcts == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def test_milestone_progress_single_file_has_baseline_and_final(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lines = _milestone_schedule(caplog, 1)

    assert lines == [
        "caching progress: 0/1 files (0%)",
        "caching progress: 1/1 files (100%)",
    ]


def test_milestone_progress_total_zero_skips_baseline(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from gain.genomic_resources.cached_repository import _MilestoneProgress

    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)
    _MilestoneProgress(0)

    lines = [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "caching progress" in rec.message
    ]
    # No misleading "0/0 (100%)" baseline when there is nothing to cache.
    assert lines == []
