# pylint: disable=W0621
"""What a GRR's ``about.md`` becomes on the generated ``about.html``.

The about page is built by ``build_index_info``, which reads ``about.md``
off the repository, renders it, and wraps the HTML in ``grr_about.jinja``.
That render is a *call site*, and gain#1278 is what happens when a call
site decides the Markdown dialect for itself: this one asked for plain
Markdown, so a curator's table arrived on the page as literal ``| ... |``
text while the same source rendered correctly on a resource page.

So the assertions here deliberately go through the real build rather than
through ``render_markdown`` or the template in isolation.  The unit-level
statement of the dialect lives in
``tests/small/templates/test_markdown_dialect.py``; it would stay green
through a regression that re-introduced a per-call-site ``extras`` here,
which is the regression this module exists to catch.

Fenced blocks are un-tagged on purpose -- see that module's docstring for
why a language tag makes the expected HTML depend on whether Pygments is
installed (gain#1289).
"""
from __future__ import annotations

import pathlib
import re
import textwrap

import pytest
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
)

_ABOUT_PAGE = "about.html"

#: An ``about.md`` in the shape a curator writes one: prose, a table
#: describing the repository's scores, and a copy-pasteable command.
_ABOUT_MD = textwrap.dedent("""\
    # About this GRR

    The scores published here:

    | score | meaning |
    |---|---|
    | 1 | high |

    Fetch one with:

    ```
    annotate --help
    ```
    """)


@pytest.fixture
def about_page(tmp_path: pathlib.Path) -> str:
    """The ``about.html`` a real repository build writes."""
    root_path = tmp_path / "grr"
    setup_directories(root_path, {
        "about.md": _ABOUT_MD,
        "one": {
            GR_CONF_FILE_NAME: "type: basic\n",
            "data.txt": "alabala",
        },
    })
    proto = build_filesystem_test_protocol(root_path)

    proto.build_index_info()

    return (root_path / _ABOUT_PAGE).read_text(encoding="utf8")


def test_a_table_in_about_md_reaches_the_page_as_a_table(
    about_page: str,
) -> None:
    """The gain#1278 defect, stated at the page it was reported on."""
    assert "<table>" in about_page
    assert "<td>high</td>" in about_page
    assert "| score | meaning |" not in about_page


def test_a_fenced_block_in_about_md_reaches_the_page_as_a_code_block(
    about_page: str,
) -> None:
    assert "<pre><code>annotate --help\n</code></pre>" in about_page


#: ``#section-about <element> { ... }`` and the body of that rule.
_ABOUT_RULE = re.compile(r"#section-about\s+(\w+)\s*\{([^}]*)\}")
_FONT_SIZE = re.compile(r"font-size:\s*([^;]+);")


def _font_sizes_in(page: str) -> dict[str, str]:
    """The font size each ``#section-about`` rule sets, by element."""
    return {
        element: size.group(1).strip()
        for element, body in _ABOUT_RULE.findall(page)
        if (size := _FONT_SIZE.search(body)) is not None
    }


def test_the_about_page_sizes_a_table_like_its_prose(
    about_page: str,
) -> None:
    """A table renders at prose size, not the browser's default.

    Asserted as the relationship rather than the literal ``0.875em`` so
    that restyling the page moves both together or fails here -- the
    point is that a table does not stand out from the paragraphs around
    it, not what the two happen to measure today.
    """
    sizes = _font_sizes_in(about_page)

    assert "p" in sizes, sizes
    assert sizes.get("table") == sizes["p"], sizes
