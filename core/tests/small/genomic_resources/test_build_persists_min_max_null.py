# pylint: disable=C0116
"""A histogram the statistics build nullifies in its min/max pass is
recorded, with that pass's reason, exactly as one nullified accumulating
already is (gain#1555).

The build has two places to give up on a configured number histogram:
the min/max pass, when the score has no values to range over or the
pass refuses its values, and the histogram pass, when a value or a
merge cannot be folded.  The second always left a ``NullHistogram``
with its reason in the resource's statistics; the first used to leave
nothing, so a reader saw ``load_histogram``'s "file not found" fallback
and could not tell a score without values from statistics never built.
"""
import pathlib
from collections.abc import Callable
from typing import Any

import pytest
import pytest_mock
from gain.genomic_resources.genomic_scores import (
    PositionScore,
    build_score_from_resource,
)
from gain.genomic_resources.histogram import NullHistogram
from gain.genomic_resources.implementations.genomic_scores_impl import (
    scan,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.statistics.min_max import NullMinMaxValue
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
    a_position_score,
)
from gain.genomic_resources.testing.statistics import (
    a_score_with_no_values,
    publish_statistics,
    refresh_manifest,
)

from tests.small.genomic_resources.info_page_html import table_after


def a_built_resource(
    score: PositionScoreBuilder, tmp_path: pathlib.Path,
    **build_kwargs: Any,
) -> GenomicResource:
    repo = a_grr().with_resource("scores/pos1", score).build_repo(
        tmp_path / "grr")
    resource = repo.get_resource("scores/pos1")
    publish_statistics(resource, **build_kwargs)
    return resource


#: Split into regions -- five of them over the fixture's contig, so the
#: reason has to survive ``merge_histograms`` unwrapped -- and the
#: one-task ``--region-size 0`` build that folds contigs in-process: the
#: same stages, two schedulings.
@pytest.mark.parametrize("region_size", [5, 0])
def test_a_score_with_no_values_records_the_missing_range_as_its_reason(
    tmp_path: pathlib.Path, region_size: int,
) -> None:
    resource = a_built_resource(
        a_score_with_no_values(), tmp_path, region_size=region_size)

    histogram = build_score_from_resource(resource).get_score_histogram(
        "score")

    assert resource.file_exists("statistics/histogram_score.json")
    assert isinstance(histogram, NullHistogram)
    assert histogram.reason == "min/max for score not found"


def test_a_score_with_no_values_draws_nothing_and_drops_an_earlier_image(
    tmp_path: pathlib.Path,
) -> None:
    # The same score built twice: with values, then after every value went
    # NA.  An image the first build drew would show values the statistics
    # no longer have.
    had_values = a_score_with_no_values().with_data("""
        chrom  pos_begin  score
        1      10         0.25
        1      20         0.75
    """)
    resource = a_built_resource(had_values, tmp_path)
    assert resource.file_exists("statistics/histogram_score.png")
    resource = a_grr().with_resource(
        "scores/pos1", a_score_with_no_values(),
    ).build_repo(tmp_path / "grr").get_resource("scores/pos1")

    publish_statistics(resource)

    assert not resource.file_exists("statistics/histogram_score.png")
    assert resource.file_exists("statistics/histogram_score.json")


def test_a_histogram_annulled_by_definition_still_writes_no_file(
    tmp_path: pathlib.Path,
) -> None:
    """Only what the BUILD found out is recorded (gain#305 stays).

    Three scores in one resource: annulled by its definition, nullified
    by the build, and ordinary.  The recording is per score, so the
    ordinary one keeps its histogram and image beside the other two.
    """
    resource = a_built_resource(
        a_score_with_no_values()
        .with_score("annulled", "float")
        .with_histogram({"type": "null", "reason": "opted out"})
        .with_score("ordinary", "float")
        .with_data("""
            chrom  pos_begin  score  annulled  ordinary
            1      10         NA     0.1       0.25
            1      20         NA     0.9       0.75
        """),
        tmp_path)

    assert not resource.file_exists("statistics/histogram_annulled.json")
    assert resource.file_exists("statistics/histogram_score.json")
    assert resource.file_exists("statistics/histogram_ordinary.json")
    assert resource.file_exists("statistics/histogram_ordinary.png")


def test_the_score_page_reports_the_missing_range_not_a_missing_file(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_built_resource(a_score_with_no_values(), tmp_path)

    page = build_resource_implementation(resource).get_info()

    _header, row = table_after(page, "<h2>Scores (1)</h2>").text
    assert "No histogram: min/max for score not found" in row
    assert "Histogram file not found" not in page


#: Which of the score's reads the histogram pass goes through: the
#: per-record scan over a plain table, the vectorized one over tabix.
_READS = [
    (lambda score: score, "region_values_from_records"),
    (lambda score: score.with_tabix(), "fetch_region_value_arrays"),
]


@pytest.mark.parametrize(("table", "read"), _READS, ids=["records", "bulk"])
def test_recording_a_nullified_score_does_not_widen_what_the_scan_reads(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
    table: Callable[[PositionScoreBuilder], PositionScoreBuilder],
    read: str,
) -> None:
    """A score nullified for want of a min/max is not a scanned column.

    ADR 0020: the segments a build stores are cut on the columns the
    histogram pass reads, and a score whose histogram the build nullified
    is not among them.  Recording its null histogram must not put it back.
    """
    resource = (
        a_grr()
        .with_resource("scores/pos1", table(
            a_score_with_no_values()
            .with_score("ordinary", "float")
            .with_data("""
                chrom  pos_begin  score  ordinary
                1      10         NA     0.25
                1      20         NA     0.75
            """)))
        .build_repo(tmp_path / "grr")
        .get_resource("scores/pos1")
    )
    min_max_scores, hist_confs = scan.unpack_score_defs(resource)
    hist_confs = scan.merge_min_max(
        min_max_scores, hist_confs,
        scan.do_min_max_task(resource, min_max_scores, "1", 1, 100))
    reads = mocker.spy(PositionScore, read)

    result = scan.do_histogram_task(resource, hist_confs, "1", 1, 100)

    assert isinstance(result.histograms["score"], NullHistogram)
    assert reads.call_count > 0
    assert [call.args[-1] for call in reads.call_args_list] == \
        [["ordinary"]] * reads.call_count


def test_a_score_the_min_max_pass_refused_records_the_refusal_as_its_reason(
    tmp_path: pathlib.Path,
) -> None:
    """The other min/max-stage nullification, run through the build's own
    stages with only the min/max region result stood in.

    No file format yields a value the min/max pass refuses that the score's
    construction would not have refused first (gain#1336), so the refusal
    is supplied as a region's ``NullMinMaxValue`` -- the shape a region
    task returns -- and everything downstream of it is the build's.
    """
    resource = (
        a_grr()
        .with_resource("scores/pos1", a_position_score()
                       .with_score("score", "float")
                       .with_histogram({"type": "number"})
                       .with_score_line(chrom="1", pos_begin=10, score=0.5))
        .build_repo(tmp_path / "grr")
        .get_resource("scores/pos1")
    )
    min_max_scores, hist_confs = scan.unpack_score_defs(resource)
    hist_confs = scan.merge_min_max(
        min_max_scores, hist_confs,
        {"score": NullMinMaxValue("score", "Cannot add non numerical value")})
    scan.merge_and_save_histograms(
        resource, scan.do_histogram_task(resource, hist_confs, "1", 1, 100))
    refresh_manifest(resource)

    histogram = build_score_from_resource(resource).get_score_histogram(
        "score")

    assert isinstance(histogram, NullHistogram)
    assert histogram.reason == \
        "min/max for score refused: Cannot add non numerical value"
