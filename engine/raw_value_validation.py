"""
Raw input to official value validation helpers.

This module validates the calculator path separately from official score
aggregation:

raw input fixture -> calculator_engine.calculate_all_formulas()
                  -> official fixture value comparison
                  -> threshold scoring / rating engine smoke check

The comparison is intentionally transparent about source quality. Some
workbooks expose a scorecard value but not the raw numerator/denominator used
to calculate it; those inputs can be marked as scorecard_implied in the raw
fixture so mismatches are data-source findings, not silent failures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Set

import pandas as pd

from engine.calculator_engine import calculate_all_formulas, clean_numeric, load_formula_library
from engine.factor_engine import load_factor_template
from engine.official_fixture_engine import clean_optional_float
from engine.rating_engine import run_rating_engine, summarize_rating_output


REQUIRED_RAW_INPUT_COLUMNS = {
    "test_case",
    "methodology_id",
    "issuer_name",
    "field_name",
    "value",
}

DEFAULT_VALUE_TOLERANCE = 0.0001
SOURCE_PENDING_TYPES = {
    "manual_source_pending",
    "manual_pending",
    "source_pending",
    "source_review",
}
SCORECARD_IMPLIED_TYPES = {
    "scorecard_implied",
    "official_implied",
    "fixture_implied",
}
SCORING_MODE_OFFICIAL_ASSISTED = "official_assisted"
SCORING_MODE_INDEPENDENT = "independent"
SUPPORTED_SCORING_MODES = {
    SCORING_MODE_OFFICIAL_ASSISTED,
    SCORING_MODE_INDEPENDENT,
}
VALUE_COMPARISON_OVERRIDES: Dict[tuple[str, str], Dict[str, float]] = {
    ("moodys_ccd_go", "tax_base_size"): {"model_value_scale": 0.001, "tolerance": 1.0},
    ("moodys_ccd_go", "full_value_per_capita"): {"tolerance": 1.0},
    ("moodys_ccd_go", "wealth_ratio"): {"tolerance": 0.001},
    ("moodys_ccd_go", "fund_balance_ratio"): {"tolerance": 0.005},
    ("moodys_ccd_go", "fund_balance_trend_5yr"): {"tolerance": 0.005},
    ("moodys_ccd_go", "cash_balance_ratio"): {"tolerance": 0.005},
    ("moodys_ccd_go", "cash_balance_trend_5yr"): {"tolerance": 0.005},
    ("moodys_ccd_go", "operating_history_ratio"): {"tolerance": 0.03},
    ("moodys_ccd_go", "debt_to_full_value"): {"tolerance": 0.001},
    ("moodys_ccd_go", "debt_to_revenue"): {"tolerance": 0.05},
    ("moodys_ccd_go", "adjusted_npl_to_full_value"): {"tolerance": 0.001},
    ("moodys_ccd_go", "adjusted_npl_to_revenue"): {"tolerance": 0.05},
}


def list_raw_input_fixture_files(
    fixture_dir: str | Path = "config/validation_raw_inputs",
) -> Dict[str, Path]:
    path = Path(fixture_dir)
    if not path.exists():
        return {}
    return {item.stem: item for item in sorted(path.glob("*.csv"))}


def load_raw_input_fixture(path: str | Path) -> pd.DataFrame:
    fixture_path = Path(path)
    df = pd.read_csv(fixture_path)
    missing = REQUIRED_RAW_INPUT_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Raw input fixture {fixture_path.name} is missing columns: {sorted(missing)}")

    df = df.copy()
    df["field_name"] = df["field_name"].astype(str).str.strip()
    df["methodology_id"] = df["methodology_id"].astype(str).str.strip()
    df["raw_fixture_file"] = fixture_path.name
    return df


def load_raw_fixture_catalog(
    fixture_dir: str | Path = "config/validation_raw_inputs",
) -> pd.DataFrame:
    rows = []
    for key, path in list_raw_input_fixture_files(fixture_dir).items():
        try:
            fixture = load_raw_input_fixture(path)
            first = fixture.iloc[0]
            rows.append(
                {
                    "fixture_key": key,
                    "raw_fixture_file": path.name,
                    "test_case": first.get("test_case", ""),
                    "methodology_id": first.get("methodology_id", ""),
                    "issuer_name": first.get("issuer_name", ""),
                    "raw_field_count": len(fixture),
                    "source_types": "|".join(sorted(set(fixture.get("source_type", pd.Series(dtype=str)).dropna().astype(str)))),
                }
            )
        except Exception as exc:
            rows.append({"fixture_key": key, "raw_fixture_file": path.name, "load_error": str(exc)})
    return pd.DataFrame(rows)


def _parse_raw_value(value: Any) -> Any:
    if isinstance(value, str) and "|" in value:
        return [clean_numeric(part.strip()) for part in value.split("|")]
    return clean_numeric(value)


def raw_fixture_to_issuer_data(raw_fixture: pd.DataFrame) -> Dict[str, Any]:
    issuer_data: Dict[str, Any] = {}
    for _, row in raw_fixture.iterrows():
        field = str(row.get("field_name", "")).strip()
        if not field:
            continue
        issuer_data[field] = _parse_raw_value(row.get("value"))
    return issuer_data


def _split_source_types(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    return [part.strip().lower() for part in raw.replace(";", "|").split("|") if part.strip()]


def raw_source_quality_summary(raw_fixture: pd.DataFrame) -> pd.DataFrame:
    """Summarize whether raw fixture rows are independent, implied, or pending."""
    if raw_fixture.empty or "source_type" not in raw_fixture.columns:
        return pd.DataFrame()

    rows = []
    for _, row in raw_fixture.iterrows():
        source_types = _split_source_types(row.get("source_type", ""))
        if not source_types:
            source_types = ["unspecified"]
        for source_type in source_types:
            if source_type in SOURCE_PENDING_TYPES:
                category = "source_pending"
            elif source_type in SCORECARD_IMPLIED_TYPES:
                category = "scorecard_implied"
            else:
                category = "independent_source"
            rows.append(
                {
                    "source_type": source_type,
                    "source_category": category,
                    "field_name": str(row.get("field_name", "")).strip(),
                }
            )

    if not rows:
        return pd.DataFrame()

    summary = (
        pd.DataFrame(rows)
        .groupby(["source_category", "source_type"], as_index=False)
        .agg(raw_field_count=("field_name", "nunique"))
        .sort_values(["source_category", "source_type"])
        .reset_index(drop=True)
    )
    return summary


def _template_formula_ids(methodology_id: str, templates_dir: str | Path) -> list[str]:
    template = load_factor_template(methodology_id, templates_dir=templates_dir)
    return template["formula_id"].dropna().astype(str).str.strip().tolist()


def _secondary_formula_ids_from_thresholds(
    methodology_id: str,
    thresholds_path: str | Path = "config/scoring_thresholds.csv",
) -> list[str]:
    path = Path(thresholds_path)
    if not path.exists():
        return []
    thresholds = pd.read_csv(path)
    required = {"methodology_id", "secondary_formula_id"}
    if not required.issubset(thresholds.columns):
        return []
    rows = thresholds[
        thresholds["methodology_id"].astype(str).str.strip().eq(str(methodology_id).strip())
    ].copy()
    if rows.empty:
        return []
    ids = rows["secondary_formula_id"].dropna().astype(str).str.strip()
    return [fid for fid in ids.tolist() if fid]


def calculate_template_formulas_from_raw(
    raw_fixture: pd.DataFrame,
    formula_library_path: str | Path = "config/formula_library.csv",
    templates_dir: str | Path = "templates",
    thresholds_path: str | Path = "config/scoring_thresholds.csv",
) -> pd.DataFrame:
    if raw_fixture.empty:
        raise ValueError("Raw input fixture is empty.")

    methodology_id = str(raw_fixture["methodology_id"].iloc[0]).strip()
    formula_ids = _template_formula_ids(methodology_id, templates_dir)
    formula_ids = list(
        dict.fromkeys(
            formula_ids
            + _secondary_formula_ids_from_thresholds(methodology_id, thresholds_path=thresholds_path)
        )
    )
    library = load_formula_library(formula_library_path)
    library = library[library["formula_id"].isin(formula_ids)].copy()
    issuer_data = raw_fixture_to_issuer_data(raw_fixture)
    results = calculate_all_formulas(issuer_data, formula_library=library)
    order = {fid: idx for idx, fid in enumerate(formula_ids)}
    results["_template_order"] = results["formula_id"].map(order)
    return results.sort_values("_template_order").drop(columns=["_template_order"]).reset_index(drop=True)


def _required_fields_by_formula(formula_library_path: str | Path = "config/formula_library.csv") -> Dict[str, list[str]]:
    library = load_formula_library(formula_library_path)
    out: Dict[str, list[str]] = {}
    for _, row in library.iterrows():
        required = str(row.get("required_data", "") or "").strip()
        if not required or required.lower() == "manual":
            out[str(row["formula_id"])] = []
            continue
        fields = [part.strip() for part in required.replace(",", ";").replace("|", ";").split(";") if part.strip()]
        out[str(row["formula_id"])] = fields
    return out


def _manual_formula_ids_from_library(formula_library_path: str | Path = "config/formula_library.csv") -> Set[str]:
    library = load_formula_library(formula_library_path)
    expression = library["expression"].fillna("").astype(str).str.strip().str.lower()
    required = library["required_data"].fillna("").astype(str).str.strip().str.lower()
    manual = library[expression.isin({"qualitative", "manual"}) | required.eq("manual")]
    return set(manual["formula_id"].dropna().astype(str).str.strip())


def _source_summary(raw_fixture: pd.DataFrame, fields: Iterable[str], column: str) -> str:
    if column not in raw_fixture.columns:
        return ""
    rows = []
    for field in fields:
        match = raw_fixture[raw_fixture["field_name"].astype(str) == str(field)]
        if match.empty:
            continue
        value = match.iloc[0].get(column, "")
        if pd.isna(value) or str(value).strip() == "":
            continue
        rows.append(f"{field}:{value}")
    return "; ".join(rows)


def _field_source_types(raw_fixture: pd.DataFrame, fields: Iterable[str]) -> list[str]:
    if "source_type" not in raw_fixture.columns:
        return []
    out: list[str] = []
    for field in fields:
        match = raw_fixture[raw_fixture["field_name"].astype(str) == str(field)]
        if match.empty:
            continue
        raw = str(match.iloc[0].get("source_type", "") or "").strip()
        if not raw:
            continue
        out.extend(_split_source_types(raw))
    return out


def formula_source_audit(
    raw_fixture: pd.DataFrame,
    official_fixture: pd.DataFrame,
    formula_library_path: str | Path = "config/formula_library.csv",
) -> pd.DataFrame:
    """Classify each official scorecard metric by data-source readiness."""
    if raw_fixture.empty or official_fixture.empty:
        return pd.DataFrame()

    required_fields = _required_fields_by_formula(formula_library_path)
    manual_formula_ids = _manual_formula_ids_from_library(formula_library_path)
    rows = []
    raw_fields = set(raw_fixture["field_name"].dropna().astype(str).str.strip())
    for _, row in official_fixture.iterrows():
        fid = str(row.get("formula_id", "")).strip()
        fields = required_fields.get(fid, [])
        missing_fields = [field for field in fields if field not in raw_fields]
        source_types = set(_field_source_types(raw_fixture, fields))

        if fid in manual_formula_ids:
            status = "manual"
        elif missing_fields:
            status = "raw_field_missing"
        elif source_types & SOURCE_PENDING_TYPES:
            status = "source_pending"
        elif source_types & SCORECARD_IMPLIED_TYPES:
            status = "scorecard_implied"
        else:
            status = "independent_source"

        rows.append(
            {
                "formula_id": fid,
                "metric": row.get("metric", ""),
                "source_quality_status": status,
                "required_fields": ";".join(fields),
                "missing_raw_fields": ";".join(missing_fields),
                "raw_source_types": _source_summary(raw_fixture, fields, "source_type"),
                "raw_source_cells": _source_summary(raw_fixture, fields, "source_cell"),
            }
        )
    return pd.DataFrame(rows)


def _source_pending_formula_ids(
    raw_fixture: pd.DataFrame,
    formula_library_path: str | Path = "config/formula_library.csv",
    thresholds_path: str | Path | None = None,
) -> Set[str]:
    required_fields = _required_fields_by_formula(formula_library_path)
    pending: Set[str] = set()
    for formula_id, fields in required_fields.items():
        source_types = set(_field_source_types(raw_fixture, fields))
        if source_types & SOURCE_PENDING_TYPES:
            pending.add(formula_id)
    if thresholds_path is not None and Path(thresholds_path).exists() and not raw_fixture.empty:
        methodology_id = str(raw_fixture["methodology_id"].iloc[0]).strip()
        thresholds = pd.read_csv(thresholds_path)
        if {"methodology_id", "formula_id", "secondary_formula_id"}.issubset(thresholds.columns):
            rows = thresholds[
                thresholds["methodology_id"].astype(str).str.strip().eq(methodology_id)
            ].copy()
            for _, row in rows.iterrows():
                formula_id = str(row.get("formula_id", "") or "").strip()
                secondary_id = str(row.get("secondary_formula_id", "") or "").strip()
                if formula_id and secondary_id and secondary_id in pending:
                    pending.add(formula_id)
    return pending


def _manual_scores_from_fixture(
    formula_results: pd.DataFrame,
    official_fixture: pd.DataFrame,
    formula_library_path: str | Path = "config/formula_library.csv",
) -> Dict[str, Dict[str, Any]]:
    manual_ids = set(
        formula_results.loc[
            formula_results["status"].astype(str).str.lower().eq("manual"),
            "formula_id",
        ].astype(str)
    )
    manual_ids.update(_manual_formula_ids_from_library(formula_library_path))
    manual_scores: Dict[str, Dict[str, Any]] = {}
    if official_fixture.empty:
        return manual_scores
    for _, row in official_fixture.iterrows():
        fid = str(row.get("formula_id", "")).strip()
        if fid not in manual_ids:
            continue
        score = clean_optional_float(row.get("official_score"))
        if score is None:
            continue
        manual_scores[fid] = {
            "numeric_score": score,
            "score_label": str(row.get("official_score_label", "") or ""),
        }
    return manual_scores


def _official_scores_for_source_pending(
    raw_fixture: pd.DataFrame,
    official_fixture: pd.DataFrame,
    formula_library_path: str | Path = "config/formula_library.csv",
    thresholds_path: str | Path | None = None,
) -> Dict[str, Dict[str, Any]]:
    pending_ids = _source_pending_formula_ids(raw_fixture, formula_library_path, thresholds_path=thresholds_path)
    out: Dict[str, Dict[str, Any]] = {}
    if official_fixture.empty:
        return out
    for _, row in official_fixture.iterrows():
        fid = str(row.get("formula_id", "")).strip()
        if fid not in pending_ids:
            continue
        score = clean_optional_float(row.get("official_score"))
        if score is None:
            continue
        out[fid] = {
            "numeric_score": score,
            "score_label": str(row.get("official_score_label", "") or ""),
        }
    return out


def compare_raw_formula_values_to_official(
    formula_results: pd.DataFrame,
    official_fixture: pd.DataFrame,
    raw_fixture: pd.DataFrame,
    formula_library_path: str | Path = "config/formula_library.csv",
) -> pd.DataFrame:
    if official_fixture.empty:
        return pd.DataFrame()

    methodology_id = str(official_fixture["methodology_id"].iloc[0]).strip()
    result_cols = ["formula_id", "formula_name", "status", "value", "missing_fields", "warning", "error"]
    available_result_cols = [col for col in result_cols if col in formula_results.columns]
    compare = official_fixture.merge(
        formula_results[available_result_cols],
        on="formula_id",
        how="left",
        suffixes=("_official", "_model"),
    )

    required_fields = _required_fields_by_formula(formula_library_path)
    manual_formula_ids = _manual_formula_ids_from_library(formula_library_path)
    rows = []
    for _, row in compare.iterrows():
        fid = str(row.get("formula_id", "")).strip()
        official_value = clean_optional_float(row.get("official_value"))
        model_value = clean_optional_float(row.get("value"))
        override = VALUE_COMPARISON_OVERRIDES.get((methodology_id, fid), {})
        scale = float(override.get("model_value_scale", 1.0))
        tolerance = float(override.get("tolerance", DEFAULT_VALUE_TOLERANCE))
        model_compare_value = None if model_value is None else model_value * scale

        status = str(row.get("status", "") or "").lower()
        if status == "manual" or fid in manual_formula_ids:
            value_status = "manual_skip"
            value_match = None
            delta = None
            abs_delta = None
            relative_delta = None
        elif model_compare_value is None:
            value_status = "model_missing"
            value_match = False
            delta = None
            abs_delta = None
            relative_delta = None
        elif official_value is None:
            value_status = "official_missing"
            value_match = None
            delta = None
            abs_delta = None
            relative_delta = None
        else:
            delta = model_compare_value - official_value
            abs_delta = abs(delta)
            denominator = abs(official_value) if official_value else None
            relative_delta = None if denominator in {None, 0} else abs_delta / denominator
            value_match = abs_delta <= tolerance
            value_status = "match" if value_match else "mismatch"

        fields = required_fields.get(fid, [])
        source_types = set(_field_source_types(raw_fixture, fields))
        source_pending = bool(source_types & SOURCE_PENDING_TYPES)
        record = row.to_dict()
        if source_pending:
            value_status = "source_pending"
            value_match = None
            delta = None
            abs_delta = None
            relative_delta = None
        record.update(
            {
                "required_fields": ";".join(fields),
                "raw_source_types": _source_summary(raw_fixture, fields, "source_type"),
                "raw_source_cells": _source_summary(raw_fixture, fields, "source_cell"),
                "model_value": model_value,
                "model_value_scale": scale,
                "model_compare_value": model_compare_value,
                "official_value_numeric": official_value,
                "value_delta": delta,
                "value_abs_delta": abs_delta,
                "value_relative_delta": relative_delta,
                "value_tolerance": tolerance,
                "value_match": value_match,
                "value_status": value_status,
            }
        )
        rows.append(record)

    out = pd.DataFrame(rows)
    preferred = [
        "value_status",
        "formula_id",
        "metric",
        "official_value",
        "model_value",
        "model_value_scale",
        "model_compare_value",
        "value_delta",
        "value_tolerance",
        "official_score",
        "official_weight",
        "status",
        "required_fields",
        "raw_source_types",
        "raw_source_cells",
        "warning",
        "error",
        "notes",
    ]
    return out[[col for col in preferred if col in out.columns] + [col for col in out.columns if col not in preferred]]


def compare_auto_scores_to_official(output: Mapping[str, Any], official_fixture: pd.DataFrame) -> pd.DataFrame:
    factor_output = output.get("factor_engine_output", {}) or {}
    metric_df = factor_output.get("metric_scores", pd.DataFrame())
    if not isinstance(metric_df, pd.DataFrame) or metric_df.empty or official_fixture.empty:
        return pd.DataFrame()

    cols = [
        "formula_id",
        "section",
        "factor",
        "metric",
        "official_score",
        "official_score_label",
        "official_weight",
    ]
    left = official_fixture[[col for col in cols if col in official_fixture.columns]].copy()
    right_cols = ["formula_id", "raw_value", "numeric_score", "score_label", "status", "missing_reason"]
    right = metric_df[[col for col in right_cols if col in metric_df.columns]].copy()
    compare = left.merge(right, on="formula_id", how="left")
    compare["model_score"] = pd.to_numeric(compare.get("numeric_score"), errors="coerce")
    compare["official_score_numeric"] = pd.to_numeric(compare.get("official_score"), errors="coerce")
    compare["score_delta"] = compare["model_score"] - compare["official_score_numeric"]
    compare["score_match"] = (compare["score_delta"].abs() <= 0.01).astype(object)
    if "official_weight" in compare.columns:
        weight = pd.to_numeric(compare["official_weight"], errors="coerce").fillna(0.0)
        rating_only_manual = weight.eq(0.0) & compare["model_score"].isna() & compare["official_score_numeric"].notna()
        compare.loc[rating_only_manual, "score_match"] = pd.NA
        compare.loc[rating_only_manual, "status"] = "rating_manual_only"
        compare.loc[rating_only_manual, "missing_reason"] = (
            "Manual rating input is used by rating engine but is not a weighted template metric."
        )
    return compare


def raw_value_validation_report(
    raw_fixture: pd.DataFrame | str | Path,
    official_fixture: pd.DataFrame,
    formula_library_path: str | Path = "config/formula_library.csv",
    thresholds_path: str | Path = "config/scoring_thresholds.csv",
    templates_dir: str | Path = "templates",
    scoring_mode: str = SCORING_MODE_OFFICIAL_ASSISTED,
) -> Dict[str, Any]:
    if isinstance(raw_fixture, (str, Path)):
        raw_df = load_raw_input_fixture(raw_fixture)
    else:
        raw_df = raw_fixture.copy()
    if raw_df.empty:
        raise ValueError("Raw input fixture is empty.")
    if official_fixture.empty:
        raise ValueError("Official fixture is empty.")
    if scoring_mode not in SUPPORTED_SCORING_MODES:
        raise ValueError(f"Unsupported raw validation scoring_mode: {scoring_mode}")

    methodology_id = str(raw_df["methodology_id"].iloc[0]).strip()
    formula_results = calculate_template_formulas_from_raw(
        raw_df,
        formula_library_path=formula_library_path,
        templates_dir=templates_dir,
        thresholds_path=thresholds_path,
    )
    manual_score_overrides: Dict[str, Dict[str, Any]] = {}
    source_pending_score_overrides: Dict[str, Dict[str, Any]] = {}
    if scoring_mode == SCORING_MODE_OFFICIAL_ASSISTED:
        manual_score_overrides = _manual_scores_from_fixture(
            formula_results,
            official_fixture,
            formula_library_path=formula_library_path,
        )
        source_pending_score_overrides = _official_scores_for_source_pending(
            raw_df,
            official_fixture,
            formula_library_path=formula_library_path,
            thresholds_path=thresholds_path,
        )
    manual_scores = {**manual_score_overrides, **source_pending_score_overrides}
    override_rows = []
    for fid, score in manual_score_overrides.items():
        override_rows.append({"formula_id": fid, "override_source": "official_manual_score", **score})
    for fid, score in source_pending_score_overrides.items():
        override_rows.append({"formula_id": fid, "override_source": "official_source_pending_score", **score})
    official_score_overrides = pd.DataFrame(override_rows)

    output = run_rating_engine(
        methodology_id=methodology_id,
        formula_results=formula_results,
        manual_scores=manual_scores,
        thresholds_path=thresholds_path,
        templates_dir=templates_dir,
    )
    value_comparison = compare_raw_formula_values_to_official(
        formula_results,
        official_fixture,
        raw_df,
        formula_library_path=formula_library_path,
    )
    score_comparison = compare_auto_scores_to_official(output, official_fixture)
    rating_summary = summarize_rating_output(output)
    return {
        "scoring_mode": scoring_mode,
        "raw_inputs": raw_df,
        "issuer_data": raw_fixture_to_issuer_data(raw_df),
        "formula_results": formula_results,
        "manual_scores": manual_scores,
        "official_score_overrides": official_score_overrides,
        "source_quality_summary": raw_source_quality_summary(raw_df),
        "formula_source_audit": formula_source_audit(
            raw_df,
            official_fixture,
            formula_library_path=formula_library_path,
        ),
        "output": output,
        "value_comparison": value_comparison,
        "score_comparison": score_comparison,
        "rating_summary": rating_summary,
    }
