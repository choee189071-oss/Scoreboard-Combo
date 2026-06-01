import streamlit as st

st.set_page_config(
    page_title="Municipal Rating Platform",
    page_icon="🏛️",
    layout="wide"
)
st.title("🏛️ Municipal Rating Platform")
st.markdown("""
### Select Methodology
Choose a rating methodology to begin.
""")
methodology = st.selectbox(
    "Methodology",
    [
        "S&P Local Government",
        "S&P Special Assessment",
        "Moody's Local Government"
    ]
)
issuer = st.text_input("Issuer Name")
if st.button("Load Framework"):
    st.success(f"Loaded: {methodology}")
