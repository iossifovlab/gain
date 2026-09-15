# pylint: disable=W0621,C0114,C0115,C0116,W0613
"""The trail a resource's pages show back to the repository root.

A resource page used to offer one way out -- a link to the index page
-- and its statistics page none.  Both now carry the same breadcrumb
the index page's hierarchical view draws (gain#1477): the root, one
crumb per id segment, and the page itself last.  What each crumb links
to is decided here, in Python, from the same two facts the root climb
is computed from: the resource id and where the page sits inside the
resource directory.  The templates only loop.

The addresses are the index page's own, fixed at render time: a folder
is ``index.html#/<segments>`` (gain#579), so a crumb into the tree is a
plain link and the index page does the rest on load.
"""
from __future__ import annotations

from html.parser import HTMLParser

import pytest
from gain.genomic_resources.implementations.basic_resource_impl import (
    BasicResourceImplementation,
)
from gain.genomic_resources.repository import (
    GR_INDEX_FILE_NAME,
    GR_STATISTICS_INDEX_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.testing import build_inmemory_test_repository
from gain.templates.breadcrumb import Crumb, page_breadcrumb

from tests.small.templates.vendored_fonts import (
    ligatures_in,
    vendored_icon_font,
)


def test_a_resource_page_climbs_to_every_folder_above_it() -> None:
    """One crumb per segment, each addressing its folder in the tree.

    The climb is three deep for a three-segment id, and the root crumb
    goes to the tree's root rather than to a bare ``index.html``: the
    trail is one kind of navigation, the folder containing this page,
    and an empty fragment on the index page means the table view.
    """
    trail = page_breadcrumb("summary/study/track", GR_INDEX_FILE_NAME)

    assert trail == [
        Crumb("All resources", "../../../index.html#/"),
        Crumb("summary", "../../../index.html#/summary"),
        Crumb("study", "../../../index.html#/summary/study"),
        Crumb("track", None),
    ]


def test_the_page_itself_is_never_a_link() -> None:
    """A one-segment id: the root, then the resource as the current crumb."""
    trail = page_breadcrumb("cadd", GR_INDEX_FILE_NAME)

    assert trail == [
        Crumb("All resources", "../index.html#/"),
        Crumb("cadd", None),
    ]


def test_a_statistics_page_links_its_resource_and_is_current_itself(
) -> None:
    """The extra directory becomes the current crumb.

    The resource's own segment is now a link, to its info page, and it
    is spelled from the root climb like every other crumb rather than
    as a bare ``../index.html``: one rule for every href, not one for
    the tree and another for the page next door.
    """
    trail = page_breadcrumb("scores/coverage", GR_STATISTICS_INDEX_FILE_NAME)

    assert trail == [
        Crumb("All resources", "../../../index.html#/"),
        Crumb("scores", "../../../index.html#/scores"),
        Crumb("coverage", "../../../scores/coverage/index.html"),
        Crumb("statistics", None),
    ]


# ---- The trail on the rendered pages ----


class _BreadcrumbReader(HTMLParser):
    """Reads the crumbs out of the page's ``#breadcrumb`` element.

    Each crumb as the template rendered it: ``(name, href)`` for a link,
    ``(name, None)`` for the current one.  Only what sits inside the
    breadcrumb element counts, so a link elsewhere on the page -- the
    files table, the description -- cannot pass as a crumb.
    """

    def __init__(self) -> None:
        super().__init__()
        self.crumbs: list[Crumb] = []
        self.header_links: list[str] = []
        self._depth: int | None = None
        self._in_header = 0
        self._open: tuple[str, str | None] | None = None
        self._text = ""

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if attributes.get("id") == "page-header":
            self._in_header = 1
        elif self._in_header:
            self._in_header += 1
        if attributes.get("id") == "breadcrumb":
            self._depth = 1
            return
        if self._depth is None:
            if self._in_header and tag == "a":
                self.header_links.append(attributes.get("href") or "")
            return
        self._depth += 1
        classes = (attributes.get("class") or "").split()
        if "breadcrumb-item" in classes:
            self._open = (tag, attributes.get("href"))
            self._text = ""

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self._text += data

    def handle_endtag(self, tag: str) -> None:
        if self._in_header:
            self._in_header -= 1
        if self._depth is None:
            return
        if self._open is not None and tag == self._open[0]:
            self.crumbs.append(Crumb(self._text.strip(), self._open[1]))
            self._open = None
        self._depth -= 1
        if self._depth == 0:
            self._depth = None


def crumbs_on(page: str) -> list[Crumb]:
    reader = _BreadcrumbReader()
    reader.feed(page)
    return reader.crumbs


def header_links_on(page: str) -> list[str]:
    """The hrefs of every link in the page header, crumbs included."""
    reader = _BreadcrumbReader()
    reader.feed(page)
    return reader.header_links + [
        crumb.href for crumb in reader.crumbs if crumb.href is not None]


@pytest.fixture
def nested_resource() -> GenomicResource:
    """A ``type: basic`` resource three segments deep."""
    repo = build_inmemory_test_repository({
        "summary": {"study": {"track": {
            "genomic_resource.yaml": "type: basic\n",
            "data.txt": "alabala",
        }}},
    })
    return repo.get_resource("summary/study/track")


@pytest.fixture
def resource_page(nested_resource: GenomicResource) -> str:
    return BasicResourceImplementation(nested_resource).get_info()


@pytest.fixture
def statistics_page(nested_resource: GenomicResource) -> str:
    return BasicResourceImplementation(nested_resource).get_statistics_info()


def test_the_resource_page_header_is_the_trail(resource_page: str) -> None:
    """The breadcrumb sits in the page header, and nothing else does.

    The "Back to main page" link it replaces addressed the table view;
    the root crumb now addresses the tree, and that is the only link
    left in the header, so a reader has one kind of way out.
    """
    assert crumbs_on(resource_page) == page_breadcrumb(
        "summary/study/track", GR_INDEX_FILE_NAME)
    assert "Back to main page" not in resource_page
    assert header_links_on(resource_page) == [
        "../../../index.html#/",
        "../../../index.html#/summary",
        "../../../index.html#/summary/study",
    ]


def test_the_statistics_page_carries_the_same_trail(
    statistics_page: str,
) -> None:
    """The page that had no way out at all now climbs like its sibling."""
    assert crumbs_on(statistics_page) == page_breadcrumb(
        "summary/study/track", GR_STATISTICS_INDEX_FILE_NAME)


def test_the_id_cell_carries_a_copy_icon(resource_page: str) -> None:
    """The icon names the id it copies, and the page can draw the tick.

    The id is on the icon as data rather than as a class, which is how
    the index page used to carry it: a class is a poor place for a
    value, and ``classList[2]`` is a poorer way to read one.  The glyphs
    are checked against the vendored subset because a glyph the file
    lacks renders as its own name, in words.
    """
    icons = copy_icons_on(resource_page)

    assert icons == [("summary/study/track", "content_copy")]
    assert ligatures_in(vendored_icon_font()) >= {"content_copy", "check"}
    assert "navigator.clipboard" in resource_page


class _CopyIconReader(HTMLParser):
    """Every ``.copy-icon`` on the page: what it copies, what it shows."""

    def __init__(self) -> None:
        super().__init__()
        self.icons: list[tuple[str, str]] = []
        self._copies: str | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if "copy-icon" in (attributes.get("class") or "").split():
            self._copies = attributes.get("data-copy-id") or ""

    def handle_data(self, data: str) -> None:
        if self._copies is not None:
            self.icons.append((self._copies, data.strip()))
            self._copies = None


def copy_icons_on(page: str) -> list[tuple[str, str]]:
    reader = _CopyIconReader()
    reader.feed(page)
    return reader.icons
