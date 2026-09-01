# pylint: disable=C0116
"""Links to a resource's documentation page on its own GRR mirror.

The link a response advertises for a resource has to come from the
resource itself, because a GRR is not necessarily one place: production
serves a *group* whose children are published on different hosts (the
main GRR and the ENCODE GRR). A single base URL pasted onto every
resource id cannot express that -- it sends every link to whichever host
the setting names, so resources served by any other child are advertised
on the wrong host (#838).
"""
import pathlib

from gain.genomic_resources.testing.builders import a_grr, a_position_score
from gain.genomic_resources.testing.group_builder import a_grr_group

from web_annotation.single_allele_annotation.views import resource_index_url


def test_link_is_built_from_the_resources_public_url(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource("scores/pos1", a_position_score())
        .with_public_url("http://grr.example.org")
        .build_repo(tmp_path)
    )

    url = resource_index_url(repo.get_resource("scores/pos1"))

    assert url == "http://grr.example.org/scores/pos1/index.html"


def test_a_grr_without_a_public_url_falls_back_to_the_repo_url(
    tmp_path: pathlib.Path,
) -> None:
    # ``public_url`` is optional -- a GRR that never declared a public
    # mirror still has to produce a link rather than raise.
    repo = (
        a_grr()
        .with_resource("scores/pos1", a_position_score())
        .build_repo(tmp_path)
    )

    url = resource_index_url(repo.get_resource("scores/pos1"))

    assert str(tmp_path) in url
    assert url.endswith("/scores/pos1/index.html")


def test_each_resource_is_linked_on_the_host_of_its_own_child_repo(
    tmp_path: pathlib.Path,
) -> None:
    # The shape production deploys: one group, two children published on
    # two different hosts. The same resource id exists in both, so a link
    # is only correct if it is derived from the child the resource
    # actually came from.
    repo = (
        a_grr_group()
        .with_child(
            "main",
            a_grr()
            .with_resource("scores/pos1", a_position_score())
            .with_public_url("http://grr.example.org"))
        .with_child(
            "encode",
            a_grr()
            .with_resource("scores/pos1", a_position_score())
            .with_public_url("http://grr-encode.example.org"))
        .build_repo(tmp_path)
    )

    main = repo.get_resource("scores/pos1", repository_id="main")
    encode = repo.get_resource("scores/pos1", repository_id="encode")

    assert resource_index_url(main) == \
        "http://grr.example.org/scores/pos1/index.html"
    assert resource_index_url(encode) == \
        "http://grr-encode.example.org/scores/pos1/index.html"


def test_a_public_url_ending_in_a_slash_does_not_double_it(
    tmp_path: pathlib.Path,
) -> None:
    # A deployment writes ``public_url`` by hand, so both spellings turn
    # up; neither may produce a "//" in the middle of the link.
    repo = (
        a_grr()
        .with_resource("scores/pos1", a_position_score())
        .with_public_url("http://grr.example.org/")
        .build_repo(tmp_path)
    )

    url = resource_index_url(repo.get_resource("scores/pos1"))

    assert url == "http://grr.example.org/scores/pos1/index.html"
