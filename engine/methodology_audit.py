"""
Methodology audit helpers.

This module checks whether each methodology template has the formula, threshold,
raw-field, scoring, and rating-engine coverage needed for an end-to-end test.
It also builds editable baseline issuer_data so the Streamlit audit page can
stress-test formulas before real source loaders are complete.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

import pandas as pd

from engine.calculator_engine import (
    calculate_all_formulas,
    load_formula_library,
    parse_required_fields,
)
from engine.factor_engine import load_factor_template, resolve_methodology_id
from engine.rating_engine import run_rating_engine


AUDIT_METHODOLOGIES = [
    "moodys_ccd_go",
    "moodys_k12",
    "sp_community_college_go",
    "sp_local_gov_k12",
    "sp_water_sewer",
]


BASELINE_FIELD_VALUES: Dict[str, float] = {
    "annual_utility_bill": 900.0,
    "assigned_fund_balance": 8_000_000.0,
    "assessed_value_current": 52_000_000_000.0,
    "assessed_value_prior": 50_000_000_000.0,
    "cash": 95_000_000.0,
    "cash_and_investments": 110_000_000.0,
    "cash_ratio_5yr_prior": 0.30,
    "cash_ratio_current": 0.38,
    "committed_fund_balance": 20_000_000.0,
    "county_ebi": 82_000.0,
    "county_gdp": 85_000_000_000.0,
    "county_gdp_current": 88_000_000_000.0,
    "county_gdp_prior": 84_000_000_000.0,
    "debt": 420_000_000.0,
    "debt_service": 18_000_000.0,
    "enrollment_3yr_prior": 25_000.0,
    "enrollment_current": 26_500.0,
    "fte_enrollment": 26_000.0,
    "full_value": 55_000_000_000.0,
    "fund_balance": 95_000_000.0,
    "fund_balance_ratio_5yr_prior": 0.25,
    "fund_balance_ratio_current": 0.33,
    "governmental_expense": 290_000_000.0,
    "governmental_revenue": 310_000_000.0,
    "issuer_mfi": 105_000.0,
    "long_term_debt": 390_000_000.0,
    "mads": 22_000_000.0,
    "median_household_ebi": 80_000.0,
    "mhi_adjusted_rpp": 95_000.0,
    "net_assets": 650_000_000.0,
    "net_direct_debt": 360_000_000.0,
    "net_pension_liability": 180_000_000.0,
    "opeb_cost": 2_000_000.0,
    "operating_expense": 285_000_000.0,
    "operating_revenue": 315_000_000.0,
    "pension_cost": 12_000_000.0,
    "personal_income": 70_000_000_000.0,
    "population": 950_000.0,
    "population_current": 960_000.0,
    "population_prior": 940_000.0,
    "population_us": 335_000_000.0,
    "poverty_rate": 0.10,
    "revenue": 310_000_000.0,
    "service_area_population": 950_000.0,
    "transfers": 0.0,
    "unassigned_fund_balance": 70_000_000.0,
    "us_ebi": 75_000.0,
    "us_gdp": 27_000_000_000_000.0,
    "us_gdp_current": 27_000_000_000_000.0,
    "us_gdp_prior": 26_200_000_000_000.0,
    "us_median_income": 74_000.0,
    "us_mfi": 92_000.0,
    "us_personal_income": 23_000_000_000_000.0,
    "wealth_ratio": 1.10,
    "adjusted_npl": 180_000_000.0,
    "adjusted_opeb": 35_000_000.0,
}


def _read_csv_if_exists(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _clean_formula_library(path: str | Path = "config/formula_library.csv") -> pd.DataFrame:
    formulas = load_formula_library(path)
    return formulas[formulas["formula_id"].astype(str).str.strip() != ""].copy()


def _load_thresholds(path: str | Path = "config/scoring_thresholds.csv") -> pd.DataFrame:
    thresholds = _read_csv_if_exists(path)
    if thresholds.empty:
        return thresholds
    for col in ["methodology_id", "formula_id", "secondary_formula_id", "rule_type"]:
        if col in thresholds.columns:
            thresholds[col] = thresholds[col].fillna("").astype(str).str.strip()
    return thresholds


def methodology_formula_ids(
    methodology_id: str,
    thresholds_path: str | Path = "config/scoring_thresholds.csv",
    templates_dir: str | Path = "templates",
) -> Dict[str, Set[str]]:
    methodology_id = resolve_methodology_id(methodology_id)
    template = load_factor_template(methodology_id, templates_dir=templates_dir)
    thresholds = _load_thresholds(thresholds_path)

    template_ids = set(template["formula_id"].dropna().astype(str).str.strip())
    threshold_ids: Set[str] = set()
    secondary_ids: Set[str] = set()
    if not thresholds.empty:
        rows = thresholds[thresholds["methodology_id"] == methodology_id]
        threshold_ids = set(rows[rows["rule_type"] != "overall_rating_bucket"]["formula_id"].dropna().astype(str).str.strip())
        secondary_ids = set(rows.get("secondary_formula_id", pd.Series(dtype=str)).dropna().astype(str).str.strip())
        threshold_ids.discard("")
        threshold_ids.discard("nan")
        secondary_ids.discard("")
        secondary_ids.discard("nan")

    return {
        "template_ids": template_ids,
        "threshold_ids": threshold_ids,
        "secondary_ids": secondary_ids,
        "all_ids": template_ids | threshold_ids | secondary_ids,
    }


def _source_lookup(
    field_mapping_path: str | Path = "config/field_mapping.csv",
    data_dictionary_path: str | Path = "config/data_dictionary.csv",
) -> Dict[str, str]:
    dictionary = _read_csv_if_exists(data_dictionary_path)
    out: Dict[str, str] = {}
    if not dictionary.empty and {"field_name", "preferred_source", "fallback_source"} <= set(dictionary.columns):
        for _, row in dictionary.iterrows():
            field_name = str(row.get("field_name", "")).strip()
            if not field_name:
                continue
            sources = []
            for col in ["preferred_source", "fallback_source"]:
                value = str(row.get(col, "") or "").strip()
                if value and value.lower() != "nan":
                    sources.extend(part.strip() for part in value.split("|") if part.strip())
            if sources:
                out[field_name] = "|".join(dict.fromkeys(sources))

    mapping = _read_csv_if_exists(field_mapping_path)
    if mapping.empty or "field_name" not in mapping.columns or "source_name" not in mapping.columns:
        return out
    grouped = (
        mapping.assign(
            field_name=mapping["field_name"].astype(str).str.strip(),
            source_name=mapping["source_name"].astype(str).str.strip(),
        )
        .groupby("field_name")["source_name"]
        .apply(lambda s: "|".join(sorted(set(x for x in s if x))))
        .to_dict()
    )
    for field_name, sources in grouped.items():
        out.setdefault(field_name, sources)
    return out


def _field_source(field_name: str, source_lookup: Mapping[str, str], template_source: str = "") -> str:
    if field_name in source_lookup:
        return source_lookup[field_name]
    if template_source:
        return template_source
    if field_name.startswith(("county_", "us_")) or "income" in field_name or "gdp" in field_name:
        return "BEA|CensusACS|Manual"
    if field_name in {"net_direct_debt", "debt_service", "mads"}:
        return "OS|ACFR|Manual"
    if field_name in {"committed_fund_balance", "assigned_fund_balance", "unassigned_fund_balance", "net_assets"}:
        return "ACFR|Manual"
    return "Manual"


def build_methodology_audit(
    methodology_id: str,
    formula_library_path: str | Path = "config/formula_library.csv",
    thresholds_path: str | Path = "config/scoring_thresholds.csv",
    templates_dir: str | Path = "templates",
    field_mapping_path: str | Path = "config/field_mapping.csv",
) -> pd.DataFrame:
    methodology_id = resolve_methodology_id(methodology_id)
    formulas = _clean_formula_library(formula_library_path)
    formula_by_id = formulas.set_index("formula_id").to_dict(orient="index")
    thresholds = _load_thresholds(thresholds_path)
    template = load_factor_template(methodology_id, templates_dir=templates_dir)
    source_lookup = _source_lookup(field_mapping_path)
    ids = methodology_formula_ids(methodology_id, thresholds_path, templates_dir)

    rows: List[Dict[str, Any]] = []
    for fid in sorted(ids["all_ids"]):
        template_rows = template[template["formula_id"].astype(str) == fid]
        threshold_rows = thresholds[
            (thresholds["methodology_id"] == methodology_id)
            & (thresholds["formula_id"] == fid)
            & (thresholds["rule_type"] != "overall_rating_bucket")
        ] if not thresholds.empty else pd.DataFrame()
        rec = formula_by_id.get(fid)
        required_fields = parse_required_fields(rec.get("required_data", "")) if rec else []
        expression = str(rec.get("expression", "")) if rec else ""
        source_priority = "|".join(sorted(set(template_rows["source_priority"].dropna().astype(str)))) if not template_rows.empty else ""
        likely_sources = sorted({
            _field_source(field, source_lookup, source_priority)
            for field in required_fields
            if field != "manual"
        })
        rows.append(
            {
                "methodology_id": methodology_id,
                "formula_id": fid,
                "in_template": fid in ids["template_ids"],
                "threshold_only": fid in ids["threshold_ids"] and fid not in ids["template_ids"],
                "secondary_only": fid in ids["secondary_ids"] and fid not in ids["template_ids"],
                "formula_exists": rec is not None,
                "expression": expression,
                "required_fields": ";".join(required_fields),
                "manual_formula": expression.lower() in {"qualitative", "manual"} or required_fields == ["manual"],
                "threshold_exists": not threshold_rows.empty,
                "threshold_rule_types": "|".join(sorted(set(threshold_rows["rule_type"].astype(str)))) if not threshold_rows.empty else "",
                "template_source_priority": source_priority,
                "likely_data_sources": "|".join(likely_sources),
                "section": "|".join(sorted(set(template_rows["section"].dropna().astype(str)))) if not template_rows.empty else "",
                "factor": "|".join(sorted(set(template_rows["factor"].dropna().astype(str)))) if not template_rows.empty else "",
                "metric": "|".join(sorted(set(template_rows["metric"].dropna().astype(str)))) if not template_rows.empty else "",
            }
        )

    audit = pd.DataFrame(rows)
    if audit.empty:
        return audit
    audit["formula_ok"] = audit["formula_exists"]
    audit["scoring_required"] = audit["in_template"] | audit["threshold_only"]
    audit["scoring_ok"] = (~audit["scoring_required"]) | audit["threshold_exists"]
    audit["structural_status"] = audit.apply(
        lambda r: "ready" if bool(r["formula_ok"]) and bool(r["scoring_ok"]) else "needs_work",
        axis=1,
    )
    return audit


def required_raw_fields_for_methodology(
    methodology_id: str,
    formula_library_path: str | Path = "config/formula_library.csv",
    thresholds_path: str | Path = "config/scoring_thresholds.csv",
    templates_dir: str | Path = "templates",
) -> List[str]:
    formulas = _clean_formula_library(formula_library_path)
    formula_by_id = formulas.set_index("formula_id").to_dict(orient="index")
    ids = methodology_formula_ids(methodology_id, thresholds_path, templates_dir)
    fields: Set[str] = set()
    for fid in ids["all_ids"]:
        rec = formula_by_id.get(fid)
        if not rec:
            continue
        required = parse_required_fields(rec.get("required_data", ""))
        if required == ["manual"]:
            continue
        fields.update(field for field in required if field and field != "manual")
    return sorted(fields)


def baseline_issuer_data(
    methodology_id: str,
    existing_data: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for field in required_raw_fields_for_methodology(methodology_id):
        out[field] = BASELINE_FIELD_VALUES.get(field, 1.0)
    for key, value in (existing_data or {}).items():
        if key in out and value is not None and not (isinstance(value, float) and pd.isna(value)):
            out[key] = value
    return out


def issuer_data_editor_frame(
    methodology_id: str,
    existing_data: Optional[Mapping[str, Any]] = None,
    field_mapping_path: str | Path = "config/field_mapping.csv",
) -> pd.DataFrame:
    source_lookup = _source_lookup(field_mapping_path)
    data = baseline_issuer_data(methodology_id, existing_data)
    rows = []
    for field, value in data.items():
        rows.append(
            {
                "field_name": field,
                "value": value,
                "likely_data_source": _field_source(field, source_lookup),
                "notes": "baseline editable test input",
            }
        )
    return pd.DataFrame(rows)


def manual_score_frame(
    methodology_id: str,
    templates_dir: str | Path = "templates",
) -> pd.DataFrame:
    audit = build_methodology_audit(methodology_id, templates_dir=templates_dir)
    rule_types = audit["threshold_rule_types"].fillna("").astype(str)
    needs_manual_score = (
        audit["manual_formula"]
        | rule_types.str.contains("manual_score", case=False, na=False)
        | rule_types.str.contains("manual_or_external_scale", case=False, na=False)
        | rule_types.str.contains("manual_categorical", case=False, na=False)
    )
    rows = audit[(needs_manual_score) & (audit["scoring_required"])].copy()
    if rows.empty:
        return pd.DataFrame(
            columns=["formula_id", "section", "factor", "metric", "numeric_score", "score_label", "score_source", "notes"]
        )
    rows["numeric_score"] = 2.0
    rows["score_label"] = ""
    rows["score_source"] = "baseline_placeholder"
    rows["notes"] = "Replace with analyst/official score before validation."
    return rows[
        ["formula_id", "section", "factor", "metric", "numeric_score", "score_label", "score_source", "notes"]
    ].reset_index(drop=True)


def frame_to_issuer_data(frame: pd.DataFrame) -> Dict[str, Any]:
    issuer_data: Dict[str, Any] = {}
    if frame is None or frame.empty:
        return issuer_data
    for _, row in frame.iterrows():
        field = str(row.get("field_name", "")).strip()
        if not field:
            continue
        value = pd.to_numeric(row.get("value"), errors="coerce")
        if pd.notna(value):
            issuer_data[field] = float(value)
    return issuer_data


def frame_to_manual_scores(frame: pd.DataFrame) -> Dict[str, Any]:
    manual_scores: Dict[str, Any] = {}
    if frame is None or frame.empty:
        return manual_scores
    for _, row in frame.iterrows():
        fid = str(row.get("formula_id", "")).strip()
        if not fid:
            continue
        numeric = pd.to_numeric(row.get("numeric_score"), errors="coerce")
        label = str(row.get("score_label", "") or "").strip()
        if pd.notna(numeric):
            manual_scores[fid] = {"numeric_score": float(numeric), "score_label": label}
        elif label:
            manual_scores[fid] = {"score_label": label}
    return manual_scores


def run_methodology_test(
    methodology_id: str,
    issuer_data: Mapping[str, Any],
    manual_scores: Optional[Mapping[str, Any]] = None,
    formula_library_path: str | Path = "config/formula_library.csv",
    thresholds_path: str | Path = "config/scoring_thresholds.csv",
    templates_dir: str | Path = "templates",
) -> Dict[str, Any]:
    methodology_id = resolve_methodology_id(methodology_id)
    formula_results = calculate_all_formulas(dict(issuer_data), formula_library=formula_library_path)
    ids = methodology_formula_ids(methodology_id, thresholds_path, templates_dir)
    method_formula_results = formula_results[
        formula_results["formula_id"].astype(str).isin(ids["all_ids"])
    ].copy()
    rating_output = run_rating_engine(
        methodology_id=methodology_id,
        formula_results=formula_results,
        manual_scores=manual_scores or {},
        thresholds_path=thresholds_path,
        templates_dir=templates_dir,
    )
    return {
        "methodology_id": methodology_id,
        "issuer_data": dict(issuer_data),
        "formula_results": formula_results,
        "method_formula_results": method_formula_results,
        "rating_output": rating_output,
        "audit": build_methodology_audit(
            methodology_id,
            formula_library_path=formula_library_path,
            thresholds_path=thresholds_path,
            templates_dir=templates_dir,
        ),
    }


def audit_all_methodologies(methodology_ids: Iterable[str] = AUDIT_METHODOLOGIES) -> pd.DataFrame:
    rows = []
    for methodology_id in methodology_ids:
        audit = build_methodology_audit(methodology_id)
        if audit.empty:
            rows.append({"methodology_id": methodology_id, "formula_count": 0})
            continue
        rows.append(
            {
                "methodology_id": methodology_id,
                "formula_count": int(len(audit)),
                "template_formula_count": int(audit["in_template"].sum()),
                "secondary_formula_count": int(audit["secondary_only"].sum()),
                "missing_formula_count": int((~audit["formula_exists"]).sum()),
                "missing_threshold_count": int((audit["scoring_required"] & ~audit["threshold_exists"]).sum()),
                "manual_formula_count": int(audit["manual_formula"].sum()),
                "structural_ready": bool((audit["structural_status"] == "ready").all()),
            }
        )
    return pd.DataFrame(rows)
