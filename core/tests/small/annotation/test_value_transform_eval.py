"""Tests for the restricted ``value_transform`` evaluator (gain#764)."""
from typing import Any

import pytest
from gain.annotation.value_transform_eval import compile_value_transform


@pytest.mark.parametrize("expr", [
    "__import__('os').system('id') or value",  # arbitrary call
    "open('/etc/passwd')",                       # non-allowlisted call
    "value.__class__",                           # attribute access
    "().__class__.__bases__",                    # attribute + tuple literal
    "'{0.__class__}'.format(value)",             # attribute call
    "value.upper()",                             # attribute call
    "[x for x in range(3)]",                      # comprehension
    "(lambda: 1)()",                             # lambda
    "(value := 3)",                              # walrus
    "f'{value}'",                                # f-string
    "value[0]",                                  # subscript
    "value ** 2",                                # power operator
    "~value",                                    # bitwise invert
    "(value, value)",                            # tuple literal
    "{1: 2}",                                     # dict literal
    "value in (1, 2)",                            # membership + tuple
    "min(open('x'), value)",                      # malicious call argument
    "round(value, ndigits=2)",                    # keyword argument
])
def test_rejects_unsafe_expression(expr: str) -> None:
    with pytest.raises(ValueError, match="disallowed"):
        compile_value_transform(expr)


def test_rejects_invalid_syntax() -> None:
    with pytest.raises(ValueError, match="not valid Python"):
        compile_value_transform("invalid syntax!!!")


@pytest.mark.parametrize("expr", [
    "value * 1000001",
    "value * -1000001",
])
def test_rejects_oversized_numeric_literal(expr: str) -> None:
    with pytest.raises(ValueError, match="numeric literal"):
        compile_value_transform(expr)


def test_rejects_oversized_string_literal() -> None:
    expr = repr("a" * 257)
    with pytest.raises(ValueError, match="string literal"):
        compile_value_transform(expr)


@pytest.mark.parametrize("expr", [
    "value * 1000000",       # numeric literal at the bound is allowed
    repr("a" * 256),         # string literal at the bound is allowed
])
def test_allows_literal_at_bound(expr: str) -> None:
    assert compile_value_transform(expr) is not None


@pytest.mark.parametrize(("expr", "value", "expected"), [
    ("value * 2", 3, 6),
    ("value + 1", 3, 4),
    ("1 - value", 3, -2),
    ("-value", 3, -3),
    ("value if value > 0 else 0", 3, 3),
    ("value if value > 0 else 0", -1, 0),
    ("min(value, 1.0)", 3, 1.0),
    ("max(value, 0)", 3, 3),
    ("round(value, 2)", 3.14159, 3.14),
    ("abs(value)", -3, 3),
    ("int(value)", 3.9, 3),
    ("float(value)", 3, 3.0),
    ("str(value)", 3, "3"),
    ("bool(value)", 0, False),
    ("value + ' gosho'", "foo", "foo gosho"),
    ("len(value)", "foo", 3),
])
def test_evaluates_legitimate_transform(
    expr: str, value: Any, expected: Any,
) -> None:
    transform = compile_value_transform(expr)

    assert transform(value) == expected


@pytest.mark.parametrize("expr", [
    "'ab' * 99 * 99 * 99 * 99",       # chained multiplication
    "99 * 99 * 99 * 99 * 'ab'",       # ... with the sequence on the right
    "str(value) * 100000",            # repeat of a str() result
    "('%99999999d') % value",         # %-format width
    "'%.3f%99999999d' % value",       # width in a later conversion
    "'x' * int('9' * 100)",           # count from int() of a long numeric str
    "('%' + '9' * 8 + 'd') % value",  # dynamically built format string
    "(value or 'ab') * 999999",       # sequence laundered through `or`
    "('ab' if value else 0) * 999999",  # ... through a conditional
    "min(value, 'ab') * 999999",      # ... through min()
    "max('ab', value) * 999999",      # ... through max()
    "(value or '%9999999d') % value",  # format string laundered through `or`
    "value * 1000 * 1000",            # chained repeat of a string-typed score
    "value * 999999 * 999999",        # ... circumventing the per-literal cap
])
def test_rejects_operator_driven_blowup(expr: str) -> None:
    # Every literal stays under the per-literal bounds, but an operator drives
    # the result size past MAX_RESULT_SIZE (gain#767).
    with pytest.raises(ValueError, match="oversized result"):
        compile_value_transform(expr)


@pytest.mark.parametrize(("expr", "value", "expected"), [
    ("value % 2", 5, 1),                 # legitimate numeric modulo
    ("'%.3f' % value", 3.14159, "3.142"),  # bounded %-format
    ("value * 999999", 2, 1999998),      # numeric scaling of a bounded score
    ("str(value) + str(value)", 7, "77"),  # bounded string growth
])
def test_allows_bounded_operator_expression(
    expr: str, value: Any, expected: Any,
) -> None:
    transform = compile_value_transform(expr)

    assert transform(value) == expected


def test_allows_sequence_repeat_at_bound() -> None:
    # A repeat whose product lands exactly on the cap is allowed; one element
    # more is rejected.
    assert compile_value_transform("'a' * 1000000") is not None
    with pytest.raises(ValueError, match="oversized result"):
        compile_value_transform("'aa' * 1000000")


def test_transform_runs_with_empty_builtins() -> None:
    # Defense in depth behind the gate: the namespace the materialised callable
    # runs in exposes no builtins, so __import__/open/exec are unreachable even
    # if a name ever slipped the whitelist.
    transform = compile_value_transform("value")

    assert transform.__globals__["__builtins__"] == {}
