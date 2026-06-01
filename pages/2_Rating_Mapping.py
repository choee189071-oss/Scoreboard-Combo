import streamlit as st
import pandas as pd

st.set_page_config(page_title="Rating Mapping", page_icon="🧭", layout="wide")

issuer_name = st.session_state.get("issuer_name", "City of Elk Grove")
icp_score = st.session_state.get("icp_score", 1.45)

st.title("🧭 Rating Mapping / Anchor Analysis")
st.markdown(f"**Issuer:** {issuer_name}")

st.divider()

st.subheader("1. Individual Credit Profile")

icp_table = pd.DataFrame({
    "Factor": [
        "Economy",
        "Financial Performance",
        "Reserves and Liquidity",
        "Management",
        "Debt & Liabilities"
    ],
    "Weight": ["20%", "20%", "20%", "20%", "20%"],
    "Score": [2.0, 1.0, 1.0, 2.0, 1.25],
    "Weighted Score": [0.40, 0.20, 0.20, 0.40, 0.25]
})

st.dataframe(icp_table, use_container_width=True, hide_index=True)
st.metric("Individual Credit Profile", f"{icp_score:.2f}")

st.divider()

st.subheader("2. Anchor Matrix")

if_assessment = st.selectbox(
    "Institutional Framework Assessment",
    [1, 2, 3],
    index=1
)

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

st.caption("For California local governments, IF Assessment is commonly treated as 2 in this illustrative setup.")

st.divider()

st.subheader("3. Mapped Rating")

# simple bucket mapping for display
icp_bucket = min([1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6], key=lambda x: abs(x - icp_score))
mapped_rating = matrix.loc[matrix["IF Assessment"] == if_assessment, str(icp_bucket)].iloc[0]

col1, col2, col3 = st.columns(3)
col1.metric("IF Assessment", if_assessment)
col2.metric("ICP Bucket", icp_bucket)
col3.metric("Mapped Rating", mapped_rating)
