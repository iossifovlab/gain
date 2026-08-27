# pylint: disable=C0116
"""A resource's address on the GRR's public mirror.

``public_url`` is what a deployment advertises its GRR as, and it is the
only address that means anything once a rendered document or an API
response leaves the server: the repository's own url may be a directory
mounted into a container. Joining the resource id to it is this module's
subject -- including the part a deployment gets to spell by hand, the
trailing separator (#841).
"""
import pathlib
from typing import Any

from gain.genomic_resources.genomic_scores import build_score_from_resource
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.testing.builders import a_position_score


def a_score_at(
    resources_dir: pathlib.Path, resource_id: str = "scores/pos1",
) -> None:
    a_position_score().realize_into(resources_dir / resource_id)


def a_repo_over(
    directory: str, public_url: str | None = None,
) -> GenomicResourceRepo:
    """Build a directory GRR, optionally advertising a public mirror."""
    definition: dict[str, Any] = {
        "id": "main", "type": "dir", "directory": directory,
    }
    if public_url is not None:
        definition["public_url"] = public_url
    return build_genomic_resource_repository(definition)


def test_the_resource_id_is_joined_to_the_advertised_public_url(
    tmp_path: pathlib.Path,
) -> None:
    a_score_at(tmp_path)
    repo = a_repo_over(str(tmp_path), "http://grr.example.org")

    resource = repo.get_resource("scores/pos1")

    assert resource.get_public_url() == \
        "http://grr.example.org/scores/pos1"


def test_a_public_url_ending_in_a_slash_does_not_double_it(
    tmp_path: pathlib.Path,
) -> None:
    # A deployment writes ``public_url`` by hand, so both spellings turn
    # up, and neither may produce a "//" in the middle of the address.
    a_score_at(tmp_path)
    repo = a_repo_over(str(tmp_path), "http://grr.example.org/")

    resource = repo.get_resource("scores/pos1")

    assert resource.get_public_url() == \
        "http://grr.example.org/scores/pos1"


def test_the_histogram_image_url_inherits_the_same_join(
    tmp_path: pathlib.Path,
) -> None:
    # The histogram address is built from the resource's public url, so a
    # trailing separator would otherwise reach it as a "//" that no call
    # site could repair -- the whole address is assembled internally.
    a_score_at(tmp_path)
    repo = a_repo_over(str(tmp_path), "http://grr.example.org/")

    score = build_score_from_resource(repo.get_resource("scores/pos1"))

    assert score.get_histogram_image_public_url("score") == \
        "http://grr.example.org/scores/pos1/statistics/histogram_score.png"


def test_a_repository_without_a_public_url_falls_back_to_its_own_url(
    tmp_path: pathlib.Path,
) -> None:
    # ``public_url`` is optional: a GRR that never declared a public
    # mirror still has to produce an address rather than raise, and it is
    # the one it produced before the mirror existed.
    a_score_at(tmp_path)
    repo = a_repo_over(str(tmp_path))

    resource = repo.get_resource("scores/pos1")

    assert resource.get_public_url() == resource.get_url()
    assert str(tmp_path) in resource.get_public_url()


def test_the_fallback_holds_when_the_repository_root_ends_in_a_slash(
    tmp_path: pathlib.Path,
) -> None:
    # The fallback above is only worth anything if it holds for every
    # spelling of the root, and a ``directory`` is written by hand too.
    # The two joins have to agree, which they cannot if only one of them
    # tolerates the separator.
    a_score_at(tmp_path)
    repo = a_repo_over(f"{tmp_path}/")

    resource = repo.get_resource("scores/pos1")

    assert resource.get_public_url() == resource.get_url()
    assert "//" not in resource.get_url().removeprefix("file://")


def test_a_scheme_only_root_keeps_its_own_separators(
    tmp_path: pathlib.Path,
) -> None:
    # Stripping must not eat the "//" that belongs to the scheme itself:
    # those separators are the url's, not a hand-written stray one.
    a_score_at(tmp_path)
    repo = a_repo_over(str(tmp_path), "https://")

    resource = repo.get_resource("scores/pos1")

    assert resource.get_public_url() == "https://scores/pos1"


def test_each_child_of_a_group_advertises_its_own_host(
    tmp_path: pathlib.Path,
) -> None:
    # The shape production deploys: one group, two children published on
    # two different hosts. The same resource id exists in both, so the
    # address is only right if it comes from the child it was found in.
    a_score_at(tmp_path / "main")
    a_score_at(tmp_path / "enc")
    repo = build_genomic_resource_repository({
        "id": "group",
        "type": "group",
        "children": [
            {
                "id": "main",
                "type": "dir",
                "directory": str(tmp_path / "main"),
                "public_url": "http://grr.example.org",
            },
            {
                "id": "encode",
                "type": "dir",
                "directory": str(tmp_path / "enc"),
                "public_url": "http://grr-encode.example.org/",
            },
        ],
    })

    main = repo.get_resource("scores/pos1", repository_id="main")
    encode = repo.get_resource("scores/pos1", repository_id="encode")

    assert main.get_public_url() == "http://grr.example.org/scores/pos1"
    assert encode.get_public_url() == \
        "http://grr-encode.example.org/scores/pos1"
