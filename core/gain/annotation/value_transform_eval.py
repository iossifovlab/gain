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

# Upper bound on the length (chars for text, elements for a sequence) any
# sub-expression may produce. ``_check_result_size`` propagates a conservative
# size bound through the tree and rejects the expression if any node exceeds
# this, closing operator-driven blow-ups that the per-literal bounds miss.
MAX_RESULT_SIZE = 1_000_000

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

    ``size`` bounds the length of a sequence result (chars for text) and is
    ``1`` for scalars; it is the quantity checked against ``MAX_RESULT_SIZE``.
    ``magnitude`` bounds ``abs()`` of a numeric result -- needed because in
    ``seq * n`` it is ``n``'s value, not its size, that scales the length.
    ``may_str``/``may_num`` record which runtime types the result may hold, so
    a *known* sequence (``may_str and not may_num``: literals, ``str(...)``,
    string concatenations) is told apart from a numeric-or-unknown operand.
    """

    size: float
    magnitude: float
    may_str: bool
    may_num: bool


# A ``value`` is a bounded score from annotation, of unknown type at build
# time; bound both interpretations conservatively.
_VALUE_BOUND = _Bound(MAX_STRING_LENGTH, MAX_NUMBER, may_str=True, may_num=True)

# printf conversion: %[flags][width][.precision]conv, width/precision a number
# or ``*`` (taken from an argument).
_PRINTF_SPEC = re.compile(
    r"%[-+ 0#]*(?P<width>\d+|\*)?(?:\.(?P<prec>\d+|\*))?[diouxXeEfFgGcrsa%]")


def _is_known_sequence(bound: _Bound) -> bool:
    """Whether the result is certainly a sequence (never a plain number)."""
    return bound.may_str and not bound.may_num


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


def _bound(node: ast.AST, arg_magnitude: float) -> _Bound:
    """Return a conservative :class:`_Bound` for ``node``.

    ``arg_magnitude`` is the largest numeric magnitude available from the
    surrounding expression (used to bound ``*`` printf widths).
    """
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, str):
            return _Bound(len(value), 0, may_str=True, may_num=False)
        if isinstance(value, bool) or value is None:
            return _Bound(1, 1, may_str=False, may_num=True)
        if isinstance(value, (int, float)):
            return _Bound(1, abs(value), may_str=False, may_num=True)
        return _Bound(1, 0, may_str=False, may_num=False)

    if isinstance(node, ast.Name):
        return _VALUE_BOUND if node.id == VALUE_NAME \
            else _Bound(1, math.inf, may_str=False, may_num=True)

    if isinstance(node, ast.BinOp):
        return _bound_binop(node, arg_magnitude)

    if isinstance(node, ast.UnaryOp):
        operand = _bound(node.operand, arg_magnitude)
        if isinstance(node.op, ast.Not):
            return _Bound(1, 1, may_str=False, may_num=True)
        return operand

    if isinstance(node, (ast.BoolOp, ast.IfExp)):
        parts = node.values if isinstance(node, ast.BoolOp) \
            else [node.body, node.orelse]
        bounds = [_bound(part, arg_magnitude) for part in parts]
        return _Bound(
            max(b.size for b in bounds), max(b.magnitude for b in bounds),
            may_str=any(b.may_str for b in bounds),
            may_num=any(b.may_num for b in bounds))

    if isinstance(node, ast.Compare):
        return _Bound(1, 1, may_str=False, may_num=True)

    if isinstance(node, ast.Call):
        return _bound_call(node, arg_magnitude)

    # Unreachable: the validator has already rejected every other construct.
    return _Bound(math.inf, math.inf, may_str=True, may_num=True)


def _bound_binop(node: ast.BinOp, arg_magnitude: float) -> _Bound:
    left = _bound(node.left, arg_magnitude)
    right = _bound(node.right, arg_magnitude)

    if isinstance(node.op, ast.Mult):
        # Sequence repetition applies only when a *known* sequence is scaled by
        # a number; otherwise the result is numeric-or-unknown (size 1).
        sizes = [1.0]
        if _is_known_sequence(left) and right.may_num:
            sizes.append(left.size * right.magnitude)
        if _is_known_sequence(right) and left.may_num:
            sizes.append(right.size * left.magnitude)
        return _Bound(
            max(sizes), left.magnitude * right.magnitude,
            may_str=left.may_str or right.may_str,
            may_num=left.may_num and right.may_num)

    if isinstance(node.op, ast.Add):
        return _Bound(
            left.size + right.size, left.magnitude + right.magnitude,
            may_str=left.may_str or right.may_str,
            may_num=left.may_num and right.may_num)

    if isinstance(node.op, ast.Mod):
        if isinstance(node.left, ast.Constant) \
                and isinstance(node.left.value, str):
            size = _printf_output_bound(node.left.value, right.magnitude)
            return _Bound(size, 0, may_str=True, may_num=False)
        if _is_known_sequence(left):
            # A non-constant string format has an unbounded runtime width.
            return _Bound(math.inf, math.inf, may_str=True, may_num=False)
        # Numeric modulo: the remainder is smaller than the divisor.
        return _Bound(1, right.magnitude, may_str=False, may_num=True)

    # Sub / Div / FloorDiv: numeric, never grows a sequence.
    return _Bound(
        max(left.size, right.size), left.magnitude + right.magnitude,
        may_str=False, may_num=True)


def _bound_call(node: ast.Call, arg_magnitude: float) -> _Bound:
    name = node.func.id if isinstance(node.func, ast.Name) else ""
    args = [_bound(arg, arg_magnitude) for arg in node.args]

    if name == "len":
        # A scalar, but its argument's size is still checked by the walk.
        return _Bound(1, args[0].size if args else 0,
                      may_str=False, may_num=True)
    if name in ("abs", "round"):
        return _Bound(1, args[0].magnitude if args else 0,
                      may_str=False, may_num=True)
    if name == "bool":
        return _Bound(1, 1, may_str=False, may_num=True)
    if name in ("int", "float"):
        # Parsing a string of unknown digits yields an unbounded magnitude
        # (e.g. ``'9e999999'``); harmless alone, caught if used as a count.
        magnitude = math.inf if not args or args[0].may_str \
            else args[0].magnitude
        return _Bound(1, magnitude, may_str=False, may_num=True)
    if name == "str":
        arg = args[0] if args else _Bound(0, 0, may_str=False, may_num=False)
        digits = len(str(int(arg.magnitude))) + 2 \
            if math.isfinite(arg.magnitude) else math.inf
        size = max(arg.size if arg.may_str else 0, digits if arg.may_num else 0)
        return _Bound(size, 0, may_str=True, may_num=False)
    if name in ("min", "max"):
        if not args:
            return _Bound(1, 1, may_str=False, may_num=True)
        return _Bound(
            max(a.size for a in args), max(a.magnitude for a in args),
            may_str=any(a.may_str for a in args),
            may_num=any(a.may_num for a in args))

    # Unreachable: the validator allows no other call target.
    return _Bound(math.inf, math.inf, may_str=True, may_num=True)


def _check_result_size(node: ast.AST, expr: str) -> None:
    """Reject ``expr`` if any sub-expression may exceed ``MAX_RESULT_SIZE``.

    Every node is bounded, so a blow-up hidden inside a scalar-returning call
    (``len('ab' * 99 * 99 * 99 * 99)``) is caught on its inner node.
    """
    def largest_magnitude(current: ast.AST) -> float:
        magnitudes = [_bound(child, 0).magnitude
                      for child in ast.walk(current)
                      if isinstance(child, (ast.Constant, ast.Name))]
        finite = [m for m in magnitudes if math.isfinite(m)]
        return max(finite, default=0.0)

    arg_magnitude = largest_magnitude(node)
    for child in ast.walk(node):
        if isinstance(child, ast.expr) \
                and _bound(child, arg_magnitude).size > MAX_RESULT_SIZE:
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
