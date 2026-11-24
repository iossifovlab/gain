"""The parse of the text of a ``search.yaml``."""
import logging

import pytest
from gain.genomic_resources.search_config import (
    Facet,
    carried_facets,
    parse_search_config,
)

SOURCE = "/grr/search.yaml"


def test_facets_come_in_file_order_with_their_titles() -> None:
    text = (
        "facets:\n"
        "  - label: species\n"
        "    title: Species\n"
        "  - label: assay\n")

    facets = parse_search_config(text, SOURCE)

    assert facets == [Facet("species", "Species"), Facet("assay", "assay")]


def test_a_title_that_is_not_text_becomes_text() -> None:
    facets = parse_search_config(
        "facets:\n  - label: year\n    title: 2024\n", SOURCE)

    assert facets == [Facet("year", "2024")]


@pytest.mark.parametrize("text", [
    "",
    "facets:\n",
    "facets: []\n",
    "other: 1\n",
])
def test_a_file_that_names_no_facet_gives_no_facet(text: str) -> None:
    assert not parse_search_config(text, SOURCE)


def test_invalid_yaml_raises_a_message_that_names_the_file() -> None:
    with pytest.raises(ValueError, match="is not valid YAML") as raised:
        parse_search_config("facets: [\n", SOURCE)

    assert SOURCE in str(raised.value)


@pytest.mark.parametrize("text", [
    "- label: species\n",
    "just text\n",
    "facets: species\n",
    "facets:\n  label: species\n",
])
def test_a_file_without_a_facets_list_raises(text: str) -> None:
    with pytest.raises(ValueError, match="a 'facets' list") as raised:
        parse_search_config(text, SOURCE)

    assert SOURCE in str(raised.value)


@pytest.mark.parametrize("text", [
    "facets:\n  - species\n",
    "facets:\n  - title: Species\n",
    "facets:\n  - label: ''\n",
    "facets:\n  - label: 7\n",
])
def test_a_facet_without_a_label_string_raises(text: str) -> None:
    with pytest.raises(ValueError, match="a 'label' string") as raised:
        parse_search_config(text, SOURCE)

    assert SOURCE in str(raised.value)


def test_a_label_in_two_facets_raises() -> None:
    text = "facets:\n  - label: species\n  - label: species\n"

    with pytest.raises(ValueError, match="more than one facet") as raised:
        parse_search_config(text, SOURCE)

    assert SOURCE in str(raised.value)


def test_a_facet_on_a_key_no_resource_carries_is_left_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    facets = [Facet("species", "Species"), Facet("assay", "Assay")]

    with caplog.at_level(logging.WARNING):
        kept = carried_facets(facets, {"assay"}, SOURCE)

    assert kept == [Facet("assay", "Assay")]
    assert "'species'" in caplog.text
