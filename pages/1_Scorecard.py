import streamlit as st
import pandas as pd

st.set_page_config(page_title="Scoreboard", page_icon="📊", layout="wide")

issuer_name = st.session_state.get("issuer_name", "City of Elk Grove")
bond_strategy = st.session_state.get("bond_strategy", "S&P Local Government ICR")

BLUE = "#073b70"
LIGHT_GRAY = "#f2f2f2"
MID_GRAY = "#8a8a8a"
RESULT_BLUE = "#0070c0"

st.markdown(
    f"""
    <style>
    .score-title {{
        background-color: {BLUE};
        color: white;
        font-weight: 700;
        text-align: center;
        padding: 8px;
        font-size: 20px;
        border: 2px solid black;
    }}

    .info-box {{
        border: 2px solid black;
        width: 520px;
        margin-top: 18px;
        margin-bottom: 22px;
    }}

    .info-row {{
        display: grid;
        grid-template-columns: 220px 300px;
        border-bottom: 1px solid black;
        min-height: 28px;
    }}

    .info-row:last-child {{
        border-bottom: none;
    }}

    .info-label {{
        font-weight: 700;
        padding: 5px;
        border-right: 1px solid black;
    }}

    .info-value {{
        color: #00a2ff;
        font-weight: 700;
        padding: 5px;
    }}

    .main-table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 15px;
    }}

    .main-table th {{
        background-color: {BLUE};
        color: white;
        padding: 7px;
        border: 1px solid white;
        text-align: center;
    }}

    .main-table td {{
        padding: 7px;
        border: 1px solid white;
        vertical-align: middle;
    }}

    .main-table tr:nth-child(even) td {{
        background-color: {LIGHT_GRAY};
    }}

    .factor-col {{
        width: 22%;
    }}

    .score-col {{
        width: 8%;
        text-align: center;
    }}

    .weight-col {{
        width: 8%;
        text-align: center;
    }}

    .wt-col {{
        width: 9%;
        text-align: center;
    }}

    .stat-col {{
        width: 53%;
    }}

    .summary-gray {{
        background-color: {MID_GRAY};
        color: white;
        font-weight: 700;
        padding: 7px;
        display: grid;
        grid-template-columns: 75% 25%;
    }}

    .summary-blue {{
        background-color: {RESULT_BLUE};
        color: white;
        font-weight: 700;
        padding: 7px;
        display: grid;
        grid-template-columns: 75% 25%;
    }}

    .small-table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 14px;
    }}

    .small-table th {{
        background-color: {BLUE};
        color: white;
        padding: 6px;
        border: 1px solid white;
        text-align: center;
    }}

    .small-table td {{
        padding: 5px;
        border: 1px solid #eeeeee;
        text-align: center;
    }}

    .small-table td:first-child {{
        text-align: left;
    }}

    .matrix-table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 14px;
        text-align: center;
    }}

    .matrix-table th {{
        background-color: {BLUE};
        color: white;
        padding: 6px;
        border: 1px solid white;
    }}

    .matrix-table td {{
        padding: 6px;
        border: 1px solid #eeeeee;
    }}

    .if-cell {{
        background-color: #5c8f3a;
        color: white;
        font-weight: 700;
    }}

    .highlight {{
        background-color: #d9e2f3 !important;
        font-weight: 700;
    }}

    .section-space {{
        height: 35px;
    }}
    </style>
    """,
    unsafe_allow_html=True
)

# -----------------------------
# Header
# -----------------------------

st.markdown(
    '<div class="score-title">S&P Local Government: ICR Bond Rating Scorecard</div>',
    unsafe_allow_html=True
)

st.markdown(
    f"""
    <div class="info-box">
        <div class="info-row">
            <div class="info-label">Sector:</div>
            <div class="info-value">Local Government</div>
        </div>
        <div class="info-row">
            <div class="info-label">Issuer:</div>
            <div class="info-value">{issuer_name}</div>
        </div>
        <div class="info-row">
            <div class="info-label">Current Issuer Credit Rating (ICR):</div>
            <div class="info-value">N/A</div>
        </div>
        <div class="info-row">
            <div class="info-label">Current ICR Outlook:</div>
            <div class="info-value">N/A</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True
)

# -----------------------------
# Dynamic Data Template
# -----------------------------

scorecard_data = [
    {
        "Factor": "Economy",
        "Score": 2.00,
        "Weight": "20%",
        "Weighted Score": 0.40,
        "Background / Statistic": "Real GCP per capita of 96% of U.S. real GDP per capita<br>County nominal PCPI of 95% of the U.S. nominal PCPI",
    },
    {
        "Factor": "Financial Performance",
        "Score": 1.00,
        "Weight": "20%",
        "Weighted Score": 0.20,
        "Background / Statistic": "Median 3-yr Performance Avg. of 33% of Revenues",
    },
    {
        "Factor": "Reserves and Liquidity",
        "Score": 1.00,
        "Weight": "20%",
        "Weighted Score": 0.20,
        "Background / Statistic": "Available Reserves of 47% of Revenues; Formal budget-based reserve targets established (Economic Uncertainty, Opportunity, Measure E Future Project funds)",
    },
    {
        "Factor": "Management",
        "Score": 2.00,
        "Weight": "20%",
        "Weighted Score": 0.40,
        "Background / Statistic": "Budgets are realistic and use standard planning techniques; culture of long-term planning and basic policies with regular reporting",
    },
    {
        "Factor": "Debt & Liabilities",
        "Score": 1.25,
        "Weight": "20%",
        "Weighted Score": 0.25,
        "Background / Statistic": "Current cost for DS and liabilities is ~5% of revenues; Net direct debt per capita of ~$500; Net pension liabilities per capita of $126",
    },
]

weighted_average = 1.50
indicative_icr = "AA+"
general_fund_rating = "AA"

# -----------------------------
# Main Scorecard Table
# -----------------------------

rows_html = ""
for row in scorecard_data:
    rows_html += f"""
    <tr>
        <td class="factor-col">{row["Factor"]}</td>
        <td class="score-col">{row["Score"]:.2f}</td>
        <td class="weight-col">{row["Weight"]}</td>
        <td class="wt-col">{row["Weighted Score"]:.2f}</td>
        <td class="stat-col">{row["Background / Statistic"]}</td>
    </tr>
    """

st.markdown(
    f"""
    <table class="main-table">
        <tr>
            <th colspan="5">S&P Scorecard — Indicative Issuer Credit Rating for the {issuer_name}</th>
        </tr>
        <tr>
            <th>Factor</th>
            <th>Score</th>
            <th>Weight</th>
            <th>Wt. Score</th>
            <th>Background / Statistic</th>
        </tr>
        {rows_html}
    </table>

    <div class="summary-gray">
        <div>Weighted Average Score</div>
        <div>{weighted_average:.1f}</div>
    </div>
    <div class="summary-gray">
        <div>Indicative Issuer Credit Rating (GO)</div>
        <div>{indicative_icr}</div>
    </div>
    <div class="summary-blue">
        <div>Indicative General Fund Credit Rating (LRBs)</div>
        <div>{general_fund_rating}</div>
    </div>
    """,
    unsafe_allow_html=True
)

st.markdown('<div class="section-space"></div>', unsafe_allow_html=True)

# -----------------------------
# Lower Tables
# -----------------------------

left, right = st.columns([1, 2.15])

with left:
    st.markdown(
        f"""
        <div style="text-align:center;font-weight:700;margin-bottom:6px;">Individual Credit Profile</div>
        <table class="small-table">
            <tr>
                <th>Factor</th>
                <th>Weight</th>
                <th>Score</th>
                <th>Weighted<br>Score</th>
            </tr>
            <tr><td>Economy</td><td>20%</td><td>2.0</td><td>0.40</td></tr>
            <tr><td>Financial Performance</td><td>20%</td><td>1.0</td><td>0.20</td></tr>
            <tr><td>Reserves and Liquidity</td><td>20%</td><td>1.0</td><td>0.20</td></tr>
            <tr><td>Management</td><td>20%</td><td>2.0</td><td>0.40</td></tr>
            <tr><td>Debt & Liabilities</td><td>20%</td><td>1.3</td><td>0.25</td></tr>
            <tr>
                <td colspan="3" style="text-align:right;font-weight:700;">Individual Credit Profile</td>
                <td style="font-weight:700;">1.45</td>
            </tr>
        </table>
        """,
        unsafe_allow_html=True
    )

with right:
    st.markdown(
        """
        <div style="text-align:center;font-weight:700;margin-bottom:6px;">Anchor — City of Elk Grove</div>
        <table class="matrix-table">
            <tr>
                <th></th>
                <th>1</th><th>1.5</th><th>2</th><th>2.5</th><th>3</th><th>3.5</th>
                <th>4</th><th>4.5</th><th>5</th><th>5.5</th><th>6</th>
            </tr>
            <tr>
                <td class="if-cell">1</td>
                <td>AAA</td><td>AAA</td><td>AA+</td><td>AA</td><td>AA−</td><td>A+</td>
                <td>A</td><td>A−</td><td>BBB</td><td>BB+</td><td>BB−</td>
            </tr>
            <tr>
                <td class="if-cell">2</td>
                <td>AAA</td><td class="highlight">AA+</td><td class="highlight">AA</td><td>AA−</td><td>A+</td><td>A</td>
                <td>A−</td><td>BBB+</td><td>BBB−</td><td>BB</td><td>B+</td>
            </tr>
            <tr>
                <td class="if-cell">3</td>
                <td>AA+</td><td>AA</td><td>AA−</td><td>A+</td><td>A</td><td>A−</td>
                <td>BBB</td><td>BBB−</td><td>BB+</td><td>BB−</td><td>B</td>
            </tr>
        </table>
        <div style="font-size:12px;color:gray;margin-top:6px;">
            *All California governments IF score is assumed as 2 in this illustrative setup.
        </div>
        """,
        unsafe_allow_html=True
    )
