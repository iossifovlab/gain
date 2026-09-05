# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""How the resource info page renders ``meta.labels`` (gain#1225).

Each label is one ``<li>`` of the Labels row.  A list value is a set of
alternatives and renders as its elements, comma-separated; rendered as a
whole it is its Python repr, ``['RNA', 'ATAC']``.  The rows are pinned
whole, so a change to either half of ``key: value`` is seen.
"""
import pathlib

import pytest
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.testing.builders import a_position_score


def _info_page(tmp_path: pathlib.Path, **labels: object) -> str:
    resource = (
        a_position_score()
        .with_labels(**labels)
        .build_resource(tmp_path)
    )
    return build_resource_implementation(resource).get_info()


def test_a_list_valued_label_renders_its_elements_comma_separated(
    tmp_path: pathlib.Path,
) -> None:
    page = _info_page(tmp_path, modality=["RNA", "ATAC"])

    assert "<li>modality: RNA, ATAC</li>" in page
    assert "[" not in page.split("<th>Labels</th>")[1].split("</tr>")[0]


@pytest.mark.parametrize(
    ("value", "rendered"),
    [
        pytest.param("10x_Multiome", "10x_Multiome", id="string"),
        pytest.param(2019, "2019", id="int"),
        pytest.param(False, "False", id="bool"),
        pytest.param({"source": "UCSC"}, "{&#39;source&#39;: &#39;UCSC&#39;}",
                     id="mapping"),
    ],
)
def test_a_non_list_label_renders_as_its_rendered_form(
    tmp_path: pathlib.Path, value: object, rendered: str,
) -> None:
    # The mapping's quotes are HTML-escaped by the template's autoescape,
    # as they were before lists were split: only a list changes shape.
    page = _info_page(tmp_path, protocol=value)

    assert f"<li>protocol: {rendered}</li>" in page
