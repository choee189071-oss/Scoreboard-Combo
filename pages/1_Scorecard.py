import streamlit as st
import pandas as pd

st.set_page_config(page_title="Scoreboard", page_icon="📊", layout="wide")

issuer_name = st.session_state.get("issuer_name", "City of Elk Grove")
bond_strategy = st.session_state.get("bond_strategy", "S&P Local Government ICR")

st.title("📊 Scoreboard Dashboard")
st.markdown(f"**Bond Strategy:** {bond_strategy}  \n**Issuer:** {issuer_name}")

st.divider()

scorecard_data = pd.DataFrame({
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
    scorecard_data,
    use_container_width=True,
    hide_index=True,
    num_rows="fixed"
)

edited["Weighted Score"] = edited["Score"] * edited["Weight"]
weighted_average = edited["Weighted Score"].sum()

st.session_state["icp_score"] = weighted_average

st.divider()

col1, col2, col3 = st.columns(3)
col1.metric("Weighted Average Score / ICP", f"{weighted_average:.2f}")
col2.metric("Indicative ICR", "AA+")
col3.metric("Indicative General Fund Rating", "AA")

st.info("Go to **Rating Mapping** to view the Anchor analysis and rating matrix.")
