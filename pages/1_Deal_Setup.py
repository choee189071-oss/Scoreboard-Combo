from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.ui_helpers import SCHEME_OPTIONS, action_panel, current_context_card, init_state, page_header

st.set_page_config(page_title="Deal Setup", layout="wide")
init_state()
page_header(
    "Deal Setup",
    "Choose the methodology, issuer, and analysis year that every downstream page should use.",
    "deal_setup",
)
current_context_card()

left, right = st.columns([1.2, 1])
with left:
    st.subheader("Deal Context")
    method_ids = list(SCHEME_OPTIONS.keys())
    current_method = st.session_state.get("methodology_id", method_ids[0])
    methodology_id = st.selectbox(
        "Methodology / scheme",
        method_ids,
        index=method_ids.index(current_method) if current_method in method_ids else 0,
        format_func=lambda x: SCHEME_OPTIONS.get(x, x),
    )
    issuer_name = st.text_input("Issuer name", value=st.session_state.get("issuer_name", ""), placeholder="e.g., Contra Costa CCD")
    analysis_year = st.text_input("Analysis year / fiscal year", value=str(st.session_state.get("analysis_year", "2023")))

    years = st.multiselect(
        "Years to include for trend formulas",
        ["Current", "1Y prior", "2Y prior", "3Y prior", "4Y prior", "5Y prior"],
        default=["Current", "1Y prior", "2Y prior", "3Y prior"],
    )

    if st.button("Save deal setup", type="primary"):
        st.session_state["methodology_id"] = methodology_id
        st.session_state["issuer_name"] = issuer_name.strip()
        st.session_state["analysis_year"] = analysis_year.strip()
        st.session_state["analysis_years_included"] = years
        st.success("Deal setup saved. Go to Data Mapping next.")
        action_panel(
            "Next step: Data Mapping",
            "Upload the raw workbook or fetch API candidates for this issuer before running formulas.",
            "good",
        )

with right:
    st.subheader("Workflow Guardrails")
    action_panel(
        "Keep one issuer context per run",
        "If you switch issuer, methodology, or source file, reset the source session in Data Mapping before saving new issuer_data.",
        "warn",
    )
    st.write("Recommended order: Deal Setup, Data Mapping, Calculators, Scoreboard, Validation, Export.")
