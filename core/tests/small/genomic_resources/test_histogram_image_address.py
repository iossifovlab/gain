# pylint: disable=C0116
"""Whether a score's histogram image has an address at all.

An address is a promise that the file is there, and two things decide it.
A histogram annulled *by definition* never gets a PNG drawn, so it has
no address (gain#1025); what "by definition" means is stated once on
``ScoreResource.get_histogram_config``.  A histogram the definition
configures is only a plan to draw, and the statistics build can decline
it -- a nan range, a value it cannot fold -- so beyond that the address
follows the resource's manifest: the image is addressed when listed, and
a resource with no stored manifest falls back to the definition
(gain#1533).  These tests pin the two accessors that answer so.
"""
import pathlib
from collections.abc import Callable

import pytest
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    build_score_from_resource,
)
from gain.genomic_resources.repository import (
    GR_MANIFEST_FILE_NAME,
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
    a_position_score,
)
from gain.genomic_resources.testing.statistics import (
    a_score_with_no_values,
    publish_statistics,
)

PUBLIC_URL = "http://grr.example.org"


def a_repo_with(
    score: PositionScoreBuilder, tmp_path: pathlib.Path,
) -> GenomicResourceRepo:
    return (
        a_grr()
        .with_resource("scores/pos1", score)
        .with_public_url(PUBLIC_URL)
        .build_repo(tmp_path / "grr")
    )


def an_annulled_score() -> PositionScoreBuilder:
    """``score`` states ``histogram: {type: null}``."""
    return (
        a_position_score()
        .with_score("score", "float")
        .with_histogram({"type": "null", "reason": "annulled by design"})
    )


def a_score_of_a_type_with_no_default_histogram() -> PositionScoreBuilder:
    """``score`` states no ``histogram:`` block and is ``bool``, which has
    no default histogram -- annulled by the definition just the same."""
    return (
        a_position_score()
        .with_score("score", "bool")
        .with_score_line(chrom="1", pos_begin=10, score=True)
    )


ANNULLED_BY_DEFINITION = [
    an_annulled_score, a_score_of_a_type_with_no_default_histogram,
]


@pytest.mark.parametrize("annulled", ANNULLED_BY_DEFINITION)
def test_an_annulled_histogram_is_not_addressed_even_with_an_image_listed(
    annulled: Callable[[], PositionScoreBuilder], tmp_path: pathlib.Path,
) -> None:
    # An image left behind by a build from before the curator annulled the
    # histogram: the manifest lists it, the definition disowns it.  The
    # definition wins -- a stale drawing is not this score's histogram.
    repo = a_repo_with(
        annulled().with_file("statistics/histogram_score.png", "stale"),
        tmp_path)
    resource = repo.get_resource("scores/pos1")
    assert "statistics/histogram_score.png" in resource.get_manifest()
    score = build_score_from_resource(resource)

    assert score.get_histogram_image_url("score") is None
    assert score.get_histogram_image_public_url("score") is None


def an_unrepaired_score(
    builder: PositionScoreBuilder, tmp_path: pathlib.Path,
) -> tuple[GenomicResource, GenomicScore]:
    """The score realized with no manifest -- neither built nor stored."""
    root = tmp_path / "grr"
    builder.realize_into(root / "scores/pos1")
    proto = build_filesystem_test_protocol(root, repair=False)
    resource = proto.get_resource("scores/pos1")
    assert resource.get_loaded_manifest() is None
    return resource, build_score_from_resource(resource)


def test_a_resource_with_no_stored_manifest_is_addressed_by_its_definition(
    tmp_path: pathlib.Path,
) -> None:
    # No file list to consult, so the definition is all there is to go
    # on.  Consulting must not BUILD one: that is an md5 scan of the whole
    # resource, and it writes state the address has no business writing.
    resource, score = an_unrepaired_score(a_position_score(), tmp_path)

    url = score.get_histogram_image_url("score")

    assert url == f"{resource.get_url()}/statistics/histogram_score.png"
    assert resource.get_loaded_manifest() is None
    resource_dir = tmp_path / "grr" / "scores/pos1"
    assert not (resource_dir / GR_MANIFEST_FILE_NAME).exists()
    assert not (resource_dir / ".grr").exists()


@pytest.mark.parametrize("annulled", ANNULLED_BY_DEFINITION)
def test_an_annulled_histogram_with_no_stored_manifest_is_not_addressed(
    annulled: Callable[[], PositionScoreBuilder], tmp_path: pathlib.Path,
) -> None:
    # The no-manifest fallback is to the definition, and the definition
    # says annulled: the fallback must not answer past that rule.
    _resource, score = an_unrepaired_score(annulled(), tmp_path)

    assert score.get_histogram_image_url("score") is None
    assert score.get_histogram_image_public_url("score") is None


def test_a_numeric_score_is_not_addressed_before_its_statistics_are_built(
    tmp_path: pathlib.Path,
) -> None:
    # The manifest is the list of files a mirror can serve, and this one
    # lists no image yet: an address now would promise a file the build
    # has not written (gain#1533).  A configured histogram is a plan to
    # draw, not a drawing.
    repo = a_repo_with(a_position_score(), tmp_path)
    score = build_score_from_resource(repo.get_resource("scores/pos1"))

    assert score.get_histogram_image_url("score") is None
    assert score.get_histogram_image_public_url("score") is None


def test_a_histogram_the_build_drew_is_addressed(
    tmp_path: pathlib.Path,
) -> None:
    repo = a_repo_with(a_position_score(), tmp_path)
    resource = repo.get_resource("scores/pos1")
    publish_statistics(resource)
    score = build_score_from_resource(resource)

    assert score.get_histogram_image_url("score") == \
        f"{resource.get_url()}/statistics/histogram_score.png"
    assert score.get_histogram_image_public_url("score") == \
        f"{PUBLIC_URL}/scores/pos1/statistics/histogram_score.png"


def test_a_histogram_nullified_by_its_build_has_no_image_address(
    tmp_path: pathlib.Path,
) -> None:
    # Configured, not annulled: the build nullifies it and draws
    # nothing, so the manifest lists no image for it.
    repo = a_repo_with(a_score_with_no_values(), tmp_path)
    resource = repo.get_resource("scores/pos1")
    publish_statistics(resource)
    assert not resource.file_exists("statistics/histogram_score.png")
    score = build_score_from_resource(resource)

    assert score.get_histogram_image_url("score") is None
    assert score.get_histogram_image_public_url("score") is None
