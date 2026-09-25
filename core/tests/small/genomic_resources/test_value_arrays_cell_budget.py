"""A bulk read's batch is bounded by CELLS, not rows (gain#1717).

A fixed row count per batch let a wide score -- dbNSFP4.9a requests 454
columns -- materialise ~45M raw string cells per batch and OOM-kill its
statistics workers.  The row count of a batch is now derived from
``VALUE_ARRAYS_CELL_BUDGET`` and the number of columns the read fetches,
so a narrow score keeps its 100,000-row batches and a wide one gets
proportionally fewer rows.
"""
# pylint: disable=C0116,W0621
import pathlib

import numpy as np
import pytest
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    PositionScore,
    batch_budget,
)
from gain.genomic_resources.histogram import (
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    scan,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    a_position_score,
    an_allele_score,
)

from tests.small.genomic_resources.histogram_parity import (
    assert_histograms_equal,
)

_WIDE_COLUMNS = 250
_WIDE_ROWS = 60


def _cell(row: int, column: int) -> int:
    return row * 1000 + column


def _wide_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """A single-base position score with ``_WIDE_COLUMNS`` int columns."""
    score_ids = [f"s{column}" for column in range(_WIDE_COLUMNS)]
    header = "  ".join(["chrom", "pos_begin", "pos_end", *score_ids])
    lines = [header]
    for row in range(1, _WIDE_ROWS + 1):
        cells = [str(_cell(row, column)) for column in range(_WIDE_COLUMNS)]
        lines.append("  ".join(["chr1", str(row), str(row), *cells]))
    builder = a_position_score()
    for score_id in score_ids:
        builder = builder.with_score(score_id, "int")
    return (
        builder.with_data("\n".join(lines) + "\n")
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_wide_read_batches_stay_within_the_cell_budget_and_lose_no_row(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 8 rows per batch at 250 columns; the region ends at row 45, which is
    # NOT a multiple of 8, so the region-end cut falls mid-batch.
    monkeypatch.setattr(
        batch_budget, "VALUE_ARRAYS_CELL_BUDGET", 8 * _WIDE_COLUMNS)
    resource = _wide_tabix(tmp_path)
    score_ids = [f"s{column}" for column in range(_WIDE_COLUMNS)]

    with PositionScore(resource).open() as score:
        batches = list(
            score.fetch_region_value_arrays("chr1", 1, 45, score_ids))

    for pos_begin, _pos_end, _values in batches:
        assert len(pos_begin) * _WIDE_COLUMNS <= 8 * _WIDE_COLUMNS
    assert len(batches) == 6  # 5 full batches of 8, then 5 rows
    positions = np.concatenate([batch[0] for batch in batches])
    assert np.array_equal(positions, np.arange(1, 46))
    for column in (0, 137, _WIDE_COLUMNS - 1):
        values = np.concatenate(
            [batch[2][f"s{column}"] for batch in batches])
        assert np.array_equal(
            values, [_cell(row, column) for row in range(1, 46)])


def test_the_shipped_budget_bounds_a_dbnsfp_wide_read() -> None:
    # Every other test here patches the budget; this one pins the SHIPPED
    # constant, so growing it back toward an OOM-sized batch fails a test.
    # 454 is dbNSFP4.9a's score count, the read that motivated gain#1717.
    rows = batch_budget.value_arrays_batch_rows(100_000, 454)

    assert rows * 454 <= 2_000_000
    assert rows < 100_000


def test_a_narrow_read_keeps_its_full_100000_row_batches(
    tmp_path: pathlib.Path,
) -> None:
    rows = 150_000
    lines = ["chrom  pos_begin  pos_end  s1  s2"]
    lines.extend(
        f"chr1  {row}  {row}  {row}  {row % 7}"
        for row in range(1, rows + 1))
    resource = (
        a_position_score()
        .with_score("s1", "int")
        .with_score("s2", "int")
        .with_data("\n".join(lines) + "\n")
        .with_tabix()
        .build_resource(tmp_path)
    )

    with PositionScore(resource).open() as score:
        sizes = [
            len(pos_begin)
            for pos_begin, _pos_end, _values in score.fetch_region_value_arrays(
                "chr1", None, None, ["s1", "s2"])]

    assert sizes == [100_000, 50_000]


def test_an_allele_read_counts_its_reference_and_alternative_columns(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Six scores plus reference and alternative is eight fetched columns:
    # a 64-cell budget allows 8 rows.  Counting the scores alone would
    # allow 10, and 10 rows x 8 columns overruns the budget.
    monkeypatch.setattr(
        batch_budget, "VALUE_ARRAYS_CELL_BUDGET", 64)
    score_ids = [f"s{column}" for column in range(6)]
    lines = ["  ".join(
        ["chrom", "pos_begin", "reference", "alternative", *score_ids])]
    lines.extend(
        "  ".join(["1", str(row), "A", "G", *(["0.5"] * len(score_ids))])
        for row in range(1, 21))
    builder = an_allele_score()
    for score_id in score_ids:
        builder = builder.with_score(score_id, "float")
    resource = (
        builder.with_data("\n".join(lines) + "\n")
        .with_tabix()
        .build_resource(tmp_path)
    )

    with AlleleScore(resource).open() as score:
        sizes = [
            len(batch.pos_begin)
            for batch in score.fetch_region_allele_arrays(
                "1", None, None, score_ids)]

    assert sizes == [8, 8, 4]


def test_both_bulk_passes_over_budgeted_batches_match_the_record_passes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The histogram and the min/max bulk passes read through the same
    # budgeted batches, and cutting a region into them changes nothing
    # either pass computes.  The passes fetch only the three score columns
    # they are asked for, so the budget is sized to THOSE: 8 rows a batch,
    # and the 45-row region crosses five batch boundaries.
    score_ids = ["s0", "s137", f"s{_WIDE_COLUMNS - 1}"]
    monkeypatch.setattr(
        batch_budget, "VALUE_ARRAYS_CELL_BUDGET", 8 * len(score_ids))
    assert batch_budget.value_arrays_batch_rows(
        100_000, len(score_ids)) == 8
    resource = _wide_tabix(tmp_path)
    confs: dict = {
        score_id: NumberHistogramConfig.from_dict({
            "type": "number",
            "view_range": {"min": 0, "max": 50_000},
            "number_of_bins": 10,
            "x_log_scale": False,
            "y_log_scale": False,
        })
        for score_id in score_ids
    }

    bulk_hists = scan.do_histogram_bulk(resource, confs, "chr1", 1, 45)
    record_hists = scan.do_histogram(resource, confs, "chr1", 1, 45)
    bulk_min_max = scan.do_min_max_bulk(resource, score_ids, "chr1", 1, 45)
    record_min_max = scan.do_min_max(resource, score_ids, "chr1", 1, 45)

    for score_id in score_ids:
        bulk_hist = bulk_hists[score_id]
        record_hist = record_hists[score_id]
        assert isinstance(bulk_hist, NumberHistogram)
        assert isinstance(record_hist, NumberHistogram)
        assert_histograms_equal(bulk_hist, record_hist, score_id)
        assert bulk_hist.count == 45
        assert (bulk_min_max[score_id].min, bulk_min_max[score_id].max) \
            == (record_min_max[score_id].min, record_min_max[score_id].max)
