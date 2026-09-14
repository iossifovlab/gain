"""Which hosts a rendered page loads from -- every way a page can.

The "no third party" assertions in ``test_grr_page_icon_font.py`` and
``test_resource_page_sorter.py`` assert an empty set, and an empty set
is also what a scanner that missed the mechanism would answer.  So this
reads every way a page can reach a host, in one place, and
``test_page_origins.py`` holds it to a page that uses each:

- attributes -- a ``<link href>``, or any ``src``;
- an ES module specifier, a string inside ``<script type="module">``
  that an HTML parser sees as opaque character data (gain#1335);
- CSS inside ``<style>`` -- a ``url()`` (a ``@font-face`` ``src``, a
  background) or an ``@import`` (gain#1400).

Ordinary hyperlinks are deliberately not counted: the browse page links
to the SQLite FTS docs beside its search box, and a page that grows
another such link has not grown a third party it loads code from.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlparse

#: ``import x from "<specifier>"`` -- an ES module specifier, which can
#: reach an origin without being an ``src``.  Any specifier, so the same
#: match serves both the relative import the page carries and the
#: origin count that must notice an absolute one.
MODULE_IMPORT = re.compile(r"\bimport\s+\w+\s+from\s+[\"']([^\"']+)[\"']")

#: Every ``<style>`` of a page.
_STYLE = re.compile(r"<style>(.*?)</style>", re.DOTALL)

#: What CSS can fetch: ``url(<it>)`` anywhere, and ``@import "<it>"``,
#: the one form of ``@import`` that takes no ``url()``.
_CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+?)['\"]?\s*\)")
_CSS_IMPORT = re.compile(r"@import\s+['\"]([^'\"]+)['\"]")

#: Tags whose ``href`` makes the browser fetch something.  ``<a>`` is
#: pointedly absent; ``src`` is a subresource on whatever carries it.
_FETCHING_HREF_TAGS = frozenset({"link"})


class _LinkReader(HTMLParser):
    """Collects the URLs a page fetches by attribute, and its preconnects."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []
        #: host -> whether the hint warms a CORS socket.
        self.preconnects: dict[str, bool] = {}

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)

        href = attributes.get("href")
        if href and tag in _FETCHING_HREF_TAGS:
            self.urls.append(href)
        src = attributes.get("src")
        if src:
            self.urls.append(src)

        if tag == "link" and attributes.get("rel") == "preconnect" and href:
            self.preconnects[urlparse(href).hostname or ""] = (
                "crossorigin" in attributes
            )


def read_page(page: str) -> _LinkReader:
    reader = _LinkReader()
    reader.feed(page)
    return reader


def urls_the_page_loads(page: str) -> list[str]:
    """Every url the page fetches, as written -- relative ones included."""
    stylesheets = _STYLE.findall(page)
    return [
        *read_page(page).urls,
        *MODULE_IMPORT.findall(page),
        *(url for sheet in stylesheets for url in _CSS_URL.findall(sheet)),
        *(url for sheet in stylesheets for url in _CSS_IMPORT.findall(sheet)),
    ]


def external_origins(page: str) -> frozenset[str]:
    """Every third-party host the page loads from."""
    return frozenset(
        urlparse(url).hostname or ""
        for url in urls_the_page_loads(page)
        if urlparse(url).scheme in ("http", "https")
    )
