# pylint: disable=C0116
"""A histogram annulled *by definition* reads back its configured reason.

Such a score writes no statistics file (gain#305), so the reason cannot
come from one: it comes from the definition, which
``ScoreResource.get_histogram_config`` resolves.  Before gain#1604 the
reader went to the files regardless and reported ``load_histogram``'s
``Histogram file not found.`` fallback in place of the reason.
"""
import pathlib

import pytest
from gain.gene_scores.gene_scores import build_gene_score_from_resource
from gain.genomic_resources.genomic_scores import build_score_from_resource
from gain.genomic_resources.histogram import (
    NullHistogram,
    truncated_histogram_filename,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_gene_score,
    a_position_score,
)
from gain.genomic_resources.testing.statistics import publish_statistics


def an_opted_out_score() -> PositionScoreBuilder:
    """``annulled`` states ``histogram: {type: null, reason: opted out}``."""
    return (
        a_position_score()
        .with_score("annulled", "float")
        .with_histogram({"type": "null", "reason": "opted out"})
        .with_data("""
            chrom  pos_begin  annulled
            1      10         0.1
            1      20         0.9
        """)
    )


def a_built_resource(
    score: PositionScoreBuilder, tmp_path: pathlib.Path,
) -> GenomicResource:
    resource = score.build_resource(tmp_path)
    publish_statistics(resource)
    return resource


def a_stale_histogram(tmp_path: pathlib.Path) -> bytes:
    """The JSON an earlier, numeric definition of ``annulled`` stored."""
    numeric = a_built_resource(
        an_opted_out_score().with_histogram(
            {"type": "number", "number_of_bins": 10}),
        tmp_path / "numeric")
    with numeric.open_raw_file("statistics/histogram_annulled.json") as inf:
        return inf.read()


def test_a_built_score_reads_back_its_configured_reason(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_built_resource(an_opted_out_score(), tmp_path)

    histogram = build_score_from_resource(resource).get_score_histogram(
        "annulled")

    assert isinstance(histogram, NullHistogram)
    assert histogram.reason == "opted out"


def test_a_truncated_read_reads_back_the_configured_reason(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_built_resource(an_opted_out_score(), tmp_path)

    histogram = build_score_from_resource(resource).get_score_histogram(
        "annulled", truncated=True)

    assert isinstance(histogram, NullHistogram)
    assert histogram.reason == "opted out"


def test_a_score_never_built_reads_back_the_configured_reason(
    tmp_path: pathlib.Path,
) -> None:
    # Decided from the definition alone: a checkout whose statistics were
    # never built answers the same as the published resource.
    resource = an_opted_out_score().build_resource(tmp_path)

    histogram = build_score_from_resource(resource).get_score_histogram(
        "annulled")

    assert isinstance(histogram, NullHistogram)
    assert histogram.reason == "opted out"


def test_a_stale_histogram_file_does_not_override_the_definition(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        an_opted_out_score()
        .with_file("statistics/histogram_annulled.json",
                   a_stale_histogram(tmp_path))
        .build_resource(tmp_path / "annulled")
    )
    assert "statistics/histogram_annulled.json" in resource.get_manifest()

    histogram = build_score_from_resource(resource).get_score_histogram(
        "annulled")

    assert isinstance(histogram, NullHistogram)
    assert histogram.reason == "opted out"


@pytest.mark.parametrize("truncated", [False, True])
def test_a_stale_truncated_sidecar_does_not_override_the_definition(
    tmp_path: pathlib.Path, truncated: bool,
) -> None:
    # A sidecar without its full file: for a histogram the definition
    # keeps, that is an unpulled DVC blob (HistogramError); for one it
    # annuls, the sidecar is stale and never read.
    sidecar = truncated_histogram_filename(
        "statistics/histogram_annulled.json")
    resource = (
        an_opted_out_score()
        .with_file(sidecar, a_stale_histogram(tmp_path))
        .build_resource(tmp_path / "annulled")
    )
    assert sidecar in resource.get_manifest()

    histogram = build_score_from_resource(resource).get_score_histogram(
        "annulled", truncated=truncated)

    assert isinstance(histogram, NullHistogram)
    assert histogram.reason == "opted out"


def test_a_gene_score_reads_back_its_configured_reason(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_gene_score()
        .with_score("annulled", "float")
        .with_histogram({"type": "null", "reason": "opted out"})
        .with_data("""
            gene  annulled
            G1    0.1
            G2    0.9
        """)
        .build_resource(tmp_path)
    )

    histogram = build_gene_score_from_resource(
        resource).get_score_histogram("annulled")

    assert isinstance(histogram, NullHistogram)
    assert histogram.reason == "opted out"


def test_a_type_without_a_default_histogram_reads_back_why(
    tmp_path: pathlib.Path,
) -> None:
    # No histogram block, and ``bool`` has no default histogram: the
    # definition resolves to a null config of its own.
    resource = (
        a_position_score()
        .with_score("flag", "bool")
        .with_data("""
            chrom  pos_begin  flag
            1      10         true
            1      20         false
        """)
        .build_resource(tmp_path)
    )

    histogram = build_score_from_resource(resource).get_score_histogram(
        "flag")

    assert isinstance(histogram, NullHistogram)
    assert histogram.reason == (
        "No histogram configured and no default config available for "
        "type bool")
