import streamlit as st
import pandas as pd

st.set_page_config(
    page_title="Municipal Rating Platform",
    page_icon="🏛️",
    layout="wide"
)

# -----------------------------
# Sample Data: City of Elk Grove
# -----------------------------

issuer = "City of Elk Grove"
sector = "Local Government"
current_icr = "N/A"
current_outlook = "N/A"

scorecard_data = pd.DataFrame({
    "Factor": [
        "Economy",
        "Financial Performance",
        "Reserves and Liquidity",
        "Management",
        "Debt & Liabilities"
    ],
    "Score": [2.00, 1.00, 1.00, 2.00, 1.25],
    "Weight": ["20%", "20%", "20%", "20%", "20%"],
    "Weighted Score": [0.40, 0.20, 0.20, 0.40, 0.25],
    "Background / Statistic": [
        "Real GCP per capita of 96% of U.S. real GDP per capita; County nominal PCPI of 95% of U.S. nominal PCPI",
        "Median 3-year operating result of 33% of revenues",
        "Available reserves of 47% of revenues; formal budget-based reserve targets established",
        "Realistic budgets and standard planning techniques; culture of long-term planning and basic policies with regular reporting",
        "Current cost for debt service and liabilities is ~5% of revenues; net direct debt per capita of ~$500; NPL per capita of $126"
    ]
})

weighted_average_score = round(scorecard_data["Weighted Score"].sum(), 2)
indicative_icr = "AA+"
indicative_gf_rating = "AA"

# -----------------------------
# Header
# -----------------------------

st.title("🏛️ S&P Local Government ICR Bond Rating Scorecard")

with st.container():
    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Sector", sector)
    col2.metric("Issuer", issuer)
    col3.metric("Current ICR", current_icr)
    col4.metric("Current Outlook", current_outlook)

st.divider()

# -----------------------------
# Section 1: Scorecard Summary
# -----------------------------

st.subheader("1. Scorecard Summary")

col_a, col_b, col_c = st.columns([2.3, 1, 1])

with col_a:
    st.dataframe(
        scorecard_data,
        use_container_width=True,
        hide_index=True
    )

with col_b:
    st.metric("Weighted Average Score", weighted_average_score)
    st.metric("Indicative ICR", indicative_icr)

with col_c:
    st.metric("Indicative General Fund Rating", indicative_gf_rating)
    st.info("This is an indicative scorecard output based on the current methodology inputs.")

st.divider()

# -----------------------------
# Section 2: Individual Credit Profile
# -----------------------------

st.subheader("2. Individual Credit Profile")

icp_data = scorecard_data[["Factor", "Score", "Weighted Score"]].copy()
icp_data.insert(1, "Weight", ["20%", "20%", "20%", "20%", "20%"])

col1, col2 = st.columns([1.4, 1])

with col1:
    st.dataframe(
        icp_data,
        use_container_width=True,
        hide_index=True
    )

with col2:
    st.metric("Individual Credit Profile", weighted_average_score)

    st.markdown("""
    **Interpretation**

    - Lower scores indicate stronger credit quality.
    - Financial Performance and Reserves are major credit strengths.
    - Management is slightly weaker relative to other factors.
    """)

st.divider()

# -----------------------------
# Section 3: Anchor Matrix
# -----------------------------

st.subheader("3. Anchor Matrix")

anchor_matrix = pd.DataFrame({
    "IF Assessment": [1, 2, 3],
    "ICP 1.0": ["AAA", "AAA", "AAA"],
    "ICP 1.5": ["AAA", "AA+", "AA"],
    "ICP 2.0": ["AA+", "AA", "AA-"],
    "ICP 2.5": ["AA", "AA-", "A+"],
    "ICP 3.0": ["AA-", "A+", "A"],
    "ICP 3.5": ["A+", "A", "A-"],
    "ICP 4.0": ["A", "A-", "BBB"],
    "ICP 4.5": ["A-", "BBB+", "BBB"],
    "ICP 5.0": ["BBB", "BBB-", "BB+"],
    "ICP 5.5": ["BB+", "BB", "BB-"],
    "ICP 6.0": ["BB", "B+", "B"]
})

col_left, col_right = st.columns([2.2, 1])

with col_left:
    st.dataframe(
        anchor_matrix,
        use_container_width=True,
        hide_index=True
    )

with col_right:
    st.metric("IF Assessment", "2")
    st.metric("ICP Assessment", weighted_average_score)
    st.metric("Mapped Rating", indicative_icr)

    st.caption("*Illustrative anchor matrix based on current scorecard setup.*")

st.divider()

# -----------------------------
# Next Step Placeholder
# -----------------------------

st.subheader("Next Steps")

st.markdown("""
After this overview page, each factor will have its own detailed page:

- Economy
- Financial Performance
- Reserves and Liquidity
- Management
- Debt and Liabilities
- Sources / Evidence Library
""")
