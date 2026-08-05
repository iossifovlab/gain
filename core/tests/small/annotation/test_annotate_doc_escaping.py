# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""HTML-injection tests for the page ``gain_annotate_doc`` writes.

A score's ``desc`` and an annotator's documentation are GRR-supplied
prose -- the same untrusted input class as #467, #528 and #558.  The
documentation page renders both through ``markdown`` and marks the
result ``|safe``, so any HTML the metadata carries used to reach the
page as markup.

The assertions are about the DOM the page produces rather than about the
exact escaping: ``html.parser`` reads the page the way a browser's
tokenizer does, so an injected tag shows up as an element and an
injected handler as an attribute.  Payloads carry a unique token because
asserting on a literal ``<script>`` would be satisfied by markup the
page itself ships (the lesson of #558); the payloads are quoted because
markdown2 mangles an unquoted one and would test something weaker.
"""
from __future__ import annotations

import collections
import pathlib
import textwrap
from html.parser import HTMLParser

import pytest
from gain.annotation.annotate_doc import cli
from gain.annotation.annotation_pipeline import Annotator
from gain.genomic_resources.testing import (
    setup_denovo,
    setup_directories,
)
from gain.testing.foobar_import import foobar_genes, foobar_genome

pytestmark = pytest.mark.usefixtures("clean_genomic_context")


class _PageDom(HTMLParser):
    """Read a page the way a browser's tokenizer does."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str | None]] = []
        self.script_data: list[str] = []
        self.text: list[str] = []
        self._open_scripts = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        self.tags.append(tag)
        self.attributes.extend(attrs)
        if tag == "script":
            self._open_scripts += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._open_scripts:
            self._open_scripts -= 1

    def handle_data(self, data: str) -> None:
        if self._open_scripts:
            self.script_data.append(data)
        else:
            self.text.append(data)


def parse_page(page: str) -> _PageDom:
    dom = _PageDom()
    dom.feed(page)
    return dom


#: Elements HTML gives no closing tag, so a reader never keeps them open.
#: Every other element stays open until its end tag arrives -- HTML5
#: ignores a trailing ``/`` on a start tag, so ``<a href="..."/>`` opens
#: an anchor exactly as ``<a href="...">`` does.
VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


class _TagBalance(HTMLParser):
    """Count the start and end tags a reader takes from the page.

    A start tag with no end tag is not a cosmetic defect: ``a``, ``em``
    and the rest of the formatting elements are carried across the
    markup that follows -- a browser reopens them after the enclosing
    ``</p>`` -- so an element a resource leaves open styles or links the
    rest of the document.  Counting tokens rather than walking a stack
    is deliberate: a stack repairs the nesting on the first end tag it
    can match, which hides exactly the imbalance under test.
    """

    def __init__(self) -> None:
        super().__init__()
        self.started: collections.Counter[str] = collections.Counter()
        self.ended: collections.Counter[str] = collections.Counter()

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag not in VOID_ELEMENTS:
            self.started[tag] += 1

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        # A browser ignores the `/`, so `<a .../>` is a start tag.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag not in VOID_ELEMENTS:
            self.ended[tag] += 1


def unclosed_elements(page: str) -> dict[str, int]:
    """Elements the page opens more often than it closes."""
    balance = _TagBalance()
    balance.feed(page)
    return {
        tag: count - balance.ended[tag]
        for tag, count in balance.started.items()
        if count > balance.ended[tag]
    }


def dangerous_url(value: str) -> bool:
    """Does an attribute value run script when a browser follows it?

    A browser ignores case and drops ASCII whitespace inside a scheme,
    so both are removed before the value is judged.
    """
    collapsed = "".join(value.split()).casefold()
    return collapsed.startswith(("javascript:", "vbscript:", "data:text/html"))


def event_handler_attributes(page: str) -> list[str]:
    """Return the ``on*`` handler attributes present in the page."""
    return sorted({
        name for name, _ in parse_page(page).attributes
        if name.startswith("on")
    })


def _build_grr(
    root_path: pathlib.Path,
    score_desc: str,
) -> None:
    """Realize a one-score GRR whose score carries ``score_desc``.

    ``score_desc`` is spliced into a SINGLE-quoted YAML scalar, so it may
    carry the double quotes an HTML attribute needs.
    """
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: t4c8_local
                type: directory
                directory: {root_path!s}
            """),
            "pipeline_config.yaml": textwrap.dedent("""
                preamble:
                    input_reference_genome: acgt
                    summary: asdf summary
                    description: sample description
                annotators:
                    - position_score: one
            """),
            "one": {
                "genomic_resource.yaml": textwrap.dedent(f"""
                        type: position_score
                        table:
                            filename: data.txt
                        scores:
                        - id: score_one
                          type: float
                          name: score
                          desc: '{score_desc}'
                """),
            },
            "acgt": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: reference_genome
                    filename: genome.fa
                """),
                "genome.fa": """blabla""",
            },
        },
    )
    setup_denovo(root_path / "one" / "data.txt", textwrap.dedent("""
        chrom  pos_begin  score
        chr1   4          0.01
    """))


def render_doc_page(
    tmp_path: pathlib.Path,
    score_desc: str,
) -> str:
    """Run the annotate_doc CLI over a score described by ``score_desc``."""
    root_path = tmp_path / "grr"
    _build_grr(root_path, score_desc)
    output_file = tmp_path / "output.html"

    cli([
        str(root_path / "pipeline_config.yaml"),
        "-o", str(output_file),
        "-g", str(root_path / "grr.yaml"),
    ])

    return output_file.read_text()


def render_gene_set_doc_page(
    tmp_path: pathlib.Path,
    collection_id: str,
) -> str:
    """Run the annotate_doc CLI over a gene set collection.

    The gene set annotator names the collection in its OWN
    documentation, so the collection id -- GRR-supplied, like every
    resource id -- is a vector into the second ``markdown`` call site.
    """
    root_path = tmp_path / "grr"
    grr_path = root_path / "grr"
    foobar_genome(grr_path)
    foobar_genes(grr_path)
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: t4c8_local
                type: directory
                directory: {grr_path!s}
            """),
            "pipeline_config.yaml": textwrap.dedent("""
                preamble:
                    input_reference_genome: foobar_genome
                annotators:
                    - effect_annotator:
                        genome: foobar_genome
                        gene_models: foobar_genes
                        attributes:
                        - source: gene_list
                          internal: true
                    - gene_set_annotator:
                        resource_id: gene_sets
                        input_gene_list: gene_list
            """),
            "grr": {
                "foobar_genome": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: genome
                        filename: chrAll.fa
                    """),
                },
                "foobar_genes": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: gene_models
                        filename: genes.txt
                        format: refflat
                    """),
                },
                "gene_sets": {
                    "genomic_resource.yaml": textwrap.dedent(f"""
                        id: '{collection_id}'
                        type: gene_set
                        format: directory
                        directory: geneSets
                    """),
                    "geneSets": {
                        "set_0.txt": "set_0\na gene set\ng1\ng2\n",
                    },
                },
            },
        },
    )
    output_file = tmp_path / "output.html"

    cli([
        str(root_path / "pipeline_config.yaml"),
        "-o", str(output_file),
        "-g", str(root_path / "grr.yaml"),
    ])

    return output_file.read_text()


def test_the_annotators_own_documentation_link_stays_a_link(
    tmp_path: pathlib.Path,
) -> None:
    """In-tree annotator documentation is markup this project authored.

    Every built-in annotator appends a ``More info`` anchor to its own
    documentation, and the spliceai plugin puts ``<br/>``/``<em>`` in an
    attribute description.  That HTML is not GRR-supplied, and a reader
    must still get a working link -- escaping the whole documentation
    string turns it into visible tag soup.
    """
    page = render_doc_page(tmp_path, "an ordinary description")

    dom = parse_page(page)
    assert (
        "href",
        f"{Annotator.BASE_DOC_URL}#position-score-annotator",
    ) in dom.attributes
    assert "More info" in "".join(dom.text)


def test_a_script_in_a_score_desc_is_not_a_script_element(
    tmp_path: pathlib.Path,
) -> None:
    """A script tag in a score desc lands as visible text, not as script."""
    page = render_doc_page(
        tmp_path, "DESC<script>gainxss623desc()</script>")

    dom = parse_page(page)
    assert "gainxss623desc" not in "".join(dom.script_data)
    assert "script" not in dom.tags
    assert "gainxss623desc" in "".join(dom.text)


@pytest.mark.parametrize("payload", [
    '<img src="x" onerror="gainxss623desc()">',
    '<svg onload="gainxss623desc()"></svg>',
    '<a href="#" onclick="gainxss623desc()">click</a>',
])
def test_a_handler_in_a_score_desc_lands_no_handler_on_the_page(
    tmp_path: pathlib.Path,
    payload: str,
) -> None:
    """An event handler in a score desc is not an attribute of any tag.

    The handler is refused on a tag documentation is allowed to write
    (``a``) as well as on tags it is not (``img``, ``svg``).  Only the
    first exercises the attribute check: the others never reach it,
    because the tag itself is already outside the vocabulary -- so
    without the anchor, a resource writing ``<a href="#"
    onclick="...">`` would land a live handler on the page and nothing
    here would notice.
    """
    page = render_doc_page(tmp_path, f"DESC{payload}")

    assert event_handler_attributes(page) == []
    assert "gainxss623desc" in "".join(parse_page(page).text)


def test_a_handler_in_an_annotator_documentation_lands_no_handler(
    tmp_path: pathlib.Path,
) -> None:
    """An annotator's own documentation is escaped, not just an attribute's."""
    page = render_gene_set_doc_page(
        tmp_path, '<img src="x" onerror="gainxss623annot()">')

    assert event_handler_attributes(page) == []
    assert "gainxss623annot" in "".join(parse_page(page).text)


def test_markdown_in_a_score_desc_still_renders(
    tmp_path: pathlib.Path,
) -> None:
    """Escaping the HTML leaves the Markdown syntax working."""
    page = render_doc_page(tmp_path, "a **bold623** description")

    assert "<strong>bold623</strong>" in page


def test_a_comparison_in_a_score_desc_stays_visible(
    tmp_path: pathlib.Path,
) -> None:
    """Prose comparing values is shown, not eaten as a tag.

    Real GRR descriptions carry bare comparison operators (``<0.3``,
    ``MutPred_score > 0.75 and p < 0.05``); ``html.parser`` resolves the
    entity back, so finding the comparison in the page's text is finding
    what a reader sees.
    """
    page = render_doc_page(tmp_path, "significant when p < 0.05 holds")

    assert "significant when p < 0.05 holds" in "".join(parse_page(page).text)


def test_a_self_closing_link_in_a_score_desc_does_not_stay_open(
    tmp_path: pathlib.Path,
) -> None:
    """A link written ``<a .../>`` is closed like any other link.

    HTML5 ignores the trailing slash on an element that is not void, so
    the anchor a resource writes this way opens for real; a reader keeps
    it open across the following markup until some later end tag closes
    it.  Left unclosed it reaches past the documentation it was written
    in -- the histogram image and the annotator sections that follow
    become a live link to the address the resource chose.
    """
    page = render_doc_page(
        tmp_path, 'DESC<a href="https://evil.example/gainxss623"/>')

    assert "a" not in unclosed_elements(page)


def test_a_self_closing_emphasis_in_a_score_desc_does_not_run_on(
    tmp_path: pathlib.Path,
) -> None:
    """Emphasis written ``<em/>`` is closed rather than left open.

    The same rule that keeps ``<a .../>`` open keeps ``<em/>`` open, and
    documentation is rendered *into* a page: an element a resource
    leaves open styles the rest of the document.
    """
    page = render_doc_page(tmp_path, "DESC<em/>gainxss623tail")

    assert "em" not in unclosed_elements(page)


def test_a_line_break_in_a_score_desc_stays_a_line_break(
    tmp_path: pathlib.Path,
) -> None:
    """``<br/>`` is markup documentation legitimately writes.

    A void element has no end tag, so writing it self-closed is the
    correct spelling and it must keep working -- the spliceai plugin
    writes ``<br/>`` in an attribute description.
    """
    page = render_doc_page(tmp_path, "before623<br/>after623")

    assert "br" in parse_page(page).tags
    assert "</br>" not in page


@pytest.mark.parametrize("payload", [
    'DESC<a href="javascript:gainxss623desc()">click</a>',
    "DESC[click](javascript:gainxss623desc())",
    "DESC[click](javascript&#58;gainxss623desc())",
    "DESC[click](javascript&colon;gainxss623desc())",
    "DESC[click](JAVASCRIPT&#x3a;gainxss623desc())",
    "DESC[click](java\tscript:gainxss623desc())",
])
def test_a_javascript_url_in_a_score_desc_lands_no_link(
    tmp_path: pathlib.Path,
    payload: str,
) -> None:
    """A javascript: URL in a score desc is text, not a live link.

    The scheme is checked the way a browser resolves it rather than by
    the literal spelling: ``html.parser`` decodes a character reference
    in an attribute value, so ``javascript&#58;`` and ``JAVASCRIPT&#x3a;``
    reach the DOM as the same runnable URL that a plain ``javascript:``
    would -- and a browser also drops whitespace inside the scheme.
    """
    page = render_doc_page(tmp_path, payload)

    dom = parse_page(page)
    assert [
        value for _, value in dom.attributes
        if value is not None and dangerous_url(value)
    ] == []
    assert "gainxss623desc" in "".join(dom.text)
