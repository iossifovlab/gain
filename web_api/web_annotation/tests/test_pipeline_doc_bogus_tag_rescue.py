# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""A bogus tag in documentation prose must not swallow the sentence here.

``PipelineDoc`` serves the pipeline-documentation HTML download, and it is
the *third* renderer of ``annotate_doc_pipeline_template.jinja`` -- the one
that lives outside ``core`` and so outside the reach of the rescue tests
gain#736 added there.  gain#742: it kept rendering through raw ``markdown2``
after the other nine sinks moved to the shared wrapper, so prose like
``values <thresh are dropped`` was still swallowed by the browser as one
bogus tag on every downloaded page.

This file is the ``web_api`` mirror of
``core/tests/small/annotation/test_annotate_doc_bogus_tag_rescue.py``.  Read
them together: the rescue's own contract is pinned in ``core``, and what is
pinned *here* is that this renderer reaches it.

Nothing here is sanitization.  ADR 0016 records that GRR content is trusted
by authorship, and the live-markup test below pins that trust at this sink
just as ``core``'s characterization tests pin it at the others.  gain#623
was closed ``wontfix`` for proposing otherwise.
"""
from __future__ import annotations

import pathlib
from html.parser import HTMLParser

from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.testing.builders import a_grr, a_position_score

from web_annotation.pipeline_cache import ThreadSafePipeline
from web_annotation.pipelines.views import PipelineDoc

#: Unique to these tests, so an assertion cannot be satisfied by prose the
#: page ships itself -- the false-signal lesson of gain#558.
MARKER = "gainrescue742"

#: Elements HTML gives no end tag, so they never enclose anything.
VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


class _PageDom(HTMLParser):
    """Read a page as markup rather than as text.

    A deliberate re-implementation rather than an import: ``core``'s
    equivalent lives under ``core/tests/``, and the ``web_api`` CI image
    copies only the ``gain`` *package* from ``core`` -- importing it would
    pass locally and fail in CI.

    Pairing each run of character data with the innermost open element is
    what the assertions need.  A bare "is there a ``script`` tag" says only
    that *some* element of that name is on the page, and these pages ship
    their own.  There is deliberately no flat ``text`` accumulator:
    ``convert_charrefs`` decodes ``&lt;script&gt;`` back to ``<script>``, so
    flat text would report escaped markup indistinguishably from live markup
    -- the very difference these tests exist to detect.
    """

    def __init__(self) -> None:
        super().__init__()
        self.element_text: list[tuple[str, str]] = []
        self._open: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag not in VOID_ELEMENTS:
            self._open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        # Unwind to the innermost matching open tag. The guard matters: a
        # stray end tag would otherwise pop an empty stack and raise.
        if tag in self._open:
            while self._open.pop() != tag:
                pass

    def handle_data(self, data: str) -> None:
        if self._open:
            self.element_text.append((self._open[-1], data))

    def text_in(self, tag: str) -> str:
        """All the character data ``tag`` encloses, concatenated."""
        return "".join(
            data for open_tag, data in self.element_text if open_tag == tag)


def parse_page(page: str) -> _PageDom:
    dom = _PageDom()
    dom.feed(page)
    return dom


def page_text(page: str) -> str:
    """All character data on the page, in document order."""
    return "".join(data for _, data in parse_page(page).element_text)


def _one_score_pipeline(
    tmp_path: pathlib.Path,
    score_desc: str,
) -> AnnotationPipeline:
    """Build a one-score pipeline over a GRR realized under ``tmp_path``."""
    repo = (
        a_grr()
        .with_resource(
            "one",
            a_position_score()
            .with_score(
                "score_one", "float", column_name="score", desc=score_desc)
            .with_data("chrom  pos_begin  score\nchr1   4          0.01\n"))
        .build_repo(tmp_path / "grr"))

    return load_pipeline_from_yaml(
        "- position_score: one\n", repo, work_dir=tmp_path / "work")


def _render(pipeline: AnnotationPipeline) -> str:
    """Render the page ``PipelineDoc`` serves for ``pipeline``.

    ``ThreadSafePipeline`` is the type the view renders in production; it is
    a thin wrapper, so this is the page a download really produces.
    """
    return PipelineDoc._render_doc(ThreadSafePipeline(pipeline))


def render_pipeline_doc(
    tmp_path: pathlib.Path,
    score_desc: str,
) -> str:
    """Render a page whose score ``desc`` is ``score_desc``.

    Reaches the template's ``attribute_info.documentation`` sink: an
    attribute's documentation falls back to its spec description, which is
    the score's ``desc`` as written in the GRR.
    """
    return _render(_one_score_pipeline(tmp_path, score_desc))


def test_prose_after_a_bogus_tag_still_reaches_the_reader(
    tmp_path: pathlib.Path,
) -> None:
    """The headline defect: ``<thresh`` must not eat the rest of the sentence.

    On the defective page the tokenizer reads ``<thresh are dropped ...``
    as one bogus start tag, so the text after ``<`` is simply absent from
    the page's character data -- while still present in the raw HTML, which
    is why the assertion is on parsed text.
    """
    sentence = f"values <thresh are dropped {MARKER}"

    page = render_pipeline_doc(tmp_path, sentence)

    assert sentence in page_text(page)


def test_prose_after_a_bogus_tag_survives_in_annotator_documentation(
    tmp_path: pathlib.Path,
) -> None:
    """The rescue reaches the template's second ``markdown(...)`` sink.

    Both sinks resolve the same ``markdown`` global from the shared Jinja
    environment (gain#751; before that, the same render kwarg), so they
    cannot diverge -- but that is a property of the current wiring, not a
    guarantee.  gain#623's first review round found exactly the shape this
    guards: a change that converted some call sites and left another.

    ``documentation`` is set on the built pipeline rather than derived from
    the GRR: for a position-score annotator the annotator assembles that
    field itself (``info.documentation += ...``), and only the gene-set
    annotator interpolates GRR-supplied text into it -- at the cost of a
    genome, gene models and a collection, and constrained to a resource id,
    which cannot carry this prose.  It is the plain, mutable field the
    template reads, so writing it is stating the precondition rather than
    reaching past the interface.
    """
    sentence = f"values <thresh are dropped {MARKER}"
    pipeline = _one_score_pipeline(tmp_path, "a score")
    pipeline.annotators[0]._info.documentation = sentence

    page = _render(pipeline)

    assert sentence in page_text(page)


def test_a_comparison_in_an_authors_script_reaches_the_page_verbatim(
    tmp_path: pathlib.Path,
) -> None:
    """The rescue must not reach inside a deliberate ``<script>``.

    Character references do not decode in script content, so an ``&lt;``
    planted there would hand the JS engine five literal characters and a
    syntax error.  This is ADR 0016's live-markup guarantee restated with a
    ``<`` *inside* the trusted payload -- the shape that makes the rescue
    and the trust boundary meet.
    """
    script_body = f"if (x <thresh) {MARKER}()"

    page = render_pipeline_doc(
        tmp_path, f"DESC<script>{script_body}</script>")

    assert script_body in parse_page(page).text_in("script")
