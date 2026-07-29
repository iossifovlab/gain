# pylint: disable=C0114,C0116,W0212,W0621
import pathlib

import numpy as np
from gain.genomic_resources.histogram import (
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation as G,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import an_allele_score


def _hist_conf() -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 1},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": False,
    })


def _assert_hists_equal(
    bulk: dict[str, NumberHistogram],
    ref: dict[str, NumberHistogram],
) -> None:
    assert set(bulk) == set(ref)
    for score_id in ref:
        got, want = bulk[score_id], ref[score_id]
        assert np.array_equal(got.bars, want.bars), \
            (score_id, got.bars, want.bars)
        assert got.out_of_range_bins == want.out_of_range_bins, score_id
        assert np.array_equal(
            [got.min_value], [want.min_value], equal_nan=True), score_id
        assert np.array_equal(
            [got.max_value], [want.max_value], equal_nan=True), score_id


def _allele_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """An allele score with THREE records sharing position 10."""
    return (
        an_allele_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
            chr1   10         A          C            0.2
            chr1   10         A          T            0.9
            chr1   14         C          T            0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_allele_score_is_bulk_scan_eligible(tmp_path: pathlib.Path) -> None:
    assert G._bulk_scan_eligible(_allele_tabix(tmp_path), ["s"])


def test_bulk_histogram_matches_per_record_allele_shared_position(
    tmp_path: pathlib.Path,
) -> None:
    # Three records sit at position 10 (distinct ref/alt) -- which is what an
    # allele score IS, and what the position-score overlap guard rejects.
    resource = _allele_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}
    ref = G._do_histogram(resource, confs, "chr1", 1, 20)
    bulk = G._do_histogram_bulk(resource, confs, "chr1", 1, 20)
    _assert_hists_equal(bulk, ref)
    # Every record weighs 1, so the bars hold one count per record.
    assert bulk["s"].bars.sum() == 4
