from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st
from utils.ui_helpers import page_header, current_context_card, init_state

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from engine.calculator_engine import load_formula_library, parse_required_fields
    from engine.factor_engine import load_factor_template
    from engine.mapping_engine import map_uploaded_file, merge_issuer_data
except Exception as exc:  # pragma: no cover - Streamlit display path
    st.set_page_config(page_title="Data Mapping", page_icon="②", layout="wide")
    st.error("Could not import mapping/calculator engines.")
    st.exception(exc)
    st.stop()


st.set_page_config(page_title="Data Mapping", page_icon="②", layout="wide")
init_state()
page_header(
    "② Data Mapping",
    "Upload source files or enter canonical raw fields. This page builds the issuer_data dictionary used by Calculators.",
    "data_mapping",
)
current_context_card()

methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")


def _default_value_for_field(field_name: str) -> Any:
    defaults = {
        "population": 100000.0,
        "population_us": 330000000.0,
        "full_value": 12000000000.0,
        "assessed_value": 10000000000.0,
        "assessed_value_prior": 9500000000.0,
        "operating_revenue": 100000000.0,
        "operating_expense": 98000000.0,
        "governmental_revenue": 100000000.0,
        "governmental_expense": 98000000.0,
        "general_fund_balance": 25000000.0,
        "available_fund_balance": 25000000.0,
        "cash_balance": 30000000.0,
        "cash_and_investments": 30000000.0,
        "unrestricted_reserves": 30000000.0,
        "net_direct_debt": 20000000.0,
        "direct_debt": 20000000.0,
        "total_go_debt": 20000000.0,
        "net_pension_liability": 35000000.0,
        "mads": 6000000.0,
        "mads_requirement": 6000000.0,
        "issuer_mfi": 120000.0,
        "us_mfi": 100000.0,
        "mhi_adjusted_rpp": 120000.0,
        "us_median_income": 80000.0,
        "county_ebi": 120.0,
        "us_ebi": 100.0,
    }
    return defaults.get(field_name, None)


def _required_fields_for_methodology(methodology_id: str) -> pd.DataFrame:
    formulas = load_formula_library("config/formula_library.csv")
    template = load_factor_template(methodology_id, templates_dir="templates")
    formula_ids = set(template["formula_id"].dropna().astype(str))
    rows: List[Dict[str, Any]] = []
    for _, formula in formulas[formulas["formula_id"].astype(str).isin(formula_ids)].iterrows():
        for field in parse_required_fields(formula.get("required_data", "")):
            if field == "manual":
                continue
            rows.append(
                {
                    "field_name": field,
                    "formula_id": formula["formula_id"],
                    "formula_name": formula["formula_name"],
                    "category": formula.get("category", ""),
                }
            )
    if not rows:
        return pd.DataFrame(columns=["field_name", "used_by", "category"])
    df = pd.DataFrame(rows)
    return (
        df.groupby("field_name", as_index=False)
        .agg(
            used_by=("formula_id", lambda x: "; ".join(sorted(set(map(str, x))))),
            category=("category", lambda x: "; ".join(sorted(set(str(v) for v in x if str(v))))),
        )
        .sort_values("field_name")
        .reset_index(drop=True)
    )

