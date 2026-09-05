# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What the FTS index stores for a list-valued label (gain#1225).

The column for a list value holds the elements joined by a space, so FTS5
tokenises each element as a term.  Pinned here: the stored text, and the
``<label> : <element>`` term search that reads it.  A label *clause* is
answered from ``meta.labels`` on both routes (gain#646, ADR 0007), so the
column serves ``search_term`` alone.
"""
import pathlib

import pytest
from gain.genomic_resources.testing.builders import a_position_score

from .conftest import index_row, indexed_repo


def test_a_list_valued_label_indexes_its_elements_joined_by_a_space(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_position_score()
        .with_labels(modality=["RNA", "ATAC"])
        .build_resource(tmp_path)
    )

    assert index_row(resource)["modality"] == "RNA ATAC"


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("modality : RNA", {"multiome", "rna_only"}),
        ("modality : ATAC", {"multiome"}),
    ],
)
def test_a_term_search_finds_a_resource_by_any_element_of_a_list_label(
    tmp_path: pathlib.Path, term: str, expected: set[str],
) -> None:
    repo = indexed_repo(tmp_path, {
        "multiome": a_position_score().with_labels(
            modality=["RNA", "ATAC"]),
        "rna_only": a_position_score().with_labels(modality="RNA"),
    })

    found = {
        res.resource_id for res in repo.search_resources(search_term=term)
    }

    assert found == expected


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
