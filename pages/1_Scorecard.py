import streamlit as st
import pandas as pd

st.set_page_config(page_title="Scoreboard", page_icon="📊", layout="wide")

issuer_name = st.session_state.get("issuer_name", "City of Elk Grove")
bond_strategy = st.session_state.get("bond_strategy", "S&P Local Government ICR")

st.title("📊 Scoreboard")
st.markdown(f"**Bond Strategy:** {bond_strategy}  \n**Issuer:** {issuer_name}")

st.divider()

# -----------------------------
# Dynamic Scorecard Template
# -----------------------------

st.subheader("1. Indicative Issuer Credit Rating Scorecard")

scorecard_template = pd.DataFrame({
    "Factor": [
        "Economy",
        "Financial Performance",
        "Reserves and Liquidity",
        "Management",
        "Debt & Liabilities"
    ],
    "Score": [2.00, 1.00, 1.00, 2.00, 1.25],
    "Weight": [0.20, 0.20, 0.20, 0.20, 0.20],
    "Background / Statistic": [
        "Real GCP per capita / PCPI comparison",
        "Three-year average operating result",
        "Available reserves as % of revenues",
        "Budget practices, long-term planning, and policies",
        "Current cost, debt per capita, pension liability per capita"
    ]
})

edited = st.data_editor(
    scorecard_template,
    use_container_width=True,
    hide_index=True,
    num_rows="fixed"
)

edited["Weighted Score"] = edited["Score"] * edited["Weight"]
weighted_average = edited["Weighted Score"].sum()

st.markdown("### Result Summary")

col1, col2, col3 = st.columns(3)
col1.metric("Weighted Average Score", f"{weighted_average:.2f}")
col2.metric("Indicative ICR", "To be mapped")
col3.metric("General Fund Rating", "To be mapped")

st.divider()

# -----------------------------
# Fixed Rating Matrix
# -----------------------------

st.subheader("2. Individual Credit Profile Assessment")

matrix = pd.DataFrame({
    "IF Assessment": [1, 2, 3],
    "1": ["AAA", "AAA", "AA+"],
    "1.5": ["AAA", "AA+", "AA"],
    "2": ["AA+", "AA", "AA-"],
    "2.5": ["AA", "AA-", "A+"],
    "3": ["AA-", "A+", "A"],
    "3.5": ["A+", "A", "A-"],
    "4": ["A", "A-", "BBB"],
    "4.5": ["A-", "BBB+", "BBB-"],
    "5": ["BBB", "BBB-", "BB+"],
    "5.5": ["BB+", "BB", "BB-"],
    "6": ["BB-", "B+", "B"]
})

st.dataframe(matrix, use_container_width=True, hide_index=True)

st.caption("Scorecard table is dynamic. Rating matrix can remain fixed for this methodology.")
