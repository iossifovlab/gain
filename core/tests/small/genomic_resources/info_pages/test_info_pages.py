"""Page-level coverage of every info page a GRR build generates.

Structural invariants over a whole generated repository, rather than
assertions about one page's wording.  gain#991 exists because the previous
attempt at page coverage asserted wording -- with ``str.find()``, which
returns ``-1`` when the string is absent, and ``-1`` is truthy, so five
assertions passed for markup that no template had rendered for months.

The net here is shaped to fail on *structure*: unrendered Jinja, markup
that does not balance, links and images that go nowhere, a section that
says "not computed" over statistics that were computed.  Wrong numbers are
out of scope -- a percentage computed against the wrong denominator is the
business of the focused unit tests, and this suite is the net under those
rather than a replacement for them.

Two habits here are deliberate and worth keeping if this file is extended.

*Assertions carry the offending value.*  Every failure message names the
page and what was wrong with it, because the failure a page suite reports
is read by someone who was not thinking about pages.

*Assertions are bidirectional where they can be.*  The Coverage and Alleles
checks assert both that a computed statistic renders and that a missing one
says so.  A one-directional version would pass just as happily against a
section extractor that had quietly started returning nothing -- which is
the same class of silent vacuity that motivated the issue.
"""
from __future__ import annotations

import functools
import pathlib
from dataclasses import dataclass, field
from html.parser import HTMLParser
from importlib.metadata import entry_points
from urllib.parse import unquote, urlparse

import pytest
import yaml
from gain.genomic_resources import get_resource_implementation_builder
from gain.genomic_resources.repository import GR_CONF_FILE_NAME

from tests.small.genomic_resources.info_pages.conftest import (
    MINI_GRR_SOURCE,
    BuiltGRR,
    check_source_available,
)
from tests.small.genomic_resources.info_pages.supplement import (
    SUPPLEMENT_RESOURCE_IDS,
)

_IMPLEMENTATIONS_GROUP = "gain.genomic_resources.implementations"

#: The marker a section renders in place of a statistic that was never
#: computed.  Asserted in both directions, so a change to this wording
#: fails loudly here rather than quietly disarming the check.
_NOT_COMPUTED = "not computed"

#: HTML elements that never take an end tag, so the balance check must not
#: expect one.
_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

#: URL schemes that point outside the generated repository.  A link with
#: one of these is not ours to resolve.
_EXTERNAL_SCHEMES = frozenset({"http", "https", "mailto", "data", "javascript"})

#: The columns every Coverage table opens with, in order.  The rest of the
#: table varies by resource: a bigWig position score also reports
#: ``Covered %`` because its header knows the chromosome lengths, and a
#: fragment score reports no ``Segments`` at all.
_COVERAGE_LEADING_COLUMNS = ("Chromosome", "Covered positions")

#: Every column heading a Coverage table is known to render.  Asserted as a
#: set equality across the whole repository rather than per table, so the
#: vocabulary is pinned without over-fitting any one resource type: adding
#: or dropping a column anywhere fails here.
_COVERAGE_KNOWN_COLUMNS = frozenset({
    "Chromosome", "Covered positions", "Covered %", "Segments",
})

#: The label on the Coverage table's totals row.
_COVERAGE_TOTALS_LABEL = "all chromosomes"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Event:
    """One step of the page walk: a start tag, an end tag, or text."""

    kind: str
    tag: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    data: str = ""


class _PageParser(HTMLParser):
    """Walks a page, recording its structure and any way it fails to close.

    ``HTMLParser`` is forgiving to the point of never raising on the markup
    a broken template produces, so "the page parses" is only worth asserting
    if something is also checked *about* the parse.  This records tag
    balance, which is what catches a template whose loop or conditional
    closes in the wrong place.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[_Event] = []
        self.unbalanced: list[str] = []
        self._open: list[tuple[str, int]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        self.events.append(
            _Event("start", tag, {k: v or "" for k, v in attrs}))
        if tag not in _VOID_ELEMENTS:
            self._open.append((tag, self.getpos()[0]))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        # Overridden so an XHTML-style `<br />` is not counted as an open
        # element; the base class would route it through handle_starttag.
        self.events.append(
            _Event("start", tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        self.events.append(_Event("end", tag))
        if tag in _VOID_ELEMENTS:
            return
        line = self.getpos()[0]
        if not self._open:
            self.unbalanced.append(f"line {line}: </{tag}> closes nothing")
            return
        open_tag, open_line = self._open[-1]
        if open_tag == tag:
            self._open.pop()
            return
        self.unbalanced.append(
            f"line {line}: </{tag}> while <{open_tag}> opened on line "
            f"{open_line} is still open")
        # Resynchronise so one fault does not cascade into a report of
        # every later tag.
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index][0] == tag:
                del self._open[index:]
                break

    def handle_data(self, data: str) -> None:
        self.events.append(_Event("data", data=data))

    def close(self) -> None:
        super().close()
        self.unbalanced.extend(
            f"<{tag}> opened on line {line} is never closed"
            for tag, line in reversed(self._open)
        )


class Page:
    """One generated page, parsed once and queried many times."""

    def __init__(self, path: pathlib.Path, root: pathlib.Path) -> None:
        self.path = path
        self.root = root
        self.text = path.read_text(encoding="utf-8")
        parser = _PageParser()
        parser.feed(self.text)
        parser.close()
        self.events = parser.events
        self.unbalanced = parser.unbalanced

    def __repr__(self) -> str:
        return f"<Page {self.name}>"

    @property
    def name(self) -> str:
        """The page's path relative to the repository root."""
        return str(self.path.relative_to(self.root))

    @property
    def images(self) -> list[str]:
        return [
            event.attrs["src"]
            for event in self.events
            if event.kind == "start" and event.tag == "img"
            and event.attrs.get("src")
        ]

    @property
    def links(self) -> list[str]:
        return [
            event.attrs["href"]
            for event in self.events
            if event.kind == "start" and event.tag == "a"
            and event.attrs.get("href")
        ]

    def headings(self) -> list[str]:
        """The text of every ``<h2>`` on the page, in document order."""
        return [text for text, _, _ in self._h2_spans()]

    def section_events(self, heading: str) -> list[_Event] | None:
        """The events between ``<h2>heading</h2>`` and the next ``<h2>``.

        ``None`` when the page has no such heading, which is different from
        a section that is present but empty -- the callers distinguish them.
        """
        for text, start, end in self._h2_spans():
            if text == heading:
                return self.events[start:end]
        return None

    def section_text(self, heading: str) -> str | None:
        """The visible text of one ``<h2>`` section."""
        events = self.section_events(heading)
        if events is None:
            return None
        return "".join(
            event.data for event in events if event.kind == "data")

    def _h2_spans(self) -> list[tuple[str, int, int]]:
        """``(heading text, first event after it, event index of next h2)``."""
        starts = [
            index for index, event in enumerate(self.events)
            if event.kind == "start" and event.tag == "h2"
        ]
        spans = []
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) \
                else len(self.events)
            text = ""
            for index in range(start + 1, end):
                event = self.events[index]
                if event.kind == "end" and event.tag == "h2":
                    body_start = index + 1
                    break
                if event.kind == "data":
                    text += event.data
            else:
                body_start = end
            spans.append((text.strip(), body_start, end))
        return spans


@functools.cache
def _parse(path: pathlib.Path, root: pathlib.Path) -> Page:
    """Parse one page, once per session.

    The built repository does not change while the session runs, so caching
    keeps the per-page invariants -- each of which walks every page -- from
    re-parsing the same few megabytes of HTML for every assertion.
    """
    return Page(path, root)


# --------------------------------------------------------------------------
# Discovering what to check
# --------------------------------------------------------------------------


def _fixture_resource_ids() -> list[str]:
    """Every resource id the fixture repository will contain.

    Read from the submodule at *collection* time, so each resource becomes
    its own test item.  If the submodule is missing this raises, and the
    resulting collection error is the hard failure gain#991 asks for -- a
    suite that quietly collected nothing would be the drift it exists to
    prevent.
    """
    check_source_available()
    ids = {
        str(config.parent.relative_to(MINI_GRR_SOURCE))
        for config in MINI_GRR_SOURCE.rglob(GR_CONF_FILE_NAME)
        if ".grr" not in config.parts
    }
    return sorted(ids | set(SUPPLEMENT_RESOURCE_IDS.values()))


def _implementations_by_class() -> dict[str, tuple[str, ...]]:
    """Map each registered implementation to the type spellings naming it.

    Keyed by implementation rather than by entry-point name, and with no
    exemption list anywhere.  Two of the fourteen registered spellings are
    duplicates of another -- ``cnv_collection`` is the deprecated spelling
    of ``fragment_score`` (ADR 0011) and ``gene_set`` the legacy spelling of
    ``gene_set_collection`` -- and grouping by the implementation they load
    drops them without anyone having to maintain a list saying so.  A list
    is what rots: nobody removes entries from one, which is how the
    assertions this suite replaces came to describe a page that no longer
    existed.

    The consequence that matters: registering a *new* implementation with no
    fixture resource fails, in the pull request that registers it.
    """
    by_class: dict[str, list[str]] = {}
    for entry_point in entry_points(group=_IMPLEMENTATIONS_GROUP):
        builder = get_resource_implementation_builder(entry_point.name)
        assert builder is not None, (
            f"the {entry_point.name!r} entry point is registered in the "
            f"{_IMPLEMENTATIONS_GROUP!r} group but does not resolve to an "
            f"implementation")
        by_class.setdefault(builder.__name__, []).append(entry_point.name)
    return {name: tuple(sorted(spellings))
            for name, spellings in sorted(by_class.items())}


def _resource_types(built: BuiltGRR) -> dict[str, str]:
    """The declared ``type:`` of every resource in the built repository."""
    types = {}
    for config in built.path.rglob(GR_CONF_FILE_NAME):
        if ".grr" in config.parts:
            continue
        resource_id = str(config.parent.relative_to(built.path))
        loaded = yaml.safe_load(config.read_text()) or {}
        types[resource_id] = loaded.get("type", "basic")
    return types


def _repository_pages(built: BuiltGRR) -> list[pathlib.Path]:
    """The pages that belong to the repository rather than to a resource."""
    return [
        path for path in (built.path / "index.html", built.path / "about.html")
        if path.exists()
    ]


def _pages_of(built: BuiltGRR, resource_id: str) -> list[Page]:
    """Every generated page of one resource: its own, and its statistics."""
    resource_dir = built.path / resource_id
    return [
        _parse(path, built.path)
        for path in (
            resource_dir / "index.html",
            resource_dir / "statistics" / "index.html",
        )
        if path.exists()
    ]


_RESOURCE_IDS = _fixture_resource_ids()
_IMPLEMENTATIONS = _implementations_by_class()


# --------------------------------------------------------------------------
# The suite really is looking at fresh output
# --------------------------------------------------------------------------


def test_the_fixture_repository_generates_a_page_for_every_resource(
    built_grr: BuiltGRR,
) -> None:
    missing = [
        resource_id for resource_id in _RESOURCE_IDS
        if not (built_grr.path / resource_id / "index.html").exists()
    ]
    assert not missing, (
        f"repo-info generated no page for {len(missing)} resource(s): "
        f"{missing}")


@pytest.mark.parametrize("resource_id", _RESOURCE_IDS)
def test_resource_pages_are_freshly_generated(
    built_grr: BuiltGRR, resource_id: str,
) -> None:
    """A page older than the build was served stale, not regenerated.

    mini-GRR commits its generated pages, and the committed ones predate
    ADR 0020 -- they carry no Coverage section at all.  Without this, a page
    that ``repo-info`` silently skipped would be validated as though the
    build had produced it, and every other assertion in this file would be
    describing checked-in output.
    """
    stale = [
        page.name for page in _pages_of(built_grr, resource_id)
        if page.path.stat().st_mtime < built_grr.build_started
    ]
    assert not stale, (
        f"{stale} predate this build, so repo-info did not regenerate them; "
        f"the assertions in this suite would be describing the pages "
        f"mini-GRR has committed rather than freshly generated output")


# --------------------------------------------------------------------------
# Every registered implementation is exercised
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("implementation", "type_spellings"),
    sorted(_IMPLEMENTATIONS.items()),
)
def test_every_registered_implementation_has_a_fixture_resource(
    built_grr: BuiltGRR, implementation: str, type_spellings: tuple[str, ...],
) -> None:
    present = set(_resource_types(built_grr).values())
    assert present & set(type_spellings), (
        f"{implementation} is registered in the {_IMPLEMENTATIONS_GROUP!r} "
        f"entry points as {list(type_spellings)}, but no resource in the "
        f"fixture repository declares any of those types, so none of its "
        f"pages is ever generated or checked.\n"
        f"Add a fixture resource: either upstream in mini-GRR if the type "
        f"belongs in the onboarding repository, or in "
        f"tests/small/genomic_resources/info_pages/supplement.py if it does "
        f"not.")


# --------------------------------------------------------------------------
# Invariants over every generated page
# --------------------------------------------------------------------------


@pytest.mark.parametrize("resource_id", _RESOURCE_IDS)
def test_resource_pages_have_no_unrendered_jinja(
    built_grr: BuiltGRR, resource_id: str,
) -> None:
    _assert_no_unrendered_jinja(_pages_of(built_grr, resource_id))


def test_repository_pages_have_no_unrendered_jinja(
    built_grr: BuiltGRR,
) -> None:
    _assert_no_unrendered_jinja(
        [_parse(path, built_grr.path)
         for path in _repository_pages(built_grr)])


def _assert_no_unrendered_jinja(pages: list[Page]) -> None:
    """A surviving ``{{`` or ``{%`` means a template did not render.

    Broken inheritance and a typo'd block name both reach the page this way,
    and both render silently -- the output simply contains the template
    source, and nothing in the build reports it.
    """
    offenders = [
        (page.name, marker, page.text.count(marker))
        for page in pages
        for marker in ("{{", "{%")
        if marker in page.text
    ]
    assert not offenders, (
        f"unrendered Jinja survived into the generated output: {offenders}")


@pytest.mark.parametrize("resource_id", _RESOURCE_IDS)
def test_resource_pages_are_well_formed(
    built_grr: BuiltGRR, resource_id: str,
) -> None:
    _assert_well_formed(_pages_of(built_grr, resource_id))


def test_repository_pages_are_well_formed(built_grr: BuiltGRR) -> None:
    _assert_well_formed(
        [_parse(path, built_grr.path)
         for path in _repository_pages(built_grr)])


def _assert_well_formed(pages: list[Page]) -> None:
    faults = {
        page.name: page.unbalanced for page in pages if page.unbalanced
    }
    assert not faults, (
        f"generated pages do not close their tags: {faults}")


@pytest.mark.parametrize("resource_id", _RESOURCE_IDS)
def test_resource_page_images_resolve(
    built_grr: BuiltGRR, resource_id: str,
) -> None:
    _assert_targets_resolve(_pages_of(built_grr, resource_id), "images")


def test_repository_page_images_resolve(built_grr: BuiltGRR) -> None:
    _assert_targets_resolve(
        [_parse(path, built_grr.path)
         for path in _repository_pages(built_grr)], "images")


@pytest.mark.parametrize("resource_id", _RESOURCE_IDS)
def test_resource_page_links_resolve(
    built_grr: BuiltGRR, resource_id: str,
) -> None:
    _assert_targets_resolve(_pages_of(built_grr, resource_id), "links")


def test_repository_page_links_resolve(built_grr: BuiltGRR) -> None:
    _assert_targets_resolve(
        [_parse(path, built_grr.path)
         for path in _repository_pages(built_grr)], "links")


def _assert_targets_resolve(pages: list[Page], attribute: str) -> None:
    """Every internal ``<img src>`` / ``<a href>`` points at a real file.

    Only the parsed attributes are considered.  The pages build their file
    listings in JavaScript, and ``html.parser`` hands ``<script>`` bodies
    back as text rather than markup, so a URL assembled inside one is never
    mistaken for a link this can resolve.
    """
    broken = []
    for page in pages:
        for target in getattr(page, attribute):
            resolved = _resolve(page, target)
            if resolved is not None and not resolved.exists():
                broken.append((page.name, target))
    assert not broken, (
        f"generated pages point at files that do not exist: {broken}")


def _resolve(page: Page, target: str) -> pathlib.Path | None:
    """Where ``target`` lands on disk, or ``None`` if it is not ours."""
    parsed = urlparse(target)
    if parsed.scheme in _EXTERNAL_SCHEMES or parsed.netloc:
        return None
    if not parsed.path:
        # A bare "#anchor" addresses this page and needs no file.
        return None
    return (page.path.parent / unquote(parsed.path)).resolve()


# --------------------------------------------------------------------------
# Computed statistics reach the page that reports them
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heading", "statistic"),
    [("Coverage", "coverage.json"), ("Alleles", "alleles.json")],
)
def test_a_section_reports_its_statistic_exactly_when_it_was_computed(
    built_grr: BuiltGRR, heading: str, statistic: str,
) -> None:
    """The single assertion that covers "computed but never reached the page".

    Asserted in both directions on purpose.  The "says not computed when it
    was not" half is not padding: it is the positive control that proves the
    section extractor can still see the phrase at all.  Without it, an
    extractor that had started returning nothing -- a renamed heading, a
    changed nesting -- would satisfy the other half for every resource on
    the page, and the check would be as green and as meaningless as the
    ``find()`` assertions this suite replaces.

    Scoped to the section, never to the whole page.  Every score resource
    renders *both* headings, and a position score legitimately says "not
    computed" under Alleles, so a whole-page substring match reports a
    failure for every one of them.
    """
    computed_but_absent = []
    uncomputed_but_claimed = []
    saw_section = 0

    for resource_id in _RESOURCE_IDS:
        page_path = built_grr.path / resource_id / "index.html"
        if not page_path.exists():
            continue
        page = _parse(page_path, built_grr.path)
        section = page.section_text(heading)
        if section is None:
            continue
        saw_section += 1
        was_computed = (
            built_grr.path / resource_id / "statistics" / statistic).exists()
        says_not_computed = _NOT_COMPUTED in section
        if was_computed and says_not_computed:
            computed_but_absent.append(resource_id)
        if not was_computed and not says_not_computed:
            uncomputed_but_claimed.append(resource_id)

    assert saw_section, (
        f"no page in the fixture repository renders a {heading!r} section, "
        f"so this check is vacuous")
    assert not computed_but_absent, (
        f"statistics/{statistic} exists for {computed_but_absent}, but their "
        f"{heading!r} section still says {_NOT_COMPUTED!r} -- the statistic "
        f"was computed and never reached the page")
    assert not uncomputed_but_claimed, (
        f"{uncomputed_but_claimed} have no statistics/{statistic}, but their "
        f"{heading!r} section does not say {_NOT_COMPUTED!r}; if the wording "
        f"changed, the other half of this assertion has silently stopped "
        f"checking anything")


# --------------------------------------------------------------------------
# The Coverage table's own structure
# --------------------------------------------------------------------------


def test_coverage_tables_render_the_expected_columns(
    built_grr: BuiltGRR,
) -> None:
    tables = _coverage_tables(built_grr)
    assert tables, "no resource rendered a populated Coverage table"

    columns = {
        name: _column_headings(events) for name, events in tables
    }
    leading = list(_COVERAGE_LEADING_COLUMNS)
    wrong = {
        name: found for name, found in columns.items()
        if found[:len(leading)] != leading
    }
    assert not wrong, (
        f"Coverage tables do not open with {leading}: {wrong}")

    duplicated = {
        name: found for name, found in columns.items()
        if len(set(found)) != len(found)
    }
    assert not duplicated, (
        f"Coverage tables repeat a column heading: {duplicated}")

    observed = {heading for found in columns.values() for heading in found}
    assert observed == _COVERAGE_KNOWN_COLUMNS, (
        f"the Coverage table's column vocabulary has changed: "
        f"{sorted(observed)} across the fixture repository, expected "
        f"{sorted(_COVERAGE_KNOWN_COLUMNS)}.  A column was added, renamed or "
        f"dropped; update _COVERAGE_KNOWN_COLUMNS once the change is "
        f"intended.")


def test_coverage_tables_are_sortable(built_grr: BuiltGRR) -> None:
    """The ``data-sort`` / ``data-sort-value`` / ``tfoot`` contract (#984).

    The client-side sort reads ``data-sort`` off the header to learn the
    column's type and ``data-sort-value`` off each body cell to sort on
    something other than the rendered text -- ``chr10`` has to sort after
    ``chr9``.  The totals row lives in ``tfoot`` so sorting leaves it at the
    bottom instead of shuffling it in among the chromosomes.
    """
    tables = _coverage_tables(built_grr)
    assert tables, "no resource rendered a populated Coverage table"

    faults: dict[str, list[str]] = {}
    for name, events in tables:
        problems = []
        headers = [
            event for event in events
            if event.kind == "start" and event.tag == "th"
        ]
        if any("data-sort" not in event.attrs for event in headers):
            problems.append(
                "a column header carries no data-sort, so the client-side "
                "sort cannot tell the column's type")
        body = _section_slice(events, "tbody")
        cells = [
            event for event in body
            if event.kind == "start" and event.tag == "td"
        ]
        if not cells:
            problems.append("the table body renders no cells")
        if any("data-sort-value" not in event.attrs for event in cells):
            problems.append(
                "a body cell carries no data-sort-value, so it sorts on its "
                "rendered text and chr10 sorts before chr9")
        foot = _section_slice(events, "tfoot")
        if not foot:
            problems.append("the totals row is not in a tfoot")
        elif _COVERAGE_TOTALS_LABEL not in "".join(
                event.data for event in foot if event.kind == "data"):
            problems.append(
                f"the tfoot does not carry the {_COVERAGE_TOTALS_LABEL!r} "
                f"totals row")
        if problems:
            faults[name] = problems
    assert not faults, f"Coverage tables break the sortable contract: {faults}"


def _coverage_tables(built: BuiltGRR) -> list[tuple[str, list[_Event]]]:
    """The Coverage section of every resource that has a computed one."""
    tables = []
    for resource_id in _RESOURCE_IDS:
        if not (built.path / resource_id / "statistics"
                / "coverage.json").exists():
            continue
        page_path = built.path / resource_id / "index.html"
        if not page_path.exists():
            continue
        events = _parse(page_path, built.path).section_events("Coverage")
        if events:
            tables.append((resource_id, events))
    return tables


def _column_headings(events: list[_Event]) -> list[str]:
    """The text of each ``<th>`` in an event slice, in order."""
    headings = []
    collecting = False
    text = ""
    for event in events:
        if event.kind == "start" and event.tag == "th":
            collecting, text = True, ""
        elif event.kind == "end" and event.tag == "th" and collecting:
            headings.append(text.strip())
            collecting = False
        elif event.kind == "data" and collecting:
            text += event.data
    return headings


def _section_slice(events: list[_Event], tag: str) -> list[_Event]:
    """The events inside the first ``<tag>`` ... ``</tag>`` of a slice."""
    for index, event in enumerate(events):
        if event.kind == "start" and event.tag == tag:
            for end in range(index + 1, len(events)):
                if events[end].kind == "end" and events[end].tag == tag:
                    return events[index + 1:end]
            return events[index + 1:]
    return []
