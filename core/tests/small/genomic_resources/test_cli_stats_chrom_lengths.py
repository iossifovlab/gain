"""The stored chromosome lengths of a score, at the repair seam (gain#1419).

``resource-stats`` writes ``statistics/chrom_lengths.json`` -- the
resolver's answer per contig plus what it was derived from -- under a
freshness gate of its own, beside the statistics hash rather than inside
it: re-pointing the ``reference_genome`` label refreshes this one file and
never the histograms (``docs/source/grr.rst`` promises as much).
"""

import hashlib
import json
import pathlib
from typing import Any, cast

import pytest
import pytest_mock
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.testing.builders import a_reference_genome

from .conftest import (
    CHR1_GENOME_LENGTH,
    CHRM_PROBE_BOUND,
    a_labelled_tabix_score_grr,
    patch_tabix_probe,
    resource_stats,
    set_label,
    truncate,
)


def _md5_of(path: pathlib.Path) -> str:
    """The manifest's digest of a file, computed independently of it."""
    return hashlib.md5(  # ruff: ignore[hashlib-insecure-hash-function]
        path.read_bytes()).hexdigest()


def _mtimes(statistics: pathlib.Path) -> dict[pathlib.Path, int]:
    return {path: path.stat().st_mtime_ns for path in statistics.iterdir()}


def _mtimes_except_the_lengths_file(
    statistics: pathlib.Path,
) -> dict[pathlib.Path, int]:
    return {
        path: mtime for path, mtime in _mtimes(statistics).items()
        if path.name != "chrom_lengths.json"}


def _stored(statistics: pathlib.Path) -> dict[str, Any]:
    """The lengths file as written; raises when it is not JSON."""
    return cast(
        dict[str, Any],
        json.loads((statistics / "chrom_lengths.json").read_text()))


def _a_repaired_labelled_score(
    tmp_path: pathlib.Path, *, genome_id: str = "genome",
) -> pathlib.Path:
    """Build the labelled score and repair it; returns its statistics dir."""
    a_labelled_tabix_score_grr(genome_id=genome_id).build_repo(tmp_path)
    resource_stats(tmp_path, "score")
    return tmp_path / "score" / "statistics"


def _resync_the_manifest(tmp_path: pathlib.Path) -> None:
    """Bring the manifest up to date with the files as they are now.

    Deleting the lengths file, or editing the config, would otherwise
    make the manifest pass that opens every run report the resource --
    and a dry run counts that too, gate or no gate.  With the manifest
    current, only the lengths gate can count it.
    """
    cli_manage(["resource-manifest", "-r", "score", "-R", str(tmp_path)])


def test_repair_stores_the_ladders_answer_and_what_it_was_derived_from(
    tmp_path: pathlib.Path,
) -> None:
    score_dir = tmp_path / "score"

    statistics = _a_repaired_labelled_score(tmp_path)

    assert _stored(statistics) == {
        "derived_from": {
            "reference_genome": "genome",
            "files_md5": {
                "data.txt.gz": _md5_of(score_dir / "data.txt.gz"),
                "data.txt.gz.tbi": _md5_of(score_dir / "data.txt.gz.tbi"),
            },
        },
        "lengths": {
            "chr1": {
                "length": CHR1_GENOME_LENGTH,
                "source": "REFERENCE_GENOME",
                "extent": None,
            },
            "chrM": {
                "length": CHRM_PROBE_BOUND,
                "source": "TABIX_ESTIMATE",
                "extent": None,
            },
        },
    }


def test_a_full_build_manifests_the_lengths_file(
    tmp_path: pathlib.Path,
) -> None:
    _a_repaired_labelled_score(tmp_path)

    manifest = (tmp_path / "score" / ".MANIFEST").read_text()
    assert "statistics/chrom_lengths.json" in manifest


def test_an_unchanged_score_has_nothing_rewritten_by_a_second_run(
    tmp_path: pathlib.Path,
) -> None:
    """The lengths gate holds too: the same key on both sides."""
    statistics = _a_repaired_labelled_score(tmp_path)
    before = _mtimes(statistics)
    assert statistics / "chrom_lengths.json" in before

    resource_stats(tmp_path, "score")

    assert _mtimes(statistics) == before


def test_repointing_the_label_rewrites_the_lengths_file_alone(
    tmp_path: pathlib.Path,
) -> None:
    """Adding or re-pointing the label needs no data rebuild -- the
    promise ``grr.rst`` makes.  The hash never learns about the label;
    the lengths file's own gate is what notices it."""
    statistics = _a_repaired_labelled_score(tmp_path)
    lengths_file = statistics / "chrom_lengths.json"
    before = _mtimes(statistics)
    set_label(tmp_path, "score", "reference_genome", "other_genome")

    resource_stats(tmp_path, "score")

    assert lengths_file.stat().st_mtime_ns > before[lengths_file]
    assert _mtimes_except_the_lengths_file(statistics) == {
        path: mtime for path, mtime in before.items()
        if path != lengths_file}


def test_the_rewritten_lengths_file_names_the_new_label(
    tmp_path: pathlib.Path,
) -> None:
    statistics = _a_repaired_labelled_score(tmp_path)
    set_label(tmp_path, "score", "reference_genome", "other_genome")

    resource_stats(tmp_path, "score")

    assert _stored(statistics)["derived_from"]["reference_genome"] == \
        "other_genome"


def test_a_label_whose_genome_does_not_resolve_is_stored_as_no_label(
    tmp_path: pathlib.Path,
) -> None:
    """The key records the label the genome was resolved FROM.

    A genome the repository does not have contributes nothing to the
    ladder, so the file says so -- rather than pinning the table's
    answer to a label that never took part.
    """
    statistics = _a_repaired_labelled_score(tmp_path, genome_id="late_genome")

    stored = _stored(statistics)
    assert stored["derived_from"]["reference_genome"] is None
    assert stored["lengths"]["chr1"]["source"] == "TABIX_ESTIMATE"


def test_a_genome_that_turns_up_later_refreshes_the_lengths_file_alone(
    tmp_path: pathlib.Path,
) -> None:
    """Recorded as none, the label reads as a change once it resolves --
    so the ordinary run picks the genome up, and no ``-f`` is needed."""
    statistics = _a_repaired_labelled_score(tmp_path, genome_id="late_genome")
    before = _mtimes(statistics)
    (a_reference_genome()
     .with_chromosome("chr1", "A" * CHR1_GENOME_LENGTH)
     .build_resource(tmp_path / "late_genome"))

    resource_stats(tmp_path, "score")

    assert _stored(statistics)["derived_from"]["reference_genome"] == \
        "late_genome"
    assert _mtimes(statistics)[statistics / "stats_hash"] == \
        before[statistics / "stats_hash"]


def test_a_truncated_lengths_file_is_rewritten_by_an_ordinary_run(
    tmp_path: pathlib.Path,
) -> None:
    """Unreadable is stale, not fatal: every other statistics file is
    recoverable by a run, and this one must not need a manual rm."""
    statistics = _a_repaired_labelled_score(tmp_path)
    truncate(statistics / "chrom_lengths.json")

    resource_stats(tmp_path, "score")

    assert _stored(statistics)


def test_a_truncated_lengths_file_does_not_defeat_a_forced_run(
    tmp_path: pathlib.Path,
) -> None:
    statistics = _a_repaired_labelled_score(tmp_path)
    truncate(statistics / "chrom_lengths.json")

    resource_stats(tmp_path, "score", "-f")

    assert _stored(statistics)


def test_a_forced_run_rewrites_a_current_lengths_file_too(
    tmp_path: pathlib.Path,
) -> None:
    statistics = _a_repaired_labelled_score(tmp_path)
    lengths_file = statistics / "chrom_lengths.json"
    before = lengths_file.stat().st_mtime_ns

    resource_stats(tmp_path, "score", "-f")

    assert lengths_file.stat().st_mtime_ns > before


def test_a_dry_run_counts_a_stale_lengths_file_as_needing_update(
    tmp_path: pathlib.Path,
) -> None:
    statistics = _a_repaired_labelled_score(tmp_path)
    (statistics / "chrom_lengths.json").unlink()
    _resync_the_manifest(tmp_path)

    with pytest.raises(SystemExit) as exit_status:
        resource_stats(tmp_path, "score", "--dry-run")

    # The status is the COUNT of resources needing an update (gain#364).
    assert exit_status.value.code == 1


def test_a_dry_run_never_runs_the_tabix_probe_to_check_the_lengths_file(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The gate compares keys; it derives nothing.  On a tabix score the
    probe is the expensive rung the stored file exists to spare."""
    _a_repaired_labelled_score(tmp_path)
    set_label(tmp_path, "score", "reference_genome", "other_genome")
    _resync_the_manifest(tmp_path)
    probe = patch_tabix_probe(mocker)

    with pytest.raises(SystemExit):
        resource_stats(tmp_path, "score", "--dry-run")

    probe.assert_not_called()


def test_a_missing_lengths_file_is_rewritten_without_rebuilding_histograms(
    tmp_path: pathlib.Path,
) -> None:
    """The lengths file has a gate of its own, beside the hash's.

    With ``stats_hash`` current, the histogram gate holds; the absent
    lengths file is what the run repairs, and it repairs only that.
    """
    statistics = _a_repaired_labelled_score(tmp_path)
    (statistics / "chrom_lengths.json").unlink()
    before = _mtimes(statistics)

    resource_stats(tmp_path, "score")

    assert (statistics / "chrom_lengths.json").exists()
    assert _mtimes_except_the_lengths_file(statistics) == before


def test_a_lengths_only_repair_manifests_the_file(
    tmp_path: pathlib.Path,
) -> None:
    """The manifest pass that opens the run drops the missing entry; the
    lengths-only path has to refresh the manifest itself, as a full
    rebuild does."""
    statistics = _a_repaired_labelled_score(tmp_path)
    (statistics / "chrom_lengths.json").unlink()

    resource_stats(tmp_path, "score")

    manifest = (tmp_path / "score" / ".MANIFEST").read_text()
    assert "statistics/chrom_lengths.json" in manifest
