from __future__ import annotations

import streamlit as st
from utils.ui_helpers import page_header, SCHEME_OPTIONS, init_state

st.set_page_config(page_title="Deal Setup", page_icon="①", layout="wide")
init_state()
page_header("① Deal Setup", "Choose the methodology, issuer, and analysis year. This becomes the shared context for the rest of the app.", "deal_setup")

left, right = st.columns([1.2, 1])
with left:
    st.subheader("Deal context")
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

with right:
    st.subheader("Workflow rule")
    st.markdown(
        """
        The app should always move in this order:

        **Deal Setup → Data Mapping → Calculators → Scoreboard → Validation → Export**

        You can still jump around, but downstream pages will show missing inputs clearly.
        """
    )
    st.warning("Do not polish UI heavily yet. First goal: make the full model path visible.")

