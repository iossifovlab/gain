# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What the FTS index's meta-derived fixed columns carry (gain#1008).

Every resource contributes a ``description`` and a ``summary`` column to
the index.  ``GenomicResource.get_summary`` falls back to the description
when ``meta.summary`` is absent, and the repository index page renders it,
so a resource carrying a description and no summary *displays* that
description as its summary.  The index row used to read the two fields
straight out of the ``meta`` mapping instead, so the same resource indexed
with an empty ``summary`` column and could not be found by a
column-qualified ``summary : <term>`` search.

The column is never displayed -- it is only reachable through the
``contents MATCH`` predicate -- and ``description`` is a sibling column
carrying the same text, so an *unqualified* search found the resource
either way.  The column-qualified form is the one that broke, and the
documented one: the GRR docs pass ``-s/--search`` to FTS5 verbatim, and
ADR 0012 names ``-s 'summary: foo'`` as a portable filter.
"""
import pathlib

import pytest
from gain.genomic_resources.testing.builders import a_position_score

from .conftest import index_row, indexed_repo

#: A term that appears only in the description of the description-only
#: resource, so a search for it cannot be answered by anything else in the
#: repository.
DESCRIPTION_ONLY_TERM = "lonelydescription"


def test_summary_search_finds_a_resource_whose_summary_is_its_description(
    tmp_path: pathlib.Path,
) -> None:
    # The user-visible seam: `summary : <term>` is a documented query form,
    # and the resource's own page already answers with this description
    # when asked for its summary.
    repo = indexed_repo(tmp_path, {
        "desconly": a_position_score().with_meta(
            description=f"a {DESCRIPTION_ONLY_TERM} and no summary"),
        "both": a_position_score().with_meta(
            description="own description", summary="own summary"),
    })

    found = [
        res.resource_id
        for res in repo.search_resources(
            search_term=f"summary : {DESCRIPTION_ONLY_TERM}")
    ]

    assert found == ["desconly"]


def test_a_description_only_resource_indexes_it_as_its_summary(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_position_score()
        .with_meta(description="only a description")
        .build_resource(tmp_path)
    )

    indexed = index_row(resource)

    assert indexed["description"] == "only a description"
    assert indexed["summary"] == "only a description"


def test_a_resource_declaring_both_indexes_its_own_summary(
    tmp_path: pathlib.Path,
) -> None:
    # The control on the fall-back: it applies only where there is nothing
    # to fall back from.  Without this, replacing the summary column with
    # the description outright would pass the test above.
    resource = (
        a_position_score()
        .with_meta(description="own description", summary="own summary")
        .build_resource(tmp_path)
    )

    indexed = index_row(resource)

    assert indexed["description"] == "own description"
    assert indexed["summary"] == "own summary"


def test_a_resource_declaring_no_meta_indexes_both_columns_empty(
    tmp_path: pathlib.Path,
) -> None:
    # Nothing to fall back to, and nothing malformed either: the columns
    # are empty and the collector does not raise.  A row is still a tuple
    # of strings, so `get_summary`'s `str | None` must not reach it.
    resource = a_position_score().build_resource(tmp_path)

    indexed = index_row(resource)

    assert indexed["description"] == ""
    assert indexed["summary"] == ""


@pytest.mark.parametrize("meta", [
    pytest.param({"description": "a description"}, id="description-only"),
    pytest.param({"summary": "a summary"}, id="summary-only"),
    pytest.param(
        {"description": "a description", "summary": "a summary"}, id="both"),
])
def test_the_indexed_columns_agree_with_the_resource_accessors(
    tmp_path: pathlib.Path, meta: dict[str, str],
) -> None:
    """The row and the page cannot answer differently for one resource.

    The anti-drift assertion: what the index carries is what the resource
    reports, whatever the ``meta`` block declares.  gain#1008 was exactly
    this agreement failing for one shape, so it is pinned across all of
    them rather than for that shape alone.
    """
    resource = a_position_score().with_meta(**meta).build_resource(tmp_path)

    indexed = index_row(resource)

    assert indexed["description"] == resource.get_description()
    assert indexed["summary"] == resource.get_summary()


def test_a_non_string_summary_does_not_abort_the_repository_index(
    tmp_path: pathlib.Path,
) -> None:
    """A summary SQLite cannot bind must not cost the whole index build.

    ``meta`` is free-form below the top level, and nothing narrows a
    *field*: the base schema types only ``meta.description`` as a string
    and leaves ``meta.summary`` unconstrained under ``allow_unknown``,
    while ``type: basic`` runs no schema at all.  So a list-valued summary
    reaches the index build, where the ``INSERT`` binding the row sits
    OUTSIDE the per-resource error handling -- one such resource used to
    raise ``TypeError: Bad binding argument type`` and take the whole
    repository's index down with it, the failure class gain#1004 fixed for
    the read side.

    Deriving the column through the accessors' helpers is what prevents
    it: they ``str()`` whatever they find.  That coercion is load-bearing
    rather than cosmetic, so it is pinned here -- dropping the ``str()``
    passes every other test in this file and reopens the abort.
    """
    repo = indexed_repo(tmp_path, {
        "listy": a_position_score().with_raw_meta({"summary": ["a", "b"]}),
        "healthy": a_position_score().with_meta(description="a description"),
    })

    # The resource beside it still indexed -- the point of "does not abort".
    assert sorted(
        res.resource_id
        for res in repo.search_resources(search_term="description")
    ) == ["healthy"]
    assert index_row(repo.get_resource("listy"))["summary"] == "['a', 'b']"
