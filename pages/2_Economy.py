import streamlit as st
import pandas as pd

st.set_page_config(page_title="Economy", page_icon="🌎", layout="wide")

issuer_name = st.session_state.get("issuer_name", "City of Elk Grove")

BLUE = "#073b70"
LIGHT_BLUE = "#d9e2f3"

st.markdown(
    f"""
    <style>
    .page-title {{
        font-size: 26px;
        font-weight: 700;
        margin-bottom: 4px;
    }}

    .issuer {{
        color: #0066ff;
        font-weight: 700;
        font-size: 20px;
        margin-bottom: 24px;
    }}

    .section-title {{
        font-size: 22px;
        font-weight: 700;
        margin-top: 10px;
        margin-bottom: 16px;
    }}

    .score-number {{
        color: #00b050;
        font-weight: 700;
        font-size: 24px;
    }}

    .economy-table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 16px;
    }}

    .economy-table th {{
        text-align: center;
        padding: 6px;
        border-bottom: 2px solid black;
        font-weight: 700;
    }}

    .economy-table td {{
        padding: 7px;
        text-align: right;
    }}

    .economy-table td:first-child {{
        text-align: left;
        font-weight: 500;
    }}

    .score-line {{
        font-weight: 700;
        color: #0033cc;
    }}

    .assessment-table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 15px;
        text-align: center;
        margin-top: 10px;
    }}

    .assessment-table th {{
        background-color: {BLUE};
        color: white;
        padding: 8px;
        border: 2px solid white;
    }}

    .assessment-table td {{
        padding: 10px;
        border: 1px solid white;
    }}

    .metric-cell {{
        text-align: left;
        font-weight: 600;
        background-color: white;
        width: 34%;
    }}

    .highlight {{
        background-color: {LIGHT_BLUE};
        font-weight: 700;
    }}

    .note-box {{
        background-color: #f5f7fb;
        border-left: 5px solid {BLUE};
        padding: 12px 16px;
        margin-top: 18px;
        font-size: 15px;
    }}
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <div class="page-title">S&P Global Ratings — Methodology for Rating U.S. Governments</div>
    <div class="section-title">ICP — Economy</div>
    """,
    unsafe_allow_html=True
)

st.markdown(f'<div class="issuer">{issuer_name}</div>', unsafe_allow_html=True)

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

gcp_pct_us = real_gcp_per_capita / us_real_gdp_per_capita

regional_pcpi = 69_693
us_pcpi = 73_204
pcpi_pct_us = regional_pcpi / us_pcpi

def economy_score_gcp(pct):
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
    else:
        return 6

def economy_score_pcpi(pct):
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
    else:
        return 6

gcp_score = economy_score_gcp(gcp_pct_us)
pcpi_score = economy_score_pcpi(pcpi_pct_us)

overall_score = round((gcp_score + pcpi_score) / 2, 1)

# -----------------------------
# Economy Calculation
# -----------------------------

col_left, col_right = st.columns([1.5, 1])

with col_left:
    st.markdown(
        f"""
        <div class="section-title">
            Economy <span class="score-number">{overall_score:.1f}</span>
        </div>

        <table class="economy-table">
            <tr>
                <th></th>
                <th>{regional_name}<br>{year}</th>
                <th>U.S.<br>{year}</th>
                <th>{regional_name} as %<br>of U.S.</th>
            </tr>
            <tr>
                <td>Real Gross County Product</td>
                <td>{real_gcp:,.0f}</td>
                <td>{us_real_gdp:,.0f}</td>
                <td></td>
            </tr>
            <tr>
                <td>Population</td>
                <td>{population:,.0f}</td>
                <td>{us_population:,.0f}</td>
                <td></td>
            </tr>
            <tr>
                <td>Per Capita</td>
                <td>{real_gcp_per_capita:,.0f}</td>
                <td>{us_real_gdp_per_capita:,.0f}</td>
                <td>{gcp_pct_us:.0%}</td>
            </tr>
            <tr>
                <td class="score-line">Score</td>
                <td></td>
                <td></td>
                <td class="score-line">{gcp_score}</td>
            </tr>
            <tr><td colspan="4" style="height:22px;"></td></tr>
            <tr>
                <td>Per Capita Personal Income</td>
                <td>{regional_pcpi:,.0f}</td>
                <td>{us_pcpi:,.0f}</td>
                <td>{pcpi_pct_us:.0%}</td>
            </tr>
            <tr>
                <td class="score-line">Score</td>
                <td></td>
                <td></td>
                <td class="score-line">{pcpi_score}</td>
            </tr>
        </table>
        """,
        unsafe_allow_html=True
    )

with col_right:
    st.markdown("### Interpretation")
    st.write(
        f"{regional_name}'s real GCP per capita equals **{gcp_pct_us:.0%}** "
        f"of the U.S. benchmark, resulting in a score of **{gcp_score}**."
    )
    st.write(
        f"{regional_name}'s nominal PCPI equals **{pcpi_pct_us:.0%}** "
        f"of the U.S. benchmark, resulting in a score of **{pcpi_score}**."
    )
    st.success(f"Indicative Economy Score: {overall_score:.1f}")

st.divider()

# -----------------------------
# Assessment Table
# -----------------------------

st.subheader("Assessment Criteria")

st.markdown(
    """
    <table class="assessment-table">
        <tr>
            <th class="metric-cell">Metric</th>
            <th>1</th>
            <th>2</th>
            <th>3</th>
            <th>4</th>
            <th>5</th>
            <th>6</th>
        </tr>
        <tr>
            <td class="metric-cell">Real GCP per capita as a % of U.S. real GDP per capita</td>
            <td>&gt;110</td>
            <td class="highlight">110–95</td>
            <td>95–85</td>
            <td>85–75</td>
            <td>75–65</td>
            <td>&lt;65</td>
        </tr>
        <tr>
            <td class="metric-cell">County nominal PCPI as a % of the U.S. nominal PCPI</td>
            <td>&gt;100</td>
            <td class="highlight">100–90</td>
            <td>90–80</td>
            <td>80–75</td>
            <td>75–70</td>
            <td>&lt;70</td>
        </tr>
    </table>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <div class="note-box">
        <b>Prototype note:</b> Current values are sample inputs based on the Elk Grove / Sacramento example.
        Later, these fields should be loaded from the methodology template and extracted data layer.
    </div>
    """,
    unsafe_allow_html=True
)
