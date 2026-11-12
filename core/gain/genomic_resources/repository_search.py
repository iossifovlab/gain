"""Helpers of the resource search over a repository.

They build the SQL statement over the ``contents`` FTS index, check a
search term before the statement runs, and consume the generator that a
search returns.
"""
from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any

import apsw

from gain.genomic_resources.resource_query import LabelClause, ResourceQuery

# The name the resource-query push-down registers on a metadata
# connection. The connection is deserialized fresh for every search, so it
# never has to be unique across concurrent searches.
_MATCH_ID_FUNCTION = "gain_query_match_id"


class SearchTermError(ValueError):
    """A search term that SQLite's FTS5 could not parse as a match expression.

    The term is bound, never interpolated, so this is not a failure to
    contain it -- it is that FTS5 reads a bound term as an *expression*,
    with a grammar of its own: quotes, ``AND``/``OR``/``NOT``, ``NEAR``,
    ``column : value``. A term that does not parse is the caller's
    mistake, and ``apsw.SQLError`` reports it as neither -- it names no
    term, no argument, and reads like the database broke (gain#632).

    A ``ValueError``, like ``ResourceQueryParseError``, so the two bad
    arguments this search can be given are handled the same way by the
    endpoint and the CLI that take them.

    The column filter is why the term is not simply quoted into a literal
    before it reaches ``MATCH``: label keys are index columns, and
    ``ref_genome : hg38`` is a supported search. Quoting would make that a
    search for the text.
    """

    def __init__(self, search_term: str, cause: Exception) -> None:
        # The cause is not kept: `raise ... from err` records it as
        # `__cause__` already, and the message carries what it said.
        self.search_term = search_term
        super().__init__(
            f"cannot search for <{search_term}>: it is not a valid "
            f"full-text search expression ({cause})",
        )


class SearchIndexUnavailableError(ValueError):
    """A repository that cannot apply a search filter for want of an index.

    Raised when the repository publishes no ``.CONTENTS.sqlite3.gz`` at all,
    and when the one it publishes carries no ``contents`` table because no
    resource could be indexed into it. Both are repository *health*: the
    filter is unobjectionable and a ``grr_manage repo-repair`` would let it
    be applied.

    Typed rather than left as a bare ``ValueError`` because a group
    repository absorbs this to skip the child and carry on (ADR 0012), and
    the layers it absorbs it through raise ``ValueError`` of their own that
    must keep propagating -- the cache layer resolving a resource it cannot
    place, for one.

    A ``ValueError`` still, so a caller that only ever distinguished bad
    arguments from working ones is unaffected.
    """

    def __init__(self, repo_id: str, reason: str) -> None:
        self.repo_id = repo_id
        self.reason = reason
        super().__init__(
            f"repository <{repo_id}> cannot be searched: {reason}. "
            f"Build a search index with `grr_manage repo-repair`, or "
            f"select by type, id and labels with a type filter and a "
            f"resource query, which need none",
        )


def _sql_matcher(match: Callable[[str], bool]) -> Callable[..., bool]:
    """Adapt a matcher to the values an index column hands back.

    A column with no value at all reads as ``""`` rather than reaching the
    matcher as ``None``: absence and emptiness are one case for the query
    language, so an id the index left null is an empty id, not a crash.

    Typed with an open parameter list because that is the shape apsw
    accepts for a scalar function; each one registered here is declared
    with exactly one argument.
    """
    def matcher(value: Any) -> bool:
        return match("" if value is None else str(value))
    return matcher


def _resource_query_condition(
    conn: apsw.Connection, query: ResourceQuery,
) -> tuple[str, list[LabelClause]]:
    """Express ``query`` as a SQL condition over the ``contents`` table.

    Only the id glob becomes a condition, and it is not restated in SQL:
    it is registered as a scalar function backed by the very matcher the
    Python path calls, so what ``*`` means is defined once no matter which
    path evaluates the query -- rewriting it as ``GLOB`` would be a second
    definition, free to drift from the first.

    Every label clause is handed back instead. A label column records the
    value the resource carried when the index was built, and nothing forces
    a published index to be current: answering a clause out of the column
    reads an edited label as it was then, which loses the resources that
    now match and returns the ones that no longer do (gain#646). No
    post-filter can repair the first of those, because it is a row the
    statement never yields. The caller re-asks each clause of the
    resources, whose labels the indexed path already materialises in full.

    Returns the condition, and the clauses the caller must apply itself;
    leaving those out of both places would widen the search.
    """
    conn.createscalarfunction(
        _MATCH_ID_FUNCTION, _sql_matcher(query.match_id), 1,
        deterministic=True)

    # A clause that accepts an absent label is dropped rather than
    # deferred: it is a tautology, because the grammar requires at least
    # one character in a value, so ``in`` can never accept ``""`` and the
    # only ``=`` values ``fnmatch`` accepts ``""`` for are globs of ``*``
    # alone -- which accept every string. That holds for every resource
    # however stale the index is, so it is an optimisation rather than a
    # decision the index takes part in.
    deferred = [
        clause for clause in query.label_clauses
        if not clause.matches_an_absent_label()
    ]

    return f"{_MATCH_ID_FUNCTION}(id)", deferred


def _search_index_statement(
    conn: apsw.Connection,
    search_term: str,
    accepted: tuple[str, ...],
    parsed_query: ResourceQuery | None,
) -> tuple[str, list[Any], list[LabelClause]]:
    """Build the search statement over the ``contents`` index.

    Return the statement, its parameters and the label clauses that the
    caller checks against the live labels of each resource.
    """
    query = "SELECT full_id FROM contents "
    # A term is what routes a search here, so it is always one of
    # the conditions -- there is no reaching this with none.
    conditions = ["contents MATCH ?"]
    params: list[Any] = [search_term]
    if accepted:
        # The type predicate has to be applied HERE rather than in
        # a caller: it is applied in SQL, so no Python-side
        # filtering downstream can recover a row this query never
        # returned.
        placeholders = ", ".join("?" * len(accepted))
        conditions.append(f"type IN ({placeholders})")
        params.extend(accepted)
    deferred: list[LabelClause] = []
    if parsed_query is not None:
        id_condition, deferred = _resource_query_condition(
            conn, parsed_query)
        conditions.append(id_condition)
    query += " WHERE " + " AND ".join(conditions)
    return query, params, deferred


def _reject_unparsable_search_term(
    conn: apsw.Connection, search_term: str,
) -> None:
    """Raise ``SearchTermError`` if FTS5 cannot read the term.

    Checked against a throwaway index carrying the same column names,
    rather than by translating whatever the repository's own index
    raises. The two are not the same test, and the difference is the
    whole point:

    * A corrupt, truncated or unreadable index fails the *same*
      ``MATCH`` statement a bad term fails -- ``vtable constructor
      failed``, ``no such tokenizer``, a ``contents`` that is not an FTS
      table. Blaming those on the term would answer 400, with "your
      search is malformed", to every caller of a repository that needs
      repairing, and would hide the breakage from anything watching for
      500s. None of them is caught here: the ones that survive being
      read far enough to name the columns go on to fail the real
      statement, and the ones that do not -- a damaged index cannot even
      be described -- come straight out of the ``pragma`` below. Either
      way the repository's failure is what the caller is told about.
    * A term is checked even when the planner never asks FTS5 to read
      it. A resource query that can match nothing folds the whole
      ``WHERE`` away, and a term validated only by being executed would
      pass unexamined -- the same request answering 400 or 200 depending
      on an unrelated parameter.

    The column names come from the index because a column filter
    (``ref_genome : hg38``) is a term that is valid only against an index
    that has the column. Every column is mirrored, including any name
    gain's own index build would have refused: this stands in for the
    index, so a name it does not carry is a column filter wrongly called
    malformed -- a working search turned into a 400, which is the one
    direction of this check that costs a caller something. The names are
    quoted rather than vetted, which is what makes that safe: a published
    index is an artefact of the repository and no more trusted than its
    manifest, and a quoted identifier carries nothing out of the
    statement it is spliced into.
    """
    columns = []
    for row in conn.execute("pragma table_info('contents')"):
        # Doubling is what keeps a name that contains a quote inside its
        # own quotes rather than ending them.
        quoted = row[1].replace('"', '""')
        columns.append(f'"{quoted}"')
    if not columns:
        # Nothing to build a probe out of. An index shaped like this
        # cannot answer a search at all; let the real statement say so.
        return

    probe = apsw.Connection(":memory:")
    try:
        try:
            probe.execute(
                "CREATE VIRTUAL TABLE contents "
                f"USING fts5({', '.join(columns)})",
            )
        except apsw.SQLError:
            # This index cannot be stood in for. Checking the term
            # against a different set of columns would refuse searches
            # the repository can answer, so it is not checked at all --
            # the real statement is left to accept or reject it.
            return
        try:
            # Stepped, not merely prepared: FTS5 parses the term when the
            # query runs. The table is empty, so this reads no rows -- it
            # only asks FTS5 whether the term is an expression.
            list(probe.execute(
                "SELECT 1 FROM contents WHERE contents MATCH ?",
                (search_term,)))
        except apsw.SQLError as err:
            raise SearchTermError(search_term, err) from err
    finally:
        probe.close()


def _map_relaying_skips[
    HitT, MappedT, SkipsT: list[tuple[str, str]] | None,
](
    hits: Generator[HitT, None, SkipsT],
    transform: Callable[[HitT], MappedT],
) -> Generator[MappedT, None, SkipsT]:
    """Yield ``transform(hit)`` per hit, relaying the skips return value.

    ``yield from`` relays a generator's return value natively but cannot
    transform the hits on the way; a ``for`` loop transforms and relays
    nothing. Every search wrapper that maps its hits threads through
    here, so the skips promise (gain#686) is kept in one place.
    """
    while True:
        try:
            hit = next(hits)
        except StopIteration as stop:
            # Deliberately PEP 380: the return value is how a search
            # reports the children a group skipped while answering
            # (gain#686), so B901's accidental shape this is not.
            skips: SkipsT = stop.value
            return skips  # ruff: ignore[return-in-generator]
        yield transform(hit)


def drain_search[HitT](
    hits: Generator[HitT, None, list[tuple[str, str]] | None],
) -> tuple[list[HitT], list[tuple[str, str]]]:
    """Exhaust a search, answering its rows and its skips.

    The consumption idiom for a caller that presents totals: a ``for``
    loop silently discards the skips a group reports on its generator's
    return value (gain#686). ``None`` and ``[]`` both arrive as ``[]``,
    so the dual spelling ends here.
    """
    rows: list[HitT] = []
    while True:
        try:
            rows.append(next(hits))
        except StopIteration as stop:
            # `list(...)` rather than an Optional-annotated local:
            # astroid does not narrow `x or []` past the annotation and
            # would call every consumer's unpacking non-iterable (E1133).
            return rows, list(stop.value or [])
