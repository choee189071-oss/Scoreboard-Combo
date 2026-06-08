from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.methodology_audit import AUDIT_METHODOLOGIES, audit_all_methodologies, build_methodology_audit
from engine.rating_audit import (
    audit_metric_csv,
    audit_pdf_bytes,
    audit_trail_to_json,
    audit_trail_to_markdown,
    build_rating_audit_trail,
)
from engine.rating_engine import summarize_rating_output
from utils.data_confirmation import data_confirmation_export
from utils.ui_helpers import clean_for_display, current_context_card, init_state, page_header


def _download_dataframe(label: str, df: pd.DataFrame, file_name: str) -> None:
    if isinstance(df, pd.DataFrame) and not df.empty:
        st.download_button(
            label,
            data=df.to_csv(index=False).encode("utf-8"),
            file_name=file_name,
            mime="text/csv",
        )
    else:
        st.info(f"No {file_name} data available.")


st.set_page_config(page_title="Developer Tools", layout="wide")
init_state()
page_header(
    "Developer Tools",
    "Validation, methodology audit, and export utilities for model QA. Normal users can stay on Workflow.",
    "developer_tools",
)
current_context_card()

methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")
issuer_data = st.session_state.get("issuer_data", {}) or {}
source_report = st.session_state.get("source_report", pd.DataFrame())
formula_results = st.session_state.get("formula_results", pd.DataFrame())
rating_output = st.session_state.get("rating_output")

tabs = st.tabs(["Methodology Audit", "Session Export", "Data Confirmation", "Session State"])

with tabs[0]:
    st.subheader("Structural Audit")
    summary_df = audit_all_methodologies(AUDIT_METHODOLOGIES)
    st.dataframe(clean_for_display(summary_df), width="stretch", hide_index=True)

    selected_methodology = st.selectbox(
        "Methodology",
        AUDIT_METHODOLOGIES,
        index=AUDIT_METHODOLOGIES.index(methodology_id) if methodology_id in AUDIT_METHODOLOGIES else 0,
    )
    audit_df = build_methodology_audit(selected_methodology)
    metric_cols = st.columns(5)
    metric_cols[0].metric("Formula IDs", len(audit_df))
    metric_cols[1].metric("Template", int(audit_df["in_template"].sum()) if not audit_df.empty else 0)
    metric_cols[2].metric("Secondary", int(audit_df["secondary_only"].sum()) if not audit_df.empty else 0)
    metric_cols[3].metric("Missing Formulas", int((~audit_df["formula_exists"]).sum()) if not audit_df.empty else 0)
    metric_cols[4].metric(
        "Missing Thresholds",
        int((audit_df["scoring_required"] & ~audit_df["threshold_exists"]).sum()) if not audit_df.empty else 0,
    )

    show_cols = [
        "structural_status",
        "formula_id",
        "in_template",
        "secondary_only",
        "formula_exists",
        "scoring_required",
        "threshold_exists",
        "manual_formula",
        "required_fields",
        "likely_data_sources",
        "section",
        "factor",
        "metric",
    ]
    st.dataframe(
        clean_for_display(audit_df[[col for col in show_cols if col in audit_df.columns]]),
        width="stretch",
        hide_index=True,
    )
    _download_dataframe("Download methodology_audit.csv", audit_df, "methodology_audit.csv")

with tabs[1]:
    st.subheader("Exports")
    cols = st.columns(4)
    cols[0].metric("Issuer Fields", len(issuer_data))
    cols[1].metric("Source Rows", len(source_report) if isinstance(source_report, pd.DataFrame) else 0)
    cols[2].metric("Formula Rows", len(formula_results) if isinstance(formula_results, pd.DataFrame) else 0)
    cols[3].metric("Rating Run", "Available" if isinstance(rating_output, dict) else "Not run")

    dl_cols = st.columns(2)
    with dl_cols[0]:
        st.write("Source data")
        if issuer_data:
            issuer_df = pd.DataFrame([{"field_name": key, "value": value} for key, value in sorted(issuer_data.items())])
            _download_dataframe("Download issuer_data.csv", issuer_df, "issuer_data.csv")
            st.download_button(
                "Download issuer_data.json",
                data=json.dumps(issuer_data, indent=2, default=str).encode("utf-8"),
                file_name="issuer_data.json",
                mime="application/json",
            )
        else:
            st.info("No issuer_data saved.")
        _download_dataframe("Download source_report.csv", source_report, "source_report.csv")

    with dl_cols[1]:
        st.write("Model outputs")
        _download_dataframe("Download formula_results.csv", formula_results, "formula_results.csv")
        if isinstance(rating_output, dict):
            rating_summary = summarize_rating_output(rating_output)
            _download_dataframe("Download rating_summary.csv", rating_summary, "rating_summary.csv")
            audit = build_rating_audit_trail(
                methodology_id=methodology_id,
                rating_output=rating_output,
                formula_results=formula_results,
                source_report=source_report,
                issuer_data=issuer_data,
                manual_scores=st.session_state.get("manual_scores", {}) or {},
            )
            audit_markdown = audit_trail_to_markdown(audit, title="CreditScope Rating Audit Trail")
            st.download_button(
                "Download rating_audit_trail.csv",
                data=audit_metric_csv(audit),
                file_name="rating_audit_trail.csv",
                mime="text/csv",
            )
            st.download_button(
                "Download rating_audit_trail.json",
                data=audit_trail_to_json(audit).encode("utf-8"),
                file_name="rating_audit_trail.json",
                mime="application/json",
            )
            st.download_button(
                "Download rating_report.md",
                data=audit_markdown.encode("utf-8"),
                file_name="rating_report.md",
                mime="text/markdown",
            )
            st.download_button(
                "Download rating_audit_report.pdf",
                data=audit_pdf_bytes(audit_markdown),
                file_name="rating_audit_report.pdf",
                mime="application/pdf",
            )
            st.download_button(
                "Download presentation_audit_outline.md",
                data=audit_markdown.encode("utf-8"),
                file_name="presentation_audit_outline.md",
                mime="text/markdown",
            )
            st.download_button(
                "Download rating_output.json",
                data=json.dumps(rating_output, default=str, indent=2).encode("utf-8"),
                file_name="rating_output.json",
                mime="application/json",
            )
        else:
            st.info("No rating_output saved.")

with tabs[2]:
    st.subheader("Data Confirmation Plan")
    plan_df = data_confirmation_export()
    st.dataframe(clean_for_display(plan_df), width="stretch", hide_index=True)
    _download_dataframe("Download data_confirmation_plan.csv", plan_df, "data_confirmation_plan.csv")

with tabs[3]:
    st.subheader("Current Session Preview")
    if issuer_data:
        with st.expander("issuer_data", expanded=False):
            st.json(issuer_data)
    if isinstance(source_report, pd.DataFrame) and not source_report.empty:
        with st.expander("source_report", expanded=True):
            st.dataframe(clean_for_display(source_report), width="stretch", hide_index=True)
    if isinstance(formula_results, pd.DataFrame) and not formula_results.empty:
        with st.expander("formula_results", expanded=False):
            st.dataframe(clean_for_display(formula_results), width="stretch", hide_index=True)
    if isinstance(rating_output, dict):
        with st.expander("rating_output", expanded=False):
            st.json(rating_output)
