# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
"""Attribute-injection tests for the rendered resource info pages.

Score ids and resource ids are GRR-supplied strings -- nothing validates
them (a score ``id`` is typed as a bare string, and the templates' own
``score_id | replace(" ", "_")`` shows ids with spaces are expected).
Autoescaping alone does not make them safe in an *unquoted* attribute:
it rewrites only ``& < > " '``, so a value carrying a space ends the
attribute and everything after it is parsed as further attributes of the
same tag -- an event handler among them.

The assertion is therefore not about escaping but about the DOM the page
produces: no tag on a resource page may carry an ``on*`` handler
attribute.  ``html.parser`` reads the page the way a browser's tokenizer
does, so an unquoted attribute value shows up as the injected attribute
it is; a quoted one stays a single attribute value.
"""
from __future__ import annotations

import pathlib
import textwrap
from collections.abc import Iterator
from html.parser import HTMLParser

import gain.templates as templates_module
import pytest
from gain.gene_scores.implementations.gene_scores_impl import (
    GeneScoreImplementation,
)
from gain.gene_sets.implementations.gene_sets_impl import GeneSetCollectionImpl
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_directories,
)
from gain.genomic_resources.testing.builders import GeneScoreBuilder

PAYLOAD = "onload=gainxss604()"


@pytest.fixture(autouse=True)
def reset_template_caches() -> Iterator[None]:
    """Reset singleton caches before and after each test."""
    templates_module._state.env = None
    templates_module._state.provider_cache = None
    yield
    templates_module._state.env = None
    templates_module._state.provider_cache = None


class _AttributeNameCollector(HTMLParser):
    """Collect every attribute name the page's tags carry."""

    def __init__(self) -> None:
        super().__init__()
        self.attribute_names: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        self.attribute_names.extend(name for name, _ in attrs)


def event_handler_attributes(page: str) -> list[str]:
    """Return the ``on*`` handler attributes present in the page."""
    collector = _AttributeNameCollector()
    collector.feed(page)
    return sorted({
        name for name in collector.attribute_names if name.startswith("on")
    })


def test_gene_score_id_cannot_add_an_event_handler(
    tmp_path: pathlib.Path,
) -> None:
    """A space in a gene score id does not land a handler on the page."""
    score_id = f"x {PAYLOAD}"
    resource = (
        GeneScoreBuilder()
        .with_score(score_id, column_name="sc")
        .with_data(textwrap.dedent("""
            gene sc
            A  1.0
            B  2.0
        """))
        .build_resource(tmp_path)
    )

    page = GeneScoreImplementation(resource).get_info()

    assert event_handler_attributes(page) == []
    assert f'alt="{score_id}"' in page


def _build_gene_set_collection(
    tmp_path: pathlib.Path, collection_id: str,
) -> GenomicResource:
    """Realize a one-set gene_set_collection carrying ``collection_id``."""
    setup_directories(tmp_path, {"gene_sets": {
        "genomic_resource.yaml": textwrap.dedent(f"""
            type: gene_set_collection
            id: "{collection_id}"
            format: directory
            directory: GeneSets
        """),
        "GeneSets": {
            "main_candidates.txt":
                "main_candidates\nMain Candidates\nPOGZ\nCHD8\n",
        },
    }})
    resource = build_filesystem_test_repository(tmp_path).get_resource(
        "gene_sets")
    assert resource is not None
    return resource


def test_gene_set_collection_id_cannot_add_an_event_handler(
    tmp_path: pathlib.Path,
) -> None:
    """A space in a gene set collection id lands no handler on the page."""
    collection_id = f"x {PAYLOAD}"
    resource = _build_gene_set_collection(tmp_path, collection_id)
    impl = GeneSetCollectionImpl(resource)
    impl._compute_and_save_all_statistics()

    page = impl.get_info()

    assert event_handler_attributes(page) == []
    assert f'alt="{collection_id}"' in page


def _build_position_score(
    tmp_path: pathlib.Path, score_id: str,
) -> GenomicResource:
    """Realize a two-line position score whose score is ``score_id``.

    Hand-rolled rather than built with ``PositionScoreBuilder``: the
    builder interpolates a score id into the config unquoted, so it
    cannot express an id carrying a tab -- which is exactly the id this
    test needs, the genomic score page passing its ids through
    ``replace(" ", "_")`` and so surviving a space.
    """
    setup_directories(tmp_path, {"score": {
        "genomic_resource.yaml": textwrap.dedent(f"""
            type: position_score
            table:
              filename: data.txt
              zero_based: false
            scores:
              - id: "{score_id}"
                type: float
                name: sc
        """),
        "data.txt": (
            "chrom\tpos_begin\tpos_end\tsc\n"
            "chr1\t1\t10\t0.1\n"
            "chr1\t11\t20\t0.9\n"
        ),
    }})
    resource = build_filesystem_test_repository(tmp_path).get_resource("score")
    assert resource is not None
    return resource


def test_genomic_score_id_cannot_add_an_event_handler(
    tmp_path: pathlib.Path,
) -> None:
    """A tab in a genomic score id lands no handler on the page.

    The page underscores the spaces out of a score id before using it,
    so a space-carrying id is already inert here -- a tab is not, and
    ends an unquoted attribute just as well.
    """
    score_id = f"x\t{PAYLOAD}"
    resource = _build_position_score(tmp_path, f"x\\t{PAYLOAD}")
    GenomicScoreImplementation._do_noregion_histograms(resource)

    page = GenomicScoreImplementation(resource).get_info()

    assert event_handler_attributes(page) == []
    assert f'title="{score_id}"' in page
