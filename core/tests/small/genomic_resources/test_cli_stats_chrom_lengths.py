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

from gain.genomic_resources.cli import cli_manage
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
    """A tabix score labelled with a genome that lists chr1 but not chrM."""
    (
        a_grr()
        .with_resource(
            "genome",
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
