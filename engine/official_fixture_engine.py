"""
Official scorecard fixture comparison helpers.

These utilities validate methodology mechanics against official/workbook
scorecard rows. They intentionally separate two checks:

1. Official-score aggregation:
   Use official metric scores from a fixture to validate weights, factor
   aggregation, profile/section aggregation, and rating mapping.

2. Raw-value scoring:
   Keep official raw values beside those scores so later tests can compare the
   calculator/threshold path against the same fixture without changing schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pandas as pd

from engine.rating_engine import compare_to_benchmark, run_rating_engine, summarize_rating_output


REQUIRED_FIXTURE_COLUMNS = {
    "test_case",
    "methodology_id",
    "issuer_name",
    "official_rating",
    "official_weighted_score",
    "formula_id",
    "official_weight",
    "official_value",
    "official_score",
    "official_score_label",
}

SUMMARY_OPTIONAL_COLUMNS = [
    "official_enterprise_score",
    "official_financial_score",
    "official_icp_score",
    "official_institutional_framework_score",
    "official_anchor",
    "official_sacp",
    "official_icr",
]


def clean_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"none", "nan", "na", "n/a", "-", "--"}:
        return None
    text = text.replace("$", "").replace(",", "")
    is_pct = text.endswith("%")
    text = text[:-1].strip() if is_pct else text
    try:
        number = float(text)
    except Exception:
        return None
    return number / 100.0 if is_pct else number


def _has_fixture_value(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return text != "" and text.lower() not in {"none", "nan", "na", "n/a", "-", "--"}


def list_official_fixture_files(
    fixture_dir: str | Path = "config/validation_fixtures",
) -> Dict[str, Path]:
    path = Path(fixture_dir)
    if not path.exists():
        return {}
    return {item.stem: item for item in sorted(path.glob("*.csv"))}


def load_official_fixture(path: str | Path) -> pd.DataFrame:
    fixture_path = Path(path)
    df = pd.read_csv(fixture_path)
    missing = REQUIRED_FIXTURE_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Fixture {fixture_path.name} is missing columns: {sorted(missing)}")

    df = df.copy()
    df["formula_id"] = df["formula_id"].astype(str).str.strip()
    df["official_score"] = pd.to_numeric(df["official_score"], errors="coerce")
    df["official_weight"] = pd.to_numeric(df["official_weight"], errors="coerce")
    df["official_weighted_score_contribution"] = df["official_weight"] * df["official_score"]
    df["fixture_file"] = fixture_path.name
    return df


def fixture_summary(fixture: pd.DataFrame) -> pd.DataFrame:
    if fixture.empty:
        return pd.DataFrame()
    first = fixture.iloc[0]
    row = {
        "fixture_file": first.get("fixture_file", ""),
        "test_case": first.get("test_case", ""),
        "methodology_id": first.get("methodology_id", ""),
        "issuer_name": first.get("issuer_name", ""),
        "official_rating": first.get("official_rating", ""),
        "official_weighted_score": clean_optional_float(first.get("official_weighted_score")),
        "fixture_weighted_sum": fixture["official_weighted_score_contribution"].sum(),
        "metric_count": len(fixture),
    }
    for col in SUMMARY_OPTIONAL_COLUMNS:
        if col in fixture.columns:
            row[col] = first.get(col)
    return pd.DataFrame([row])


def load_fixture_catalog(
    fixture_dir: str | Path = "config/validation_fixtures",
) -> pd.DataFrame:
    rows = []
    for key, path in list_official_fixture_files(fixture_dir).items():
        try:
            fixture = load_official_fixture(path)
            summary = fixture_summary(fixture)
            if not summary.empty:
                record = summary.iloc[0].to_dict()
                record["fixture_key"] = key
                rows.append(record)
        except Exception as exc:
            rows.append({"fixture_key": key, "fixture_file": path.name, "load_error": str(exc)})
    return pd.DataFrame(rows)


def fixture_formula_records(fixture: pd.DataFrame) -> pd.DataFrame:
    formula_name = fixture["formula_id"]
    if "metric" in fixture.columns:
        formula_name = fixture["metric"].fillna(fixture["formula_id"])
    return pd.DataFrame(
        {
            "formula_id": fixture["formula_id"].astype(str),
            "formula_name": formula_name.astype(str),
            "status": "ready",
            "value": fixture["official_value"],
            "numeric_score": fixture["official_score"],
            "score_label": fixture["official_score_label"],
        }
    )


def fixture_metric_scores(fixture: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for _, row in fixture.iterrows():
        score = clean_optional_float(row.get("official_score"))
        if score is None:
            continue
        out[str(row["formula_id"])] = {
            "numeric_score": score,
            "score_label": str(row.get("official_score_label", "") or ""),
        }
    return out


def run_official_fixture(
    fixture: pd.DataFrame,
    thresholds_path: str | Path = "config/scoring_thresholds.csv",
    templates_dir: str | Path = "templates",
) -> Dict[str, Any]:
    if fixture.empty:
        raise ValueError("Official fixture is empty.")

    methodology_id = str(fixture["methodology_id"].iloc[0])
    formula_df = fixture_formula_records(fixture)
    metric_scores = fixture_metric_scores(fixture)
    return run_rating_engine(
        methodology_id=methodology_id,
        formula_results=formula_df,
        metric_scores=metric_scores,
        manual_scores=metric_scores,
        thresholds_path=thresholds_path,
        templates_dir=templates_dir,
    )


def compare_metric_scores_to_fixture(output: Mapping[str, Any], fixture: pd.DataFrame) -> pd.DataFrame:
    factor_output = output.get("factor_engine_output", {}) or {}
    metric_df = factor_output.get("metric_scores", pd.DataFrame())
    if not isinstance(metric_df, pd.DataFrame) or metric_df.empty:
        return pd.DataFrame()

    fixture_cols = [
        "section",
        "factor",
        "metric",
        "formula_id",
        "official_value",
        "official_score",
        "official_score_label",
        "official_weight",
        "official_weighted_score_contribution",
    ]
    cols = [col for col in fixture_cols if col in fixture.columns]
    compare = fixture[cols].merge(
        metric_df[["formula_id", "raw_value", "numeric_score", "score_label", "status", "missing_reason"]],
        on="formula_id",
        how="left",
    )
    compare["score_delta"] = pd.to_numeric(compare["numeric_score"], errors="coerce") - pd.to_numeric(compare["official_score"], errors="coerce")
    compare["score_match"] = compare["score_delta"].abs() <= 0.0001
    return compare


def compare_rating_summary_to_fixture(
    output: Mapping[str, Any],
    fixture: pd.DataFrame,
    score_tolerance: float = 0.02,
) -> pd.DataFrame:
    if fixture.empty:
        return pd.DataFrame()

    first = fixture.iloc[0]
    official_rating = str(first.get("official_rating", "") or "")
    official_score = clean_optional_float(first.get("official_weighted_score"))
    benchmark = compare_to_benchmark(
        output,
        benchmark_rating=official_rating,
        benchmark_score=official_score,
        tolerance=score_tolerance,
    )

    rows = [
        {
            "check": "indicative_rating",
            "official": official_rating,
            "model": benchmark.get("model_rating"),
            "delta": "",
            "match": benchmark.get("rating_match"),
        },
        {
            "check": "overall_score",
            "official": official_score,
            "model": benchmark.get("model_score"),
            "delta": None if official_score is None or benchmark.get("model_score") is None else benchmark["model_score"] - official_score,
            "match": benchmark.get("score_match"),
        },
    ]

    rr = output.get("rating_result", {}) or {}
    summary_pairs = {
        "enterprise_score": ("official_enterprise_score", "enterprise_score"),
        "financial_score": ("official_financial_score", "financial_score"),
        "icp_score": ("official_icp_score", "icp_score"),
        "institutional_framework_score": ("official_institutional_framework_score", "institutional_framework_score"),
        "anchor": ("official_anchor", "anchor"),
        "sacp": ("official_sacp", "sacp"),
        "icr": ("official_icr", "icr"),
    }
    for check, (official_col, model_col) in summary_pairs.items():
        if official_col not in fixture.columns:
            continue
        official_raw = first.get(official_col)
        if not _has_fixture_value(official_raw):
            continue
        model_raw = rr.get(model_col)
        official_num = clean_optional_float(official_raw)
        model_num = clean_optional_float(model_raw)
        if official_num is not None and model_num is not None:
            delta = model_num - official_num
            match = abs(delta) <= score_tolerance
        else:
            delta = ""
            match = str(model_raw).strip().lower() == str(official_raw).strip().lower()
        rows.append({"check": check, "official": official_raw, "model": model_raw, "delta": delta, "match": match})

    return pd.DataFrame(rows)


def official_fixture_report(
    fixture: pd.DataFrame,
    thresholds_path: str | Path = "config/scoring_thresholds.csv",
    templates_dir: str | Path = "templates",
) -> Dict[str, Any]:
    output = run_official_fixture(fixture, thresholds_path=thresholds_path, templates_dir=templates_dir)
    return {
        "output": output,
        "fixture_summary": fixture_summary(fixture),
        "rating_summary": summarize_rating_output(output),
        "rating_comparison": compare_rating_summary_to_fixture(output, fixture),
        "metric_comparison": compare_metric_scores_to_fixture(output, fixture),
    }
