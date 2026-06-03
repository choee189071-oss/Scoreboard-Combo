from __future__ import annotations

import pandas as pd
import streamlit as st
from utils.ui_helpers import page_header, current_context_card, init_state

st.set_page_config(page_title="Data Mapping", page_icon="②", layout="wide")
init_state()
page_header("② Data Mapping", "Upload raw files or enter core fields. The goal is to create one canonical issuer_data dictionary.", "data_mapping")
current_context_card()

st.divider()
st.subheader("Source files")
cols = st.columns(4)
sources = {
    "creditscope": "CreditScope CSV/XLSX",
    "ipeds": "IPEDS Excel",
    "os": "Official Statement / OS",
    "acfr": "ACFR / Audit",
}
for i, (key, label) in enumerate(sources.items()):
    with cols[i]:
        uploaded = st.file_uploader(label, type=["csv", "xlsx", "xls", "pdf"], key=f"upload_{key}")
        if uploaded is not None:
            st.session_state["uploaded_sources"][key] = uploaded.name
            st.success(uploaded.name)

st.divider()
st.subheader("Manual canonical fields for first workflow test")
st.caption("Use this while the real file parser/mapping is being tested. These fields feed the calculator page directly.")

schema = [
    ("population", "Population", 100000.0),
    ("full_value", "Full Value / Tax Base", 12000000000.0),
    ("median_family_income_pct_us", "MFI % of U.S. median", 120.0),
    ("available_fund_balance", "Available fund balance", 25000000.0),
    ("cash_balance", "Cash balance", 30000000.0),
    ("operating_revenue", "Operating revenue", 100000000.0),
    ("operating_expense", "Operating expense", 98000000.0),
    ("net_direct_debt", "Net direct debt", 20000000.0),
    ("net_pension_liability", "Net pension liability", 35000000.0),
    ("mads", "MADS", 6000000.0),
]

existing = st.session_state.get("issuer_data", {}) or {}
rows = []
for fid, label, default in schema:
    rows.append({"field_name": fid, "display_name": label, "value": existing.get(fid, default), "source": "manual"})

edited = st.data_editor(pd.DataFrame(rows), use_container_width=True, hide_index=True, num_rows="fixed")

if st.button("Save issuer_data", type="primary"):
    issuer_data = {}
    for _, row in edited.iterrows():
        key = str(row.get("field_name", "")).strip()
        val = row.get("value")
        if key:
            try:
                issuer_data[key] = float(val)
            except Exception:
                issuer_data[key] = val
    st.session_state["issuer_data"] = issuer_data
    st.success(f"Saved {len(issuer_data)} canonical fields. Go to Calculators next.")

with st.expander("Current issuer_data", expanded=False):
    data = st.session_state.get("issuer_data", {}) or {}
    if data:
        st.json(data)
    else:
        st.info("No issuer_data saved yet.")

