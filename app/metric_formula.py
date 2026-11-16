"""Safe metric formula parse and evaluate. No eval()."""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<num>\d+(?:\.\d+)?)
      | (?P<fn>count|area)\s*\(\s*(?P<label>[A-Za-z_][A-Za-z0-9_]*)\s*\)
      | (?P<op>[+\-*/()])
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


def ratio_to_formula(measure: str, numerator: list[str], denominator: list[str]) -> str:
    """Convert a legacy sum-ratio metric into a formula string."""
    kind = "area" if str(measure).strip().lower() == "area" else "count"

    def side(labels: list[str]) -> str:
        parts = [f"{kind}({name})" for name in labels if str(name).strip()]
        if not parts:
            return "0"
        if len(parts) == 1:
            return parts[0]
        return "(" + " + ".join(parts) + ")"

    return f"{side(numerator)} / {side(denominator)}"


def normalize_metric(row: dict[str, Any]) -> dict[str, Any]:
    """Return a metric dict with a formula, migrating legacy ratio fields."""
    formula = str(row.get("formula") or "").strip()
    if formula:
        return {
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or "").strip(),
            "formula": formula,
        }
    measure = str(row.get("measure") or "count").strip().lower()
    numerator = [str(name) for name in (row.get("numerator") or []) if str(name).strip()]
    denominator = [str(name) for name in (row.get("denominator") or []) if str(name).strip()]
    if numerator and denominator:
        return {
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or "").strip(),
            "formula": ratio_to_formula(measure, numerator, denominator),
        }
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or "").strip(),
        "formula": formula,
    }


def formula_labels(formula: str) -> list[str]:
    """Class names referenced by count(...) or area(...)."""
    labels: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"(?:count|area)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
        formula,
        flags=re.IGNORECASE,
    ):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            labels.append(name)
    return labels


def validate_formula(formula: str, allowed: set[str]) -> str:
    """Parse and check class names. Returns the stripped formula."""
    text = str(formula or "").strip()
    if not text:
        raise ValueError("Metric needs a formula.")
    for name in formula_labels(text):
        if name not in allowed:
            raise ValueError(f"Unknown class {name!r} in formula.")
    # Dry-run with zeros to catch syntax errors.
    evaluate_formula(text, {})
    return text


def evaluate_formula(
    formula: str,
    source: dict[str, dict[str, float]],
) -> float | None:
    """Evaluate a formula against per-class count/area totals.

    Returns None on divide-by-zero. Raises ValueError on bad syntax.
    """
    tokens = _tokenize(formula)
    if not tokens:
        raise ValueError("Metric needs a formula.")
    value, index = _parse_expr(tokens, 0, source)
    if index != len(tokens):
        raise ValueError(f"Unexpected token {tokens[index]!r} in formula.")
    return value


def _lookup(source: dict[str, dict[str, float]], label: str, measure: str) -> float:
    row = source.get(label) or {}
    return float(row.get(measure) or 0)


def _tokenize(formula: str) -> list[tuple[str, Any]]:
    text = str(formula or "")
    pos = 0
    tokens: list[tuple[str, Any]] = []
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if not match:
            raise ValueError(f"Invalid formula near {text[pos:pos + 20]!r}.")
        pos = match.end()
        if match.group("num") is not None:
            tokens.append(("num", float(match.group("num"))))
        elif match.group("fn") is not None:
            tokens.append(("fn", (match.group("fn").lower(), match.group("label"))))
        else:
            tokens.append(("op", match.group("op")))
    return tokens


def _parse_expr(
    tokens: list[tuple[str, Any]],
    index: int,
    source: dict[str, dict[str, float]],
) -> tuple[float | None, int]:
    left, index = _parse_term(tokens, index, source)
    while index < len(tokens) and tokens[index][0] == "op" and tokens[index][1] in "+-":
        op = tokens[index][1]
        right, index = _parse_term(tokens, index + 1, source)
        if left is None or right is None:
            left = None
        elif op == "+":
            left = left + right
        else:
            left = left - right
    return left, index


def _parse_term(
    tokens: list[tuple[str, Any]],
    index: int,
    source: dict[str, dict[str, float]],
) -> tuple[float | None, int]:
    left, index = _parse_factor(tokens, index, source)
    while index < len(tokens) and tokens[index][0] == "op" and tokens[index][1] in "*/":
        op = tokens[index][1]
        right, index = _parse_factor(tokens, index + 1, source)
        if left is None or right is None:
            left = None
        elif op == "*":
            left = left * right
        elif right == 0:
            left = None
        else:
            left = left / right
    return left, index


def _parse_factor(
    tokens: list[tuple[str, Any]],
    index: int,
    source: dict[str, dict[str, float]],
) -> tuple[float | None, int]:
    if index >= len(tokens):
        raise ValueError("Formula ended early.")
    kind, value = tokens[index]
    if kind == "op" and value == "-":
        inner, index = _parse_factor(tokens, index + 1, source)
        return (None if inner is None else -inner), index
    if kind == "op" and value == "+":
        return _parse_factor(tokens, index + 1, source)
    if kind == "num":
        return float(value), index + 1
    if kind == "fn":
        fn_name, label = value
        return _lookup(source, label, "area" if fn_name == "area" else "count"), index + 1
    if kind == "op" and value == "(":
        inner, index = _parse_expr(tokens, index + 1, source)
        if index >= len(tokens) or tokens[index] != ("op", ")"):
            raise ValueError("Missing closing parenthesis in formula.")
        return inner, index + 1
    raise ValueError(f"Unexpected token {value!r} in formula.")
