"""The stored chromosome lengths of a score, at the repair seam (gain#1576).

A repair writes ``statistics/chrom_lengths.json`` -- every source's
answer per contig, one block per source, plus what the file was derived
from -- under a freshness gate of its own, beside the statistics hash
rather than inside it: re-pointing the ``reference_genome`` label
refreshes this one file and never the histograms (``docs/source/grr.rst``
promises as much).
"""

import hashlib
import json
import logging
import pathlib
from typing import Any, cast

import pytest
import pytest_mock
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.testing.builders import a_bigwig_score, a_grr

from .test_genomic_scores_impl_chrom_lengths import (
    CHR1_GENOME_LENGTH,
    CHR1_PROBE_BOUND,
    CHRM_PROBE_BOUND,
    a_labelled_tabix_score_grr,
    patch_tabix_probe,
    set_label,
)


def _md5_of(path: pathlib.Path) -> str:
    """The manifest's digest of a file, computed independently of it."""
    return hashlib.md5(  # ruff: ignore[hashlib-insecure-hash-function]
        path.read_bytes()).hexdigest()


def _stored(statistics: pathlib.Path) -> dict[str, Any]:
    """The lengths file as written; raises when it is not JSON."""
    return cast(
        dict[str, Any],
        json.loads((statistics / "chrom_lengths.json").read_text()))


def resource_stats(
    tmp_path: pathlib.Path, resource_id: str, *args: str,
) -> None:
    cli_manage([
        "resource-stats", "-r", resource_id, "-R", str(tmp_path), "-j", "1",
        *args])


def _a_repaired_labelled_score(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build the labelled score and repair it; returns its statistics dir."""
    a_labelled_tabix_score_grr().build_repo(tmp_path)
    resource_stats(tmp_path, "score")
    return tmp_path / "score" / "statistics"


def test_repair_stores_every_sources_answer_and_what_they_derive_from(
    tmp_path: pathlib.Path,
) -> None:
    """The genome lists chr1 and not chrM: chr1 is in both blocks, chrM
    only in the table's."""
    score_dir = tmp_path / "score"

    statistics = _a_repaired_labelled_score(tmp_path)

    assert _stored(statistics) == {
        "format": 1,
        "derived_from": {
            "reference_genome": "genome",
            "files_md5": {
                "data.txt.gz": _md5_of(score_dir / "data.txt.gz"),
                "data.txt.gz.tbi": _md5_of(score_dir / "data.txt.gz.tbi"),
            },
        },
        "sources": {
            "reference_genome": {"chr1": CHR1_GENOME_LENGTH},
            "tabix_estimate": {
                "chr1": CHR1_PROBE_BOUND,
                "chrM": CHRM_PROBE_BOUND,
            },
        },
    }


def test_a_bigwig_score_stores_its_headers_lengths_in_one_block(
    tmp_path: pathlib.Path,
) -> None:
    """Unlabelled, the header is the only source the score has."""
    (
        a_grr()
        .with_resource(
            "score",
            a_bigwig_score()
            .with_data("""
                chr1  10  20  0.1
                chr2  10  20  0.2
            """)
            .with_chrom_lens({"chr1": 100, "chr2": 200}))
        .build_repo(tmp_path)
    )

    resource_stats(tmp_path, "score")

    assert _stored(tmp_path / "score" / "statistics")["sources"] == {
        "bigwig": {"chr1": 100, "chr2": 200},
    }


def test_a_full_build_manifests_the_lengths_file(
    tmp_path: pathlib.Path,
) -> None:
    _a_repaired_labelled_score(tmp_path)

    manifest = (tmp_path / "score" / ".MANIFEST").read_text()
    assert "statistics/chrom_lengths.json" in manifest


def _mtimes(statistics: pathlib.Path) -> dict[pathlib.Path, int]:
    return {path: path.stat().st_mtime_ns for path in statistics.iterdir()}


def _mtimes_except_the_lengths_file(
    statistics: pathlib.Path,
) -> dict[pathlib.Path, int]:
    return {
        path: mtime for path, mtime in _mtimes(statistics).items()
        if path.name != "chrom_lengths.json"}


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
    assert _stored(statistics)["derived_from"]["reference_genome"] == \
        "other_genome"
    assert _mtimes_except_the_lengths_file(statistics) == {
        path: mtime for path, mtime in before.items()
        if path != lengths_file}


def test_an_unchanged_score_has_nothing_rewritten_and_no_table_opened(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The lengths gate holds too: the same key on both sides, compared
    without opening the score."""
    statistics = _a_repaired_labelled_score(tmp_path)
    before = _mtimes(statistics)
    assert statistics / "chrom_lengths.json" in before
    opened = mocker.spy(GenomicScore, "open")

    resource_stats(tmp_path, "score")

    opened.assert_not_called()
    assert _mtimes(statistics) == before


def test_a_dry_run_never_runs_the_tabix_probe_to_check_the_lengths_file(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The gate compares keys; it derives nothing.  On a tabix score the
    probe is the expensive rung the stored file exists to spare."""
    _a_repaired_labelled_score(tmp_path)
    set_label(tmp_path, "score", "reference_genome", "other_genome")
    _resync_the_manifest(tmp_path)
    probe = patch_tabix_probe(mocker)

    with pytest.raises(SystemExit) as exit_status:
        resource_stats(tmp_path, "score", "--dry-run")

    # The status is the COUNT of resources needing an update (gain#364).
    assert exit_status.value.code == 1
    probe.assert_not_called()


def _resync_the_manifest(tmp_path: pathlib.Path) -> None:
    """Bring the manifest up to date with the files as they are now.

    Deleting the lengths file, or editing the config, would otherwise
    make the manifest pass that opens every run report the resource --
    and a dry run counts that too, gate or no gate.  With the manifest
    current, only the lengths gate can count it.
    """
    cli_manage(["resource-manifest", "-r", "score", "-R", str(tmp_path)])


def test_a_table_file_missing_without_a_sidecar_still_fails_the_resource(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Nothing says the bytes exist anywhere; the lengths gate has no
    say, and the resource fails as any read of it would."""
    _a_repaired_labelled_score(tmp_path)
    (tmp_path / "score" / "data.txt.gz").unlink()

    with caplog.at_level(logging.ERROR), \
            pytest.raises(SystemExit) as exit_status:
        resource_stats(tmp_path, "score")

    assert exit_status.value.code == 1
    assert "failed resources" in caplog.text
    assert "score" in caplog.text


@pytest.mark.parametrize("content", [
    pytest.param("", id="truncated"),
    pytest.param('{"format": 0, "sources": {}}', id="another-format"),
    pytest.param(
        '{"format": 1, "derived_from": {"reference_genome": null, '
        '"files_md5": {}}, "sources": {"nonsense": {"chr1": 1}}}',
        id="unknown-source"),
    pytest.param(
        '{"format": 1, "derived_from": {"reference_genome": null, '
        '"files_md5": {}}, "sources": {"tabix_estimate": {"chr1": null}}}',
        id="neither-length-nor-reason"),
])
def test_an_unreadable_lengths_file_is_rewritten_by_an_ordinary_run(
    tmp_path: pathlib.Path, content: str,
) -> None:
    """Unreadable is stale, not fatal: every other statistics file is
    recoverable by a run, and this one must not need a manual rm."""
    statistics = _a_repaired_labelled_score(tmp_path)
    (statistics / "chrom_lengths.json").write_text(content)
    _resync_the_manifest(tmp_path)

    resource_stats(tmp_path, "score")

    assert _stored(statistics)["format"] == 1
    assert _stored(statistics)["sources"]["reference_genome"] == {
        "chr1": CHR1_GENOME_LENGTH}
