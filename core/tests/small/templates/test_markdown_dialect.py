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
a fenced code block are markup under the default, and a caller that asks
for plain Markdown by passing ``extras=[]`` still gets it.  Between them
they pin both extras by name -- drop either from the default and one of
them goes red.

**Fenced blocks here are deliberately un-tagged.**  markdown2's
``fenced-code-blocks`` has two output shapes, and which one you get is
decided by the environment rather than by the source: a fence carrying a
language routes through Pygments when it is importable, emitting
``<div class="codehilite">`` around highlighted spans, and falls back to
plain ``<pre><code>`` when it is not.  Pygments is not a declared
dependency of GAIn, so a language-tagged fence would make this suite's
expected HTML depend on what happens to be installed.  gain#1289 carries
that decision; until it lands, un-tagged fences are the only shape that
is a function of the Markdown alone.

The wrapper's other job -- rescuing prose from bogus tags -- is covered
in ``test_markdown_support.py``, and the template global that exposes it
to Jinja in ``test_markdown_global.py``.
"""
from __future__ import annotations

from gain.templates.markdown_support import render_markdown

#: A table in the shape a curator writes it, header row included.
_TABLE = "| score | meaning |\n|---|---|\n| 1 | high |\n"

#: A fenced block with no language tag -- see the module docstring for
#: why the tag is deliberately absent.
_FENCE = "```\nannotate --help\n```\n"


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
