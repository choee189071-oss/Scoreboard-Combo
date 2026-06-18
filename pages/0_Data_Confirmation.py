from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.data_confirmation import render_data_confirmation_workflow
from utils.ui_helpers import current_context_card, init_state, page_header


st.set_page_config(page_title="Review & Adjust", layout="wide")
init_state()

page_header(
    "Review & Adjust",
    "Investigate the current Workflow issuer_data, confirm evidence, and apply only approved replacements back to the same table.",
    "data_confirmation",
)
current_context_card()

st.info(
    "Workflow remains the source of truth. This page starts from the saved issuer_data table; evidence or manual changes "
    "only affect scoring after you save/apply them here and rerun formulas."
)
try:
    st.page_link("streamlit_app.py", label="Open Workflow")
except Exception:
    st.caption("Open Workflow from the sidebar when running this page outside the full Streamlit app.")

render_data_confirmation_workflow(st.session_state.get("methodology_id", "moodys_ccd_go"))
