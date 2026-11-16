"""The labels of a resource page's Resource table read on one line, at the top.

The label column is 100px wide and padded 10px on each side, which leaves
80px for the label; a bold "Description" needs about 82px.  The shared
``th`` rule lets a word break anywhere (``overflow-wrap: break-word``),
so without a rule of its own the label rendered as "Descriptio / n" on
every resource page (iossifovlab/gain#2407).  A label that does not wrap
widens its column instead.

The shared rule itself stays: curator tables inside a description need it.

A tall value -- a description runs to several paragraphs -- would also
leave its label floating halfway down the row, so labels sit at the top,
beside the first line of what they name.
"""
from __future__ import annotations

from gain.genomic_resources.implementations.basic_resource_impl import (
    BasicResourceImplementation,
)

from tests.small.templates.conftest import basic_resource_described_by
from tests.small.templates.page_css import rules_in


def _label_declarations() -> dict[str, str]:
    page = BasicResourceImplementation(
        basic_resource_described_by("A description.")).get_info()
    declarations: dict[str, str] = {}
    for rule in rules_in(page):
        if "#resource-table th" in rule.selectors:
            declarations.update(rule.declarations)
    return declarations


def test_a_resource_table_label_never_wraps() -> None:
    assert _label_declarations().get("white-space") == "nowrap"


def test_a_resource_table_label_sits_at_the_top_of_its_row() -> None:
    assert _label_declarations().get("vertical-align") == "top"
