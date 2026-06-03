from __future__ import annotations

import pandas as pd
import streamlit as st
from utils.ui_helpers import page_header, current_context_card, init_state

st.set_page_config(page_title="Calculators", page_icon="③", layout="wide")
init_state()
page_header("③ Calculators", "Convert canonical raw fields into formula results. For now, this page runs the core MVP formulas directly.", "calculators")
current_context_card()

issuer_data = st.session_state.get("issuer_data", {}) or {}
if not issuer_data:
    st.warning("No issuer_data found. Go to Data Mapping first.")
    st.stop()

st.subheader("Formula preview")
st.caption("These are enough to visualize the model path. More formulas can be connected from formula_library.csv later.")

def safe_div(a, b):
    try:
        if b in [0, None] or pd.isna(b):
            return None
        return a / b
    except Exception:
        return None

formula_rows = []

def add(fid, name, value, required):
    missing = [x for x in required if x not in issuer_data or issuer_data.get(x) in [None, ""]]
    formula_rows.append({
        "formula_id": fid,
        "formula_name": name,
        "value": value if not missing else None,
        "status": "ready" if not missing and value is not None else "missing",
        "missing_fields": ", ".join(missing),
    })

add("full_value_per_capita", "Full value per capita", safe_div(issuer_data.get("full_value"), issuer_data.get("population")), ["full_value", "population"])
add("mfi_pct_us", "MFI % of U.S.", issuer_data.get("median_family_income_pct_us"), ["median_family_income_pct_us"])
add("available_fund_balance_ratio", "Available fund balance / operating revenue", safe_div(issuer_data.get("available_fund_balance"), issuer_data.get("operating_revenue")), ["available_fund_balance", "operating_revenue"])
add("cash_balance_ratio", "Cash balance / operating revenue", safe_div(issuer_data.get("cash_balance"), issuer_data.get("operating_revenue")), ["cash_balance", "operating_revenue"])
add("net_direct_debt_to_full_value", "Net direct debt / full value", safe_div(issuer_data.get("net_direct_debt"), issuer_data.get("full_value")), ["net_direct_debt", "full_value"])
add("net_direct_debt_to_revenue", "Net direct debt / operating revenue", safe_div(issuer_data.get("net_direct_debt"), issuer_data.get("operating_revenue")), ["net_direct_debt", "operating_revenue"])
add("net_pension_liability_to_full_value", "Net pension liability / full value", safe_div(issuer_data.get("net_pension_liability"), issuer_data.get("full_value")), ["net_pension_liability", "full_value"])
add("net_pension_liability_to_revenue", "Net pension liability / operating revenue", safe_div(issuer_data.get("net_pension_liability"), issuer_data.get("operating_revenue")), ["net_pension_liability", "operating_revenue"])
add("operating_history", "Operating revenue / operating expense", safe_div(issuer_data.get("operating_revenue"), issuer_data.get("operating_expense")), ["operating_revenue", "operating_expense"])
add("mads_burden", "MADS / operating revenue", safe_div(issuer_data.get("mads"), issuer_data.get("operating_revenue")), ["mads", "operating_revenue"])

formula_df = pd.DataFrame(formula_rows)
st.dataframe(formula_df, use_container_width=True, hide_index=True)

if st.button("Save formula results", type="primary"):
    st.session_state["formula_results"] = formula_df
    st.success("Formula results saved. Go to Scoreboard next.")

st.download_button("Download formula_results.csv", formula_df.to_csv(index=False).encode("utf-8"), "formula_results.csv", "text/csv")
