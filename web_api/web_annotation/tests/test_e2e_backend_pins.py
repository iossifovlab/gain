# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The backend strings the ``web_e2e`` Playwright specs pin still render.

**Why this exists.**  A spec under ``web_e2e/tests`` asserts user-visible
text with ``toContainText`` / ``toHaveText`` / ``getByText``.  Some of that
text is the backend's -- a refusal composed in ``gain`` or a response
literal from a ``web_api`` view -- and nothing connected the TypeScript
literal to the Python that emits it.  When gain#1329 reworded the
missing-``resource_id`` refusal it updated every Python assertion and could
not see the spec, so the deployed-stack e2e job went red seven minutes
after the next deploy (gain#1353).  This module is the connection: it runs
in the ordinary ``web_api`` suite, with no browser and no stack, and fails
on the day a pin and its producer part ways.

**What it checks, in both directions.**

1. Every key in :data:`REGISTRY` is a substring of the message its producer
   renders *now*, from the real code path -- the exact contract
   ``toContainText`` enforces in the browser.  A reworded backend fails
   here, naming the spec that still pins the old text.
2. Every sentence-shaped pin found in the live spec tree is a substring of
   what some registered producer renders, or is listed in
   :data:`NOT_BACKEND`.  A new or stale backend pin fails here, naming the
   spec and line.  The test is on the pin's whole text, never on a
   fragment of it: a pin that merely *contains* a registered key
   (``Invalid configuration, reason: <reworded tail>``) is not accounted
   for by that key.
3. Every key in both tables is still pinned by some spec, so the tables
   cannot silently accumulate dead entries.

Both sides are compared with whitespace :func:`collapsed`, as every
Playwright text matcher compares, so where core wraps a sentence is
invisible and the order of sentences is not.  Where the page renders the
backend's Markdown before the spec sees it (the annotator modal shows
core's ``AnnotatorInfo.documentation``), the producer renders it to text
too, with core's own renderer.

**Why substrings are rendered, not grepped.**  Neither side spells a
message in one literal: the motivating pin is two ``+``-joined TypeScript
strings, and its producer is an f-string split across two implicitly
concatenated lines.  Grepping either source for the other's text would
pass vacuously on the very case this guards.  Producers therefore call the
functions the views call -- ``format_config_error`` around the pipeline
loader, against the same GRR the test settings hand the views -- or the
constants the views return (``web_annotation.messages``).  Each producer is
rendered once per session.

**Why the scope rule is sentence shape.**  The specs also pin identifiers
(``allele_score``, resource ids), the UI's own text, and their own inputs
echoed back.  Only backend *messages* rot the way #1353 did, and those are
sentence-shaped: they end in ``.`` or ``!``, or run to four or more words.
Anything shorter is out of scope by declaration -- it may still be
registered voluntarily (``not found`` is), but direction 2 does not demand
it.

**Where it runs.**  In a source tree (a checkout, or ``/workspace`` in the
``web_api`` CI image, whose Dockerfile copies ``web_e2e/tests`` and
``web_e2e/pages``) the spec tree must be present and its absence fails; an
install without the source tree around it skips with the reason.

**Adding a pin.**  Register it here with a producer that renders the
message through the backend's public seam.  If the text is not the
backend's (the Angular UI's, an external tool's, or the spec's own
input), add it to :data:`NOT_BACKEND` with its origin instead.  If the
page renders the backend's Markdown, the producer returns its
:func:`markdown_text`, built from a pipeline in the shape the spec's
instance has -- the decorators that shape adds write into the
documentation too -- as the annotator modal's entry does.
"""
from __future__ import annotations

import pathlib
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from html.parser import HTMLParser

import pytest
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.templates.markdown_support import render_markdown

from web_annotation import messages
from web_annotation.annotation_base_view import GRR, format_config_error

#: The repository root, three levels above this module both in a checkout
#: and in the CI image (``/workspace/web_api/web_annotation/tests``).
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

E2E_ROOT = REPO_ROOT / "web_e2e"

#: The directories under ``web_e2e/`` that assert text -- the ones the
#: ``web_api`` Dockerfile copies.  ``utils.ts`` asserts nothing;
#: ``node_modules`` and ``reports`` are neither copied nor scanned.
SPEC_DIRS = ("tests", "pages")

#: Playwright matchers whose string argument is a pin on rendered text.
#: Deliberately outside the set: ``toContain`` / ``toMatch`` on downloaded
#: content (the documentation specs read core's HTML export, not the page)
#: and the array form ``toHaveText([...])``, which pins identifiers.
MATCHERS = frozenset({"toContainText", "toHaveText", "getByText"})


@dataclass(frozen=True)
class Pin:
    """One text assertion in a spec, with its fragments already joined."""

    spec: str
    line: int
    matcher: str
    text: str

    def __str__(self) -> str:
        return f"{self.spec}:{self.line}  {self.matcher}({self.text!r})"


_MATCHER_CALL = re.compile(
    r"\.(?P<matcher>" + "|".join(sorted(MATCHERS)) + r")\(")

_ESCAPES = re.compile(r"\\(.)")


def _unescape(raw: str) -> str:
    return _ESCAPES.sub(lambda m: "\n" if m[1] == "n" else m[1], raw)


def _skip_blanks(source: str, pos: int) -> int:
    """Past whitespace and ``// …`` comments from ``pos``."""
    while pos < len(source):
        if source[pos].isspace():
            pos += 1
        elif source.startswith("//", pos):
            end = source.find("\n", pos)
            pos = len(source) if end < 0 else end + 1
        else:
            break
    return pos


def _read_literal(source: str, pos: int) -> tuple[str, int] | None:
    """Parse one string literal starting at ``pos``; ``None`` if not one.

    A template literal stops at its first ``${``: the interpolated tail is
    volatile, so only the literal prefix is a pin.
    """
    quote = source[pos]
    if quote not in "'\"`":
        return None
    i = pos + 1
    out: list[str] = []
    while i < len(source):
        ch = source[i]
        if ch == "\\":
            out.append(source[i:i + 2])
            i += 2
            continue
        if ch == quote:
            return _unescape("".join(out)), i + 1
        if quote == "`" and source.startswith("${", i):
            close = source.find("`", i)
            return _unescape("".join(out)), close + 1
        out.append(ch)
        i += 1
    return None


def _read_concatenation(source: str, pos: int) -> str | None:
    """Join ``'a' + 'b' + …`` starting at ``pos``; ``None`` if no literal."""
    parts: list[str] = []
    while True:
        pos = _skip_blanks(source, pos)
        if pos >= len(source):
            break
        literal = _read_literal(source, pos)
        if literal is None:
            break
        text, pos = literal
        parts.append(text)
        pos = _skip_blanks(source, pos)
        if pos >= len(source) or source[pos] != "+":
            break
        pos += 1
    return "".join(parts) if parts else None


def extract_pins(source: str, spec: str = "<source>") -> list[Pin]:
    """Every text pin in one spec's source, in file order."""
    pins: list[Pin] = []
    for call in _MATCHER_CALL.finditer(source):
        text = _read_concatenation(source, call.end())
        if text is None:
            continue
        line = source.count("\n", 0, call.start()) + 1
        pins.append(Pin(spec, line, call.group("matcher"), text))
    return pins


def is_sentence_shaped(text: str) -> bool:
    """The shape of a backend message, as opposed to an identifier."""
    words = text.split()
    if len(words) < 2:
        return False
    return text.rstrip().endswith((".", "!")) or len(words) >= 4


def collapsed(text: str) -> str:
    """``text`` with every run of whitespace, newlines included, one space.

    What every Playwright text matcher compares: whitespace is normalised
    on both sides, so where core wraps a sentence is invisible to the
    browser.
    """
    return " ".join(text.split())


class _Text(HTMLParser):
    """The character data of an HTML document, tags dropped."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def markdown_text(markdown: str) -> str:
    """The text a browser shows for ``markdown``, :func:`collapsed`.

    Rendered by core's own renderer.  The UI's is a different one, but the
    two agree on the text of what the annotators write.
    """
    parser = _Text()
    parser.feed(render_markdown(markdown))
    parser.close()
    return collapsed("".join(parser.parts))


def unrendered(text: str, shown: str) -> list[str]:
    """The lines of the pin ``text`` that ``shown`` does not carry.

    Empty when the collapsed pin is a substring of ``shown``.  Otherwise
    the pin's lines not found, so that a reword is named by line -- or the
    whole pin, when every line is found but not in this order.
    """
    if collapsed(text) in shown:
        return []
    lines = [collapsed(line) for line in text.splitlines()]
    missing = [line for line in lines if line and line not in shown]
    return missing or [collapsed(text)]


def _config_error(config: str) -> Callable[[], str]:
    """Render what the validation endpoint says about ``config``."""
    def render() -> str:
        try:
            load_pipeline_from_yaml(config, GRR)
        except Exception as exc:  # ruff: ignore[blind-except]
            return format_config_error(exc)
        return "<the configuration loaded without error>"
    return render


def _annotator_documentation(config: str) -> Callable[[], str]:
    """The text the modal shows for ``config``'s last annotator.

    The last, and from a whole pipeline, because the documentation is not
    the annotator's alone: an ``input_annotatable`` parameter wraps it in
    ``InputAnnotableAnnotatorDecorator``, which appends that parameter to
    it as a list item.
    """
    def render() -> str:
        pipeline = load_pipeline_from_yaml(config, GRR)
        return markdown_text(pipeline.annotators[-1].get_info().documentation)
    return render


#: The genome the ``web_api`` test GRR ships; the e2e instance's
#: ``hg38/genomes/GRCh38-hg38`` is absent here and would be refused first.
_FIXTURE_GENOME = "hg38/GRCh38-hg38/genome"

#: An allele score the ``web_api`` test GRR ships.
_FIXTURE_ALLELE_SCORE = "scores/allele1"

# One producer per distinct configuration; several pins share a refusal.
_no_annotators = _config_error(
    "preamble:\n input_reference_genome: hg38/genomes/GRCh38-hg38")
_no_preamble = _config_error(
    "annotators:\n - allele_score: hg38/scores/CADD_v1.7")
_bare_allele_score = _config_error("- allele_score")
_missing_score = _config_error(
    "preamble:\n"
    f"   input_reference_genome: {_FIXTURE_GENOME}\n"
    "annotators:\n"
    "- allele_score:\n"
    "    resource_id: hg38/scores/THIS_RESOURCE_DOES_NOT_EXIST\n"
    "    input_annotatable: normalized_allele\n")
# The e2e pipeline's shape (web_e2e/tests/single-annotation/helpers.ts):
# the normalizer's allele fed to allele_score by input_annotatable.
_allele_score_documentation = _annotator_documentation(
    "preamble:\n"
    f"   input_reference_genome: {_FIXTURE_GENOME}\n"
    "annotators:\n"
    "- normalize_allele_annotator\n"
    "- allele_score:\n"
    f"    resource_id: {_FIXTURE_ALLELE_SCORE}\n"
    "    input_annotatable: normalized_allele\n")

#: Pinned backend text -> how the backend produces it.  A producer is the
#: rendered message or a callable returning it; the key must be a substring
#: of what it renders, both :func:`collapsed`.  Keys are the pins spelled
#: out, on purpose: a key written as ``messages.X`` with producer
#: ``messages.X`` would check nothing.
REGISTRY: dict[str, str | Callable[[], str]] = {
    # -- pipeline validation (core refusals through format_config_error) --
    "Invalid configuration": _no_annotators,
    "Invalid configuration, reason: 'annotators'": _no_annotators,
    "Invalid configuration, reason: 'preamble'": _no_preamble,
    "Invalid configuration, reason: "
    "The A0 annotator configuration is incorrect:": _bare_allele_score,
    "needs a 'resource_id' parameter naming the resource "
    "the annotator reads.": _bare_allele_score,
    "not found": _missing_score,
    # -- annotator documentation (core Markdown, rendered by the UI) --
    # The annotator modal as a whole, the decorator's input_annotatable
    # line included; the producer renders the Markdown to text.
    "Annotator to use with scores that depend on allele like\n"
    "variant frequencies, etc.\n"
    "Mode (mode parameter, applies to VCFAllele inputs only):\n\n"
    "allele (default): exact chrom/pos/ref/alt match.\n"
    "region: aggregates scores for all allele lines overlapping the\n"
    "annotatable's span.\n\n"
    "Non-VCFAllele annotatables always use region aggregation.\n\n"
    "More info\n\n"
    "input_annotatable: normalized_allele\n\n": _allele_score_documentation,
    # -- web_api response literals --
    "Job quota exceeded!": messages.JOB_QUOTA_EXCEEDED,
    "Single allele query quota exceeded!":
        messages.SINGLE_ALLELE_QUOTA_EXCEEDED,
    "Cannot build annotatable from selected columns!":
        messages.CANNOT_BUILD_ANNOTATABLE,
    "Invalid login credentials": messages.INVALID_LOGIN_CREDENTIALS,
    "This email is already in use": messages.EMAIL_ALREADY_IN_USE,
    # The spec interpolates the address; the pin is the literal prefix.
    "An e-mail has been sent to ":
        messages.RESET_LINK_EMAIL_SENT.format(email="user@example.com"),
}

#: Sentence-shaped pins that are not the backend's, each with its origin.
NOT_BACKEND: dict[str, str] = {
    # bcftools, on the VCF-upload validation path (validate_vcf's stderr).
    "does not have valid header": "external: bcftools",
    # Angular UI (web_ui/src).
    "Password must be at least 6 characters long.": "ui",
    "Invalid email format.": "ui",
    "GAIn: Genomic Annotation Infrastructure": "ui",
    "Attribute with this name already exists": "ui",
    "Unsupported format!": "ui",
    "Upload limit reached!": "ui",
    "Selecting more than 1000 attributes": "ui",
    "Pipeline with this name already exists.": "ui",
    "No columns selected!": "ui",
    "Invalid annotatable format!": "ui",
    "Are you sure? You are going to lose your changes.": "ui",
    # The spec's own input, echoed back by the page (the Monaco editor
    # flattens the YAML it was given; the two echoes differ in spacing).
    "label that will be lost": "spec input",
    "preamble: input_reference_genome: hg38/genomes/GRCh38-hg38"
    "annotators:- allele_score:   resource_id: hg38/scores/CADD_v1.7":
        "spec input",
    "preamble:   input_reference_genome: hg38/genomes/GRCh38-hg38"
    "annotators:- allele_score:    resource_id: hg38/scores/CADD_v1.7":
        "spec input",
}


def _spec_files() -> Iterator[pathlib.Path]:
    for sub in SPEC_DIRS:
        yield from sorted((E2E_ROOT / sub).rglob("*.ts"))


@pytest.fixture(scope="module")
def spec_pins() -> list[Pin]:
    """Every pin in the live spec tree; skips or fails when it is absent.

    A source tree (``pyproject.toml`` beside ``web_e2e/``) must carry the
    specs -- the CI image copies them for exactly this -- so their absence
    there is a failure.  Anywhere else it is a skip.
    """
    for sub in SPEC_DIRS:
        if (E2E_ROOT / sub).is_dir():
            continue
        reason = f"{E2E_ROOT / sub} is absent"
        if (REPO_ROOT / "pyproject.toml").is_file():
            pytest.fail(f"{reason} from the source tree {REPO_ROOT}")
        pytest.skip(f"{reason}: not a source tree")
    return [
        pin
        for path in _spec_files()
        for pin in extract_pins(
            path.read_text(), str(path.relative_to(E2E_ROOT)))
    ]


@pytest.fixture(scope="module")
def rendered(spec_pins: list[Pin]) -> dict[str, str]:
    """What each registered producer renders, :func:`collapsed`, once.

    Depends on ``spec_pins`` so that a skip is decided before any producer
    runs.
    """
    return {
        key: collapsed(producer() if callable(producer) else producer)
        for key, producer in REGISTRY.items()
    }


# --- the extractor ---------------------------------------------------------


@pytest.mark.parametrize(("source", "expected"), [
    pytest.param(
        "await expect(x).toContainText('needs a \\'resource_id\\' "
        "parameter ' +\n      'the annotator reads.');",
        "needs a 'resource_id' parameter the annotator reads.",
        id="plus-joined fragments with escaped quotes"),
    pytest.param(
        'expect(page.getByText("Invalid configuration")).toBeVisible();',
        "Invalid configuration",
        id="double quotes"),
    pytest.param(
        "expect(m).toContainText(`An e-mail has been sent to ${randomEmail}`)",
        "An e-mail has been sent to ",
        id="template literal keeps only the prefix"),
    pytest.param(
        "expect(m).toHaveText('a' +\n  'b' + 'c')",
        "abc",
        id="three fragments across lines"),
    pytest.param(
        "expect(m).toHaveText('allele like\\nvariant frequencies, etc.\\n')",
        "allele like\nvariant frequencies, etc.\n",
        id="newline escapes become newlines"),
    pytest.param(
        "expect(m).toHaveText(\n  // eslint-disable-next-line max-len\n"
        "  'a' + // and\n  'b')",
        "ab",
        id="line comments before and between fragments"),
])
def test_extract_pins_joins_the_literal(source: str, expected: str) -> None:
    pins = extract_pins(source)

    assert [pin.text for pin in pins] == [expected]


def test_extract_pins_skips_non_literal_arguments() -> None:
    source = (
        "locator('mat-option').getByText(name, { exact: true });\n"
        "expect(x).toHaveText(expected);\n"
        "expect(y).toContainText('kept');\n")

    pins = extract_pins(source, spec="s.spec.ts")

    assert [(pin.line, pin.text) for pin in pins] == [(3, "kept")]


@pytest.mark.parametrize(("text", "sentence"), [
    ("Job quota exceeded!", True),
    ("Invalid configuration, reason: 'annotators'", True),
    ("Cannot build annotatable from selected columns", True),
    ("not found", False),
    ("allele_score", False),
    ("Annotator type: allele_score", False),
    ("267 resources", False),
])
def test_sentence_shape_rule(text: str, *, sentence: bool) -> None:
    assert is_sentence_shaped(text) is sentence


# --- the Markdown comparison ----------------------------------------------


@pytest.mark.parametrize(("markdown", "expected"), [
    pytest.param(
        "**Mode** (applies to inputs only):",
        "Mode (applies to inputs only):",
        id="bold"),
    pytest.param(
        "Non-``VCFAllele`` annotatables always use region aggregation.",
        "Non-VCFAllele annotatables always use region aggregation.",
        id="double-backtick code"),
    pytest.param(
        "- ``allele`` (default): exact match.\n"
        "- ``region``: aggregates scores for all allele lines overlapping "
        "the\n  annotatable's span.\n",
        "allele (default): exact match. "
        "region: aggregates scores for all allele lines overlapping the "
        "annotatable's span.",
        id="list items, one wrapped onto an indented line"),
    pytest.param(
        '<a href="https://example.org/x.html#y" target="_blank">More info</a>',
        "More info",
        id="link keeps only its text"),
    pytest.param(
        "\n* **input_annotatable**: `normalized_allele`",
        "input_annotatable: normalized_allele",
        id="the input_annotatable decorator's list item"),
])
def test_markdown_text_is_what_the_browser_shows(
    markdown: str, expected: str,
) -> None:
    assert markdown_text(markdown) == expected


def test_unrendered_names_the_lines_the_backend_no_longer_renders() -> None:
    shown = markdown_text(
        "**Mode** (``mode`` parameter):\n\n- ``allele``: exact.\n")
    pin = "Mode (mode parameter):\n\nallele: exact.\nregion: overlapping.\n\n"

    assert unrendered(pin, shown) == ["region: overlapping."]


def test_unrendered_does_not_care_where_the_backend_wraps() -> None:
    shown = markdown_text(
        "- ``region``: aggregates scores\n  overlapping the span.\n")
    pin = "region: aggregates scores overlapping the\nspan.\n"

    assert unrendered(pin, shown) == []


def test_unrendered_wants_the_backends_text_in_the_backends_order() -> None:
    shown = markdown_text("First.\n\nSecond.\n")
    pin = "Second.\n\nFirst.\n"

    assert unrendered(pin, shown) == ["Second. First."]


# --- the guard, in both directions ----------------------------------------


@pytest.mark.parametrize("key", list(REGISTRY), ids=repr)
def test_registered_pin_is_what_the_backend_renders(
    key: str, spec_pins: list[Pin], rendered: dict[str, str],
) -> None:
    pinned_by = [str(pin) for pin in spec_pins if pin.text == key]

    missing = unrendered(key, rendered[key])

    assert not missing, (
        "the backend no longer says:\n  "
        + "\n  ".join(map(repr, missing))
        + f"\nit renders:\n  {rendered[key]!r}\n"
        "the specs still pinning the old text:\n  "
        + "\n  ".join(pinned_by))


def test_every_sentence_shaped_pin_is_rendered_or_listed(
    spec_pins: list[Pin], rendered: dict[str, str],
) -> None:
    unaccounted = [
        str(pin) for pin in spec_pins
        if is_sentence_shaped(pin.text)
        and pin.text not in NOT_BACKEND
        and not any(collapsed(pin.text) in text for text in rendered.values())
    ]

    assert not unaccounted, (
        "specs pin backend-shaped text no registered producer renders; add "
        "each to REGISTRY with a producer, or to NOT_BACKEND with its "
        "origin:\n  "
        + "\n  ".join(unaccounted))


def test_tables_carry_no_dead_entries(spec_pins: list[Pin]) -> None:
    pinned = {pin.text for pin in spec_pins}
    dead = sorted(key for key in (*REGISTRY, *NOT_BACKEND) if key not in pinned)

    assert not dead, (
        "no spec pins these any more; delete the entries:\n  "
        + "\n  ".join(map(repr, dead)))
