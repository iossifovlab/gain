# pylint: disable=C0114,C0116,W0212,W0621
"""The statistics figures on a score's info page share the page's width.

``.page-content`` is 1200px wide with 40px of padding, so a page-flow
figure has 1120px to fill, and every table on the page fills exactly
that.  The five figures used to fill three different widths -- two of
them unstyled and 1500px natural, overflowing the container outright,
three pinned to an inline ``width:50%`` -- so a reader met a page whose
charts matched neither the tables nor each other (gain#986).

The figures are asserted through the DOM the page renders: each carries
the shared figure class and none carries an inline width of its own.
The in-table thumbnails and the modal image are *not* page-flow figures
and keep their own sizing.

Scoped to the genomic- and fragment-score pages, which are the only
ones that render these five.  Gene-score and gene-set-collection pages
have page-flow figures of their own, sized inline; those are covered
only by the blanket cap, not by the class.
"""
from __future__ import annotations

import pathlib
import re
from collections.abc import Callable
from html.parser import HTMLParser

import pytest
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.alleles import (
    ALLELE_COMPLEX_GRID_IMAGE_FILE,
    ALLELE_DELETION_LENGTHS_IMAGE_FILE,
    ALLELE_INSERTION_LENGTHS_IMAGE_FILE,
    COMPLEX_GRID_TABLE_MAX_CELLS,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_position_score,
    an_allele_score,
)

FIGURE_CLASS = "statistics-figure"

_Builder = Callable[[pathlib.Path], GenomicResource]


class _ImageCollector(HTMLParser):
    """Every ``<img>`` on the page, as its attribute dict."""

    def __init__(self) -> None:
        super().__init__()
        self.images: list[dict[str, str]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag == "img":
            self.images.append({name: value or "" for name, value in attrs})


def _images(page: str) -> list[dict[str, str]]:
    collector = _ImageCollector()
    collector.feed(page)
    return collector.images


def _image_named(page: str, filename: str) -> dict[str, str]:
    matches = [
        image for image in _images(page)
        if image.get("src", "").endswith(filename)
    ]
    assert len(matches) == 1, \
        f"expected exactly one <img> for {filename}, got {len(matches)}"
    return matches[0]


def _classes(image: dict[str, str]) -> set[str]:
    return set(image.get("class", "").split())


def _blanket_image_cap(page: str) -> str:
    """The selector of the stylesheet's blanket cap on page-flow images.

    Found by what the rule does rather than by how it is spelled, so the
    tests about how it is spelled have something to say.
    """
    matches = [
        selector for selector, declarations in _rules(page)
        if selector.endswith("img") and declarations.get("max-width") == "100%"
    ]
    assert len(matches) == 1, \
        f"expected exactly one blanket image cap, got {len(matches)}"
    return matches[0]


def _stylesheet(page: str) -> str:
    """The page's own stylesheet -- the ``<style>`` element in its head.

    Scoped deliberately: the description field embeds a second, shadow-DOM
    stylesheet with its own ``img`` rule, which the page's cascade never
    reaches and which no assertion here is about.
    """
    match = re.search(r"<style>(.*?)</style>", page, re.DOTALL)
    assert match is not None, "the page carries no stylesheet"
    return re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.DOTALL)


def _parse_declarations(body: str) -> dict[str, str]:
    """A declaration block, as property -> value."""
    return {
        property_.strip(): value.strip()
        for property_, _, value in (
            declaration.partition(":") for declaration in body.split(";")
        )
        if property_.strip()
    }


def _inline(image: dict[str, str]) -> dict[str, str]:
    """An element's own ``style`` attribute, as declarations."""
    return _parse_declarations(image.get("style", ""))


def _rules(page: str) -> list[tuple[str, dict[str, str]]]:
    """The page stylesheet's rules, as ``(selector, declarations)``.

    Flat by assumption: a resource page's stylesheet has no at-rules, so
    nothing here nests.  Wrap one rule in an ``@media`` block and this
    reads the two as separate rules with a stray selector between them.
    """
    return [
        (selector.strip(), _parse_declarations(body))
        for selector, body in re.findall(
            r"([^{}]+)\{([^{}]*)\}", _stylesheet(page))
    ]


def _declarations(page: str, selector: str) -> dict[str, str]:
    """What the page's stylesheet ends up saying about ``selector``.

    Folded in document order, later winning, as the cascade resolves
    rules of equal specificity: a resource page states ``table`` twice,
    once in the base styles and once in the per-type ones.
    """
    matches = [
        declarations for found, declarations in _rules(page)
        if found == selector
    ]
    assert matches, f"the page's stylesheet says nothing about {selector}"
    folded: dict[str, str] = {}
    for declarations in matches:
        folded.update(declarations)
    return folded


def _specificity(selector: str) -> tuple[int, int, int]:
    """A selector's (id, class, type) specificity.

    ``:where()`` contributes nothing, by definition -- which is the whole
    point of writing a blanket rule with it.

    Enough of the grammar for the selectors this page actually uses, not
    a parser: a nested ``:not()`` inside the ``:where()``, an attribute
    selector, a pseudo-element or a comma-separated list all come out
    too high.  Every one of those errors *over*-counts, so the ordering
    assertion below can only fail spuriously, never pass spuriously --
    but read the selector before believing a surprising failure.
    """
    bare = re.sub(r":where\([^()]*\)", "", selector)
    ids = len(re.findall(r"#[\w-]+", bare))
    classes = len(re.findall(r"[.:\[][\w-]+", bare))
    types = len(re.findall(r"(?:^|[\s>+~])([a-zA-Z][\w-]*)", bare))
    return (ids, classes, types)


def _position_score_with_segments(tmp_path: pathlib.Path) -> GenomicResource:
    """A covered score: its page carries the segment-lengths figure."""
    return (
        a_position_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  score
            chr1   5          9        0.1
            chr1   10         14       0.1
            chr1   15         20       0.2
            chr1   30         33       0.2
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _fragment_score(tmp_path: pathlib.Path) -> GenomicResource:
    """A fragment score: its page adds the fragment-lengths figure."""
    return (
        a_fragment_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  score
            chr1   10         100      0.1
            chr1   20         30       0.2
            chr2   1          4        0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _indel_allele_score(tmp_path: pathlib.Path) -> GenomicResource:
    """Insertions, deletions and a complex allele: three more figures."""
    return (
        an_allele_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  score
            chr1   10         A          AT           0.1
            chr1   20         ACGT       A            0.2
            chr1   30         AC         GT           0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _dense_complex_allele_score(tmp_path: pathlib.Path) -> GenomicResource:
    """An allele score whose complex grid is DRAWN rather than tabled.

    At or below ``COMPLEX_GRID_TABLE_MAX_CELLS`` occupied cells the page
    lists the cells and writes no image at all (gain#989), so the figure
    this file is about would not exist -- ``_indel_allele_score``'s one
    complex row is far below it.  Each row's pair of lengths is distinct,
    so the occupied-cell count is the row count.
    """
    builder = an_allele_score().with_score("score", "float")
    for index in range(COMPLEX_GRID_TABLE_MAX_CELLS + 1):
        builder = builder.with_score_line(
            chrom="chr1", pos_begin=(index + 1) * 10,
            reference="A" + "C" * (1 + index // 6),
            alternative="G" + "T" * (1 + index % 6),
            score=0.1)
    return builder.with_tabix().build_resource(tmp_path)


def _built_page(resource: GenomicResource) -> str:
    GenomicScoreImplementation._do_noregion_histograms(resource)
    return build_score_implementation_from_resource(resource).get_info()


_FIGURES: list[tuple[str, _Builder, str]] = [
    ("segment lengths",
     _position_score_with_segments, "statistics/coverage_segment_lengths.png"),
    # The fragments section lives in a child template, so this is the
    # one figure the genomic-score page itself never renders.
    ("fragment lengths",
     _fragment_score, "statistics/coverage_fragment_lengths.png"),
    # These three used to be pinned to an inline `width:50%` -- half the
    # page, and a third width beside the tables and the two unstyled
    # charts.  The class replaces the inline style outright.
    ("insertion lengths",
     _indel_allele_score, ALLELE_INSERTION_LENGTHS_IMAGE_FILE),
    ("deletion lengths",
     _indel_allele_score, ALLELE_DELETION_LENGTHS_IMAGE_FILE),
    # Its own resource: this is the one figure whose existence depends
    # on the DATA and not just on the group being present.
    ("complex grid",
     _dense_complex_allele_score, ALLELE_COMPLEX_GRID_IMAGE_FILE),
]


@pytest.mark.parametrize(
    ("build_resource", "filename"),
    [(builder, filename) for _, builder, filename in _FIGURES],
    ids=[name for name, _, _ in _FIGURES],
)
def test_every_statistics_figure_carries_the_shared_figure_class(
    build_resource: _Builder,
    filename: str,
    tmp_path: pathlib.Path,
) -> None:
    """A figure takes its width from the class and nowhere else."""
    page = _built_page(build_resource(tmp_path))

    figure = _image_named(page, filename)

    assert FIGURE_CLASS in _classes(figure)
    assert not _inline(figure)


def test_page_flow_images_are_capped_at_the_content_width(
    tmp_path: pathlib.Path,
) -> None:
    """The cap is scoped to the content box, catching an unclassed image."""
    page = _built_page(_position_score_with_segments(tmp_path))

    selector = _blanket_image_cap(page)

    assert "page-content" in selector


def test_the_blanket_image_cap_does_not_outrank_the_thumbnail(
    tmp_path: pathlib.Path,
) -> None:
    """The cap is a floor anything overrides, not a ceiling.

    Written as ``.page-content img`` it is (0,1,1) and OUTRANKS the
    (0,1,0) ``.histogram-thumbnail``, quietly releasing the in-table
    thumbnails from their 200px to fill their column instead.
    """
    page = _built_page(_position_score_with_segments(tmp_path))

    selector = _blanket_image_cap(page)

    assert _specificity(selector) < _specificity(".histogram-thumbnail")


def test_the_in_table_thumbnails_are_not_page_flow_figures(
    tmp_path: pathlib.Path,
) -> None:
    """The scores table's thumbnails keep their own 200px cap.

    They sit in a 220px column and open a modal; they are not charts
    laid out down the page, and the width work leaves them as they were.
    """
    page = _built_page(_position_score_with_segments(tmp_path))

    thumbnails = [
        image for image in _images(page)
        if "histogram-thumbnail" in _classes(image)
    ]

    assert thumbnails, "no thumbnail on the page to guard"
    for thumbnail in thumbnails:
        assert FIGURE_CLASS not in _classes(thumbnail)
    assert _declarations(page, ".histogram-thumbnail")["max-width"] == "200px"


def test_the_modal_image_keeps_its_own_sizing(
    tmp_path: pathlib.Path,
) -> None:
    """The modal is not page flow either: its image is capped at 800px."""
    page = _built_page(_position_score_with_segments(tmp_path))

    modal_images = [
        image for image in _images(page)
        if "max-width" in _inline(image)
    ]

    assert modal_images, "no modal image on the page to guard"
    for image in modal_images:
        # Read as declarations, not as a string: the templates spell
        # this same cap with and without a trailing semicolon.
        assert _inline(image)["max-width"] == "min(100%, 800px)"
        assert FIGURE_CLASS not in _classes(image)


def test_the_shared_figure_class_fills_the_page_width(
    tmp_path: pathlib.Path,
) -> None:
    """`width`, not `max-width`.

    A chart narrower than the page is widened to it rather than left at
    its natural size -- which is what leaves the 1000px complex grid
    short of the 1120px tables.
    """
    page = _built_page(_position_score_with_segments(tmp_path))

    figure = _declarations(page, f".{FIGURE_CLASS}")

    assert figure["display"] == "block"
    assert figure["width"] == "100%"


def test_the_figures_and_the_tables_are_given_the_same_width(
    tmp_path: pathlib.Path,
) -> None:
    """A figure and a table in the content box come out the same width.

    The pairing is the point, not 100% for its own sake: narrowing the
    tables alone would break the shared width with every figure rule
    still intact.
    """
    page = _built_page(_position_score_with_segments(tmp_path))

    figure = _declarations(page, f".{FIGURE_CLASS}")
    table = _declarations(page, "table")

    assert figure.get("width") == table.get("width") == "100%"
