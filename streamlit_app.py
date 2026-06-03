from __future__ import annotations

import streamlit as st
from utils.ui_helpers import init_state, inject_css, render_workflow, SCHEME_OPTIONS

st.set_page_config(page_title="CreditScope MVP", page_icon="🏛️", layout="wide")
init_state()
inject_css()

st.title("🏛️ CreditScope Scoreboard MVP")
st.caption("A clean workflow for testing raw data → mapping → formulas → factor scores → indicative rating.")
render_workflow(active="deal_setup")

c1, c2, c3 = st.columns(3)
c1.metric("Current issuer", st.session_state.get("issuer_name") or "Not set")
c2.metric("Methodology", SCHEME_OPTIONS.get(st.session_state.get("methodology_id"), "Not set"))
c3.metric("Analysis year", st.session_state.get("analysis_year") or "Not set")

st.markdown("### What to do next")
st.markdown(
    """
    1. Open **Deal Setup** and set methodology / issuer / year.  
    2. Open **Data Mapping** and upload source files or enter key raw fields.  
    3. Open **Calculators** to produce formula results.  
    4. Open **Scoreboard** to generate factor scores and rating.  
    5. Use **Validation** to compare against the official scorecard.
    """
)

st.info("This UI is intentionally simple: the goal is to visualize the full workflow before polishing the product.")
