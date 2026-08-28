"""Characterization tests: GRR documentation reaches the page as live markup.

**These tests pin a deliberate decision, not an oversight.** ADR 0016, "GRR
content is trusted by authorship", records that GAIn does not defend against a
hostile GRR -- not as code, not as filesystem reach, and not as the markup
tested here.  A score's ``desc`` and an annotator's documentation are rendered
through ``markdown`` and marked ``|safe``, so whatever HTML they carry lands in
the generated page as markup.  That is the intended behavior: resource authors
write documentation prose, and it renders.

``markdown(...)|safe`` looks exactly like a bug to anyone reading it cold.
gain#623 was triaged, implemented and reviewed over three rounds -- producing a
228-line hand-rolled sanitizer, since discarded -- before anyone asked whether
the threat model held.  It did not, and gain#623 and gain#699 were closed
``wontfix``.  These tests exist so the next attempt fails CI in seconds instead.

**If a test here fails, the code changed or the trust boundary did.**  If the
boundary moved -- a community-contributed GRR, a user-supplied GRR URL in
gainweb, GRR prose rendered into an authenticated origin -- then re-read ADR
0016's "What would reopen this" checklist and reopen gain#623/gain#699 as filed.
Do not "fix" these tests to make a sanitizer pass.

**What is pinned, and what is not.**  This file covers
``annotate_doc_pipeline_template.jinja`` through the two call sites a ``core``
test can reach.  Since #952 there is one *renderer* --
``gain.annotation.pipeline_doc.render_pipeline_doc`` -- and three callers of
it; the third is the web API download endpoint, which lives in a separate
package with its own CI image and so cannot be reached from here.  It no
longer renders anything of its own, and that it stays that way is pinned in
``web_api/web_annotation/tests/test_pipeline_doc_delegation.py``.
Also deliberately *not* covered: ``resource_template.jinja``'s
``meta.description`` sink, and the ``about.md`` render in
``fsspec_protocol.py`` that ADR 0016 lists as unfiled.  Green here is not a
statement about those.

The mirror image of this file is
``core/tests/small/templates/test_resource_page_escaping.py``, which pins the
*escaping* that does apply to resource ids.  The two make opposite assertions
about neighbouring machinery; read them together.

See ``docs/adr/0016-grr-content-is-trusted-by-authorship.md``, gain#623,
gain#728, gain#731.
"""
from __future__ import annotations

import pathlib
from collections.abc import Callable
from html.parser import HTMLParser

import pytest
import yaml
from gain.annotation import annotate_doc
from gain.genomic_resources.implementations.annotation_pipeline_impl import (
    AnnotationPipelineImplementation,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_directories,
)
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    a_reference_genome,
)
from gain.templates import get_jinja_env
from gain.testing.foobar_import import foobar_genes, foobar_genome

#: Unique to these tests, so an assertion cannot be satisfied by markup the
#: page ships itself -- the false-signal lesson of gain#558.  The pages really
#: do carry their own ``<script>`` and ``<em>``, so every assertion below is
#: scoped to this marker rather than to a tag name.
MARKER = "gaintrust731"

#: Elements HTML gives no end tag, so they never enclose anything.
VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


class _PageDom(HTMLParser):
    """Read a page as markup rather than as text.

    ``html.parser`` is not an HTML5-spec tokenizer, and where it differs it
    under-reports live markup (``<script/>`` opens an element for a browser
    but not here).  That is the safe direction for these tests: anything it
    reports as an element really is one.

    ``element_text`` pairs each run of character data with the innermost
    element open at the time.  Assertions need that pairing: a bare
    "is there a ``script`` tag" says only that *some* element of that name is
    on the page, and these pages ship their own.

    Deliberately no ``text`` accumulator.  ``convert_charrefs`` (on by
    default) decodes ``&lt;script&gt;`` back into ``<script>``, so a plain
    text collection reports *escaped* markup indistinguishably from live
    markup -- the exact difference this file exists to detect.  The stack
    also has no implied-end-tag handling, so ``<p>one<p>two`` nests rather
    than siblings; that is immaterial for these payloads but is a property of
    the stack, not of the tokenizer.
    """

    def __init__(self) -> None:
        super().__init__()
        self.attributes: list[tuple[str, str | None]] = []
        self.element_text: list[tuple[str, str]] = []
        self._open: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        self.attributes.extend(attrs)
        if tag not in VOID_ELEMENTS:
            self._open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        # Unwind to the innermost matching open tag.  The guard matters: a
        # stray end tag in a future payload would otherwise pop an empty
        # stack and raise.
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


def _realize_score_grr(root_path: pathlib.Path, score_desc: str) -> None:
    """Realize a one-score GRR whose score carries ``score_desc``.

    The builders render ``desc`` through ``yaml.safe_dump``
    (``testing/score_specs.py``), so a payload may carry quotes of either
    kind without turning an assertion failure into a YAML scanner error.
    ``build_definition`` writes the ``grr.yaml`` the CLI's ``-g`` flag wants,
    outside the resources directory so it is not walked as a resource.
    """
    (a_grr()
     .with_resource(
         "one",
         a_position_score()
         .with_score(
             "score_one", "float", column_name="score", desc=score_desc)
         .with_data("chrom  pos_begin  score\nchr1   4          0.01\n"))
     .with_resource(
         "acgt",
         a_reference_genome().with_chromosome("chr1", "ACGT" * 25))
     .build_definition(root_path, grr_id="trust_local"))


def _run_doc_cli(root_path: pathlib.Path, output_file: pathlib.Path) -> str:
    """Run the annotate_doc CLI over the GRR realized under ``root_path``.

    Called through the module rather than an imported ``cli`` name, which
    is how the CLI is reached everywhere else in this file.  The sink
    census below no longer needs that -- since gain#751 it patches the
    shared environment's ``markdown`` global, which the CLI shares with
    every other renderer -- but keeping one spelling of the call keeps the
    helpers here uniform.
    """
    annotate_doc.cli([
        str(root_path / "pipeline_config.yaml"),
        "-o", str(output_file),
        "-g", str(root_path / "grr.yaml"),
    ])
    return output_file.read_text()


def render_doc_page(tmp_path: pathlib.Path, score_desc: str) -> str:
    """Run the annotate_doc CLI over a score described by ``score_desc``."""
    root_path = tmp_path / "grr"
    _realize_score_grr(root_path, score_desc)
    setup_directories(root_path / "pipeline_config.yaml", yaml.safe_dump({
        "preamble": {"input_reference_genome": "acgt"},
        "annotators": [{"position_score": "one"}],
    }))

    return _run_doc_cli(root_path, tmp_path / "output.html")


def render_pipeline_resource_page(
    tmp_path: pathlib.Path,
    score_desc: str,
) -> str:
    """Render the page ``grr_manage`` builds for a pipeline resource.

    A distinct route to the template, not a repeat of the CLI's:
    ``AnnotationPipelineImplementation`` reaches the shared renderer from its
    own module and supplies its own, repository-relative address policy.
    gain#623's review round 1 found exactly this class of gap -- a change
    that converted two of the three call sites and left the third -- so
    every one a ``core`` test can reach is pinned.

    This is also the call site with the widest blast radius: its output is
    published as the static resource pages under ``grr-*.iossifovlab.com``.
    That those pages keep their relative addresses is pinned separately, by
    ``test_the_rendered_page_carries_the_relative_addresses``.
    """
    root_path = tmp_path / "grr"
    (a_position_score()
     .with_score("score", "float", column_name="s1", desc=score_desc)
     .with_data("chrom  pos_begin  s1\nchr1   4          0.01\n")
     .realize_into(root_path / "one"))
    setup_directories(root_path, {
        "pipeline": {
            "genomic_resource.yaml": yaml.safe_dump({
                "type": "annotation_pipeline",
                "filename": "annotation.yaml",
            }),
            "annotation.yaml": "- position_score: one\n",
        },
    })
    repo = build_filesystem_test_repository(root_path)

    return AnnotationPipelineImplementation(
        repo.get_resource("pipeline")).get_info(repo=repo)


#: Every call site of ``annotate_doc_pipeline_template.jinja`` a ``core`` test
#: can reach.  (Before #952 these were separate *renderers*, each binding the
#: template itself; they now share one, but they still reach it by different
#: routes and with different address policies, so each is still worth running
#: every payload through.)  A change that converts one and not another goes
#: red -- and a new call site is one entry here rather than a bespoke test.
RENDERERS: list[tuple[str, Callable[[pathlib.Path, str], str]]] = [
    ("annotate_doc_cli", render_doc_page),
    ("pipeline_resource_impl", render_pipeline_resource_page),
]

_RENDERER_IDS = [name for name, _ in RENDERERS]
_RENDERER_FUNCS = [render for _, render in RENDERERS]


def render_gene_set_doc_page(
    tmp_path: pathlib.Path,
    collection_id: str,
) -> str:
    """Run the annotate_doc CLI over a gene set collection.

    Reaches the template's *second* ``markdown(...)|safe`` call site --
    ``annotator._info.documentation`` rather than an attribute's.  The gene
    set annotator names the collection in its own documentation
    (``gene_set_annotator.py``), so a collection id is GRR-supplied text
    arriving at a sink the score ``desc`` tests never touch.
    """
    root_path = tmp_path / "grr"
    grr_path = root_path / "grr"
    # These write each resource's genomic_resource.yaml themselves.
    foobar_genome(grr_path)
    foobar_genes(grr_path)

    setup_directories(root_path, {
        "grr.yaml": yaml.safe_dump({
            "id": "trust_local",
            "type": "directory",
            "directory": str(grr_path),
        }),
        "pipeline_config.yaml": yaml.safe_dump({
            "preamble": {"input_reference_genome": "foobar_genome"},
            "annotators": [
                {"effect_annotator": {
                    "genome": "foobar_genome",
                    "gene_models": "foobar_genes",
                    "attributes": [{"source": "gene_list", "internal": True}],
                }},
                {"gene_set_annotator": {
                    "resource_id": "gene_sets",
                    "input_gene_list": "gene_list",
                }},
            ],
        }),
        "grr": {
            "gene_sets": {
                "genomic_resource.yaml": yaml.safe_dump({
                    "id": collection_id,
                    "type": "gene_set_collection",
                    "format": "directory",
                    "directory": "geneSets",
                }),
                "geneSets": {"set_0.txt": "set_0\na gene set\ng1\ng2\n"},
            },
        },
    })

    return _run_doc_cli(root_path, tmp_path / "output.html")


@pytest.mark.parametrize("render", _RENDERER_FUNCS, ids=_RENDERER_IDS)
def test_a_script_in_a_score_desc_reaches_the_page_as_a_live_script(
    tmp_path: pathlib.Path,
    render: Callable[[pathlib.Path, str], str],
) -> None:
    """A ``<script>`` a resource writes is a real script element on the page.

    The bluntest statement of ADR 0016 that a test can make.  A change that
    escapes or sanitizes the documentation sink fails here first.
    """
    page = render(tmp_path, f"DESC<script>{MARKER}()</script>")

    assert f"{MARKER}()" in parse_page(page).text_in("script")


@pytest.mark.parametrize("render", _RENDERER_FUNCS, ids=_RENDERER_IDS)
def test_an_event_handler_in_a_score_desc_reaches_the_page_as_an_attribute(
    tmp_path: pathlib.Path,
    render: Callable[[pathlib.Path, str], str],
) -> None:
    """An ``on*`` handler a resource writes is a real attribute on the page.

    Asserted as a specific ``(name, value)`` pair carrying the marker rather
    than by scanning the page for any ``on*`` attribute: the base template is
    free to grow a legitimate handler of its own without touching this test.
    """
    page = render(tmp_path, f'DESC<a href="#" onclick="{MARKER}()">click</a>')

    assert ("onclick", f"{MARKER}()") in parse_page(page).attributes


@pytest.mark.parametrize("render", _RENDERER_FUNCS, ids=_RENDERER_IDS)
def test_a_javascript_url_in_a_score_desc_reaches_the_page_intact(
    tmp_path: pathlib.Path,
    render: Callable[[pathlib.Path, str], str],
) -> None:
    """The third exposure ADR 0016 names, and the only one not raw HTML.

    A ``javascript:`` URL arrives through plain *Markdown link syntax*, so
    markdown2 neutralizes it on its own the moment ``safe_mode`` is set --
    ``[x](javascript:...)`` renders ``href="#"`` under escaping and the real
    URL without it.  That makes this the one payload here that a narrow
    "scrub dangerous hrefs only" change would trip over; every other test in
    this file would stay green through such a change.
    """
    page = render(tmp_path, f"DESC [click](javascript:{MARKER}())")

    assert ("href", f"javascript:{MARKER}()") in parse_page(page).attributes


@pytest.mark.parametrize("render", _RENDERER_FUNCS, ids=_RENDERER_IDS)
def test_inline_html_in_a_score_desc_renders_as_markup(
    tmp_path: pathlib.Path,
    render: Callable[[pathlib.Path, str], str],
) -> None:
    """Documentation prose renders -- this is the feature the decision buys.

    The payload is *raw inline HTML*, deliberately.  Markdown emphasis
    (``*text*``) would be the obvious thing to assert and would pin nothing
    against an escaping sink: markdown2 emits ``<em>`` from ``*text*``
    whether or not ``safe_mode`` is set.  (Raw HTML separates escaping from
    *sanitizing* only partly -- an allowlist that permits ``em`` passes this
    too.  The tags no allowlist carries are covered by the script and handler
    tests above.)

    The payload carries Markdown emphasis *as well*, so a live ``<em>`` is on
    the page either way and the assertion cannot be satisfied by it.  That is
    the trap this test is shaped to avoid: asserting merely that some ``em``
    exists would pass the moment any annotator's documentation grows a
    ``*star*``.
    """
    page = render(tmp_path, f"DESC <em>{MARKER}</em> and *emph* tail")

    assert ("em", MARKER) in parse_page(page).element_text


def test_a_script_in_annotator_documentation_reaches_the_page_as_a_script(
    tmp_path: pathlib.Path,
) -> None:
    """The decision holds at the annotator-documentation call site too.

    The template renders documentation through ``markdown(...)|safe`` twice --
    once for an attribute's documentation and once for the annotator's own.
    A change touching only the attribute call site leaves this one alone, and
    the two are pinned separately so that shows up as a single red test.
    """
    page = render_gene_set_doc_page(
        tmp_path, f"sets<script>{MARKER}()</script>")

    assert f"{MARKER}()" in parse_page(page).text_in("script")


def test_the_template_funnels_documentation_through_two_markdown_calls(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A census of the sinks, so a *new* one cannot arrive unnoticed.

    The tests above each pin one sink that exists today.  None of them
    notices a third ``markdown(...)|safe`` added to the template -- it would
    simply be untested, and green CI would read as though it were covered.

    This asserts the whole set: for a one-score, one-annotator pipeline the
    template reaches ``markdown`` exactly twice, once with the score's
    ``desc`` and once with the annotator's own documentation.  A new call
    site makes it three and lands the reader in this file.

    The spy is installed on the shared environment's ``globals`` because
    that is where the template resolves the name since gain#751.  That
    also makes this the guard on both halves of that change *for the
    renderer it drives*: a render kwarg shadows a global, so if the
    ``annotate_doc`` CLI went back to passing ``markdown=`` the spy would
    be bypassed and the census would read zero rather than two.

    Only that one renderer, though -- ``render_doc_page`` runs the CLI.
    A kwarg re-added at one of the other former call sites would not show
    up here.  That is a coverage limit and not a hole: a kwarg bound to
    the wrapper renders identically, and one bound to markdown2's raw
    output is refused by the architecture fence.
    """
    seen: list[str] = []
    env = get_jinja_env()
    real_markdown = env.globals["markdown"]

    def spy(text: str, *args: object, **kwargs: object) -> str:
        seen.append(text)
        return real_markdown(text, *args, **kwargs)

    monkeypatch.setitem(env.globals, "markdown", spy)

    render_doc_page(tmp_path, f"DESC {MARKER}")

    assert len(seen) == 2, seen
    assert any(MARKER in text for text in seen)
