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
    "Operational validation workflow for missing fields, evidence entry, confirmed values, and clean rating-engine inputs.",
    "data_confirmation",
)
current_context_card()

st.info("Use this before scoring: resolve Blocking Required fields first. Validation Support fields are for ACFR/API/workbook double-check and do not block rating readiness.")
st.page_link("streamlit_app.py", label="Open Workflow")

render_data_confirmation_workflow(st.session_state.get("methodology_id", "moodys_ccd_go"))
