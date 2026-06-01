import streamlit as st
import pandas as pd

st.set_page_config(page_title="Financial Performance", layout="wide")

issuer_name = st.session_state.get("issuer_name", "City of Elk Grove")

st.title("Financial Performance")

st.markdown(f"""
**Issuer:** {issuer_name}  
**Methodology:** S&P Local Government ICR
""")

st.divider()

# -----------------------------
# Sample Data
# -----------------------------

financial_data = {
    2025: {
        "revenues": 324_862_477,
        "expenses": -205_236_539,
        "transfers": 5_013_178,
    },
    2024: {
        "revenues": 270_429_047,
        "expenses": -174_543_374,
        "transfers": -3_240_470,
    },
    2023: {
        "revenues": 203_376_884,
        "expenses": -155_528_151,
        "transfers": 4_822_809,
    },
}


def format_amount(value):
    if value < 0:
        return f"({abs(value):,.0f})"
    return f"{value:,.0f}"


def operating_result_score(avg_pct):
    if avg_pct > 3:
        return 1
    elif avg_pct >= 0:
        return 2
    elif avg_pct >= -3:
        return 3
    return 4


# -----------------------------
# Calculation
# -----------------------------

years = [2025, 2024, 2023]

surpluses = {}
operating_results = {}

for year in years:
    revenues = financial_data[year]["revenues"]
    expenses = financial_data[year]["expenses"]
    transfers = financial_data[year]["transfers"]

    surplus = revenues + expenses + transfers
    operating_result = surplus / revenues

    surpluses[year] = surplus
    operating_results[year] = operating_result

three_year_avg = sum(operating_results.values()) / len(operating_results)
assessment = operating_result_score(three_year_avg * 100)

# -----------------------------
# Summary
# -----------------------------

col1, col2, col3 = st.columns(3)

col1.metric("Financial Performance Score", assessment)
col2.metric("Three-Year Average Operating Result", f"{three_year_avg:.0%}")
col3.metric("Latest-Year Operating Result", f"{operating_results[2025]:.0%}")

st.divider()

# -----------------------------
# Main Calculation Table
# -----------------------------

st.subheader("1. Operating Result Calculation")

calculation_table = pd.DataFrame({
    "Metric": [
        "Governmental Revenues",
        "Governmental Expenses",
        "Transfers",
        "Operating Surplus / Deficit",
        "Operating Result"
    ],
    "2025": [
        format_amount(financial_data[2025]["revenues"]),
        format_amount(financial_data[2025]["expenses"]),
        format_amount(financial_data[2025]["transfers"]),
        format_amount(surpluses[2025]),
        f"{operating_results[2025]:.0%}"
    ],
    "2024": [
        format_amount(financial_data[2024]["revenues"]),
        format_amount(financial_data[2024]["expenses"]),
        format_amount(financial_data[2024]["transfers"]),
        format_amount(surpluses[2024]),
        f"{operating_results[2024]:.0%}"
    ],
    "2023": [
        format_amount(financial_data[2023]["revenues"]),
        format_amount(financial_data[2023]["expenses"]),
        format_amount(financial_data[2023]["transfers"]),
        format_amount(surpluses[2023]),
        f"{operating_results[2023]:.0%}"
    ]
})

st.dataframe(
    calculation_table,
    use_container_width=True,
    hide_index=True
)

st.caption(
    "Operating Result = (Governmental Revenues - Governmental Expenses + Transfers) / Governmental Revenues"
)

st.divider()

# -----------------------------
# Assessment Criteria
# -----------------------------

st.subheader("2. Assessment Criteria")

criteria = pd.DataFrame({
    "Metric": [
        "Three-year average operating result (%)"
    ],
    "1": [">3%"],
    "2": ["3%–0%"],
    "3": ["0%–(3%)"],
    "4": ["<(3%)"]
})

st.dataframe(
    criteria,
    use_container_width=True,
    hide_index=True
)

st.divider()

# -----------------------------
# Source Placeholder
# -----------------------------

st.subheader("3. Sources")

sources = pd.DataFrame({
    "Fiscal Year": ["FY2025", "FY2024", "FY2023"],
    "Source": [
        "ACFR p.27 — Statement of Revenues, Expenditures and Changes in Fund Balances — Governmental Funds",
        "ACFR p.27 — Statement of Revenues, Expenditures and Changes in Fund Balances — Governmental Funds",
        "ACFR p.29 — Statement of Revenues, Expenditures and Changes in Fund Balances — Governmental Funds",
    ]
})

st.dataframe(
    sources,
    use_container_width=True,
    hide_index=True
)

st.info(
    "Prototype note: Current values are sample inputs based on the Elk Grove example. "
    "Later, this page should read from extracted ACFR data and methodology templates."
)
