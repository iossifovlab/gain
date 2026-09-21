# pylint: disable=C0116
"""Whether a score's histogram image has an address at all.

A score whose histogram is annulled *by definition* never gets a PNG
written for it, so an address for one points at a file nobody writes
(gain#1025).  What "by definition" means, and why the loaded statistics
are the wrong oracle, is stated once on
``ScoreResource.get_histogram_config``; these tests pin the two accessors
that answer from it.
"""
import pathlib

from gain.genomic_resources.genomic_scores import build_score_from_resource
from gain.genomic_resources.repository import (
    GR_MANIFEST_FILE_NAME,
    GenomicResourceRepo,
)
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
    a_position_score,
)
from gain.genomic_resources.testing.statistics import build_statistics

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


def test_an_annulled_histogram_has_no_image_address(
    tmp_path: pathlib.Path,
) -> None:
    repo = a_repo_with(
        a_position_score()
        .with_score("score", "float")
        .with_histogram({"type": "null", "reason": "annulled by design"}),
        tmp_path)
    score = build_score_from_resource(repo.get_resource("scores/pos1"))

    assert score.get_histogram_image_url("score") is None
    assert score.get_histogram_image_public_url("score") is None


def test_an_annulled_histogram_is_not_addressed_even_with_an_image_listed(
    tmp_path: pathlib.Path,
) -> None:
    # An image left behind by a build from before the curator annulled the
    # histogram: the manifest lists it, the definition disowns it.  The
    # definition wins -- a stale drawing is not this score's histogram.
    repo = a_repo_with(
        a_position_score()
        .with_score("score", "float")
        .with_histogram({"type": "null", "reason": "annulled by design"})
        .with_file("statistics/histogram_score.png", "stale drawing"),
        tmp_path)
    resource = repo.get_resource("scores/pos1")
    assert "statistics/histogram_score.png" in resource.get_manifest()
    score = build_score_from_resource(resource)

    assert score.get_histogram_image_url("score") is None
    assert score.get_histogram_image_public_url("score") is None


def test_a_resource_with_no_stored_manifest_is_addressed_by_its_definition(
    tmp_path: pathlib.Path,
) -> None:
    # No manifest at all -- neither built nor stored -- so there is no
    # file list to consult, and the definition is all there is to go on.
    # Consulting must not BUILD one: that is an md5 scan of the whole
    # resource, and it writes state the address has no business writing.
    root = tmp_path / "grr"
    a_position_score().realize_into(root / "scores/pos1")
    proto = build_filesystem_test_protocol(root, repair=False)
    resource = proto.get_resource("scores/pos1")
    assert resource.get_loaded_manifest() is None

    score = build_score_from_resource(resource)
    url = score.get_histogram_image_url("score")

    assert url == f"{resource.get_url()}/statistics/histogram_score.png"
    assert resource.get_loaded_manifest() is None
    assert not (root / "scores/pos1" / GR_MANIFEST_FILE_NAME).exists()
    assert not (root / "scores/pos1" / ".grr").exists()


def test_a_type_with_no_default_histogram_is_annulled_too(
    tmp_path: pathlib.Path,
) -> None:
    # No ``histogram:`` block at all: the definition falls back to the
    # value type's default, and ``bool`` has none.
    repo = a_repo_with(
        a_position_score()
        .with_score("flag", "bool")
        .with_score_line(chrom="1", pos_begin=10, flag=True),
        tmp_path)
    score = build_score_from_resource(repo.get_resource("scores/pos1"))

    assert score.get_histogram_image_url("flag") is None
    assert score.get_histogram_image_public_url("flag") is None


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
    build_statistics(resource)
    score = build_score_from_resource(resource)

    assert score.get_histogram_image_url("score") == \
        f"{resource.get_url()}/statistics/histogram_score.png"
    assert score.get_histogram_image_public_url("score") == \
        f"{PUBLIC_URL}/scores/pos1/statistics/histogram_score.png"


def test_a_histogram_nullified_by_its_build_has_no_image_address(
    tmp_path: pathlib.Path,
) -> None:
    # Configured, not annulled: a number histogram over a score that
    # turns out to have no values.  The build's min/max pass nullifies
    # it and draws nothing, so the manifest lists no image for it.
    repo = a_repo_with(
        a_position_score()
        .with_score("score", "float")
        .with_histogram({"type": "number", "number_of_bins": 10})
        .with_na_values("NA")
        .with_data("""
            chrom  pos_begin  score
            1      10         NA
            1      20         NA
        """),
        tmp_path)
    resource = repo.get_resource("scores/pos1")
    build_statistics(resource)
    score = build_score_from_resource(resource)

    assert score.get_histogram_image_url("score") is None
    assert score.get_histogram_image_public_url("score") is None
