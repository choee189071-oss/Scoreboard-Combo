import streamlit as st
import pandas as pd

st.set_page_config(page_title="Economy", page_icon="🌎", layout="wide")

issuer_name = st.session_state.get("issuer_name", "City of Elk Grove")

st.title("Economy")

st.markdown(f"""
**Issuer:** {issuer_name}  
**Methodology:** S&P Local Government ICR
""")

st.divider()

# -----------------------------
# Sample Data
# -----------------------------

regional_name = "Sacramento"
year = 2024

real_gcp = 106_654_911_000
population = 1_611_231
real_gcp_per_capita = 66_195

us_real_gdp = 23_358_435_000_000
us_population = 340_110_988
us_real_gdp_per_capita = 68_679

regional_pcpi = 69_693
us_pcpi = 73_204

gcp_pct_us = real_gcp_per_capita / us_real_gdp_per_capita
pcpi_pct_us = regional_pcpi / us_pcpi


# -----------------------------
# Scoring Functions
# -----------------------------

def score_real_gcp(pct):
    pct = pct * 100
    if pct > 110:
        return 1
    elif pct >= 95:
        return 2
    elif pct >= 85:
        return 3
    elif pct >= 75:
        return 4
    elif pct >= 65:
        return 5
    return 6


def score_pcpi(pct):
    pct = pct * 100
    if pct > 100:
        return 1
    elif pct >= 90:
        return 2
    elif pct >= 80:
        return 3
    elif pct >= 75:
        return 4
    elif pct >= 70:
        return 5
    return 6


gcp_score = score_real_gcp(gcp_pct_us)
pcpi_score = score_pcpi(pcpi_pct_us)
economy_score = round((gcp_score + pcpi_score) / 2, 1)

# -----------------------------
# Summary
# -----------------------------

col1, col2, col3 = st.columns(3)

col1.metric("Economy Score", f"{economy_score:.1f}")
col2.metric("GCP Score", gcp_score)
col3.metric("PCPI Score", pcpi_score)

st.divider()

# -----------------------------
# Part 1: Real Gross County Product
# -----------------------------

st.subheader("1. Real Gross County Product")

gcp_table = pd.DataFrame({
    "Metric": [
        "Real Gross County Product",
        "Population",
        "Real GCP Per Capita",
        "Real GCP Per Capita as % of U.S."
    ],
    regional_name: [
        f"{real_gcp:,.0f}",
        f"{population:,.0f}",
        f"{real_gcp_per_capita:,.0f}",
        f"{gcp_pct_us:.0%}"
    ],
    "U.S.": [
        f"{us_real_gdp:,.0f}",
        f"{us_population:,.0f}",
        f"{us_real_gdp_per_capita:,.0f}",
        "100%"
    ],
    "Year": [year, year, year, year]
})

left, right = st.columns([4, 1])

with left:
    st.dataframe(
        gcp_table,
        use_container_width=True,
        hide_index=True
    )

with right:
    st.metric("Assessment", gcp_score)
    st.caption("Based on Real GCP per capita as % of U.S. real GDP per capita.")

st.divider()

# -----------------------------
# Part 2: Personal Income
# -----------------------------

st.subheader("2. Per Capita Personal Income")

pcpi_table = pd.DataFrame({
    "Metric": [
        "Per Capita Personal Income",
        "PCPI as % of U.S."
    ],
    regional_name: [
        f"{regional_pcpi:,.0f}",
        f"{pcpi_pct_us:.0%}"
    ],
    "U.S.": [
        f"{us_pcpi:,.0f}",
        "100%"
    ],
    "Year": [year, year]
})

left, right = st.columns([4, 1])

with left:
    st.dataframe(
        pcpi_table,
        use_container_width=True,
        hide_index=True
    )

with right:
    st.metric("Assessment", pcpi_score)
    st.caption("Based on county nominal PCPI as % of U.S. nominal PCPI.")

st.divider()

# -----------------------------
# Assessment Criteria
# -----------------------------

st.subheader("3. Assessment Criteria")

criteria = pd.DataFrame({
    "Metric": [
        "Real GCP per capita as % of U.S. real GDP per capita",
        "County nominal PCPI as % of U.S. nominal PCPI"
    ],
    "1": [">110%", ">100%"],
    "2": ["110%–95%", "100%–90%"],
    "3": ["95%–85%", "90%–80%"],
    "4": ["85%–75%", "80%–75%"],
    "5": ["75%–65%", "75%–70%"],
    "6": ["<65%", "<70%"]
})

st.dataframe(
    criteria,
    use_container_width=True,
    hide_index=True
)

st.info(
    "Prototype note: Current values are sample inputs based on the Elk Grove / Sacramento example. "
    "Later, this page should read from extracted data and methodology templates."
)
