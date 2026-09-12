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
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    a_reference_genome,
)

#: The genome's exact length for chr1, past every row of the score.
_CHR1_GENOME_LENGTH = 3000
#: The tabix probe's bound for a lone chrM row at 40, as the region-split
#: pin in test_genomic_scores_impl measures for the same rows.
_CHRM_PROBE_BOUND = 48


def _a_labelled_tabix_score_repo(
    tmp_path: pathlib.Path, *, genome_id: str = "genome",
) -> None:
    """A tabix score labelled with a genome that lists chr1 but not chrM.

    A second genome, ``other_genome``, sits beside it for the label to be
    re-pointed at.
    """
    (
        a_grr()
        .with_resource(
            "genome",
            a_reference_genome()
            .with_chromosome("chr1", "A" * _CHR1_GENOME_LENGTH))
        .with_resource(
            "other_genome",
            a_reference_genome()
            .with_chromosome("chr1", "A" * _CHR1_GENOME_LENGTH))
        .with_resource(
            "score",
            a_position_score()
            .with_score("score", "float")
            .with_data("""
                chrom  pos_begin  score
                chr1   10         0.1
                chr1   2500       0.2
                chrM   40         0.3
            """)
            .with_tabix()
            .with_labels(reference_genome=genome_id))
        .build_repo(tmp_path)
    )


def _md5_of(path: pathlib.Path) -> str:
    """The manifest's digest of a file, computed independently of it."""
    return hashlib.md5(  # ruff: ignore[hashlib-insecure-hash-function]
        path.read_bytes()).hexdigest()


def _resource_stats(tmp_path: pathlib.Path, *extra: str) -> None:
    cli_manage([
        "resource-stats", "-r", "score", "-R", str(tmp_path), "-j", "1",
        *extra])


def _mtimes(statistics: pathlib.Path) -> dict[pathlib.Path, int]:
    return {path: path.stat().st_mtime_ns for path in statistics.iterdir()}


def _a_repaired_labelled_score(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build the labelled score and repair it; returns its statistics dir."""
    _a_labelled_tabix_score_repo(tmp_path)
    _resource_stats(tmp_path)
    return tmp_path / "score" / "statistics"


def _repoint_the_label(tmp_path: pathlib.Path, genome_id: str) -> None:
    config = tmp_path / "score" / GR_CONF_FILE_NAME
    config.write_text(config.read_text().replace(
        "reference_genome: genome", f"reference_genome: {genome_id}"))


def _stored_derived_from(statistics: pathlib.Path) -> dict[str, Any]:
    document = json.loads((statistics / "chrom_lengths.json").read_text())
    return cast(dict[str, Any], document["derived_from"])


def test_repair_stores_the_ladders_answer_and_what_it_was_derived_from(
    tmp_path: pathlib.Path,
) -> None:
    _a_labelled_tabix_score_repo(tmp_path)
    score_dir = tmp_path / "score"

    _resource_stats(tmp_path)

    stored = json.loads(
        (score_dir / "statistics" / "chrom_lengths.json").read_text())
    assert stored == {
        "derived_from": {
            "reference_genome": "genome",
            "files_md5": {
                "data.txt.gz": _md5_of(score_dir / "data.txt.gz"),
                "data.txt.gz.tbi": _md5_of(score_dir / "data.txt.gz.tbi"),
            },
        },
        "lengths": {
            "chr1": {
                "length": _CHR1_GENOME_LENGTH,
                "source": "REFERENCE_GENOME",
                "extent": None,
            },
            "chrM": {
                "length": _CHRM_PROBE_BOUND,
                "source": "TABIX_ESTIMATE",
                "extent": None,
            },
        },
    }


def test_a_full_build_manifests_the_lengths_file(
    tmp_path: pathlib.Path,
) -> None:
    _a_labelled_tabix_score_repo(tmp_path)

    _resource_stats(tmp_path)

    manifest = (tmp_path / "score" / ".MANIFEST").read_text()
    assert "statistics/chrom_lengths.json" in manifest


def test_an_unchanged_score_has_nothing_rewritten_by_a_second_run(
    tmp_path: pathlib.Path,
) -> None:
    """The lengths gate holds too: the same key on both sides."""
    statistics = _a_repaired_labelled_score(tmp_path)
    before = _mtimes(statistics)
    assert statistics / "chrom_lengths.json" in before

    _resource_stats(tmp_path)

    assert _mtimes(statistics) == before


def test_repointing_the_label_rewrites_the_lengths_file_alone(
    tmp_path: pathlib.Path,
) -> None:
    """Adding or re-pointing the label needs no data rebuild -- the
    promise ``grr.rst`` makes.  The hash never learns about the label;
    the lengths file's own gate is what notices it."""
    statistics = _a_repaired_labelled_score(tmp_path)
    before = _mtimes(statistics)
    _repoint_the_label(tmp_path, "other_genome")

    _resource_stats(tmp_path)

    after = _mtimes(statistics)
    lengths_file = statistics / "chrom_lengths.json"
    assert after[lengths_file] > before[lengths_file]
    assert {p: m for p, m in after.items() if p != lengths_file} == {
        p: m for p, m in before.items() if p != lengths_file}


def test_the_rewritten_lengths_file_names_the_new_label(
    tmp_path: pathlib.Path,
) -> None:
    statistics = _a_repaired_labelled_score(tmp_path)
    _repoint_the_label(tmp_path, "other_genome")

    _resource_stats(tmp_path)

    assert _stored_derived_from(statistics)["reference_genome"] == \
        "other_genome"


def test_a_label_whose_genome_does_not_resolve_is_stored_as_no_label(
    tmp_path: pathlib.Path,
) -> None:
    """The key records the label the genome was resolved FROM.

    A genome the repository does not have contributes nothing to the
    ladder, so the file says so -- rather than pinning the table's
    answer to a label that never took part.
    """
    _a_labelled_tabix_score_repo(tmp_path, genome_id="late_genome")

    _resource_stats(tmp_path)

    stored = json.loads(
        (tmp_path / "score" / "statistics" / "chrom_lengths.json")
        .read_text())
    assert stored["derived_from"]["reference_genome"] is None
    assert stored["lengths"]["chr1"]["source"] == "TABIX_ESTIMATE"


def test_a_genome_that_turns_up_later_refreshes_the_lengths_file_alone(
    tmp_path: pathlib.Path,
) -> None:
    """Recorded as none, the label reads as a change once it resolves --
    so the ordinary run picks the genome up, and no ``-f`` is needed."""
    _a_labelled_tabix_score_repo(tmp_path, genome_id="late_genome")
    _resource_stats(tmp_path)
    statistics = tmp_path / "score" / "statistics"
    before = _mtimes(statistics)
    (a_reference_genome()
     .with_chromosome("chr1", "A" * _CHR1_GENOME_LENGTH)
     .build_resource(tmp_path / "late_genome"))

    _resource_stats(tmp_path)

    assert _stored_derived_from(statistics)["reference_genome"] == \
        "late_genome"
    assert _mtimes(statistics)[statistics / "stats_hash"] == \
        before[statistics / "stats_hash"]


def _truncate(path: pathlib.Path) -> None:
    """What a repair killed mid-write leaves behind."""
    path.write_text(path.read_text()[:40])


def test_a_truncated_lengths_file_is_rewritten_by_an_ordinary_run(
    tmp_path: pathlib.Path,
) -> None:
    """Unreadable is stale, not fatal: every other statistics file is
    recoverable by a run, and this one must not need a manual rm."""
    statistics = _a_repaired_labelled_score(tmp_path)
    _truncate(statistics / "chrom_lengths.json")

    _resource_stats(tmp_path)

    assert json.loads((statistics / "chrom_lengths.json").read_text())


def test_a_truncated_lengths_file_does_not_defeat_a_forced_run(
    tmp_path: pathlib.Path,
) -> None:
    statistics = _a_repaired_labelled_score(tmp_path)
    _truncate(statistics / "chrom_lengths.json")

    _resource_stats(tmp_path, "-f")

    assert json.loads((statistics / "chrom_lengths.json").read_text())


def test_a_forced_run_rewrites_a_current_lengths_file_too(
    tmp_path: pathlib.Path,
) -> None:
    statistics = _a_repaired_labelled_score(tmp_path)
    lengths_file = statistics / "chrom_lengths.json"
    before = lengths_file.stat().st_mtime_ns

    _resource_stats(tmp_path, "-f")

    assert lengths_file.stat().st_mtime_ns > before


def _resync_the_manifest(tmp_path: pathlib.Path) -> None:
    """Bring the manifest up to date with the files as they are now.

    Deleting the lengths file, or editing the config, would otherwise
    make the manifest pass that opens every run report the resource --
    and a dry run counts that too, gate or no gate.  With the manifest
    current, only the lengths gate can count it.
    """
    cli_manage(["resource-manifest", "-r", "score", "-R", str(tmp_path)])


def test_a_dry_run_counts_a_stale_lengths_file_as_needing_update(
    tmp_path: pathlib.Path,
) -> None:
    statistics = _a_repaired_labelled_score(tmp_path)
    (statistics / "chrom_lengths.json").unlink()
    _resync_the_manifest(tmp_path)

    with pytest.raises(SystemExit) as exit_status:
        _resource_stats(tmp_path, "--dry-run")

    # The status is the COUNT of resources needing an update (gain#364).
    assert exit_status.value.code == 1


def test_a_dry_run_never_runs_the_tabix_probe_to_check_the_lengths_file(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The gate compares keys; it derives nothing.  On a tabix score the
    probe is the expensive rung the stored file exists to spare."""
    _a_repaired_labelled_score(tmp_path)
    _repoint_the_label(tmp_path, "other_genome")
    _resync_the_manifest(tmp_path)
    probe = mocker.patch(
        "gain.genomic_resources.genomic_position_table.table_tabix"
        ".get_chromosome_length_tabix")

    with pytest.raises(SystemExit):
        _resource_stats(tmp_path, "--dry-run")

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

    _resource_stats(tmp_path)

    assert (statistics / "chrom_lengths.json").exists()
    assert {
        path: mtime for path, mtime in _mtimes(statistics).items()
        if path.name != "chrom_lengths.json"
    } == before


def test_a_lengths_only_repair_manifests_the_file(
    tmp_path: pathlib.Path,
) -> None:
    """The manifest pass that opens the run drops the missing entry; the
    lengths-only path has to refresh the manifest itself, as a full
    rebuild does."""
    statistics = _a_repaired_labelled_score(tmp_path)
    (statistics / "chrom_lengths.json").unlink()

    _resource_stats(tmp_path)

    manifest = (tmp_path / "score" / ".MANIFEST").read_text()
    assert "statistics/chrom_lengths.json" in manifest
