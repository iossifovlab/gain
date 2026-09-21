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
import pathlib
from typing import Any, cast

from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.testing.builders import a_bigwig_score, a_grr

from .test_genomic_scores_impl_chrom_lengths import (
    CHR1_GENOME_LENGTH,
    CHR1_PROBE_BOUND,
    CHRM_PROBE_BOUND,
    a_labelled_tabix_score_grr,
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
