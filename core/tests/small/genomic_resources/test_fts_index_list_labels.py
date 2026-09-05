# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What the FTS index stores for a list-valued label (gain#1225).

A ``meta.labels`` value may be a list -- a multiome resource is both
``RNA`` and ``ATAC`` -- and the index column for the label then holds the
elements joined by a space, so FTS5 tokenises each element as a term.
Rendered as a whole the value is its Python repr, ``['RNA', 'ATAC']``,
which FTS5 happens to split on its punctuation into the same two tokens
plus noise; a ``modality : RNA`` search then finds the resource by
accident, and any exact form of the column reads as a repr.  The label
columns only serve ``search_term`` -- a label *clause* is answered from
the resource's own ``meta.labels`` on both routes (gain#646) -- so what is
pinned here is the term search and the stored text.
"""
import pathlib

import pytest
from gain.genomic_resources.cli import _create_contents_db
from gain.genomic_resources.repository import GenomicResourceProtocolRepo
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.builders import (
    ResourceBuilder,
    a_grr,
    a_position_score,
)

from .conftest import index_row


def _indexed_repo(
    tmp_path: pathlib.Path, resources: dict[str, ResourceBuilder],
) -> GenomicResourceProtocolRepo:
    """Realize ``resources`` as a GRR and build its FTS index."""
    grr = a_grr()
    for resource_id, builder in resources.items():
        grr = grr.with_resource(resource_id, builder)
    grr.build_repo(tmp_path)

    proto = build_filesystem_test_protocol(tmp_path, repair=False)
    assert _create_contents_db(proto) == frozenset()
    return GenomicResourceProtocolRepo(proto)


def test_a_list_valued_label_indexes_its_elements_joined_by_a_space(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_position_score()
        .with_labels(modality=["RNA", "ATAC"])
        .build_resource(tmp_path)
    )

    assert index_row(resource)["modality"] == "RNA ATAC"


@pytest.mark.parametrize("term", ["modality : RNA", "modality : ATAC"])
def test_a_term_search_finds_a_resource_by_any_element_of_a_list_label(
    tmp_path: pathlib.Path, term: str,
) -> None:
    repo = _indexed_repo(tmp_path, {
        "multiome": a_position_score().with_labels(
            modality=["RNA", "ATAC"]),
        "rna_only": a_position_score().with_labels(modality="RNA"),
    })

    found = {
        res.resource_id for res in repo.search_resources(search_term=term)
    }

    assert "multiome" in found
    assert ("rna_only" in found) == (term == "modality : RNA")


@pytest.mark.parametrize(
    ("value", "stored"),
    [
        pytest.param("RNA", "RNA", id="string"),
        pytest.param(2019, "2019", id="int"),
        pytest.param(False, "False", id="bool"),
        pytest.param({"source": "UCSC"}, "{'source': 'UCSC'}", id="mapping"),
        pytest.param([], "", id="empty-list"),
    ],
)
def test_a_non_list_label_indexes_as_its_rendered_form(
    tmp_path: pathlib.Path, value: object, stored: str,
) -> None:
    """Only a list is split; every other value is stored as ``str()``.

    A nested mapping stays a repr: the any-element rule is for lists, and
    a mapping is neither a scalar nor a set of alternatives.  An empty
    list stores ``""``, which is what an absent label stores too.
    """
    resource = (
        a_position_score()
        .with_labels(label=value)
        .build_resource(tmp_path)
    )

    assert index_row(resource)["label"] == stored
