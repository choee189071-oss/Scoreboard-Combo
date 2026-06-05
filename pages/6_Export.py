from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from utils.ui_helpers import action_panel, clean_for_display, current_context_card, init_state, page_header

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from engine.rating_engine import summarize_rating_output
except Exception as exc:  # pragma: no cover - Streamlit display path
    st.set_page_config(page_title="Export", layout="wide")
    st.error("Could not import export helpers.")
    st.exception(exc)
    st.stop()


st.set_page_config(page_title="Export", layout="wide")
init_state()
page_header(
    "Export",
    "Download the current source, formula, and rating artifacts from this session.",
    "export",
)
current_context_card()

issuer_data = st.session_state.get("issuer_data", {}) or {}
source_report = st.session_state.get("source_report", pd.DataFrame())
formula_results = st.session_state.get("formula_results", pd.DataFrame())
rating_output = st.session_state.get("rating_output")

cols = st.columns(4)
cols[0].metric("Issuer Fields", len(issuer_data))
cols[1].metric("Source Rows", len(source_report) if isinstance(source_report, pd.DataFrame) else 0)
cols[2].metric("Formula Rows", len(formula_results) if isinstance(formula_results, pd.DataFrame) else 0)
cols[3].metric("Rating Run", "Available" if isinstance(rating_output, dict) else "Not run")

if not issuer_data and not isinstance(rating_output, dict):
    action_panel(
        "Nothing to export yet",
        "Run Data Mapping, Calculators, and Scoreboard before downloading session artifacts.",
        "warn",
    )
else:
    action_panel(
        "Session artifacts are ready",
        "Download these files for review, regression notes, or handoff to the next workflow step.",
        "good",
    )

st.subheader("Downloads")
download_cols = st.columns(2)

with download_cols[0]:
    st.write("Source and raw data")
    if issuer_data:
        issuer_df = pd.DataFrame(
            [{"field_name": key, "value": value} for key, value in sorted(issuer_data.items())]
        )
        st.download_button(
            "Download issuer_data.csv",
            data=issuer_df.to_csv(index=False).encode("utf-8"),
            file_name="issuer_data.csv",
            mime="text/csv",
        )
        st.download_button(
            "Download issuer_data.json",
            data=json.dumps(issuer_data, indent=2, default=str).encode("utf-8"),
            file_name="issuer_data.json",
            mime="application/json",
        )
    else:
        st.info("No issuer_data saved.")

    if isinstance(source_report, pd.DataFrame) and not source_report.empty:
        st.download_button(
            "Download source_report.csv",
            data=source_report.to_csv(index=False).encode("utf-8"),
            file_name="source_report.csv",
            mime="text/csv",
        )

with download_cols[1]:
    st.write("Model outputs")
    if isinstance(formula_results, pd.DataFrame) and not formula_results.empty:
        st.download_button(
            "Download formula_results.csv",
            data=formula_results.to_csv(index=False).encode("utf-8"),
            file_name="formula_results.csv",
            mime="text/csv",
        )
    else:
        st.info("No formula_results saved.")

    if isinstance(rating_output, dict):
        rating_summary = summarize_rating_output(rating_output)
        st.download_button(
            "Download rating_summary.csv",
            data=rating_summary.to_csv(index=False).encode("utf-8"),
            file_name="rating_summary.csv",
            mime="text/csv",
        )
        st.download_button(
            "Download rating_output.json",
            data=json.dumps(rating_output, default=str, indent=2).encode("utf-8"),
            file_name="rating_output.json",
            mime="application/json",
        )
    else:
        st.info("No rating_output saved.")

with st.expander("Preview current session artifacts", expanded=False):
    if issuer_data:
        st.write("issuer_data")
        st.json(issuer_data)
    if isinstance(source_report, pd.DataFrame) and not source_report.empty:
        st.write("source_report")
        st.dataframe(clean_for_display(source_report), width="stretch", hide_index=True)
    if isinstance(formula_results, pd.DataFrame) and not formula_results.empty:
        st.write("formula_results")
        st.dataframe(clean_for_display(formula_results), width="stretch", hide_index=True)
