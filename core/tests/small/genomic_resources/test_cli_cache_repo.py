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
    """Run a full file-fallback _MilestoneProgress; return its lines.

    ``byte_total=0`` with ``file_total>0`` selects the zero-byte fallback
    (file-driven milestones), which must behave exactly as the pre-gain#79
    file-based milestone schedule. See gain#79.
    """
    import logging

    from gain.genomic_resources.cached_repository import _MilestoneProgress

    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)
    reporter = _MilestoneProgress(0, total)
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
    _MilestoneProgress(0, 200)

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
    _MilestoneProgress(0, 0)

    lines = [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "caching progress" in rec.message
    ]
    # No misleading "0/0 (100%)" baseline when there is nothing to cache.
    assert lines == []


def test_milestone_progress_zero_byte_fallback_progresses_on_update(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # byte_total=0 but file_total>0 (only zero-byte files need downloading):
    # fall back to file-driven milestones so there is still motion, instead
    # of a frozen 0/0 byte bar. See gain#79.
    import logging

    from gain.genomic_resources.cached_repository import _MilestoneProgress

    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)
    reporter = _MilestoneProgress(byte_total=0, file_total=2)
    # on_bytes is a no-op in fallback mode; update drives the file bar.
    reporter.on_bytes(0)
    reporter.update(failed=False)
    reporter.update(failed=False)
    reporter.close()

    lines = _byte_milestone_lines(caplog)
    # File-unit lines, not byte lines.
    assert all("files" in line and "B/" not in line for line in lines)
    assert lines[0] == "caching progress: 0/2 files (0%)"
    assert lines[-1] == "caching progress: 2/2 files (100%)"


def _byte_milestone_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "caching progress" in rec.message
    ]


def test_milestone_progress_byte_mode_schedule_and_file_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # In byte mode (byte_total > 0) milestones fire on byte-percentage
    # thresholds driven by on_bytes, and each line carries the file count
    # context updated by update(). See gain#79.
    import logging

    from gain.genomic_resources.cached_repository import _MilestoneProgress

    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)
    reporter = _MilestoneProgress(byte_total=1000, file_total=4)

    # 0% baseline emitted at construction.
    assert _byte_milestone_lines(caplog) == [
        "caching progress: 0.0 B/1000.0 B (0%), 0/4 files",
    ]

    # Drive bytes in 100-byte steps so every 10% bucket is crossed; complete
    # one file at each quarter so the file context advances on later lines.
    for step in range(1, 11):
        reporter.on_bytes(100)
        if step % 3 == 0:
            reporter.update(failed=False)
    reporter.update(failed=False)  # 4th file completes
    reporter.close()

    lines = _byte_milestone_lines(caplog)
    pcts = [int(line.split("(")[1].split("%")[0]) for line in lines]
    # 0% baseline, then each 10% crossing as bytes accumulate, then 100%.
    assert pcts == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    # The final byte-milestone line carries the accumulated file context.
    assert "files" in lines[-1]
    assert "3/4 files" in lines[-1]


def test_milestone_progress_byte_mode_rollback_no_double_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A retryable failure rolls bytes back (negative on_bytes); when the
    # retry re-crosses the same 10% bucket no duplicate line is logged.
    # See gain#79 / slice 1.
    import logging

    from gain.genomic_resources.cached_repository import _MilestoneProgress

    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)
    reporter = _MilestoneProgress(byte_total=1000, file_total=1)

    reporter.on_bytes(250)   # crosses into 20% bucket -> line at 25%
    reporter.on_bytes(-250)  # rollback to 0%
    reporter.on_bytes(250)   # re-cross 25% -- must NOT log again
    reporter.on_bytes(750)   # 100%
    reporter.close()

    lines = _byte_milestone_lines(caplog)
    pcts = [int(line.split("(")[1].split("%")[0]) for line in lines]
    # 0% baseline, the first 25% crossing, and 100%; the re-cross is deduped.
    assert pcts == [0, 25, 100]


def test_milestone_progress_byte_mode_terminal_failure_topup_reaches_100(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A terminal failure credits the file's full size (after slice-1 rolled
    # its bytes back to ~0), so the milestone bar still reaches 100% with a
    # failed tally. pct is clamped at 100. See gain#79 / gain#43.
    import logging

    from gain.genomic_resources.cached_repository import _MilestoneProgress

    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)
    reporter = _MilestoneProgress(byte_total=400, file_total=2)

    reporter.on_bytes(200)            # first file ok -> 50%
    reporter.update(failed=False)
    # second file hard-fails: top-up its full size, then mark failed.
    reporter.on_bytes(200)
    reporter.update(failed=True)
    reporter.close()

    lines = _byte_milestone_lines(caplog)
    pcts = [int(line.split("(")[1].split("%")[0]) for line in lines]
    assert pcts[-1] == 100
    assert all(p <= 100 for p in pcts)
    assert "failed=1" in lines[-1]


def test_milestone_progress_byte_mode_no_respam_after_full(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Once the byte total is reached (e.g. a terminal-failure top-up overshot
    # it while other files are still streaming under workers>1), further
    # positive on_bytes must NOT re-log the 100% line. The 100% line is
    # emitted exactly once, on the first crossing. See gain#79.
    import logging

    from gain.genomic_resources.cached_repository import _MilestoneProgress

    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)
    reporter = _MilestoneProgress(byte_total=100, file_total=2)

    reporter.on_bytes(100)   # reaches 100% -> single 100% line
    reporter.on_bytes(50)    # overshoot (e.g. concurrent chunk) -> no re-log
    reporter.on_bytes(10)    # still overshooting -> no re-log
    reporter.close()

    lines = _byte_milestone_lines(caplog)
    pcts = [int(line.split("(")[1].split("%")[0]) for line in lines]
    # 0% baseline + exactly one 100% line; the overshoot deltas are deduped.
    assert pcts == [0, 100]
    assert sum(1 for p in pcts if p == 100) == 1


def test_make_cache_progress_selects_reporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # _make_cache_progress picks: off -> base; tty + bytes -> tqdm byte mode;
    # non-tty + bytes -> milestone byte mode. See gain#79.
    from gain.genomic_resources.cached_repository import (
        _CacheProgress,
        _make_cache_progress,
        _MilestoneProgress,
        _TqdmProgress,
    )

    # off: always the no-op base, regardless of tty / totals. Exact-type
    # check (not isinstance) -- the subclasses derive from _CacheProgress.
    off = _make_cache_progress(1000, 3, progress=False)
    assert type(off) is _CacheProgress  # pylint: disable=unidiomatic-typecheck

    # tty + byte_total>0 -> tqdm byte-mode bar.
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    tty = _make_cache_progress(1000, 3, progress=True)
    assert isinstance(tty, _TqdmProgress)
    assert tty._byte_mode is True
    tty.close()

    # non-tty + byte_total>0 -> milestone byte mode.
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    non_tty = _make_cache_progress(1000, 3, progress=True)
    assert isinstance(non_tty, _MilestoneProgress)
    assert non_tty._byte_mode is True


def _cached_files(cache_dir: pathlib.Path, resource: str) -> set[str]:
    base = cache_dir / "cache_test" / resource
    return {
        str(p.relative_to(base))
        for p in base.rglob("*")
        if p.is_file() and not p.name.endswith(".lockfile")
    }


def test_cache_resources_header_reports_bytes_and_already_cached(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Phase A must log a header line with the download count, byte total and
    # already-cached count, before any download happens. See gain#78.
    import logging
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

    header = [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "to download" in rec.message
    ]
    assert header, "expected a header line reporting bytes to download"
    assert "already cached" in header[0]


def test_cli_cache_byte_milestones_and_human_header(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # e2e: off a TTY, the header reports human bytes ("to download" +
    # "already cached"), and byte-percentage milestone lines (0% .. 100%,
    # carrying file counts) appear. See gain#79.
    import logging

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

    header = [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "to download" in rec.message
    ]
    assert header, "expected a header line"
    # Human-readable bytes in the header (a unit suffix before "to download").
    assert "B to download" in header[0]
    assert "already cached" in header[0]

    progress = [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "caching progress" in rec.message
    ]
    assert progress, "expected byte-milestone lines"
    # Byte-mode lines carry a human byte figure and a file count.
    assert all("files" in line for line in progress)
    assert any("B/" in line for line in progress)
    assert any("(0%)" in line for line in progress)
    assert any("(100%)" in line for line in progress)


def test_cli_cache_fully_cached_rerun_emits_no_progress(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A fully-cached re-run logs the "0 file(s)" header and, thanks to the
    # empty-worklist early return, emits NO milestone/bar lines. See gain#79.
    import logging

    monkeypatch.setattr("sys.stderr.isatty", lambda: False)

    pipeline_yaml = tmp_path / "annotation.yaml"
    pipeline_yaml.write_text(textwrap.dedent("""
        - position_score: one
    """))

    cli_cache_repo([
        "--grr", str(grr_config_file), "-j", "1", str(pipeline_yaml),
    ])

    caplog.clear()
    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)
    cli_cache_repo([
        "--grr", str(grr_config_file), "-j", "1", str(pipeline_yaml),
    ])

    header = [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "to download" in rec.message
    ]
    assert header and "0 file(s)" in header[0]
    progress = [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "caching progress" in rec.message
    ]
    assert progress == [], "early return must emit no progress lines"


def test_cache_resources_preserves_files_and_state(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
) -> None:
    # Behavior preservation: caching a resource yields the expected cached
    # files, each verified fresh by re-classification. See gain#78.
    from gain.genomic_resources.cached_repository import CachingProtocol

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
    cached = _cached_files(cache_dir, "one")
    # the data file and the config must be cached
    assert "genomic_resource.yaml" in cached
    assert "data.txt" in cached

    # every cached payload file re-classifies as fresh (md5 verified)
    repo = build_genomic_resource_repository(file_name=str(grr_config_file))
    res = repo.get_resource("one")
    proto = res.proto
    assert isinstance(proto, CachingProtocol)
    for filename in ("genomic_resource.yaml", "data.txt"):
        verdict = proto.classify_cached_resource_file(res, filename)
        assert verdict.needs_download is False, filename


def test_cache_resources_rerun_downloads_nothing(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Re-running cache on an already-cached GRR builds an empty work-list and
    # the header reports everything already cached. See gain#78.
    import logging

    pipeline_yaml = tmp_path / "annotation.yaml"
    pipeline_yaml.write_text(textwrap.dedent("""
        - position_score: one
    """))

    cli_cache_repo([
        "--grr", str(grr_config_file),
        "-j", "1",
        str(pipeline_yaml),
    ])

    # second run: nothing to download
    caplog.clear()
    caplog.set_level(logging.INFO, logger=CACHE_LOGGER)
    cli_cache_repo([
        "--grr", str(grr_config_file),
        "-j", "1",
        str(pipeline_yaml),
    ])

    header = [
        rec.message for rec in caplog.records
        if rec.name == CACHE_LOGGER and "to download" in rec.message
    ]
    assert header, "expected header on re-run"
    # zero files to download on the second run
    assert "0 file(s)" in header[0]


def test_cache_resources_worklist_byte_total(
    tmp_path: pathlib.Path,
    grr_config_file: pathlib.Path,
) -> None:
    # cache_resources over a GRR with a mix of fresh and stale files must
    # download exactly the stale ones, with bytes summed from the manifest.
    from gain.genomic_resources.cached_repository import (
        CachingProtocol,
        _build_cache_worklist,
    )

    pipeline_yaml = tmp_path / "annotation.yaml"
    pipeline_yaml.write_text(textwrap.dedent("""
        - position_score: one
    """))

    # first cache everything for resource "one"
    cli_cache_repo([
        "--grr", str(grr_config_file),
        "-j", "1",
        str(pipeline_yaml),
    ])

    repo = build_genomic_resource_repository(file_name=str(grr_config_file))
    res = repo.get_resource("one")
    proto = res.proto
    assert isinstance(proto, CachingProtocol)

    # On a fully-cached resource, the work-list is empty and total bytes 0.
    worklist, total_bytes, already_cached, failures = _build_cache_worklist(
        proto, res, ["genomic_resource.yaml", "data.txt"], workers=1)
    assert not worklist
    assert total_bytes == 0
    assert already_cached == 2
    assert not failures

    # Make data.txt stale: delete it so it must be re-downloaded.
    proto.local_protocol.delete_resource_file(res, "data.txt")
    expected_size = res.get_manifest()["data.txt"].size

    worklist, total_bytes, already_cached, failures = _build_cache_worklist(
        proto, res, ["genomic_resource.yaml", "data.txt"], workers=1)
    assert [(r.resource_id, fn) for r, fn, _ in worklist] == \
        [(res.resource_id, "data.txt")]
    assert total_bytes == expected_size
    assert already_cached == 1
    assert not failures


def test_cache_resources_closes_reporter_on_keyboard_interrupt(
    grr_config_file: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A KeyboardInterrupt (or any BaseException) escaping the as_completed
    # loop must not skip reporter.close() -- otherwise a live tqdm bar is
    # left dangling on the terminal. See gain#68.
    from gain.genomic_resources import cached_repository

    closed: dict[str, bool] = {"close": False}

    class _SpyReporter:
        def update(self, *, failed: bool) -> None:
            pass

        def on_bytes(self, n: int) -> None:
            pass

        def report_failure(self, message: str) -> None:
            pass

        def close(self) -> None:
            closed["close"] = True

    monkeypatch.setattr(
        cached_repository, "_make_cache_progress",
        lambda *_a, **_k: _SpyReporter(),
    )

    # as_completed(futures) is evaluated inside cache_resources' try block,
    # so raising on the call exercises the finally-cleanup path just like an
    # interrupt mid-iteration would.
    def _raising_as_completed(_futures: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        cached_repository, "as_completed", _raising_as_completed)

    repository = build_genomic_resource_repository(
        file_name=str(grr_config_file))

    with pytest.raises(KeyboardInterrupt):
        cached_repository.cache_resources(repository, ["one"], workers=1)

    assert closed["close"], \
        "reporter.close() must run even when the loop is interrupted"
