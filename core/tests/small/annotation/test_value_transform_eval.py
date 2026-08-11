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


def test_eval_globals_carry_empty_builtins() -> None:
    # Defense in depth behind the gate: the namespace the compiled expression
    # runs in exposes no builtins, so __import__/open/exec are unreachable even
    # if a name ever slipped the whitelist.
    from gain.annotation.value_transform_eval import _EVAL_GLOBALS

    assert _EVAL_GLOBALS["__builtins__"] == {}
