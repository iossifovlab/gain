# pylint: disable=W0621,C0114,C0116,W0212,W0613
import hashlib
import json
import os
import pathlib
import textwrap
from collections.abc import Callable

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.histogram import CategoricalHistogram
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
    a_position_score,
)

CATEGORIES_PAST_LIMIT = CategoricalHistogram.UNIQUE_VALUES_LIMIT + 50
CATEGORIES_WITHIN_LIMIT = 50


def a_categorical_score_builder(unique_values: int) -> PositionScoreBuilder:
    """A tabix position score with distinct per-position str values."""
    data_rows = "\n".join(
        f"1 {10 + i} {10 + i} v{i:03d}"
        for i in range(unique_values))
    return (
        a_position_score()
        .with_score("cell", "str")
        .with_histogram({"type": "categorical", "value_order": []})
        .with_data("chrom pos_begin pos_end cell\n" + data_rows)
        .with_tabix()
    )


def a_number_score_builder() -> PositionScoreBuilder:
    """A tabix position score with a number histogram."""
    return (
        a_position_score()
        .with_score("score", "float")
        .with_histogram({"type": "number", "number_of_bins": 3})
        .with_data(
            "chrom pos_begin pos_end score\n"
            "1     10        10      0.1\n"
            "1     11        11      0.2\n")
        .with_tabix()
    )


def a_categorical_score(
    tmp_path: pathlib.Path,
    unique_values: int,
) -> None:
    """Realize a tabix position score with distinct per-position str values."""
    a_categorical_score_builder(unique_values).build_resource(tmp_path)


def a_file_snapshot(root: pathlib.Path) -> dict[str, tuple[int, bytes]]:
    """Map every file under ``root`` to its mtime and content."""
    return {
        str(path.relative_to(root)): (path.stat().st_mtime_ns,
                                      path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def a_repo_built_without_the_sidecar(tmp_path: pathlib.Path) -> bytes:
    """Realize a pre-sidecar repo; return the sidecar a fresh build writes.

    Statistics are built with the current code (which writes the sidecar),
    then the sidecar is deleted and the manifest rebuilt -- the exact state
    a repository built before sidecars existed is in.
    """
    a_categorical_score(tmp_path, CATEGORIES_PAST_LIMIT)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    sidecar_path = (
        tmp_path / "statistics" / "truncated" / "histogram_cell.json")
    expected = sidecar_path.read_bytes()
    sidecar_path.unlink()
    cli_manage(["repo-manifest", "-R", str(tmp_path)])
    manifest = (tmp_path / ".MANIFEST").read_text()
    assert "truncated/histogram_cell" not in manifest
    return expected


def test_fix_histograms_migrates_a_score_named_cell_truncated(
        tmp_path: pathlib.Path) -> None:
    """A score id ending in ``_truncated`` is migrated like any other.

    Its full histogram is ``statistics/histogram_cell_truncated.json`` --
    with sidecars in their own directory that name is unambiguous, so no
    filename filter may skip it.
    """
    data_rows = "\n".join(
        f"1 {10 + i} {10 + i} v{i:03d}"
        for i in range(CATEGORIES_PAST_LIMIT))
    (
        a_position_score()
        .with_score("cell_truncated", "str")
        .with_histogram({"type": "categorical", "value_order": []})
        .with_data("chrom pos_begin pos_end cell_truncated\n" + data_rows)
        .with_tabix()
        .build_resource(tmp_path)
    )
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    sidecar_path = (
        tmp_path / "statistics" / "truncated"
        / "histogram_cell_truncated.json")
    expected_sidecar = sidecar_path.read_bytes()
    sidecar_path.unlink()
    cli_manage(["repo-manifest", "-R", str(tmp_path)])

    cli_manage(["repo-fix-histograms", "-R", str(tmp_path)])

    assert sidecar_path.read_bytes() == expected_sidecar


def test_fix_histograms_writes_the_missing_sidecar(
        tmp_path: pathlib.Path) -> None:
    expected_sidecar = a_repo_built_without_the_sidecar(tmp_path)

    cli_manage(["repo-fix-histograms", "-R", str(tmp_path)])

    sidecar_path = tmp_path / "statistics" / "truncated" / "histogram_cell.json"
    assert sidecar_path.read_bytes() == expected_sidecar


def test_fix_histograms_manifests_the_written_sidecar(
        tmp_path: pathlib.Path) -> None:
    a_repo_built_without_the_sidecar(tmp_path)

    cli_manage(["repo-fix-histograms", "-R", str(tmp_path)])

    manifest = (tmp_path / ".MANIFEST").read_text()
    assert "statistics/truncated/histogram_cell.json" in manifest


def test_fix_histograms_never_touches_statistics_outputs(
        tmp_path: pathlib.Path) -> None:
    a_repo_built_without_the_sidecar(tmp_path)
    statistics_dir = tmp_path / "statistics"
    before = {
        name: a_file_snapshot(statistics_dir)[name]
        for name in ("stats_hash", "histogram_cell.png",
                     "histogram_cell.json")
    }

    cli_manage(["repo-fix-histograms", "-R", str(tmp_path)])

    after = a_file_snapshot(statistics_dir)
    assert {name: after[name] for name in before} == before


def test_fix_histograms_regenerates_a_stale_sidecar(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score(tmp_path, CATEGORIES_PAST_LIMIT)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    sidecar_path = tmp_path / "statistics" / "truncated" / "histogram_cell.json"
    expected_sidecar = sidecar_path.read_bytes()
    full_path = tmp_path / "statistics" / "histogram_cell.json"
    sidecar_path.write_bytes(b'{"stale": true}')
    stale_mtime = full_path.stat().st_mtime - 60
    os.utime(sidecar_path, (stale_mtime, stale_mtime))

    cli_manage(["repo-fix-histograms", "-R", str(tmp_path)])

    assert sidecar_path.read_bytes() == expected_sidecar


def test_fix_histograms_manifests_an_orphaned_up_to_date_sidecar(
        tmp_path: pathlib.Path) -> None:
    # The state an interrupted run leaves behind: the sidecar was
    # written, but the manifest pass never ran.
    expected_sidecar = a_repo_built_without_the_sidecar(tmp_path)
    sidecar_path = tmp_path / "statistics" / "truncated" / "histogram_cell.json"
    sidecar_path.write_bytes(expected_sidecar)

    cli_manage(["repo-fix-histograms", "-R", str(tmp_path)])

    manifest = (tmp_path / ".MANIFEST").read_text()
    assert "statistics/truncated/histogram_cell.json" in manifest


def test_fix_histograms_deletes_a_stale_sidecar_of_a_small_histogram(
        tmp_path: pathlib.Path) -> None:
    # Mixed build history: a stale sidecar (older than the full file)
    # survives although the histogram is now within the limit -- a fresh
    # statistics build would delete it.
    a_categorical_score(tmp_path, CATEGORIES_WITHIN_LIMIT)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    full_path = tmp_path / "statistics" / "histogram_cell.json"
    sidecar_path = (
        tmp_path / "statistics" / "truncated" / "histogram_cell.json")
    sidecar_path.parent.mkdir(exist_ok=True)
    sidecar_path.write_bytes(b'{"stale": true}')
    stale_mtime = full_path.stat().st_mtime - 60
    os.utime(sidecar_path, (stale_mtime, stale_mtime))
    cli_manage(["repo-manifest", "-R", str(tmp_path)])

    cli_manage(["repo-fix-histograms", "-R", str(tmp_path)])

    assert not sidecar_path.exists()
    manifest = (tmp_path / ".MANIFEST").read_text()
    assert "truncated/histogram_cell" not in manifest


def test_fix_histograms_matches_a_fresh_build_for_int_value_order(
        tmp_path: pathlib.Path) -> None:
    data_rows = "\n".join(
        f"1 {10 + i} {10 + i} {i}"
        for i in range(CATEGORIES_PAST_LIMIT))
    (
        a_position_score()
        .with_score("cell", "int")
        .with_histogram({
            "type": "categorical",
            "value_order": list(range(CATEGORIES_PAST_LIMIT)),
        })
        .with_data("chrom pos_begin pos_end cell\n" + data_rows)
        .with_tabix()
        .build_resource(tmp_path)
    )
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    sidecar_path = tmp_path / "statistics" / "truncated" / "histogram_cell.json"
    expected_sidecar = sidecar_path.read_bytes()
    # Guard the oracle: a fresh build carries the real counts, so an
    # all-zero derived sidecar cannot sneak through byte-equality.
    assert any(json.loads(expected_sidecar)["values"].values())
    sidecar_path.unlink()
    cli_manage(["repo-manifest", "-R", str(tmp_path)])

    cli_manage(["repo-fix-histograms", "-R", str(tmp_path)])

    assert sidecar_path.read_bytes() == expected_sidecar


def test_fix_histograms_help_carries_the_removal_notice(
        capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_manage(["repo-fix-histograms", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "One-shot migration" in help_text
    assert "removal end of 2026" in help_text


def a_dvc_only_full_histogram(
        resource_dir: pathlib.Path, score_id: str = "cell") -> None:
    """Replace the built full histogram with a `.dvc` pointer to it.

    The state a fresh clone of a DVC-tracked repository is in before
    `dvc pull`: the manifest still lists the full histogram (its md5 comes
    from the pointer), but its content is absent.
    """
    full_path = resource_dir / "statistics" / f"histogram_{score_id}.json"
    content = full_path.read_bytes()
    content_md5 = hashlib.md5(content).hexdigest()  # ruff: ignore[hashlib-insecure-hash-function]
    (resource_dir / "statistics"
     / f"histogram_{score_id}.json.dvc").write_text(
        textwrap.dedent(f"""
            outs:
            - md5: {content_md5}
              path: histogram_{score_id}.json
              size: {len(content)}
        """))
    full_path.unlink()
    (resource_dir / "statistics" / "truncated"
     / f"histogram_{score_id}.json").unlink()


def test_fix_histograms_manifests_what_it_wrote_despite_a_failure(
        tmp_path: pathlib.Path) -> None:
    data_rows = "\n".join(
        f"1 {10 + i} {10 + i} a{i:03d} z{i:03d}"
        for i in range(CATEGORIES_PAST_LIMIT))
    (
        a_position_score()
        .with_score("aaa", "str")
        .with_histogram(
            {"type": "categorical", "value_order": []}, score_id="aaa")
        .with_score("zzz", "str")
        .with_histogram(
            {"type": "categorical", "value_order": []}, score_id="zzz")
        .with_data("chrom pos_begin pos_end aaa zzz\n" + data_rows)
        .with_tabix()
        .build_resource(tmp_path)
    )
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    (tmp_path / "statistics" / "truncated" / "histogram_aaa.json").unlink()
    a_dvc_only_full_histogram(tmp_path, "zzz")
    cli_manage(["repo-manifest", "-R", str(tmp_path)])

    with pytest.raises(SystemExit) as exc_info:
        cli_manage(["repo-fix-histograms", "-R", str(tmp_path)])

    assert exc_info.value.code != 0
    assert (
        tmp_path / "statistics" / "truncated" / "histogram_aaa.json").exists()
    manifest = (tmp_path / ".MANIFEST").read_text()
    assert "statistics/truncated/histogram_aaa.json" in manifest


def test_fix_histograms_reports_the_unpulled_full_and_continues(
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture) -> None:
    (
        a_grr()
        .with_resource(
            "scores/broken",
            a_categorical_score_builder(CATEGORIES_PAST_LIMIT))
        .with_resource(
            "scores/fixable",
            a_categorical_score_builder(CATEGORIES_PAST_LIMIT))
        .build_repo(tmp_path)
    )
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    a_dvc_only_full_histogram(tmp_path / "scores" / "broken")
    fixable_sidecar = (tmp_path / "scores" / "fixable" / "statistics"
                       / "truncated" / "histogram_cell.json")
    fixable_sidecar.unlink()
    cli_manage(["repo-manifest", "-R", str(tmp_path)])
    caplog.clear()

    with pytest.raises(SystemExit) as exc_info:
        cli_manage(["repo-fix-histograms", "-R", str(tmp_path)])

    assert exc_info.value.code != 0
    assert fixable_sidecar.exists()
    error_messages = [
        record.getMessage() for record in caplog.records
        if record.levelname == "ERROR"
    ]
    assert any(
        "statistics/histogram_cell.json" in message
        and "scores/broken" in message
        for message in error_messages), error_messages


@pytest.mark.parametrize("build_score", [
    pytest.param(
        lambda: a_categorical_score_builder(CATEGORIES_PAST_LIMIT),
        id="categorical_with_up_to_date_sidecar"),
    pytest.param(
        lambda: a_categorical_score_builder(CATEGORIES_WITHIN_LIMIT),
        id="categorical_within_limit"),
    pytest.param(a_number_score_builder, id="number_histogram"),
])
def test_fix_histograms_leaves_settled_resources_untouched(
        tmp_path: pathlib.Path,
        build_score: Callable[[], PositionScoreBuilder]) -> None:
    a_grr().with_resource("scores/one", build_score()).build_repo(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    before = a_file_snapshot(tmp_path)

    cli_manage(["repo-fix-histograms", "-R", str(tmp_path)])

    # The whole repository: repo-level files (.CONTENTS.json, indexes)
    # must not churn either, not just the resource's own files.
    assert a_file_snapshot(tmp_path) == before
