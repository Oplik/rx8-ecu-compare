"""Safe evaluation of RomRaider scaling expressions."""

from __future__ import annotations

import ast
import math
import operator
from dataclasses import dataclass

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class ScalingError(ValueError):
    pass


def normalize_expr(expr: str) -> str:
    s = (expr or "x").strip()
    if not s:
        s = "x"
    s = s.replace("^", "**")
    return s


def _eval_node(node: ast.AST, x: float) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, x)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ScalingError(f"Unsupported constant: {node.value!r}")
    _num = getattr(ast, "Num", ())
    if _num and isinstance(node, _num):
        return float(node.n)
    if isinstance(node, ast.Name):
        if node.id == "x":
            return float(x)
        raise ScalingError(f"Unknown name: {node.id}")
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return float(_UNARY[type(node.op)](_eval_node(node.operand, x)))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left = _eval_node(node.left, x)
        right = _eval_node(node.right, x)
        if type(node.op) in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
            raise ScalingError("Division by zero in scaling expression")
        return float(_BINOPS[type(node.op)](left, right))
    if isinstance(node, ast.Call):
        raise ScalingError("Function calls are not allowed in scaling expressions")
    raise ScalingError(f"Unsupported expression node: {type(node).__name__}")


def eval_expr(expr: str, x: float) -> float:
    tree = ast.parse(normalize_expr(expr), mode="eval")
    value = _eval_node(tree, x)
    if not math.isfinite(value):
        raise ScalingError("Scaling produced a non-finite value")
    return value


def format_scaled(value: float | None, fmt: str | None, error: str | None = None) -> str:
    if error:
        return f"scaling error ({error})"
    if value is None:
        return "n/a"
    fmt = (fmt or "").strip()
    if not fmt:
        if abs(value - round(value)) < 1e-9:
            return f"{value:.0f}"
        return f"{value:.4g}"
    if "." in fmt:
        decimals = len(fmt.split(".", 1)[1])
        return f"{value:.{decimals}f}"
    return f"{value:.0f}"


@dataclass(frozen=True)
class Scaling:
    expression: str = "x"
    to_byte: str = "x"
    format: str = ""
    units: str = ""

    def to_display(self, raw: float) -> tuple[float | None, str | None]:
        try:
            return eval_expr(self.expression, raw), None
        except (ScalingError, SyntaxError, TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
            return None, str(exc)

    def from_display(self, scaled: float) -> float:
        return eval_expr(self.to_byte or "x", scaled)
