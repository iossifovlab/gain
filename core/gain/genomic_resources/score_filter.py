"""Boolean record filters over a genomic score's own values.

One grammar and one compiler, owned by the score rather than by whichever
annotator happens to want filtering: a filter reads score values off a
record, so it belongs where the score definitions are.  See
``docs/adr/0017-score-filtering-is-a-score-capability.md``.
"""
from __future__ import annotations

import functools
import operator
import textwrap
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from lark import Lark, LarkError, Token, Tree

from gain.utils.log_safety import escape_unsafe_characters

if TYPE_CHECKING:
    from gain.genomic_resources.genomic_position_table.record import Record
    from gain.genomic_resources.genomic_scores import GenomicScore

#: The filter language.  Deliberately frozen at these operators; adding one
#: is a change to what every score's configuration means, not a tweak.
SCORE_FILTER_GRAMMAR = textwrap.dedent("""
    ?start: filter | and_ | or

    and_: filter "and" filter

    or: filter "or" filter

    ?filter: subject operator subject | or | and_

    ?subject: variable | value

    value: "\\"" word "\\"" | number

    variable: word

    operator: equals | greater_than | less_than | in

    equals: "=="

    greater_than: ">"

    less_than: "<"

    in: "in"

    word: /[0-9]*[a-zA-Z_!@#$%^&*()_+][a-zA-Z0-9!@#$%^&*()_+]*/

    number: /-?(?:[0-9]+\\.?[0-9]*|\\.[0-9]+)/

    %ignore /\\s+/
""")


@functools.cache
def _get_parser() -> Lark:
    """Build the grammar once, on first use.

    Cached rather than built at import, for the reason
    :func:`resource_query._get_parser` gives: this module is imported by
    ``genomic_scores``, so building here would charge every gain entry
    point ~13ms of Earley grammar construction -- including the many that
    never compile a filter.
    """
    return Lark(SCORE_FILTER_GRAMMAR)


_RecordAccessor = Callable[["Record"], Any]


def _is_missing(value: Any) -> bool:
    """Whether a value is absent as far as a comparison is concerned.

    An NA cell parses to ``None``; a float column may also carry a real
    ``nan``.  Both mean "this record does not say", and neither orders
    against anything.

    ``value != value`` is true of nan and of nothing else, so it asks the
    question without asking the TYPE first.  That matters here: this runs
    twice per comparison per record, and an `isinstance` against a float
    width -- or worse, against the ``numbers.Real`` ABC, which costs ~300ns
    -- would dominate a whole-contig scan.  It is also the more general
    test, covering every float width and any other type whose nan is its
    own kind (``Decimal('NaN')``), where naming concrete types cannot.
    """
    # The self-comparison is the point, not a slip: see above.
    # pylint: disable-next=comparison-with-itself
    return value is None or bool(value != value)


def _compare(
    left: _RecordAccessor, right: _RecordAccessor,
    operation: Callable[[Any, Any], bool],
) -> Callable[[Record], bool]:
    """Apply ``operation``, answering False if either operand is missing.

    The missing case is decided here, once, rather than per operator: every
    operator in this language relates two values, and none of them can say
    anything about a value that is not there.  Answering False (rather than
    raising, as ``None > 0.1`` used to) keeps a filter total -- the record
    is simply not selected by this clause, and can still be selected by the
    other arm of an ``or``.
    """
    def evaluate(record: Record) -> bool:
        left_value = left(record)
        right_value = right(record)
        if _is_missing(left_value) or _is_missing(right_value):
            return False
        return bool(operation(left_value, right_value))
    return evaluate


#: The operators the grammar admits, and what each one asks of two values.
#: ``operator.contains`` is not ``in``: its arguments are the other way
#: round.
_OPERATIONS: dict[str, Callable[[Any, Any], bool]] = {
    "equals": operator.eq,
    "greater_than": operator.gt,
    "less_than": operator.lt,
    "in": lambda left, right: left in right,
}


class ScoreFilter:
    """A compiled record predicate, bound to the score that compiled it.

    Opaque on purpose: a caller compiles one through
    :meth:`GenomicScore.compile_filter` and passes it back to a fetch, and
    the tree it was compiled from is nobody else's business.  The source
    expression is kept for error messages and ``repr``.

    It carries the score because its variables are bound to that score's
    definitions -- a column index, a value type, an NA set -- and none of
    those travel with a record.  See :meth:`require_owner`.
    """

    def __init__(
        self, score: GenomicScore, expression: str,
        predicate: Callable[[Record], bool],
    ) -> None:
        self.score = score
        self.expression = expression
        self._predicate = predicate

    def require_owner(self, score: GenomicScore) -> None:
        """Refuse to read the records of a score that did not compile this.

        Checked once per fetch, not per record.  The failure it prevents is
        silent: two resources both defining ``freq`` put it at different
        column indexes, so a foreign filter reads a real value from the
        wrong column and selects records nobody can tell are wrong.
        """
        if self.score is score:
            return
        raise ScoreFilterError(
            f"filter {self.expression!r} was compiled against genomic score "
            f"<{self.score.resource_id}> and cannot read records of "
            f"<{score.resource_id}>; compile it through the score you are "
            f"reading")

    def __call__(self, record: Record) -> bool:
        """Whether this record passes the filter."""
        return self._predicate(record)

    def __repr__(self) -> str:
        return (f"ScoreFilter({self.expression!r}, "
                f"score=<{self.score.resource_id}>)")


class ScoreFilterError(ValueError):
    """A filter expression that does not compile against a score."""


def compile_score_filter(
    score: GenomicScore, expression: str,
) -> ScoreFilter:
    """Compile ``expression`` into a predicate over ``score``'s records.

    Every variable is checked against the score's ``score_definitions``
    HERE, so an expression naming a score the resource does not define is
    refused now, with the valid names, rather than per record at read time.
    """
    try:
        tree = _get_parser().parse(expression)
    except LarkError as e:
        # Both halves are escaped: the expression is config text a user
        # wrote, and Lark quotes a context line of that same text back, so
        # either can smuggle a line break into a logged message
        # (iossifovlab/gain#655), as ``ResourceQueryParseError`` does.
        raise ScoreFilterError(
            f"cannot parse '{escape_unsafe_characters(expression)}': "
            f"{escape_unsafe_characters(str(e))}") from e
    return ScoreFilter(score, expression, _build_predicate(tree, score))


def _build_predicate(
    tree: Tree, score: GenomicScore,
) -> Callable[[Record], bool]:
    """Compile a parse tree into a record predicate."""
    if tree.data in ("and_", "or"):
        # One branch for both: the two differ only in the connective, and
        # keeping them apart is how the copies this module replaces drifted
        # -- the fragment one had lost the operand checks from its `or`.
        assert isinstance(tree.children[0], Tree)
        assert isinstance(tree.children[1], Tree)
        left_func = _build_predicate(tree.children[0], score)
        right_func = _build_predicate(tree.children[1], score)
        if tree.data == "and_":
            return lambda rec: left_func(rec) and right_func(rec)
        return lambda rec: left_func(rec) or right_func(rec)

    left = _build_accessor(tree.children[0], score)
    right = _build_accessor(tree.children[2], score)
    # Indexed, not `.get`-with-a-fallback: the grammar's `operator:` rule
    # admits exactly these four, so a miss here means the grammar and this
    # table have been edited apart -- a programming error, and a KeyError
    # naming the operator says so better than a filter error would.
    return _compare(left, right, _OPERATIONS[_operator_name(tree.children[1])])


def _operator_name(node: Any) -> str:
    assert isinstance(node, Tree)
    assert isinstance(node.children[0], Tree)
    assert isinstance(node.children[0].data, Token)
    return str(node.children[0].data.value)


def _build_accessor(node: Any, score: GenomicScore) -> _RecordAccessor:
    """Compile one operand into a per-record read.

    A variable becomes a read through the score that owns the definitions;
    a literal becomes a constant, parsed once here rather than per record.
    """
    assert isinstance(node, Tree)
    assert isinstance(node.data, Token)
    child = node.children[0]
    assert isinstance(child, Tree)
    assert isinstance(child.data, Token)
    assert isinstance(child.children[0], Token)
    raw_value = child.children[0].value

    if node.data.value == "variable":
        assert child.data.value == "word"
        score_id = _require_score_id(score, raw_value)

        def read_score(record: Record) -> Any:
            return score.get_score_value_from_record(record, score_id)
        return read_score

    literal: Any = raw_value
    if child.data.value == "number":
        literal = float(raw_value)

    def read_literal(_record: Record) -> Any:
        return literal
    return read_literal


def _require_score_id(score: GenomicScore, name: str) -> str:
    """Refuse a variable naming no score of this resource."""
    if name not in score.score_definitions:
        raise ScoreFilterError(
            f"filter names {name!r}, which genomic score "
            f"<{score.resource_id}> does not define; it has "
            f"{sorted(score.score_definitions)}")
    return name
