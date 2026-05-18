"""Safe restricted expression evaluator for DAG edge conditions."""
from __future__ import annotations

import ast
import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ConditionEvaluationError",
    "SAFE_BUILTINS",
    "restricted_eval",
]


# ═══════════════════════════════════════════════════════════════════════════════
# Exceptions
# ═══════════════════════════════════════════════════════════════════════════════


class ConditionEvaluationError(Exception):
    """Raised when condition expression is unsafe or fails evaluation."""

    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Whitelisted Builtins
# ═══════════════════════════════════════════════════════════════════════════════

SAFE_BUILTINS: dict[str, Any] = {
    "len": len,
    "any": any,
    "all": all,
    "bool": bool,
    "int": int,
    "float": float,
    "str": str,
    "min": min,
    "max": max,
    "abs": abs,
    "True": True,
    "False": False,
    "None": None,
}

# Names that are NEVER allowed in expressions
_BLOCKED_NAMES: frozenset[str] = frozenset({
    "exec",
    "eval",
    "compile",
    "__import__",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
    "open",
    "input",
    "breakpoint",
    "exit",
    "quit",
    "type",
    "object",
    "super",
    "classmethod",
    "staticmethod",
    "property",
    "memoryview",
    "bytearray",
    "bytes",
})

# AST node types that are ALLOWED in condition expressions
ALLOWED_AST_NODES: frozenset[type] = frozenset({
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Call,
    ast.Constant,
    ast.Attribute,
    ast.Subscript,
    ast.Name,
    ast.Load,
    ast.Tuple,
    ast.List,
    ast.Dict,
    # Boolean operators
    ast.And,
    ast.Or,
    ast.Not,
    # Arithmetic operators
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.FloorDiv,
    # Comparison operators
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
    # Unary operators
    ast.USub,
    ast.UAdd,
    # Ternary
    ast.IfExp,
    # Slicing
    ast.Slice,
})


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════


def restricted_eval(expr: str, context: dict[str, Any]) -> bool:
    """Safely evaluate a Python expression against a context dict.

    The expression can access:
    - Variables from context dict (e.g., state fields)
    - Safe builtins: len, any, all, bool, int, float, str, min, max, abs
    - Comparison operators: ==, !=, <, <=, >, >=
    - Boolean operators: and, or, not
    - Membership: in, not in
    - Attribute access: obj.field (no private attrs)
    - Indexing: obj[key]
    - Literals: numbers, strings, booleans, None, lists, dicts

    Blocks:
    - Import statements
    - Assignments (=, +=, etc.)
    - Comprehensions (listcomp, setcomp, dictcomp, genexpr)
    - __dunder__ attribute access
    - Private attribute access (_prefix)
    - exec, eval, compile, __import__
    - Lambda expressions

    Args:
        expr: A Python expression string.
        context: Dictionary of variable names to values accessible in the
            expression. Typically contains state-derived values like
            ``{"chunks_count": 5, "has_evidence": True, "confidence": 0.8}``.

    Returns:
        The boolean result of the expression (coerced via bool()).

    Raises:
        ConditionEvaluationError: If expression is unsafe, unparseable,
            or fails during evaluation.
    """
    if not expr or not expr.strip():
        raise ConditionEvaluationError("Empty condition expression")

    expr = expr.strip()

    # Parse to AST
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ConditionEvaluationError(
            f"Syntax error in condition expression: {e}"
        ) from e

    # Validate AST safety
    _validate_ast(tree)

    # Compile to bytecode
    try:
        code = compile(tree, filename="<condition>", mode="eval")
    except Exception as e:
        raise ConditionEvaluationError(
            f"Failed to compile condition expression: {e}"
        ) from e

    # Build restricted namespace
    namespace: dict[str, Any] = {"__builtins__": {}}
    namespace.update(SAFE_BUILTINS)
    namespace.update(context)

    # Evaluate
    try:
        result = eval(code, namespace)  # noqa: S307
    except Exception as e:
        raise ConditionEvaluationError(
            f"Condition evaluation failed: {type(e).__name__}: {e}"
        ) from e

    return bool(result)


# ═══════════════════════════════════════════════════════════════════════════════
# Internal AST Validation
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_ast(tree: ast.AST) -> None:
    """Walk AST tree and verify all nodes are in the allowed set.

    Raises:
        ConditionEvaluationError: If any unsafe construct is found.
    """
    for node in ast.walk(tree):
        node_type = type(node)

        # Check node type is allowed
        if node_type not in ALLOWED_AST_NODES:
            raise ConditionEvaluationError(
                f"Disallowed expression construct: {node_type.__name__}"
            )

        # Check Name nodes for blocked identifiers
        if isinstance(node, ast.Name):
            if node.id in _BLOCKED_NAMES:
                raise ConditionEvaluationError(
                    f"Access to '{node.id}' is not allowed in conditions"
                )
            if node.id.startswith("__") and node.id.endswith("__"):
                raise ConditionEvaluationError(
                    f"Dunder name access is not allowed: '{node.id}'"
                )

        # Check Attribute nodes for dunder/private access
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                raise ConditionEvaluationError(
                    f"Dunder attribute access is not allowed: '.{node.attr}'"
                )
            if node.attr.startswith("_"):
                raise ConditionEvaluationError(
                    f"Private attribute access is not allowed: '.{node.attr}'"
                )

        # Check Call nodes — only allow calls to safe builtins or context attrs
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in _BLOCKED_NAMES:
                    raise ConditionEvaluationError(
                        f"Call to '{node.func.id}' is not allowed in conditions"
                    )
