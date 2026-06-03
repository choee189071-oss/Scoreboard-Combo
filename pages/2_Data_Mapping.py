from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st
from utils.ui_helpers import page_header, current_context_card, init_state

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from engine.calculator_engine import load_formula_library, parse_required_fields
    from engine.factor_engine import load_factor_template
    from engine.mapping_engine import map_creditscope_workbook, map_uploaded_file
    from engine.data_sourcing_engine import (
        mapping_report_to_source_candidates,
        manual_data_to_source_candidates,
        run_data_sourcing_pipeline,
    )
except Exception as exc:  # pragma: no cover - Streamlit display path
    st.set_page_config(page_title="Data Mapping", page_icon="②", layout="wide")
    st.error("Could not import mapping/calculator engines.")
    st.exception(exc)
    st.stop()


st.set_page_config(page_title="Data Mapping", page_icon="②", layout="wide")
init_state()
page_header(
    "② Data Mapping",
    "Upload source files or enter canonical raw fields. This page builds the issuer_data dictionary used by Calculators.",
    "data_mapping",
)
current_context_card()

methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")


def _default_value_for_field(field_name: str) -> Any:
    defaults = {
        "population": 100000.0,
        "population_us": 330000000.0,
        "full_value": 12000000000.0,
        "assessed_value": 10000000000.0,
        "assessed_value_prior": 9500000000.0,
        "operating_revenue": 100000000.0,
        "operating_expense": 98000000.0,
        "governmental_revenue": 100000000.0,
        "governmental_expense": 98000000.0,
        "general_fund_balance": 25000000.0,
        "available_fund_balance": 25000000.0,
        "cash_balance": 30000000.0,
        "cash_and_investments": 30000000.0,
        "unrestricted_reserves": 30000000.0,
        "net_direct_debt": 20000000.0,
        "direct_debt": 20000000.0,
        "total_go_debt": 20000000.0,
        "net_pension_liability": 35000000.0,
        "mads": 6000000.0,
        "mads_requirement": 6000000.0,
        "issuer_mfi": 120000.0,
        "us_mfi": 100000.0,
        "mhi_adjusted_rpp": 120000.0,
        "us_median_income": 80000.0,
        "county_ebi": 120.0,
        "us_ebi": 100.0,
        "county_gdp_current": 105.0,
        "county_gdp_prior": 100.0,
        "us_gdp_current": 103.0,
        "us_gdp_prior": 100.0,
    }
    return defaults.get(field_name, None)


def _required_fields_for_methodology(methodology_id: str) -> pd.DataFrame:
    formulas = load_formula_library("config/formula_library.csv")
    template = load_factor_template(methodology_id, templates_dir="templates")
    formula_ids = set(template["formula_id"].dropna().astype(str))
    try:
        thresholds = pd.read_csv("config/scoring_thresholds.csv")
        related_thresholds = thresholds[thresholds["methodology_id"].astype(str) == str(methodology_id)]
        secondary_ids = set(related_thresholds["secondary_formula_id"].dropna().astype(str))
        secondary_ids.discard("")
        secondary_ids.discard("nan")
        formula_ids |= secondary_ids
    except Exception:
        pass
    rows: List[Dict[str, Any]] = []
    for _, formula in formulas[formulas["formula_id"].astype(str).isin(formula_ids)].iterrows():
        for field in parse_required_fields(formula.get("required_data", "")):
            if field == "manual":
                continue
            rows.append(
                {
                    "field_name": field,
                    "formula_id": formula["formula_id"],
                    "formula_name": formula["formula_name"],
                    "category": formula.get("category", ""),
                }
            )
    if not rows:
        return pd.DataFrame(columns=["field_name", "used_by", "category"])
    df = pd.DataFrame(rows)
    return (
        df.groupby("field_name", as_index=False)
        .agg(
            used_by=("formula_id", lambda x: "; ".join(sorted(set(map(str, x))))),
            category=("category", lambda x: "; ".join(sorted(set(str(v) for v in x if str(v))))),
        )
        .sort_values("field_name")
        .reset_index(drop=True)
    )


def _clean_edited_values(df: pd.DataFrame) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for _, row in df.iterrows():
        key = str(row.get("field_name", "")).strip()
        raw_value = row.get("value")
        if not key or raw_value in [None, ""]:
            continue
        try:
            data[key] = float(raw_value)
        except Exception:
            data[key] = raw_value
    return data


st.divider()
st.subheader("Mapped Source Files")
st.caption("CSV/XLSX uploads are mapped through engine.mapping_engine. PDF parsing is intentionally left for the next parser layer.")

source_options = {
    "creditscope": ("CreditScope", "CreditScope CSV/XLSX"),
    "ipeds": ("IPEDS_Excel", "IPEDS Excel"),
    "os": ("OS", "Official Statement table extract"),
    "acfr": ("ACFR", "ACFR / Audit table extract"),
}

mapped_candidate_frames: List[pd.DataFrame] = []
match_reports: List[pd.DataFrame] = []
cols = st.columns(4)
for i, (key, (source_name, label)) in enumerate(source_options.items()):
    with cols[i]:
        uploaded = st.file_uploader(label, type=["csv", "xlsx", "xls"], key=f"upload_{key}")
        if uploaded is None:
            continue
        try:
            if source_name == "CreditScope" and uploaded.name.lower().endswith((".xlsx", ".xls")):
                source_data, report = map_creditscope_workbook(
                    uploaded_file=uploaded,
                    mapping_path="config/field_mapping.csv",
                    value_col=2,
                )
                if not source_data:
                    uploaded.seek(0)
                    source_data, report = map_uploaded_file(
                        uploaded_file=uploaded,
                        source_name=source_name,
                        mapping_path="config/field_mapping.csv",
                    )
            else:
                source_data, report = map_uploaded_file(
                    uploaded_file=uploaded,
                    source_name=source_name,
                    mapping_path="config/field_mapping.csv",
                )
            st.session_state["uploaded_sources"][key] = uploaded.name
            st.success(f"{len(source_data)} fields mapped")
            if not report.empty:
                report.insert(0, "uploaded_file", uploaded.name)
                match_reports.append(report)
                mapped_candidate_frames.append(
                    mapping_report_to_source_candidates(report, uploaded_file=uploaded.name)
                )
        except Exception as exc:
            st.error(f"Could not map {uploaded.name}.")
            st.exception(exc)

if match_reports:
    with st.expander("Source mapping reports", expanded=True):
        st.dataframe(pd.concat(match_reports, ignore_index=True), use_container_width=True, hide_index=True)

st.divider()
st.subheader("Manual Canonical Fields")
st.caption("These fields come from the selected methodology template and formula_library.csv.")

try:
    required_fields = _required_fields_for_methodology(methodology_id)
except Exception as exc:
    st.warning(f"Could not load required fields for {methodology_id}: {exc}")
    required_fields = pd.DataFrame(columns=["field_name", "used_by", "category"])

existing = st.session_state.get("issuer_data", {}) or {}
rows = []
for _, row in required_fields.iterrows():
    field_name = str(row["field_name"])
    rows.append(
        {
            "field_name": field_name,
            "value": existing.get(field_name, ""),
            "used_by": row.get("used_by", ""),
            "category": row.get("category", ""),
        }
    )

manual_df = pd.DataFrame(rows)
if not manual_df.empty:
    manual_df["value"] = manual_df["value"].apply(lambda x: "" if x is None else str(x))
if manual_df.empty:
    st.info("No formula-driven raw fields found for the selected methodology.")
    edited = manual_df
else:
    edited = st.data_editor(
        manual_df,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        column_config={
            "value": st.column_config.TextColumn("value"),
            "used_by": st.column_config.TextColumn("used_by", disabled=True),
            "category": st.column_config.TextColumn("category", disabled=True),
        },
    )

if st.button("Save issuer_data", type="primary"):
    manual_data = _clean_edited_values(edited)
    manual_candidates = manual_data_to_source_candidates(manual_data)
    required_field_names = required_fields["field_name"].dropna().astype(str).str.strip().tolist()
    sourcing_output = run_data_sourcing_pipeline(
        [*mapped_candidate_frames, manual_candidates],
        methodology_id=methodology_id,
        required_fields=required_field_names,
        source_priority_path="config/source_priority.csv",
    )
    st.session_state["issuer_data"] = sourcing_output["issuer_data"]
    st.session_state["source_report"] = sourcing_output["source_report"]
    st.session_state["source_candidates"] = sourcing_output["source_candidates"]
    st.session_state["source_readiness_summary"] = sourcing_output["source_readiness_summary"]
    st.session_state["source_match_reports"] = pd.concat(match_reports, ignore_index=True) if match_reports else pd.DataFrame()
    st.success(f"Saved {len(sourcing_output['issuer_data'])} canonical fields. Go to Calculators next.")

with st.expander("Source readiness summary", expanded=True):
    readiness = st.session_state.get("source_readiness_summary", pd.DataFrame())
    source_report = st.session_state.get("source_report", pd.DataFrame())
    if isinstance(readiness, pd.DataFrame) and not readiness.empty:
        st.dataframe(readiness, use_container_width=True, hide_index=True)
    else:
        st.info("No source selection has been saved yet.")
    if isinstance(source_report, pd.DataFrame) and not source_report.empty:
        st.dataframe(source_report, use_container_width=True, hide_index=True)

with st.expander("Current issuer_data", expanded=False):
    data = st.session_state.get("issuer_data", {}) or {}
    if data:
        st.json(data)
    else:
        st.info("No issuer_data saved yet.")
