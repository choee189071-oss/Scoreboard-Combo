import streamlit as st
import pandas as pd

st.set_page_config(
    page_title="Municipal Credit Scoreboard",
    page_icon="🏛️",
    layout="wide"
)

# -----------------------------
# Session State
# -----------------------------

if "started" not in st.session_state:
    st.session_state.started = False

if "bond_type" not in st.session_state:
    st.session_state.bond_type = None

if "issuer_name" not in st.session_state:
    st.session_state.issuer_name = ""

# -----------------------------
# Home Page
# -----------------------------

if not st.session_state.started:
    st.title("🏛️ Municipal Credit Scoreboard Platform")

    st.markdown("### Start a New Credit Assessment")

    bond_type = st.selectbox(
        "Select Bond Credit Type",
        [
            "Local Government Bond",
            "Special Assessment Bond",
            "Revenue Bond",
            "School District Bond",
            "Utility Revenue Bond"
        ]
    )

    issuer_name = st.text_input(
        "Issuer Name",
        placeholder="e.g., City of Elk Grove"
    )

    if st.button("Start Scorecard"):
        if issuer_name.strip() == "":
            st.warning("Please enter an issuer name.")
        else:
            st.session_state.bond_type = bond_type
            st.session_state.issuer_name = issuer_name
            st.session_state.started = True
            st.rerun()

# -----------------------------
# Local Government Scoreboard
# -----------------------------

else:
    st.title("S&P Local Government ICR Bond Rating Scorecard")

    st.markdown(f"""
    **Bond Credit Type:** {st.session_state.bond_type}  
    **Issuer:** {st.session_state.issuer_name}
    """)

    if st.button("← Back to Home"):
        st.session_state.started = False
        st.rerun()

    st.divider()

    # Dynamic Template Table
    st.subheader("1. Scorecard Template")

    scorecard_data = pd.DataFrame({
        "Factor": [
            "Economy",
            "Financial Performance",
            "Reserves and Liquidity",
            "Management",
            "Debt & Liabilities"
        ],
        "Score": [None, None, None, None, None],
        "Weight": [0.20, 0.20, 0.20, 0.20, 0.20],
        "Weighted Score": [None, None, None, None, None],
        "Background / Statistic": [
            "",
            "",
            "",
            "",
            ""
        ]
    })

    edited_scorecard = st.data_editor(
        scorecard_data,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed"
    )

    # Calculate weighted score
    try:
        edited_scorecard["Weighted Score"] = (
            edited_scorecard["Score"].astype(float)
            * edited_scorecard["Weight"].astype(float)
        )

        weighted_average = edited_scorecard["Weighted Score"].sum()

    except Exception:
        weighted_average = None

    st.divider()

    # Result Summary
    st.subheader("2. Indicative Rating Summary")

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "Weighted Average Score",
        f"{weighted_average:.2f}" if weighted_average is not None else "N/A"
    )

    col2.metric("Indicative ICR", "To be mapped")

    col3.metric("General Fund Rating", "To be mapped")

    st.divider()

    # Fixed Matrix
    st.subheader("3. Individual Credit Profile Assessment")

    anchor_matrix = pd.DataFrame({
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

    st.dataframe(
        anchor_matrix,
        use_container_width=True,
        hide_index=True
    )

    st.caption("Matrix can remain fixed for the methodology. The scorecard table above should remain dynamic.")

    st.divider()

    st.subheader("4. Next Factor Sections")

    st.markdown("""
    After this overview, each factor should open into its own detail section:

    - Economy
    - Financial Performance
    - Reserves and Liquidity
    - Management
    - Debt and Liabilities
    - Sources / Evidence
    """)
