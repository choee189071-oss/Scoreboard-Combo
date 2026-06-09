from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.data_confirmation import render_data_confirmation_workflow
from utils.ui_helpers import current_context_card, init_state, page_header


st.set_page_config(page_title="Data Confirmation", layout="wide")
init_state()

page_header(
    "Data Confirmation",
    "Decision queue for true rating blockers, ACFR/API evidence checks, AI extraction, and approved values.",
    "data_confirmation",
)
current_context_card()

st.info(
    "Start with Rating Readiness. Blocking Required fields affect scoring; Rating Inputs are evidence checks; "
    "Optional / Contextual rows do not block the current bond type."
)
try:
    st.page_link("streamlit_app.py", label="Open Workflow")
except Exception:
    st.caption("Open Workflow from the sidebar when running this page outside the full Streamlit app.")

render_data_confirmation_workflow(st.session_state.get("methodology_id", "moodys_ccd_go"))
