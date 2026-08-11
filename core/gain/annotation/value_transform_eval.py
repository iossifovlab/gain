"""Restricted evaluator for pipeline ``value_transform`` expressions.

A ``value_transform`` is an expression supplied through pipeline configuration,
and that configuration can arrive verbatim in an anonymous HTTP request body.
It must therefore never reach an unrestricted ``eval``.

``compile_value_transform`` validates the expression against a small whitelist
of AST nodes, operators, names and calls, rejects everything else, and returns
a callable that evaluates the expression over a single bound name, ``value``,
with no access to builtins.  Static bounds on numeric and string literals cap
the cheapest resource-exhaustion inputs; the residual (blow-up via chained
multiplication) is tracked separately.
"""
import ast
from collections.abc import Callable
from typing import Any

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

# Static resource-exhaustion bounds. The chained-multiplication residual these
# do not close is tracked as a separate follow-up.
MAX_NUMBER = 1_000_000
MAX_STRING_LENGTH = 256

# AST nodes permitted anywhere without a dedicated visitor below. ``Pow`` is
# deliberately absent, so ``**`` is rejected.
_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.Load,
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
    code = compile(tree, "<value_transform>", "eval")

    def transform(value: Any) -> Any:
        # The code object was compiled from an expression validated against the
        # whitelist above, and runs with empty builtins and a single bound name.
        return eval(  # pylint: disable=eval-used  # noqa: S307
            code, _EVAL_GLOBALS, {VALUE_NAME: value})

    return transform
