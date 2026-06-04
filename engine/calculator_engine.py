"""
Calculator Engine for Scoreboard-Combo / CreditScope MVP
========================================================

Purpose
-------
Take canonical raw fields produced by the Mapping Engine, load formula_library.csv,
and calculate formula outputs such as:

    full_value_per_capita = full_value / population

Expected inputs
---------------
issuer_data: dict
    Example:
    {
        "population": 530000,
        "full_value": 40000000000,
        "net_direct_debt": 700000000,
    }

formula_library.csv columns used
--------------------------------
- formula_id
- formula_name
- expression
- required_data
- category

Output
------
A pandas DataFrame with one row per formula:
- formula_id
- formula_name
- category
- expression
- status: ready / missing / manual / error
- value
- missing_fields
- error
- warning

Design notes
------------
- This engine intentionally does NOT use Python eval directly.
- It uses a small AST-based arithmetic evaluator for safety.
- Supported operations: +, -, *, /, **, %, parentheses, unary +/-.
- Formula expressions can use ^ for exponentiation; it is converted to **.
- qualitative/manual formulas are returned as status="manual".
- avg_3yr(...) and avg_5yr(...) are supported. If the raw inputs are lists/Series,
  the engine calculates the expression row-by-row and averages it. If only scalar
  current-year fields are available, it calculates a current-year proxy and adds a warning.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


STATUS_READY = "ready"
STATUS_MISSING = "missing"
STATUS_MANUAL = "manual"
STATUS_ERROR = "error"


@dataclass
class FormulaResult:
    formula_id: str
    formula_name: str
    category: str
    expression: str
    status: str
    value: Optional[float] = None
    missing_fields: str = ""
    error: str = ""
    warning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "formula_id": self.formula_id,
            "formula_name": self.formula_name,
            "category": self.category,
            "expression": self.expression,
            "status": self.status,
            "value": self.value,
            "missing_fields": self.missing_fields,
            "error": self.error,
            "warning": self.warning,
        }


def load_formula_library(path: str | Path = "config/formula_library.csv") -> pd.DataFrame:
    """Load formula_library.csv and normalize empty values."""
    df = pd.read_csv(path)
    required = {"formula_id", "formula_name", "expression", "required_data"}
    missing_cols = required - set(df.columns)
    if missing_cols:
        raise ValueError(f"formula_library.csv is missing required columns: {sorted(missing_cols)}")
    for col in ["formula_id", "formula_name", "expression", "required_data", "category"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()
    if "category" not in df.columns:
        df["category"] = ""
    return df


def parse_required_fields(required_data: Any) -> List[str]:
    """Parse semicolon/comma/pipe separated required fields."""
    if required_data is None or (isinstance(required_data, float) and math.isnan(required_data)):
        return []
    text = str(required_data).strip()
    if not text:
        return []
    parts = re.split(r"[;,|]", text)
    return [p.strip() for p in parts if p.strip()]


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def clean_numeric(value: Any) -> Any:
    """
    Convert common financial strings to numbers.

    Examples:
    "$1,200" -> 1200
    "15.3%" -> 0.153
    "(1,200)" -> -1200
    "N/A" -> None

    Lists/Series are converted element-wise.
    """
    if isinstance(value, pd.Series):
        return value.apply(clean_numeric)
    if isinstance(value, (list, tuple)):
        return [clean_numeric(v) for v in value]
    if _is_missing_value(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if text.lower() in {"na", "n/a", "none", "null", "missing", "--", "-"}:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    is_percent = text.endswith("%")
    text = text.replace("$", "").replace(",", "").replace("%", "").strip()

    try:
        num = float(text)
    except ValueError:
        return value

    if negative:
        num = -num
    if is_percent:
        num = num / 100.0
    return num


class SafeArithmeticEvaluator(ast.NodeVisitor):
    """Small safe arithmetic evaluator for formula expressions."""

    ALLOWED_BINOPS = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.Pow: lambda a, b: a ** b,
        ast.Mod: lambda a, b: a % b,
    }
    ALLOWED_UNARYOPS = {
        ast.UAdd: lambda a: +a,
        ast.USub: lambda a: -a,
    }
    ALLOWED_FUNCS = {
        "min": min,
        "max": max,
        "abs": abs,
        "sqrt": math.sqrt,
        "log": math.log,
        "ln": math.log,
        "exp": math.exp,
    }

    def __init__(self, variables: Dict[str, Any]):
        self.variables = variables

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Unsupported constant: {node.value!r}")

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id not in self.variables:
            raise KeyError(f"Missing variable: {node.id}")
        value = self.variables[node.id]
        if _is_missing_value(value):
            raise KeyError(f"Missing variable: {node.id}")
        if not isinstance(value, (int, float)):
            raise ValueError(f"Variable {node.id} is not numeric: {value!r}")
        return float(value)

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        op_type = type(node.op)
        if op_type not in self.ALLOWED_BINOPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        left = self.visit(node.left)
        right = self.visit(node.right)
        return self.ALLOWED_BINOPS[op_type](left, right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        op_type = type(node.op)
        if op_type not in self.ALLOWED_UNARYOPS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        return self.ALLOWED_UNARYOPS[op_type](self.visit(node.operand))

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are allowed")
        name = node.func.id
        if name not in self.ALLOWED_FUNCS:
            raise ValueError(f"Unsupported function: {name}")
        args = [self.visit(arg) for arg in node.args]
        return self.ALLOWED_FUNCS[name](*args)

    def generic_visit(self, node: ast.AST) -> Any:
        raise ValueError(f"Unsupported expression element: {type(node).__name__}")


def normalize_expression(expression: str) -> str:
    """Normalize formula expression syntax before parsing."""
    expression = str(expression).strip()
    expression = expression.replace("^", "**")
    return expression


def evaluate_arithmetic_expression(expression: str, variables: Dict[str, Any]) -> float:
    """Safely evaluate one arithmetic expression against scalar variables."""
    expr = normalize_expression(expression)
    tree = ast.parse(expr, mode="eval")
    value = SafeArithmeticEvaluator(variables).visit(tree)
    if isinstance(value, (int, float)):
        if math.isinf(value) or math.isnan(value):
            raise ValueError("Formula result is infinite or NaN")
        return float(value)
    raise ValueError(f"Formula result is not numeric: {value!r}")


def _is_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple, pd.Series))


def _sequence_length(value: Any) -> int:
    if isinstance(value, pd.Series):
        return len(value)
    if isinstance(value, (list, tuple)):
        return len(value)
    return 0


def _get_at(value: Any, idx: int) -> Any:
    if isinstance(value, pd.Series):
        return value.iloc[idx]
    if isinstance(value, (list, tuple)):
        return value[idx]
    return value


def evaluate_avg_expression(expression: str, variables: Dict[str, Any]) -> Tuple[float, str]:
    """
    Evaluate avg_3yr(...) or avg_5yr(...).

    If variables are sequences, evaluate row-wise and average valid results.
    If all variables are scalar, evaluate once and return a warning.
    """
    match = re.fullmatch(r"avg_(\d+)yr\((.*)\)", expression.strip())
    if not match:
        raise ValueError("Not an avg_Nyr expression")

    years = int(match.group(1))
    inner = match.group(2).strip()

    sequence_lengths = [_sequence_length(v) for v in variables.values() if _is_sequence(v)]
    if not sequence_lengths:
        value = evaluate_arithmetic_expression(inner, variables)
        return value, f"avg_{years}yr used current-year scalar proxy because no time-series values were provided."

    n = min(sequence_lengths)
    if n == 0:
        raise ValueError("Time-series inputs are empty")

    values: List[float] = []
    for i in range(n):
        row_vars = {k: clean_numeric(_get_at(v, i)) for k, v in variables.items()}
        try:
            values.append(evaluate_arithmetic_expression(inner, row_vars))
        except Exception:
            continue

    if not values:
        raise ValueError("No valid yearly values could be calculated for average formula")

    warning = ""
    if len(values) < years:
        warning = f"Only {len(values)} valid period(s) available for avg_{years}yr formula."
    return float(sum(values) / len(values)), warning


def current_period_variables(variables: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """
    Convert time-series variables to current-period scalar values.

    Non-average formulas still represent one current-period metric. When the
    source layer provides a multi-period series for a shared raw field, the
    first value is treated as the current period and the formula output keeps a
    warning so the convention stays visible.
    """
    sequence_fields = [field for field, value in variables.items() if _is_sequence(value)]
    if not sequence_fields:
        return variables, ""
    current = {
        field: clean_numeric(_get_at(value, 0)) if _is_sequence(value) else value
        for field, value in variables.items()
    }
    warning = "Current-period formula used the first value from time-series field(s): " + ";".join(sequence_fields)
    return current, warning


def calculate_formula(row: pd.Series | Dict[str, Any], issuer_data: Dict[str, Any]) -> FormulaResult:
    """Calculate a single formula row from formula_library.csv."""
    if not isinstance(row, dict):
        row = row.to_dict()

    formula_id = str(row.get("formula_id", "")).strip()
    formula_name = str(row.get("formula_name", formula_id)).strip()
    category = str(row.get("category", "")).strip()
    expression = str(row.get("expression", "")).strip()
    required_fields = parse_required_fields(row.get("required_data", ""))

    if expression.lower() in {"qualitative", "manual"} or required_fields == ["manual"]:
        return FormulaResult(
            formula_id=formula_id,
            formula_name=formula_name,
            category=category,
            expression=expression,
            status=STATUS_MANUAL,
            missing_fields="manual",
            warning="Manual / qualitative formula; not calculated by calculator engine.",
        )

    missing = [field for field in required_fields if field not in issuer_data or _is_missing_value(issuer_data.get(field))]
    if missing:
        return FormulaResult(
            formula_id=formula_id,
            formula_name=formula_name,
            category=category,
            expression=expression,
            status=STATUS_MISSING,
            missing_fields=";".join(missing),
            error="Required raw field(s) missing from issuer_data.",
        )

    variables = {field: clean_numeric(issuer_data.get(field)) for field in required_fields}
    still_missing = [field for field, value in variables.items() if _is_missing_value(value)]
    if still_missing:
        return FormulaResult(
            formula_id=formula_id,
            formula_name=formula_name,
            category=category,
            expression=expression,
            status=STATUS_MISSING,
            missing_fields=";".join(still_missing),
            error="Required raw field(s) could not be converted to numeric values.",
        )

    try:
        if re.fullmatch(r"avg_\d+yr\(.*\)", expression.strip()):
            value, warning = evaluate_avg_expression(expression, variables)
        else:
            scalar_variables, warning = current_period_variables(variables)
            value = evaluate_arithmetic_expression(expression, scalar_variables)
        return FormulaResult(
            formula_id=formula_id,
            formula_name=formula_name,
            category=category,
            expression=expression,
            status=STATUS_READY,
            value=value,
            warning=warning,
        )
    except ZeroDivisionError:
        return FormulaResult(
            formula_id=formula_id,
            formula_name=formula_name,
            category=category,
            expression=expression,
            status=STATUS_ERROR,
            missing_fields="",
            error="Division by zero.",
        )
    except Exception as exc:
        return FormulaResult(
            formula_id=formula_id,
            formula_name=formula_name,
            category=category,
            expression=expression,
            status=STATUS_ERROR,
            error=str(exc),
        )


def calculate_all_formulas(
    issuer_data: Dict[str, Any],
    formula_library: pd.DataFrame | str | Path = "config/formula_library.csv",
    categories: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """
    Calculate all formulas in formula_library.csv against issuer_data.

    Parameters
    ----------
    issuer_data:
        Canonical raw data dictionary from the Mapping Engine.
    formula_library:
        DataFrame or path to formula_library.csv.
    categories:
        Optional category filter, e.g. ["Economy", "Debt"].
    """
    if isinstance(formula_library, (str, Path)):
        formulas = load_formula_library(formula_library)
    else:
        formulas = formula_library.copy()

    if categories:
        categories_set = set(categories)
        formulas = formulas[formulas["category"].isin(categories_set)]

    results = [calculate_formula(row, issuer_data).to_dict() for _, row in formulas.iterrows()]
    return pd.DataFrame(results)


def summarize_calculation_results(results: pd.DataFrame) -> Dict[str, int]:
    """Return simple status counts for Streamlit summary cards."""
    if results.empty or "status" not in results.columns:
        return {STATUS_READY: 0, STATUS_MISSING: 0, STATUS_MANUAL: 0, STATUS_ERROR: 0}
    counts = results["status"].value_counts().to_dict()
    return {
        STATUS_READY: int(counts.get(STATUS_READY, 0)),
        STATUS_MISSING: int(counts.get(STATUS_MISSING, 0)),
        STATUS_MANUAL: int(counts.get(STATUS_MANUAL, 0)),
        STATUS_ERROR: int(counts.get(STATUS_ERROR, 0)),
    }


def get_ready_metrics(results: pd.DataFrame) -> pd.DataFrame:
    """Return only successfully calculated formulas for Scoreboard Preview."""
    if results.empty:
        return results
    return results[results["status"] == STATUS_READY].copy()


def get_missing_reasons(results: pd.DataFrame) -> pd.DataFrame:
    """Return missing/error/manual formulas with reasons for coverage display."""
    if results.empty:
        return results
    return results[results["status"] != STATUS_READY].copy()
