from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from utils.ui_helpers import current_context_card, init_state, page_header

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
    from engine.rating_engine import summarize_rating_output
except Exception as exc:  # pragma: no cover - Streamlit display path
    st.set_page_config(page_title="Methodology Audit", page_icon="⑦", layout="wide")
    st.error("Could not import methodology audit tools.")
    st.exception(exc)
    st.stop()


st.set_page_config(page_title="Methodology Audit", page_icon="⑦", layout="wide")
init_state()
page_header(
    "⑦ Methodology Audit",
    "Verify formulas, thresholds, scoring, and rating aggregation before source loaders are complete.",
    "methodology_audit",
)
current_context_card()

st.subheader("Five-methodology structural audit")
summary_df = audit_all_methodologies(AUDIT_METHODOLOGIES)
st.dataframe(summary_df, use_container_width=True, hide_index=True)

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
    st.dataframe(audit_df[[c for c in show_cols if c in audit_df.columns]], use_container_width=True, hide_index=True)

st.subheader("Editable test inputs")
st.caption("These are baseline raw inputs for formula testing. Replace them with official/workbook values as you validate each methodology.")

existing_data = st.session_state.get("issuer_data", {}) or {}
default_input_df = issuer_data_editor_frame(methodology_id, existing_data=existing_data)

edited_inputs = st.data_editor(
    default_input_df,
    use_container_width=True,
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
manual_df = manual_score_frame(methodology_id)
if manual_df.empty:
    st.info("No manual qualitative formulas in this template.")
    edited_manual = manual_df
else:
    edited_manual = st.data_editor(
        manual_df,
        use_container_width=True,
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
        st.dataframe(summarize_rating_output(rating_output), use_container_width=True, hide_index=True)
        warnings = rr.get("warnings", []) or []
        if warnings:
            for warning in warnings:
                st.warning(str(warning))

    with tabs[1]:
        df = result.get("method_formula_results", pd.DataFrame())
        display_cols = ["formula_id", "formula_name", "category", "status", "value", "missing_fields", "warning", "error"]
        if isinstance(df, pd.DataFrame) and not df.empty:
            st.dataframe(df[[c for c in display_cols if c in df.columns]], use_container_width=True, hide_index=True)
        else:
            st.info("No formula results.")

    fe = rating_output.get("factor_engine_output", {}) if isinstance(rating_output, dict) else {}
    with tabs[2]:
        df = fe.get("metric_scores", pd.DataFrame()) if isinstance(fe, dict) else pd.DataFrame()
        st.dataframe(df, use_container_width=True, hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No metric scores.")
    with tabs[3]:
        df = fe.get("factor_scores", pd.DataFrame()) if isinstance(fe, dict) else pd.DataFrame()
        st.dataframe(df, use_container_width=True, hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No factor scores.")
    with tabs[4]:
        df = fe.get("section_scores", pd.DataFrame()) if isinstance(fe, dict) else pd.DataFrame()
        st.dataframe(df, use_container_width=True, hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No section scores.")
    with tabs[5]:
        auto_scores = rating_output.get("scored_metric_overrides", {}) if isinstance(rating_output, dict) else {}
        if auto_scores:
            auto_df = pd.DataFrame.from_dict(auto_scores, orient="index")
            auto_df.insert(0, "formula_id", auto_df.index)
            st.dataframe(auto_df.reset_index(drop=True), use_container_width=True, hide_index=True)
        else:
            st.info("No automatic threshold scores were produced.")

    st.download_button(
        "Download audit_formula_results.csv",
        result["method_formula_results"].to_csv(index=False).encode("utf-8"),
        "audit_formula_results.csv",
        "text/csv",
    )
