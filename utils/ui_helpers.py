from __future__ import annotations

from typing import Dict, List, Tuple
import pandas as pd
import streamlit as st

WORKFLOW_STEPS: List[Tuple[str, str, str]] = [
    ("deal_setup", "1", "Deal Setup"),
    ("data_mapping", "2", "Data Mapping"),
    ("calculators", "3", "Calculators"),
    ("scoreboard", "4", "Scoreboard"),
    ("validation", "5", "Validation"),
    ("export", "6", "Export"),
]

SCHEME_OPTIONS: Dict[str, str] = {
    "moodys_ccd_go": "Moody's CCD GO",
    "moodys_k12": "Moody's K-12",
    "sp_local_gov_k12": "S&P Local Gov / K-12 GO",
    "sp_local_gov": "S&P Local Government",
    "sp_water_sewer": "S&P Water / Sewer Utility",
    "sp_community_college_go": "S&P Community College GO",
}

DEFAULT_SESSION = {
    "methodology_id": "moodys_ccd_go",
    "issuer_name": "Contra Costa CCD",
    "analysis_year": "2023",
    "uploaded_sources": {},
    "issuer_data": {},
    "formula_results": pd.DataFrame(),
    "rating_output": None,
}


def init_state() -> None:
    for k, v in DEFAULT_SESSION.items():
        if k not in st.session_state:
            st.session_state[k] = v.copy() if isinstance(v, dict) else v


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .main .block-container {padding-top: 2.1rem; max-width: 1180px;}
        h1 {letter-spacing: -0.03em;}
        h2, h3 {letter-spacing: -0.02em;}
        div[data-testid="stMetric"] {
            background: #f7f8fb;
            border: 1px solid #edf0f5;
            padding: 16px 18px;
            border-radius: 18px;
        }
        .cs-card {
            background: #ffffff;
            border: 1px solid #e8ebf1;
            border-radius: 20px;
            padding: 22px 24px;
            box-shadow: 0 8px 24px rgba(24, 36, 62, 0.045);
        }
        .cs-muted {color: #697386; font-size: 0.95rem;}
        .cs-stepbar {
            display: grid;
            grid-template-columns: repeat(6, 1fr);
            gap: 10px;
            margin: 10px 0 26px 0;
        }
        .cs-step {
            border: 1px solid #e3e7ef;
            border-radius: 15px;
            padding: 12px 10px;
            background: #fbfcff;
            text-align: center;
            font-size: 0.86rem;
            color: #536071;
        }
        .cs-step-active {
            background: #0B3B75;
            color: white;
            border-color: #0B3B75;
            font-weight: 700;
        }
        .cs-step-done {
            background: #ecfdf3;
            color: #087443;
            border-color: #c8f0d8;
            font-weight: 650;
        }
        .cs-pill {
            display:inline-block;
            padding: 6px 10px;
            border-radius: 999px;
            background:#eef4ff;
            color:#0B3B75;
            font-weight: 650;
            font-size: .82rem;
            margin-right: 6px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def step_status(step_key: str) -> str:
    if step_key == "deal_setup":
        return "done" if st.session_state.get("issuer_name") and st.session_state.get("methodology_id") else "todo"
    if step_key == "data_mapping":
        return "done" if st.session_state.get("issuer_data") else "todo"
    if step_key == "calculators":
        df = st.session_state.get("formula_results")
        return "done" if isinstance(df, pd.DataFrame) and not df.empty else "todo"
    if step_key == "scoreboard":
        return "done" if st.session_state.get("rating_output") else "todo"
    if step_key == "validation":
        return "done" if st.session_state.get("validation_output") else "todo"
    return "todo"


def render_workflow(active: str) -> None:
    html = ['<div class="cs-stepbar">']
    for key, num, label in WORKFLOW_STEPS:
        status = step_status(key)
        klass = "cs-step"
        prefix = num
        if key == active:
            klass += " cs-step-active"
        elif status == "done":
            klass += " cs-step-done"
            prefix = "✓"
        html.append(f'<div class="{klass}"><b>{prefix}</b><br>{label}</div>')
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def page_header(title: str, subtitle: str, active: str) -> None:
    init_state()
    inject_css()
    st.title(title)
    st.caption(subtitle)
    render_workflow(active)


def current_context_card() -> None:
    methodology = SCHEME_OPTIONS.get(st.session_state.get("methodology_id"), st.session_state.get("methodology_id", "—"))
    issuer = st.session_state.get("issuer_name", "—") or "—"
    year = st.session_state.get("analysis_year", "—") or "—"
    st.markdown(
        f"""
        <div class="cs-card">
          <span class="cs-pill">{methodology}</span>
          <span class="cs-pill">{issuer}</span>
          <span class="cs-pill">FY {year}</span>
          <div class="cs-muted" style="margin-top:10px;">Current deal context used across Data Mapping → Calculators → Scoreboard.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
