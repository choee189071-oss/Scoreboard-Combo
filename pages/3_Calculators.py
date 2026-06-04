from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from utils.ui_helpers import current_context_card, formula_action, init_state, page_header, readiness_action

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from engine.calculator_engine import calculate_all_formulas, summarize_calculation_results
    from engine.factor_engine import load_factor_template
except Exception as exc:  # pragma: no cover - Streamlit display path
    st.set_page_config(page_title="Calculators", layout="wide")
    st.error("Could not import calculator/factor engines.")
    st.exception(exc)
    st.stop()


st.set_page_config(page_title="Calculators", layout="wide")
init_state()
page_header(
    "Calculators",
    "Run formula_library.csv against canonical issuer_data and save formula_results for Scoreboard.",
    "calculators",
)
current_context_card()

issuer_data = st.session_state.get("issuer_data", {}) or {}
if not issuer_data:
    st.warning("No issuer_data found. Go to Data Mapping first.")
    st.stop()

methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")

try:
    all_formula_df = calculate_all_formulas(issuer_data, formula_library="config/formula_library.csv")
    template = load_factor_template(methodology_id, templates_dir="templates")
except Exception as exc:
    st.error("Formula calculation failed.")
    st.exception(exc)
    st.stop()

template_formula_ids = set(template["formula_id"].dropna().astype(str))
try:
    thresholds = pd.read_csv("config/scoring_thresholds.csv")
    related_thresholds = thresholds[thresholds["methodology_id"].astype(str) == str(methodology_id)]
    secondary_ids = set(related_thresholds["secondary_formula_id"].dropna().astype(str))
    secondary_ids.discard("")
    secondary_ids.discard("nan")
    template_formula_ids |= secondary_ids
except Exception:
    pass
method_formula_df = all_formula_df[all_formula_df["formula_id"].astype(str).isin(template_formula_ids)].copy()

summary = summarize_calculation_results(method_formula_df)
cols = st.columns(4)
cols[0].metric("Ready", summary.get("ready", 0))
cols[1].metric("Missing", summary.get("missing", 0))
cols[2].metric("Manual", summary.get("manual", 0))
cols[3].metric("Error", summary.get("error", 0))
formula_action(summary)

source_report = st.session_state.get("source_report")
if source_report is not None:
    with st.expander("Source coverage feeding these formulas", expanded=False):
        readiness_action(source_report)

st.subheader("Methodology Formula Results")
st.caption("Filtered to formula_id values used by the selected template and related threshold logic.")
display_cols = [
    "formula_id",
    "formula_name",
    "category",
    "status",
    "value",
    "missing_fields",
    "warning",
    "error",
]
available_cols = [c for c in display_cols if c in method_formula_df.columns]
missing_df = method_formula_df[method_formula_df["status"] != "ready"].copy()
ready_df = method_formula_df[method_formula_df["status"] == "ready"].copy()
tab1, tab2, tab3 = st.tabs(["Ready", "Missing / Manual / Error", "All Methodology Formulas"])
with tab1:
    st.dataframe(ready_df[available_cols], width="stretch", hide_index=True) if not ready_df.empty else st.info("No ready formulas yet.")
with tab2:
    st.dataframe(missing_df[available_cols], width="stretch", hide_index=True) if not missing_df.empty else st.info("No missing, manual, or error formulas.")
with tab3:
    st.dataframe(method_formula_df[available_cols], width="stretch", hide_index=True)

with st.expander("All formula_library results", expanded=False):
    st.dataframe(all_formula_df, width="stretch", hide_index=True)

if st.button("Save formula results", type="primary"):
    st.session_state["formula_results"] = all_formula_df
    st.session_state["methodology_formula_results"] = method_formula_df
    st.success(f"Saved {len(all_formula_df)} formula results. Go to Scoreboard next.")

st.download_button(
    "Download methodology_formula_results.csv",
    method_formula_df.to_csv(index=False).encode("utf-8"),
    "methodology_formula_results.csv",
    "text/csv",
)
