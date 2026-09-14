# pylint: disable=W0621,C0114,C0115,C0116,W0613
"""The origin scanner the "no third party" tests stand on.

Those tests assert an empty set, and an empty set is what a scanner
that missed the mechanism would also answer -- so the scanner is held
to a page that reaches a host each way a page can, and must name every
host.  The two CSS ways are the ones gain#1400 introduced: a
``@font-face`` whose ``src`` is absolute, and an ``@import`` of a
stylesheet, both invisible to a reader of attributes.
"""
from __future__ import annotations

from tests.small.templates.page_origins import external_origins

#: One page reaching a distinct host through every mechanism.
PAGE = """
<html><head>
<link rel="stylesheet" href="https://link.test/sheet.css">
<style>
  @import url("https://import.test/sheet.css");
  @font-face {
    font-family: "F";
    src: url(https://font.test/f.woff2) format("woff2");
  }
  body { background: url('https://image.test/bg.png'); }
</style>
<script src="https://script.test/s.js"></script>
<script type="module">import x from "https://module.test/m.mjs";</script>
</head><body>
<img src="https://img.test/i.png">
<a href="https://hyperlink.test/">a link is not a load</a>
</body></html>
"""


def test_every_way_a_page_can_load_from_a_host_is_counted() -> None:
    assert external_origins(PAGE) == frozenset({
        "link.test",
        "import.test",
        "font.test",
        "image.test",
        "script.test",
        "module.test",
        "img.test",
    })


def test_relative_urls_are_no_origin() -> None:
    """The repository's own files, reached relatively, name no host."""
    page = """
    <style>
      @font-face { font-family: "F"; src: url("../.static/fonts/f.woff2"); }
    </style>
    <script type="module">import x from "./.static/m/index.mjs";</script>
    """

    assert external_origins(page) == frozenset()
