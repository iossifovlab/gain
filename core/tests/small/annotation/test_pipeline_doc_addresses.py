"""The address policies the pipeline-doc renderer chooses between.

A page's addresses are a policy, not a constant: two callers want the GRR's
public mirror, and the ``annotation_pipeline`` resource implementation
publishes its pages from *inside* the GRR tree and wants addresses relative
to the repository root.  #952 made that injectable but passed it as two
independent callables, so an incoherent pair -- a public resource address
beside a relative histogram address -- was representable.  #970 made the
policy one object; these are its tests.

How an address is *built* is not this module's subject.  The join, the
per-child-repo hosts and the no-``public_url`` fallback belong to
``get_public_url()`` and are pinned in
``tests/small/genomic_resources/test_resource_public_url``.
"""
# pylint: disable=W0621,C0116
import pathlib
from unittest.mock import patch

import pytest
from gain.annotation.pipeline_doc import (
    RepositoryRelativeAddresses,
)
from gain.genomic_resources.genomic_scores import build_score_from_resource
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_directories,
)
from gain.genomic_resources.testing.builders import a_grr, a_position_score


def a_pipeline(filename: str = "annotation.yaml") -> dict[str, str]:
    """A minimal annotation-pipeline resource over the ``one`` score."""
    return {
        "genomic_resource.yaml": f"""
            type: annotation_pipeline
            filename: {filename}
        """,
        filename: "- position_score: one\n",
    }


@pytest.fixture
def grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A GRR holding a score and two pipelines, one of them nested."""
    root_path = tmp_path / "grr"
    setup_directories(root_path, {
        "pipeline": a_pipeline(),
        "nested/deep/pipeline": a_pipeline("config.yaml"),
    })
    a_position_score().realize_into(root_path / "one")
    return build_filesystem_test_repository(root_path)


#: Advertised by ``other_grr`` so that its mirror address differs from its
#: own url.  Without that the two are equal, and a fallback that wrongly
#: returned ``get_url()`` would satisfy the assertions below.
OTHER_PUBLIC_URL = "http://other-grr.example.org"


@pytest.fixture
def other_grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A second, unmanaged GRR -- nothing in it is addressable relatively."""
    return (
        a_grr()
        .with_resource("other_score", a_position_score())
        .with_public_url(OTHER_PUBLIC_URL)
        .build_repo(tmp_path / "other_grr")
    )


def test_a_managed_resource_is_addressed_relative_to_the_repository_root(
    grr: GenomicResourceRepo,
) -> None:
    addresses = RepositoryRelativeAddresses(grr.get_resource("pipeline"))

    assert addresses.resource_url(grr.get_resource("one")) == "../one"


def test_a_resource_outside_the_managed_grr_falls_back_to_the_mirror(
    grr: GenomicResourceRepo,
    other_grr: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A relative address only resolves for something published under the
    # same root. Anything else has to be named absolutely, and the
    # curator wants to hear about it.
    addresses = RepositoryRelativeAddresses(grr.get_resource("pipeline"))
    stranger = other_grr.get_resource("other_score")

    url = addresses.resource_url(stranger)

    # Spelled against the advertised mirror, not `stranger.get_public_url()`:
    # the two agree, and asserting the accessor against itself would also
    # accept a fallback that had returned `get_url()`.
    assert url == f"{OTHER_PUBLIC_URL}/other_score"
    assert "Referencing resource outside managed GRR" in caplog.text


def test_a_managed_histogram_image_is_addressed_relative_to_the_root(
    grr: GenomicResourceRepo,
) -> None:
    addresses = RepositoryRelativeAddresses(grr.get_resource("pipeline"))
    score = build_score_from_resource(grr.get_resource("one"))

    url = addresses.histogram_url(score, "s1")

    assert url == "../one/statistics/histogram_s1.png"


def test_the_histogram_file_name_is_percent_quoted(
    grr: GenomicResourceRepo,
) -> None:
    # The asymmetry with `resource_url`, which does *not* quote: this half
    # is a file name built from a score id, and lands in the page as a
    # bare `src`. Unquoted it would be `histogram_score id.png`, which no
    # browser resolves. `quote` spares the "statistics/" separator.
    addresses = RepositoryRelativeAddresses(grr.get_resource("pipeline"))
    score = build_score_from_resource(grr.get_resource("one"))

    url = addresses.histogram_url(score, "score id")

    assert url == "../one/statistics/histogram_score%20id.png"


def test_a_histogram_outside_the_managed_grr_falls_back_to_the_mirror(
    grr: GenomicResourceRepo,
    other_grr: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    addresses = RepositoryRelativeAddresses(grr.get_resource("pipeline"))
    stranger = build_score_from_resource(
        other_grr.get_resource("other_score"))

    url = addresses.histogram_url(stranger, "s1")

    assert url == (
        f"{OTHER_PUBLIC_URL}/other_score/statistics/histogram_s1.png"
    )
    assert "Referencing resource outside managed GRR" in caplog.text


def test_a_score_with_no_histogram_image_has_no_address_and_no_warning(
    grr: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The guard has to fire *before* the containment rule: absence of an
    # image is not a complaint about which repository the score lives in.
    addresses = RepositoryRelativeAddresses(grr.get_resource("pipeline"))
    score = build_score_from_resource(grr.get_resource("one"))

    with patch.object(score, "get_histogram_image_url", return_value=None):
        url = addresses.histogram_url(score, "score")

    assert url is None
    assert "Referencing resource outside managed GRR" not in caplog.text


@pytest.mark.parametrize("pipeline_id,prefix", [
    ("pipeline", ".."),
    ("nested/deep/pipeline", "../../.."),
])
def test_the_prefix_climbs_once_per_level_of_the_pages_own_id(
    grr: GenomicResourceRepo, pipeline_id: str, prefix: str,
) -> None:
    # The page is published at its own resource id, so how far the root
    # is depends on how deep *the pipeline* sits -- not the target.
    addresses = RepositoryRelativeAddresses(grr.get_resource(pipeline_id))
    score = build_score_from_resource(grr.get_resource("one"))

    assert addresses.resource_url(grr.get_resource("one")) == f"{prefix}/one"
    assert addresses.histogram_url(score, "s1") == (
        f"{prefix}/one/statistics/histogram_s1.png"
    )
