"""A bogus tag in documentation prose must not swallow the sentence.

A score ``desc`` containing ``<`` immediately followed by a letter --
``values <thresh are dropped`` -- survives markdown2 raw, and the browser's
tokenizer then consumes everything up to the next ``>`` as one bogus tag.
The reader sees the sentence end early, silently (gain#736).

This is the *complement* of ``test_annotate_doc_trust.py``, not its rival.
That file pins ADR 0016: deliberate markup from the trusted author renders
live.  This file pins the rescue of prose that never meant to write a tag:
a ``<``/``</`` whose tag name is neither a known HTML element nor hyphenated
is escaped in the rendered page.  Known elements -- including ``<script>`` --
stay live unconditionally; nothing here is sanitization.

Read the two files together; a change that breaks either boundary should go
red in exactly one of them.
"""
from __future__ import annotations

import pathlib
from collections.abc import Callable

import pytest
from gain.genomic_resources.implementations.genomic_scores_impl import (
    PositionScoreImplementation,
)
from gain.genomic_resources.testing.builders import a_position_score

from tests.small.annotation.test_annotate_doc_trust import (
    RENDERERS,
    parse_page,
)

_RENDERER_IDS = [name for name, _ in RENDERERS]
_RENDERER_FUNCS = [render for _, render in RENDERERS]

#: Unique to these tests, so an assertion cannot be satisfied by text the
#: page ships itself (the false-signal lesson of gain#558).
MARKER = "gainrescue736"


def page_text(page: str) -> str:
    """All character data on the page, in document order."""
    return "".join(data for _, data in parse_page(page).element_text)


@pytest.mark.parametrize("render", _RENDERER_FUNCS, ids=_RENDERER_IDS)
def test_prose_after_a_bogus_tag_still_reaches_the_reader(
    tmp_path: pathlib.Path,
    render: Callable[[pathlib.Path, str], str],
) -> None:
    """The headline defect: ``<thresh`` must not eat the rest of the sentence.

    On the defective page the tokenizer reads ``<thresh are dropped ...</p>``
    as one bogus start tag, so the text after ``<`` is simply absent from the
    page's character data.
    """
    sentence = f"values <thresh are dropped {MARKER}"

    page = render(tmp_path, sentence)

    assert sentence in page_text(page)


@pytest.mark.parametrize("render", _RENDERER_FUNCS, ids=_RENDERER_IDS)
def test_a_comparison_in_an_authors_script_reaches_the_page_verbatim(
    tmp_path: pathlib.Path,
    render: Callable[[pathlib.Path, str], str],
) -> None:
    """The rescue must not reach inside a deliberate ``<script>``.

    Character references do not decode in script content, so an ``&lt;``
    planted there would hand the JS engine five literal characters and a
    syntax error.  This is the live-markup guarantee of ADR 0016 restated
    with a ``<`` *inside* the trusted payload -- the trust tests' own
    payloads carry none, so this file pins it.
    """
    script_body = f"if (x <thresh) {MARKER}()"

    page = render(tmp_path, f"DESC<script>{script_body}</script>")

    assert script_body in parse_page(page).text_in("script")


def test_prose_in_a_meta_description_survives_on_the_resource_info_page(
    tmp_path: pathlib.Path,
) -> None:
    """The rescue also reaches the resource info page renderer.

    ``resource_template.jinja`` renders ``meta.description`` through the
    shared wrapper -- the sink shape the resource-implementation classes
    all funnel through, which the annotate-doc renderers above never
    touch.
    """
    sentence = f"values <thresh are dropped {MARKER}"
    resource = (
        a_position_score()
        .with_score("score_one", "float", column_name="score")
        .with_data("chrom  pos_begin  score\nchr1   4          0.01\n")
        .with_meta(description=sentence)
        .build_resource(tmp_path))

    page = PositionScoreImplementation(resource).get_info()

    assert sentence in page_text(page)
