"""Restricted evaluator for pipeline ``value_transform`` expressions.

A ``value_transform`` is an expression supplied through pipeline configuration,
and that configuration can arrive verbatim in an anonymous HTTP request body.
It must therefore never reach an unrestricted ``eval``.

``compile_value_transform`` validates the expression against a small whitelist
of AST nodes, operators, names and calls, rejects everything else, and returns
a callable that evaluates the expression over a single bound name, ``value``,
with no access to builtins.  Static bounds on numeric and string literals cap
the cheapest resource-exhaustion inputs, and a size-propagation pass
(``_check_result_size``) rejects operator-driven blow-ups whose individual
literals stay under those bounds -- chained sequence repetition
(``'ab' * 99 * 99 * 99 * 99``) and ``%``-format width (``'%99999999d' % value``)
(gain#767).
"""
import ast
import math
import re
from collections.abc import Callable
from typing import Any, NamedTuple

# Builtins an expression may call; reachable by bare name only.
SAFE_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "len": len,
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
}

# The single free variable an expression may reference.
VALUE_NAME = "value"

# Static resource-exhaustion bounds on individual literals.
MAX_NUMBER = 1_000_000
MAX_STRING_LENGTH = 256

# Upper bound on the attacker-grounded length (from string literals / ``str``)
# any sub-expression may produce. ``_check_result_size`` propagates a
# conservative bound through the tree and rejects the expression if any node
# exceeds this, closing operator-driven blow-ups the per-literal bounds miss.
MAX_RESULT_SIZE = 1_000_000

# Upper bound on a result's total length *including* ``value``'s own text. This
# is the single-scale allowance -- ``value`` (<= MAX_STRING_LENGTH chars)
# repeated by one count (<= MAX_NUMBER) -- so ``value * 1000000`` sits at the
# boundary and passes, while chaining a second factor (``value * 1000 * 1000``)
# exceeds it and is rejected.
MAX_STR_LEN = MAX_STRING_LENGTH * MAX_NUMBER

# AST nodes permitted anywhere without a dedicated visitor below. ``Pow`` is
# deliberately absent, so ``**`` is rejected.
_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.USub,
    ast.UAdd,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)

_EVAL_GLOBALS: dict[str, Any] = {"__builtins__": {}, **SAFE_FUNCTIONS}


class _TransformValidator(ast.NodeVisitor):
    """Reject any construct outside the ``value_transform`` whitelist."""

    # ``ast.NodeVisitor`` dispatches to ``visit_<NodeClass>`` methods, whose
    # names cannot be snake_case.
    # pylint: disable=invalid-name

    def __init__(self, expr: str) -> None:
        self._expr = expr

    def _reject(self, what: str) -> None:
        raise ValueError(
            f"value_transform expression uses a disallowed {what}: "
            f"|{self._expr}|",
        )

    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, _ALLOWED_NODES):
            self._reject(f"construct ({type(node).__name__})")
        super().generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Allow only ``value`` and whitelisted function names."""
        if node.id != VALUE_NAME and node.id not in SAFE_FUNCTIONS:
            self._reject(f"name ({node.id!r})")

    def visit_Call(self, node: ast.Call) -> None:
        """Allow only positional calls to whitelisted function names."""
        if not (isinstance(node.func, ast.Name)
                and node.func.id in SAFE_FUNCTIONS):
            self._reject("function call")
        if node.keywords:
            self._reject("call with keyword arguments")
        for arg in node.args:
            self.visit(arg)

    def visit_Constant(self, node: ast.Constant) -> None:
        """Allow only bounded numeric, string, bool and None literals."""
        value = node.value
        if value is None or isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            if abs(value) > MAX_NUMBER:
                self._reject("numeric literal")
            return
        if isinstance(value, str):
            if len(value) > MAX_STRING_LENGTH:
                self._reject("string literal")
            return
        self._reject(f"literal ({type(value).__name__})")


class _Bound(NamedTuple):
    """A conservative upper bound on the value a sub-expression may produce.

    ``grounded_size`` bounds the result length attributable to attacker-supplied
    sequence content -- string/bytes literals, ``str(...)`` output, and their
    concatenations and repetitions. It is the quantity checked against
    ``MAX_RESULT_SIZE``. Crucially, ``value`` contributes ``0`` to it: ``value``
    is a bounded, trusted score, so scaling it (``value * 1000000``) is fine,
    while repeating a literal (``'ab' * 999999``) is not -- even when that
    literal is laundered through ``or`` / ``min`` / a conditional.

    ``str_len`` bounds the result length *including* ``value``'s own text; it is
    never capped directly (that would reject ``value * 1000000``) but feeds the
    length of a downstream ``str(...)`` or repetition.

    ``magnitude`` bounds ``abs()`` of a numeric result, used when the result
    serves as a repetition count. ``may_num`` records whether the result may be
    a number (and so eligible as a count or numeric operand).
    """

    grounded_size: float
    str_len: float
    magnitude: float
    may_num: bool


# printf conversion: %[flags][width][.precision]conv, width/precision a number
# or ``*`` (taken from an argument).
_PRINTF_SPEC = re.compile(
    r"%[-+ 0#]*(?P<width>\d+|\*)?(?:\.(?P<prec>\d+|\*))?[diouxXeEfFgGcrsa%]")


def _printf_output_bound(fmt: str, arg_magnitude: float) -> float:
    """Upper bound on the length of ``fmt % args`` for a constant ``fmt``.

    Field widths live in the format literal, so a constant format is fully
    bounded; ``*`` widths draw from an argument, bounded by ``arg_magnitude``.
    """
    total = float(len(fmt))
    for match in _PRINTF_SPEC.finditer(fmt):
        for group in ("width", "prec"):
            spec = match.group(group)
            if spec == "*":
                total += arg_magnitude
            elif spec is not None:
                total += float(spec)
    return total


def _bound(node: ast.AST) -> _Bound:
    """Return a conservative :class:`_Bound` for ``node``."""
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, str):
            return _Bound(len(value), len(value), 0, may_num=False)
        if isinstance(value, bool) or value is None:
            return _Bound(0, 1, 1, may_num=True)
        if isinstance(value, (int, float)):
            return _Bound(0, 1, abs(value), may_num=True)
        return _Bound(0, 1, 0, may_num=False)

    if isinstance(node, ast.Name):
        if node.id == VALUE_NAME:
            # A bounded, trusted score: no attacker-grounded content, but its
            # own text is at most MAX_STRING_LENGTH.
            return _Bound(0, MAX_STRING_LENGTH, MAX_NUMBER, may_num=True)
        return _Bound(0, 1, math.inf, may_num=True)

    if isinstance(node, ast.BinOp):
        return _bound_binop(node)

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return _Bound(0, 1, 1, may_num=True)
        return _bound(node.operand)

    if isinstance(node, (ast.BoolOp, ast.IfExp)):
        parts = node.values if isinstance(node, ast.BoolOp) \
            else [node.body, node.orelse]
        return _combine(_bound(part) for part in parts)

    if isinstance(node, ast.Compare):
        return _Bound(0, 1, 1, may_num=True)

    if isinstance(node, ast.Call):
        return _bound_call(node)

    # Unreachable: the validator has already rejected every other construct.
    return _Bound(math.inf, math.inf, math.inf, may_num=True)


def _combine(bounds: Any) -> _Bound:
    """Bound a result that is one of several branches (``or`` / ``min`` ...)."""
    items = list(bounds)
    return _Bound(
        max(b.grounded_size for b in items),
        max(b.str_len for b in items),
        max(b.magnitude for b in items),
        may_num=any(b.may_num for b in items))


def _bound_binop(node: ast.BinOp) -> _Bound:
    left = _bound(node.left)
    right = _bound(node.right)

    if isinstance(node.op, ast.Mult):
        # Repetition: a sequence (``str_len > 0``) scaled by a numeric count.
        # Either operand may play either role, so take the larger result.
        grounded = [0.0]
        str_len = [0.0]
        for seq, count in ((left, right), (right, left)):
            if seq.str_len > 0 and count.may_num:
                grounded.append(seq.grounded_size * count.magnitude)
                str_len.append(seq.str_len * count.magnitude)
        return _Bound(
            max(grounded), max(str_len), left.magnitude * right.magnitude,
            may_num=left.may_num and right.may_num)

    if isinstance(node.op, ast.Add):
        return _Bound(
            left.grounded_size + right.grounded_size,
            left.str_len + right.str_len,
            left.magnitude + right.magnitude,
            may_num=left.may_num and right.may_num)

    if isinstance(node.op, ast.Mod):
        if isinstance(node.left, ast.Constant) \
                and isinstance(node.left.value, str):
            size = _printf_output_bound(node.left.value, right.magnitude)
            return _Bound(size, size, 0, may_num=False)
        if left.grounded_size > 0:
            # A non-constant string format has an unbounded runtime width.
            return _Bound(math.inf, math.inf, math.inf, may_num=False)
        # Numeric modulo: the remainder is smaller than the divisor.
        return _Bound(0, 1, right.magnitude, may_num=True)

    # Sub / Div / FloorDiv: numeric, never grows a sequence.
    return _Bound(0, 1, left.magnitude + right.magnitude, may_num=True)


def _bound_call(node: ast.Call) -> _Bound:
    name = node.func.id if isinstance(node.func, ast.Name) else ""
    args = [_bound(arg) for arg in node.args]
    first = args[0] if args else _Bound(0, 0, 0, may_num=True)

    if name == "len":
        # A scalar; the argument's own size is still checked by the walk.
        return _Bound(0, 1, first.str_len, may_num=True)
    if name in ("abs", "round"):
        return _Bound(0, 1, first.magnitude, may_num=True)
    if name == "bool":
        return _Bound(0, 1, 1, may_num=True)
    if name in ("int", "float"):
        # The result may be a large number -- a numeric score up to MAX_NUMBER,
        # or a string parsed to something huge (``'9e999999'``). Leave the
        # magnitude unbounded; harmless alone, and it only over-rejects a
        # literal repeated by such a count (``seq * int(...)``).
        return _Bound(0, 1, math.inf, may_num=True)
    if name == "str":
        # The result is concrete text, so its whole length becomes grounded.
        digits = len(str(int(first.magnitude))) + 2 \
            if first.may_num and math.isfinite(first.magnitude) else 0.0
        length = max(first.str_len, digits)
        return _Bound(length, length, 0, may_num=False)
    if name in ("min", "max"):
        return _combine(args) if args else _Bound(0, 1, 1, may_num=True)

    # Unreachable: the validator allows no other call target.
    return _Bound(math.inf, math.inf, math.inf, may_num=True)


def _check_result_size(node: ast.AST, expr: str) -> None:
    """Reject ``expr`` if any sub-expression may exceed ``MAX_RESULT_SIZE``.

    Every node is bounded, so a blow-up hidden inside a scalar-returning call
    (``len('ab' * 99 * 99 * 99 * 99)``) is caught on its inner node. Both the
    attacker-grounded length and the value-inclusive length are checked, so a
    chained repetition of ``value`` (``value * 1000 * 1000``) is caught too.
    """
    for child in ast.walk(node):
        if not isinstance(child, ast.expr):
            continue
        bound = _bound(child)
        if bound.grounded_size > MAX_RESULT_SIZE or bound.str_len > MAX_STR_LEN:
            raise ValueError(
                "value_transform expression may produce an oversized result: "
                f"|{expr}|",
            )


def compile_value_transform(expr: str) -> Callable[[Any], Any]:
    """Validate ``expr`` and return a callable applying it to ``value``.

    Raise ``ValueError`` if ``expr`` is not valid Python or uses any construct
    outside the restricted whitelist.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as error:
        raise ValueError(
            f"value_transform expression is not valid Python: |{expr}|",
        ) from error

    _TransformValidator(expr).visit(tree)
    _check_result_size(tree.body, expr)

    # Wrap the validated expression as ``lambda value: <expr>`` and materialise
    # it once. The body then runs as ``value``-is-a-fast-local on every call,
    # with empty builtins -- no per-call eval or namespace dict.
    lambda_node = ast.Expression(ast.Lambda(
        args=ast.arguments(
            posonlyargs=[], args=[ast.arg(arg=VALUE_NAME)],
            kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=tree.body,
    ))
    ast.fix_missing_locations(lambda_node)
    code = compile(lambda_node, "<value_transform>", "eval")

    # Building the lambda does not run its body; the defaults are fixed and
    # empty, so this eval only materialises the validated callable.
    # pylint: disable-next=eval-used
    transform: Callable[[Any], Any] = eval(code, _EVAL_GLOBALS)  # noqa: S307
    return transform
