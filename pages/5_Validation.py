"""
Validation Workspace for Scoreboard-Combo / CreditScope MVP
===========================================================

Purpose
-------
This page is the first end-to-end QA workspace for the rating model.
It lets you validate whether the model output matches a benchmark scorecard.

Supported validation modes
--------------------------
1. Quick rating check
   - Moody's: weighted score -> rating
   - S&P Local Gov: IF + ICP -> anchor/rating
   - S&P Water/Sewer or Education: Enterprise + Financial -> anchor/rating

2. Formula-results validation
   - Upload a CSV/XLSX with formula_id + value/status/numeric_score columns.
   - The rating engine will score available metrics, aggregate factors, and produce a rating.

3. Manual metric-score validation
   - Loads the selected methodology template.
   - You can paste/enter numeric scores directly for each formula_id.
   - Useful when replicating an official scorecard row-by-row.

Expected project structure
--------------------------
config/scoring_thresholds.csv
engine/factor_engine.py
engine/rating_engine.py
templates/*.csv
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import streamlit as st

# Make imports work when Streamlit runs from the project root or from pages/.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from engine.factor_engine import list_supported_schemes, load_factor_template
    from engine.rating_engine import (
        compare_to_benchmark,
        run_rating_engine,
        summarize_rating_output,
    )
except Exception as exc:  # pragma: no cover - Streamlit display path
    st.set_page_config(page_title="Validation", page_icon="🧪", layout="wide")
    st.error("Could not import engine modules. Please confirm engine/factor_engine.py and engine/rating_engine.py exist.")
    st.exception(exc)
    st.stop()


st.set_page_config(page_title="Validation", page_icon="🧪", layout="wide")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

SCHEME_LABELS = {
    "moodys_ccd_go": "Moody's CCD GO",
    "moodys_k12": "Moody's K-12",
    "sp_local_gov_k12": "S&P Local Gov / K-12 GO",
    "sp_local_gov": "S&P Local Government",
    "sp_us_government_2024": "S&P U.S. Government 2024",
    "sp_water_sewer": "S&P Water / Sewer Utility",
    "sp_community_college_go": "S&P Community College GO",
}


def _read_uploaded_table(uploaded_file) -> pd.DataFrame:
    """Read CSV/XLSX into a DataFrame."""
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file)
    raise ValueError("Please upload a .csv, .xlsx, or .xls file.")


def _clean_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"none", "nan", "na", "n/a", "-", "--"}:
        return None
    try:
        return float(text)
    except Exception:
        return None
