"""The Markdown dialect GAIn renders GRR-supplied prose in.

``render_markdown`` decides the dialect once, for every caller: the about
page, a resource's ``meta.description``, the ``meta`` sinks in the
resource implementations, the Jinja ``markdown`` global and the annotator
doc templates.  Before gain#1278 the dialect was decided per call site
instead, and two of them had drifted apart -- the about page rendered
plain Markdown, so a curator's table reached the reader as literal
``| ... |`` text, while the resource page passed ``extras=["tables"]``
and rendered the same source correctly.

The tests here are that single decision, stated as behaviour: a table and
a fenced code block are markup under the default, a language-tagged fence
is *not* highlighted (gain#1289 -- the why is on ``DEFAULT_EXTRAS``), and
a caller that asks for plain Markdown by passing ``extras=[]`` still gets
it.  Each of the first three pins one extra by name.

The wrapper's other job -- rescuing prose from bogus tags -- is covered
in ``test_markdown_support.py``, and the template global that exposes it
to Jinja in ``test_markdown_global.py``.
"""
from __future__ import annotations

import pytest
from gain.templates.markdown_support import render_markdown

#: A table in the shape a curator writes it, header row included.
_TABLE = "| score | meaning |\n|---|---|\n| 1 | high |\n"

#: A fenced block with no language tag.
_FENCE = "```\nannotate --help\n```\n"

#: The same block naming a language.
_TAGGED_FENCE = "```python\nx = 1\n```\n"


def test_a_table_is_markup_under_the_default_dialect() -> None:
    """The gain#1278 defect: this used to reach the reader as pipe text."""
    out = render_markdown(_TABLE)

    assert "<table>" in out
    assert "<td>high</td>" in out
    assert "| score | meaning |" not in out


def test_a_fenced_block_is_a_code_block_under_the_default_dialect() -> None:
    """Without the extra, markdown2 reads the fence as an inline span."""
    out = render_markdown(_FENCE)

    assert "<pre><code>annotate --help\n</code></pre>" in out


def test_a_language_tagged_fence_is_not_highlighted_even_when_it_could_be(
) -> None:
    """markdown2 would highlight this through Pygments; the default says no.

    It only would where Pygments is importable, so this test proves
    nothing elsewhere -- it skips there rather than pass vacuously.
    """
    pytest.importorskip("pygments")

    out = render_markdown(_TAGGED_FENCE)

    assert '<pre><code class="python language-python">x = 1\n</code></pre>' \
        in out
    assert "codehilite" not in out
    assert "<span" not in out


def test_a_caller_asking_for_plain_markdown_still_gets_it() -> None:
    """The default is a default, not a floor.

    ``extras=[]`` is the way a caller opts out of the dialect entirely,
    so the table below must stay the literal text the author typed.
    Without this, ``render_markdown`` could force the dialect on every
    caller and the two tests above would not notice.
    """
    out = render_markdown(_TABLE, extras=[])

    assert "<table>" not in out
    assert "| score | meaning |" in out
