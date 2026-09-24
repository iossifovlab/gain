"""Which hosts a rendered page loads from -- every way a page can.

The "no third party" assertions in ``test_grr_page_icon_font.py`` and
``test_resource_page_sorter.py`` assert an empty set, and an empty set
is also what a scanner that missed the mechanism would answer.  So this
reads every way a page can reach a host, in one place, and
``test_page_origins.py`` holds it to a page that uses each:

- attributes -- a ``<link href>`` (a stylesheet, a preconnect hint), or
  any ``src``;
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
from urllib.parse import urlparse

from tests.small.templates.page_css import CSS_URL, stylesheets_in
from tests.small.templates.page_dom import parse_page

#: ``import x from "<specifier>"`` -- an ES module specifier, which can
#: reach an origin without being an ``src``.  Any specifier, so the same
#: match serves both the relative import the page carries and the
#: origin count that must notice an absolute one.
MODULE_IMPORT = re.compile(r"\bimport\s+\w+\s+from\s+[\"']([^\"']+)[\"']")

#: ``@import "<it>"`` -- the one form of ``@import`` that takes no
#: ``url()``, which ``CSS_URL`` would otherwise already read.
_CSS_IMPORT = re.compile(r"@import\s+['\"]([^'\"]+)['\"]")

#: Tags whose ``href`` makes the browser fetch something.  ``<a>`` is
#: pointedly absent; ``src`` is a subresource on whatever carries it.
_FETCHING_HREF_TAGS = frozenset({"link"})


def _attribute_urls(page: str) -> list[str]:
    """Every url the page fetches by attribute."""
    urls: list[str] = []
    for element in parse_page(page).elements:
        href = element.attributes.get("href")
        if href and element.tag in _FETCHING_HREF_TAGS:
            urls.append(href)
        src = element.attributes.get("src")
        if src:
            urls.append(src)
    return urls


def urls_the_page_loads(page: str) -> list[str]:
    """Every url the page fetches, as written -- relative ones included."""
    return [
        *_attribute_urls(page),
        *MODULE_IMPORT.findall(page),
        *(
            url for sheet in stylesheets_in(page)
            for url in (*CSS_URL.findall(sheet), *_CSS_IMPORT.findall(sheet))
        ),
    ]


def external_origins(page: str) -> frozenset[str]:
    """Every third-party host the page loads from."""
    parsed = [urlparse(url) for url in urls_the_page_loads(page)]
    return frozenset(
        url.hostname or "" for url in parsed
        if url.scheme in ("http", "https")
    )


def pointed_at_google(page: str) -> str:
    """The page with every font url sent back to Google's font host.

    For the "no third party" tests, whose empty set is also what a
    scanner blind to *this* page's stylesheets would answer: the urls
    are the ones the page carries, rewritten in place, so a scan that
    then names ``fonts.gstatic.com`` has read the page's own ``<style>``
    blocks, however the tag is written.  Vacuous on a page with no font
    url -- which the face assertions beside each use rule out.
    """
    assert _FONT_URL_PREFIX.search(page), "the page carries no font url"
    return _FONT_URL_PREFIX.sub("https://fonts.gstatic.com/", page)


#: Where a page's font urls start: the climb to the root, if the page
#: sits below it, then the published fonts directory.
_FONT_URL_PREFIX = re.compile(r"(?:\.\./)*\.static/fonts/")
