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

from gain.genomic_resources.genomic_scores import build_score_from_resource
from gain.genomic_resources.histogram import NullHistogram
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
)
from gain.genomic_resources.testing.statistics import (
    a_score_with_no_values,
    publish_statistics,
)


def a_built_resource(
    score: PositionScoreBuilder, tmp_path: pathlib.Path,
) -> GenomicResource:
    repo = a_grr().with_resource("scores/pos1", score).build_repo(
        tmp_path / "grr")
    resource = repo.get_resource("scores/pos1")
    publish_statistics(resource)
    return resource


def test_a_score_with_no_values_records_the_missing_range_as_its_reason(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_built_resource(a_score_with_no_values(), tmp_path)

    histogram = build_score_from_resource(resource).get_score_histogram(
        "score")

    assert resource.file_exists("statistics/histogram_score.json")
    assert isinstance(histogram, NullHistogram)
    assert histogram.reason == "min/max for score not found"
