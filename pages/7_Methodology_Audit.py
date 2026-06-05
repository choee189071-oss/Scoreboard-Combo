from __future__ import annotations

import sys
from pathlib import Path
import re

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.ui_helpers import clean_for_display, current_context_card, init_state, page_header

try:
    from engine.methodology_audit import (
        AUDIT_METHODOLOGIES,
        audit_all_methodologies,
        build_methodology_audit,
        frame_to_issuer_data,
        frame_to_manual_scores,
        issuer_data_editor_frame,
        manual_score_frame,
        run_methodology_test,
    )
except Exception as exc:  # pragma: no cover - Streamlit display path
    st.set_page_config(page_title="Methodology Audit", layout="wide")
    st.error("Could not import methodology audit tools.")
    st.exception(exc)
    st.stop()


def _clean_display_df(df: pd.DataFrame) -> pd.DataFrame:
    out = clean_for_display(df)
    for col in ["score_label", "warning", "error", "missing_fields"]:
        if col in out.columns:
            out[col] = out[col].replace({"nan": "", "None": "", "NaN": ""}).fillna("")
    return out


def _show_df_or_info(df: pd.DataFrame, empty_message: str) -> None:
    if isinstance(df, pd.DataFrame) and not df.empty:
        st.dataframe(_clean_display_df(df), width="stretch", hide_index=True)
    else:
        st.info(empty_message)


def _slug(value: str) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def _rating_summary_for_methodology(rating_output: dict) -> pd.DataFrame:
    rr = rating_output.get("rating_result", {}) if isinstance(rating_output, dict) else {}
    fe = rating_output.get("factor_engine_output", {}) if isinstance(rating_output, dict) else {}
    methodology_id = str(rr.get("methodology_id", ""))

    row = {
        "methodology_id": rr.get("methodology_id"),
        "agency": rr.get("agency"),
        "rating_style": rr.get("rating_style"),
        "overall_score": rr.get("overall_score"),
        "anchor": rr.get("anchor"),
        "sacp": rr.get("sacp"),
        "icr": rr.get("icr"),
        "indicative_rating": rr.get("indicative_rating"),
        "coverage_status": rr.get("coverage_status"),
    }

    if methodology_id in {"sp_water_sewer", "sp_community_college_go"}:
        row["enterprise_score"] = rr.get("enterprise_score")
        row["financial_score"] = rr.get("financial_score")
    elif methodology_id in {"sp_local_gov_k12", "sp_local_gov", "sp_us_government_2024"}:
        row["icp_score"] = rr.get("icp_score")
        row["institutional_framework_score"] = rr.get("institutional_framework_score")
    elif methodology_id.startswith("moodys"):
        factor_df = fe.get("factor_scores", pd.DataFrame()) if isinstance(fe, dict) else pd.DataFrame()
        if isinstance(factor_df, pd.DataFrame) and not factor_df.empty:
            for _, factor_row in factor_df.iterrows():
                factor = str(factor_row.get("factor", "")).strip()
                if factor:
                    row[f"{_slug(factor)}_score"] = factor_row.get("factor_score")

    return _clean_display_df(pd.DataFrame([row]))


st.set_page_config(page_title="Methodology Audit", layout="wide")
init_state()
page_header(
    "Methodology Audit",
    "Verify formulas, thresholds, scoring, and rating aggregation before source loaders are complete.",
    "methodology_audit",
)
current_context_card()

st.subheader("Five-methodology structural audit")
summary_df = audit_all_methodologies(AUDIT_METHODOLOGIES)
st.dataframe(_clean_display_df(summary_df), width="stretch", hide_index=True)

methodology_id = st.selectbox(
    "Methodology",
    AUDIT_METHODOLOGIES,
    index=AUDIT_METHODOLOGIES.index(st.session_state.get("methodology_id", "moodys_ccd_go"))
    if st.session_state.get("methodology_id", "moodys_ccd_go") in AUDIT_METHODOLOGIES
    else 0,
)
st.session_state["methodology_id"] = methodology_id

st.divider()
audit_df = build_methodology_audit(methodology_id)

cols = st.columns(5)
cols[0].metric("Formula IDs", len(audit_df))
cols[1].metric("Template formulas", int(audit_df["in_template"].sum()) if not audit_df.empty else 0)
cols[2].metric("Secondary formulas", int(audit_df["secondary_only"].sum()) if not audit_df.empty else 0)
cols[3].metric("Missing formulas", int((~audit_df["formula_exists"]).sum()) if not audit_df.empty else 0)
missing_thresholds = int((audit_df["scoring_required"] & ~audit_df["threshold_exists"]).sum()) if not audit_df.empty else 0
cols[4].metric("Missing thresholds", missing_thresholds)

with st.expander("Formula / threshold / source audit", expanded=True):
    show_cols = [
        "structural_status",
        "formula_id",
        "in_template",
        "threshold_only",
        "secondary_only",
        "formula_exists",
        "scoring_required",
        "threshold_exists",
        "threshold_rule_types",
        "manual_formula",
        "required_fields",
        "likely_data_sources",
        "section",
        "factor",
        "metric",
        "expression",
    ]
    st.dataframe(_clean_display_df(audit_df[[c for c in show_cols if c in audit_df.columns]]), width="stretch", hide_index=True)

st.subheader("Editable test inputs")
st.caption("These are baseline raw inputs for formula testing. Replace them with official/workbook values as you validate each methodology.")

existing_data = st.session_state.get("issuer_data", {}) or {}
default_input_df = issuer_data_editor_frame(methodology_id, existing_data=existing_data)
if "value" in default_input_df.columns:
    default_input_df["value"] = pd.to_numeric(default_input_df["value"], errors="coerce")

edited_inputs = st.data_editor(
    default_input_df,
    width="stretch",
    hide_index=True,
    num_rows="fixed",
    key=f"audit_inputs_{methodology_id}",
    column_config={
        "field_name": st.column_config.TextColumn("field_name", disabled=True),
        "value": st.column_config.NumberColumn("value", step=0.01, format="%.6f"),
        "likely_data_source": st.column_config.TextColumn("likely_data_source", disabled=True),
        "notes": st.column_config.TextColumn("notes"),
    },
)

st.subheader("Manual qualitative scores")
st.caption(
    "Default numeric scores are baseline placeholders so the audit can run end-to-end. "
    "Replace them with analyst or official scorecard values for validation."
)
manual_df = manual_score_frame(methodology_id)
if manual_df.empty:
    st.info("No manual qualitative formulas in this template.")
    edited_manual = manual_df
else:
    edited_manual = st.data_editor(
        manual_df,
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        key=f"audit_manual_{methodology_id}",
        column_config={
            "formula_id": st.column_config.TextColumn("formula_id", disabled=True),
            "section": st.column_config.TextColumn("section", disabled=True),
            "factor": st.column_config.TextColumn("factor", disabled=True),
            "metric": st.column_config.TextColumn("metric", disabled=True),
            "numeric_score": st.column_config.NumberColumn("numeric_score", min_value=1.0, max_value=21.0, step=0.5),
            "score_label": st.column_config.TextColumn("score_label"),
            "score_source": st.column_config.TextColumn("score_source", disabled=True),
            "notes": st.column_config.TextColumn("notes", disabled=True),
        },
    )

run_col, save_col = st.columns([1, 1])
run_clicked = run_col.button("Run methodology test", type="primary")
save_clicked = save_col.button("Save test data to session")

issuer_data = frame_to_issuer_data(edited_inputs)
manual_scores = frame_to_manual_scores(edited_manual)

if save_clicked:
    st.session_state["issuer_data"] = issuer_data
    st.success(f"Saved {len(issuer_data)} raw fields to session issuer_data.")

if run_clicked:
    try:
        result = run_methodology_test(
            methodology_id=methodology_id,
            issuer_data=issuer_data,
            manual_scores=manual_scores,
        )
        st.session_state["methodology_audit_result"] = result
        st.session_state["formula_results"] = result["formula_results"]
        st.session_state["methodology_formula_results"] = result["method_formula_results"]
        st.session_state["rating_output"] = result["rating_output"]
    except Exception as exc:
        st.error("Methodology test failed.")
        st.exception(exc)
        st.stop()

result = st.session_state.get("methodology_audit_result")
if isinstance(result, dict) and result.get("methodology_id") == methodology_id:
    st.divider()
    st.subheader("Run output")
    rating_output = result.get("rating_output", {})
    rr = rating_output.get("rating_result", {}) if isinstance(rating_output, dict) else {}

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Indicative Rating", rr.get("indicative_rating", "") or "Missing")
    c2.metric("Weighted Score", "" if rr.get("overall_score") is None else f"{float(rr['overall_score']):.3f}")
    c3.metric("Coverage", rr.get("coverage_status", "unknown"))
    c4.metric("Warnings", len(rr.get("warnings", []) or []))

    tabs = st.tabs(["Rating", "Formula Results", "Metric Scores", "Factor Scores", "Section Scores", "Auto Scores"])

    with tabs[0]:
        st.dataframe(_rating_summary_for_methodology(rating_output), width="stretch", hide_index=True)
        warnings = rr.get("warnings", []) or []
        if warnings:
            for warning in warnings:
                st.warning(str(warning))

    with tabs[1]:
        df = result.get("method_formula_results", pd.DataFrame())
        display_cols = ["formula_id", "formula_name", "category", "status", "value", "missing_fields", "warning", "error"]
        if isinstance(df, pd.DataFrame) and not df.empty:
            st.dataframe(_clean_display_df(df[[c for c in display_cols if c in df.columns]]), width="stretch", hide_index=True)
        else:
            st.info("No formula results.")

    fe = rating_output.get("factor_engine_output", {}) if isinstance(rating_output, dict) else {}
    with tabs[2]:
        df = fe.get("metric_scores", pd.DataFrame()) if isinstance(fe, dict) else pd.DataFrame()
        _show_df_or_info(df, "No metric scores.")
    with tabs[3]:
        df = fe.get("factor_scores", pd.DataFrame()) if isinstance(fe, dict) else pd.DataFrame()
        _show_df_or_info(df, "No factor scores.")
    with tabs[4]:
        df = fe.get("section_scores", pd.DataFrame()) if isinstance(fe, dict) else pd.DataFrame()
        _show_df_or_info(df, "No section scores.")
    with tabs[5]:
        auto_scores = rating_output.get("scored_metric_overrides", {}) if isinstance(rating_output, dict) else {}
        if auto_scores:
            auto_df = pd.DataFrame.from_dict(auto_scores, orient="index")
            auto_df.insert(0, "formula_id", auto_df.index)
            st.dataframe(_clean_display_df(auto_df.reset_index(drop=True)), width="stretch", hide_index=True)
        else:
            st.info("No automatic threshold scores were produced.")

    st.download_button(
        "Download audit_formula_results.csv",
        result["method_formula_results"].to_csv(index=False).encode("utf-8"),
        "audit_formula_results.csv",
        "text/csv",
    )
