from __future__ import annotations

import pandas as pd
import streamlit as st

from utils.ui_helpers import (
    SCHEME_OPTIONS,
    action_panel,
    current_context_card,
    init_state,
    page_header,
    source_readiness_counts,
    status_counts,
)

st.set_page_config(page_title="Scoreboard Combo", layout="wide")
init_state()

page_header(
    "Workflow Console",
    "A focused workspace for sourcing raw issuer data, calculating methodology formulas, and producing an indicative rating.",
    "deal_setup",
)
current_context_card()

source_report = st.session_state.get("source_report")
formula_results = st.session_state.get("methodology_formula_results")
if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
    formula_results = st.session_state.get("formula_results")
rating_output = st.session_state.get("rating_output")

source_counts = source_readiness_counts(source_report)
formula_counts = status_counts(formula_results, "status")
rating_result = rating_output.get("rating_result", {}) if isinstance(rating_output, dict) else {}

st.subheader("Run Status")
status_cols = st.columns(4)
status_cols[0].metric("Source Ready", source_counts.get("independent_ready", 0))
status_cols[1].metric(
    "Source Gaps",
    source_counts.get("missing", 0) + source_counts.get("source_pending", 0) + source_counts.get("needs_review", 0),
)
status_cols[2].metric("Formula Ready", formula_counts.get("ready", 0))
status_cols[3].metric("Rating", rating_result.get("indicative_rating") or "Not run")

if not source_counts:
    action_panel(
        "Next step: set up data sources",
        "Open Deal Setup to confirm the issuer and methodology, then use Data Mapping to upload CreditScope or fetch API candidates.",
        "warn",
    )
elif source_counts.get("missing", 0):
    action_panel(
        "Next step: close source gaps",
        "Data Mapping has saved issuer_data, but required raw fields are still missing. Review the missing list before trusting downstream scores.",
        "bad",
    )
elif not formula_counts:
    action_panel(
        "Next step: run Calculators",
        "The source layer has data. Run the methodology formulas and save formula_results for the Scoreboard.",
        "good",
    )
elif formula_counts.get("missing", 0) or formula_counts.get("error", 0):
    action_panel(
        "Next step: fix formula inputs",
        "Some formulas still need raw fields or returned errors. Use Calculators to identify the exact missing fields.",
        "warn",
    )
elif not rating_result:
    action_panel(
        "Next step: run Scoreboard",
        "Formula results are available. Enter any true qualitative scores and run the rating engine.",
        "good",
    )
else:
    action_panel(
        "Workflow run is available",
        "Use Scoreboard for factor detail, Validation for fixture comparison, or Export for deliverables.",
        "good",
    )

st.subheader("Main Workflow")
st.caption("Most users should move left to right through these four steps. Validation and Audit are developer checks, not required for a normal run.")
workflow_cols = st.columns(4)
with workflow_cols[0]:
    st.markdown("**1. Deal Setup**")
    st.caption("Pick methodology, issuer, and year.")
    st.page_link("pages/1_Deal_Setup.py", label="Open Deal Setup")
with workflow_cols[1]:
    st.markdown("**2. Data Mapping**")
    st.caption("Upload sources, fetch APIs, and fill manual gaps.")
    st.page_link("pages/2_Data_Mapping.py", label="Open Data Mapping")
with workflow_cols[2]:
    st.markdown("**3. Calculators**")
    st.caption("Run formulas and patch missing raw inputs.")
    st.page_link("pages/3_Calculators.py", label="Open Calculators")
with workflow_cols[3]:
    st.markdown("**4. Scoreboard**")
    st.caption("Run scoring and review factor output.")
    st.page_link("pages/4_Scoreboard.py", label="Open Scoreboard")

with st.expander("Developer validation tools", expanded=False):
    dev_cols = st.columns(3)
    with dev_cols[0]:
        st.page_link("pages/5_Validation.py", label="Validation")
        st.caption("Official fixture and raw-value comparisons.")
    with dev_cols[1]:
        st.page_link("pages/7_Methodology_Audit.py", label="Methodology Audit")
        st.caption("Template, formula, threshold, and source coverage checks.")
    with dev_cols[2]:
        st.page_link("pages/6_Export.py", label="Export")
        st.caption("Download reports and model outputs.")

st.subheader("Current Deal")
deal_cols = st.columns(3)
deal_cols[0].metric("Issuer", st.session_state.get("issuer_name") or "Not set")
deal_cols[1].metric("Methodology", SCHEME_OPTIONS.get(st.session_state.get("methodology_id"), "Not set"))
deal_cols[2].metric("Analysis Year", st.session_state.get("analysis_year") or "Not set")

with st.expander("Session details", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.write("Source readiness")
        if source_counts:
            st.dataframe(
                pd.DataFrame(
                    [{"readiness_status": key, "field_count": value} for key, value in source_counts.items()]
                ),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("No source_report saved yet.")
    with c2:
        st.write("Formula status")
        if formula_counts:
            st.dataframe(
                pd.DataFrame([{"status": key, "formula_count": value} for key, value in formula_counts.items()]),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("No formula_results saved yet.")
