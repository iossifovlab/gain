# pylint: disable=C0114,C0116
import logging
import pathlib

import numpy as np
import pytest
from gain.genomic_resources.histogram import (
    NullHistogramConfig,
    NumberHistogramConfig,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    scan,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.min_max import (
    MinMaxValue,
    NullMinMaxValue,
)
from gain.genomic_resources.testing.builders import a_position_score

#: Enough records that a per-record refusal is unmistakably not a per-score
#: one.  The latch is invisible on a single-record region.
REFUSED_RECORD_COUNT = 40


def _text_and_number_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """A resource whose two scores differ in what a min/max can fold."""
    return (
        a_position_score()
        .with_score("s", "str")
        .with_score("f", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s     f
            chr1   1          3        aaa   0.1
            chr1   4          4        bbb   0.5
            chr1   5          10       ccc   0.95
            chr1   11         20       ddd   1.0
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


@pytest.mark.parametrize("region_order", ["refused first", "refused last"])
def test_one_region_refusing_nullifies_a_score_the_others_folded(
    region_order: str,
) -> None:
    """A refusal cannot be diluted by the regions that did fold values.

    ``merge_histograms`` already works this way -- one region's
    ``NullHistogram`` nullifies the score -- and min/max has to match, or a
    score refused in one region would be binned over a view range built from
    only the records that happened to fold, with nothing saying so.
    """
    folded = {"s": MinMaxValue("s", 0.1, 1.0)}
    refused = {"s": NullMinMaxValue("s", "refused in this region")}
    regions = [folded, refused] if region_order == "refused last" \
        else [refused, folded]
    hist_confs: dict = {"s": NumberHistogramConfig.default_config(None)}

    merged = scan.merge_min_max(["s"], hist_confs, *regions)

    assert isinstance(merged["s"], NullHistogramConfig)


def test_a_refused_scores_null_config_names_the_score_and_the_cause() -> None:
    """A refusal and an empty region are not the same resource fact.

    Both reach the nan handling with no values to bin, and both nullify -- but
    "there were no values here" and "the values could not be folded" send a
    curator to different places, so the config says which.
    """
    # A score id no other word in the reason can spell: a one-letter id
    # "matches" reason text that never named the score at all.
    score_id = "refusable_score"
    refusal_reason = "Cannot add non numerical value 'aaa'"
    refused: dict = {score_id: NullMinMaxValue(score_id, refusal_reason)}
    hist_confs: dict = {
        score_id: NumberHistogramConfig.default_config(None)}

    updated = scan.update_hist_confs(hist_confs, refused)

    assert isinstance(updated[score_id], NullHistogramConfig)
    assert score_id in updated[score_id].reason
    assert refusal_reason in updated[score_id].reason


def _many_rowed_text_tabix(
    tmp_path: pathlib.Path, record_count: int,
) -> GenomicResource:
    """A text score spread over many single-position records."""
    rows = "\n".join(
        f"chr1  {pos}  {pos}  v{pos}"
        for pos in range(1, record_count + 1))
    return (
        a_position_score()
        .with_score("s", "str")
        .with_data(f"chrom  pos_begin  pos_end  s\n{rows}")
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_the_refusal_is_reported_once_per_score_not_once_per_record(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The latch, seen from outside: one report, not one per record.

    A per-value catch that did not swap the accumulator out would raise, log
    and recover on EVERY record for the rest of the region -- on a
    genome-scale resource, multiples of the cost of the pass it protects.
    """
    resource = _many_rowed_text_tabix(tmp_path, REFUSED_RECORD_COUNT)

    with caplog.at_level(logging.ERROR):
        scan.do_min_max(resource, ["s"], "chr1", 1, REFUSED_RECORD_COUNT)

    refusals = [
        record for record in caplog.records
        if "min/max" in record.getMessage()
    ]
    assert len(refusals) == 1


def test_a_refused_score_does_not_cost_the_others_their_min_max(
    tmp_path: pathlib.Path,
) -> None:
    """One score's refusal leaves the rest of the resource's min/max.

    The containment ``do_histogram`` has and ``do_min_max`` did not: a value
    the reducer cannot fold nullifies that ONE score, where it used to abort
    the whole statistics build (gain#1285, gain#1313).
    """
    resource = _text_and_number_tabix(tmp_path)

    result = scan.do_min_max(resource, ["s", "f"], "chr1", 1, 20)

    assert isinstance(result["s"], NullMinMaxValue)
    assert np.isnan(result["s"].min)
    assert np.isnan(result["s"].max)
    assert (result["f"].min, result["f"].max) == (0.1, 1.0)


def test_a_refused_score_leaves_the_others_exactly_their_solo_min_max(
    tmp_path: pathlib.Path,
) -> None:
    """Scanning beside a refused score measures what scanning alone does.

    Stronger than the extremes being right: the refusal must not perturb the
    record partition the other scores reduce.
    """
    resource = _text_and_number_tabix(tmp_path)

    beside_refused = scan.do_min_max(resource, ["s", "f"], "chr1", 1, 20)
    alone = scan.do_min_max(resource, ["f"], "chr1", 1, 20)

    assert (beside_refused["f"].min, beside_refused["f"].max) == \
        (alone["f"].min, alone["f"].max)
