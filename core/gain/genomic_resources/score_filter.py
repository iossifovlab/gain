"""Boolean record filters over a genomic score's own values.

One grammar and one compiler, owned by the score rather than by whichever
annotator happens to want filtering: a filter reads score values off a
record, so it belongs where the score definitions are.  See
``docs/adr/0017-score-filtering-is-a-score-capability.md``.
"""
from __future__ import annotations

import math
import numbers
import textwrap
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from lark import Lark, Token, Tree

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

    %ignore " "
""")

#: Built once: a Lark grammar compile is not cheap, and this one is fixed.
_PARSER = Lark(SCORE_FILTER_GRAMMAR)

_RecordAccessor = Callable[["Record"], Any]


def _is_missing(value: Any) -> bool:
    """Whether a value is absent as far as a comparison is concerned.

    An NA cell parses to ``None``; a float column may also carry a real
    ``nan``.  Both mean "this record does not say", and neither orders
    against anything.

    Tested against ``numbers.Real`` rather than ``float`` so that every
    width of float answers the same way: ``numpy`` registers its scalar
    types there, and only ``float64`` happens to subclass ``float``.  A
    string is not a Real and never reaches ``isnan``, which would raise on
    it -- and strings are the common case in these filters, so this stays a
    type test rather than a try/except in a per-record path.
    """
    return value is None or (
        isinstance(value, numbers.Real) and math.isnan(value))


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
        return operation(left_value, right_value)
    return evaluate


class ScoreFilter:
    """A compiled record predicate, bound to the score that compiled it.

    Opaque on purpose: a caller compiles one through
    :meth:`GenomicScore.compile_filter` and passes it back to a fetch, and
    the tree it was compiled from is nobody else's business.  The source
    expression is kept for error messages and ``repr``.

    It carries the score because its variables are bound to that score's
    definitions -- a column index, a value type, an NA set -- and none of
    those travel with a record.  See :meth:`bound_to`.
    """

    def __init__(
        self, score: GenomicScore, expression: str,
        predicate: Callable[[Record], bool],
    ) -> None:
        self.score = score
        self.expression = expression
        self._predicate = predicate

    def bound_to(self, score: GenomicScore) -> bool:
        """Whether this filter may read ``score``'s records."""
        return self.score is score

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

    Every variable is resolved against the score's ``score_definitions``
    HERE, so an expression naming a score the resource does not define is
    refused now, with the valid names, rather than per record at read time.
    """
    normalized = expression.replace("\n", " ").replace("\t", " ").strip()
    try:
        tree = _PARSER.parse(normalized)
    except Exception as e:
        raise ScoreFilterError(str(e)) from e
    return ScoreFilter(score, normalized, _build_predicate(tree, score))


def require_filter_owner(
    score: GenomicScore, score_filter: ScoreFilter,
) -> None:
    """Refuse a filter compiled against a different score.

    Checked once per fetch rather than per record.  The failure it prevents
    is silent: two resources both defining ``freq`` put it at different
    column indexes, so the foreign filter reads a real value from the wrong
    column and selects records nobody can tell are wrong.
    """
    if not score_filter.bound_to(score):
        raise ScoreFilterError(
            f"filter {score_filter.expression!r} was compiled against "
            f"genomic score <{score_filter.score.resource_id}> and cannot "
            f"read records of <{score.resource_id}>; compile it through "
            f"the score you are reading")


def _build_predicate(
    tree: Tree, score: GenomicScore,
) -> Callable[[Record], bool]:
    """Compile a parse tree into a record predicate."""
    if tree.data == "and_":
        assert isinstance(tree.children[0], Tree)
        assert isinstance(tree.children[1], Tree)
        left_func = _build_predicate(tree.children[0], score)
        right_func = _build_predicate(tree.children[1], score)
        return lambda rec: left_func(rec) and right_func(rec)
    if tree.data == "or":
        assert isinstance(tree.children[0], Tree)
        assert isinstance(tree.children[1], Tree)
        left_func = _build_predicate(tree.children[0], score)
        right_func = _build_predicate(tree.children[1], score)
        return lambda rec: left_func(rec) or right_func(rec)

    left = _build_accessor(tree.children[0], score)
    operator = _operator_name(tree.children[1])
    right = _build_accessor(tree.children[2], score)

    if operator == "equals":
        return _compare(left, right, lambda a, b: bool(a == b))
    if operator == "greater_than":
        return _compare(left, right, lambda a, b: bool(a > b))
    if operator == "less_than":
        return _compare(left, right, lambda a, b: bool(a < b))
    if operator == "in":
        return _compare(left, right, lambda a, b: bool(a in b))

    raise ScoreFilterError(f"Unsupported operator {operator}")


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
