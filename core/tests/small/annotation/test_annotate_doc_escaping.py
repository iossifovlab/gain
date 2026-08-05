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
])
def test_a_handler_in_a_score_desc_lands_no_handler_on_the_page(
    tmp_path: pathlib.Path,
    payload: str,
) -> None:
    """An event handler in a score desc is not an attribute of any tag."""
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
