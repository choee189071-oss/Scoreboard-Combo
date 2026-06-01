import streamlit as st

st.set_page_config(page_title="Municipal Credit Platform", page_icon="🏛️", layout="wide")

st.title("🏛️ Municipal Credit Platform")

bond_strategy = st.selectbox(
    "Select Bond Strategy",
    ["S&P Local Government ICR", "S&P Special Assessment", "Moody's Local Government"]
)

issuer_name = st.text_input("Issuer Name", placeholder="e.g., City of Elk Grove")

if st.button("Start Scoreboard"):
    if not issuer_name.strip():
        st.warning("Please enter an issuer name.")
    else:
        st.session_state["bond_strategy"] = bond_strategy
        st.session_state["issuer_name"] = issuer_name
        st.success("Saved. Please open **1 Scorecard** from the sidebar.")
