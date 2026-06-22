from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple
import pandas as pd
import streamlit as st

WORKFLOW_STEPS: List[Tuple[str, str, str]] = [
    ("workflow", "1", "Workflow"),
    ("data_confirmation", "2", "Review & Adjust"),
    ("audit_platform", "3", "Audit & Advanced"),
]

APP_NAME = "Scoreboard Combo"
BUILD_LABEL = "main-nav-v2"
PAGE_LINKS: Dict[str, Tuple[str, str]] = {
    "workflow": ("streamlit_app.py", "Workflow"),
    "data_confirmation": ("pages/0_Data_Confirmation.py", "Review & Adjust"),
    "audit_platform": ("pages/2_Audit_Platform.py", "Audit & Advanced"),
}
ADVANCED_PAGE_LINKS: Dict[str, Tuple[str, str]] = {
    "source_intake": ("pages/4_Source_Intake.py", "Source Intake Lab"),
    "data_platform": ("pages/3_Data_Platform.py", "Data Platform"),
    "developer_tools": ("pages/1_Developer_Tools.py", "Developer Tools"),
}

SCHEME_OPTIONS: Dict[str, str] = {
    "moodys_ccd_go": "Moody's CCD GO",
    "moodys_k12": "Moody's K-12",
    "sp_local_gov_k12": "S&P Local Gov / K-12 GO",
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
        .main .block-container {padding-top: 1.8rem; max-width: 1240px;}
        h1, h2, h3 {letter-spacing: 0;}
        h1 {font-size: 2.35rem; line-height: 1.12; margin-bottom: .25rem;}
        h2 {font-size: 1.55rem; margin-top: 1.7rem;}
        h3 {font-size: 1.15rem;}
        div[data-testid="stCaptionContainer"] {color: #697386;}
        div[data-testid="stAlert"] {border-radius: 8px;}
        div[data-testid="stSidebarNav"] {display: none;}
        div[data-testid="stMetric"] {
            background: #f8fafc;
            border: 1px solid #e6eaf0;
            padding: 14px 16px;
            border-radius: 8px;
            min-height: 106px;
        }
        div[data-testid="stMetricValue"] {
            font-size: clamp(1.45rem, 2.5vw, 2rem);
            line-height: 1.15;
            white-space: normal;
            overflow-wrap: anywhere;
            text-overflow: clip;
        }
        div[data-testid="stMetricLabel"] p {
            white-space: normal;
            line-height: 1.2;
        }
        .cs-sidebar-title {
            color: #1f2937;
            font-weight: 800;
            font-size: 1.02rem;
            line-height: 1.2;
            margin: .35rem 0 .8rem 0;
        }
        .cs-sidebar-active {
            background: #e8eef8;
            border: 1px solid #d7e0ef;
            border-radius: 8px;
            color: #12385f;
            font-size: .82rem;
            font-weight: 750;
            margin: .25rem 0 .35rem 0;
            padding: .45rem .65rem;
        }
        .cs-sidebar-build {
            border-top: 1px solid #e3e7ef;
            color: #7a8494;
            font-size: .82rem;
            margin-top: 1rem;
            padding-top: .8rem;
        }
        .cs-card {
            background: #ffffff;
            border: 1px solid #e6eaf0;
            border-radius: 8px;
            padding: 18px 20px;
            box-shadow: 0 1px 2px rgba(24, 36, 62, 0.04);
        }
        .cs-muted {color: #697386; font-size: 0.94rem;}
        .cs-kicker {
            color: #5d6676;
            font-size: .78rem;
            font-weight: 700;
            letter-spacing: 0;
            text-transform: uppercase;
            margin-bottom: .35rem;
        }
        .cs-stepbar {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(116px, 1fr));
            gap: 8px;
            margin: 16px 0 24px 0;
        }
        .cs-step {
            border: 1px solid #e3e7ef;
            border-radius: 8px;
            padding: 10px 10px;
            background: #fbfcff;
            text-align: center;
            font-size: 0.84rem;
            color: #536071;
        }
        .cs-step-active {
            background: #12385f;
            color: white;
            border-color: #12385f;
            font-weight: 700;
        }
        .cs-step-done {
            background: #eff8f3;
            color: #166534;
            border-color: #cdebd6;
            font-weight: 650;
        }
        .cs-pill {
            display:inline-block;
            padding: 5px 9px;
            border-radius: 999px;
            background:#edf4fb;
            color:#12385f;
            font-weight: 650;
            font-size: .82rem;
            margin-right: 6px;
            margin-bottom: 6px;
        }
        .cs-pill-neutral {background:#f1f3f6; color:#414b5a;}
        .cs-pill-good {background:#eff8f3; color:#166534;}
        .cs-pill-warn {background:#fff7ed; color:#9a3412;}
        .cs-pill-bad {background:#fef2f2; color:#b42318;}
        .cs-action {
            border-left: 4px solid #12385f;
            background: #f8fafc;
            border-radius: 8px;
            padding: 14px 16px;
            margin: 14px 0 8px 0;
        }
        .cs-action-good {border-left-color:#16a34a; background:#f6fef9;}
        .cs-action-warn {border-left-color:#f97316; background:#fff7ed;}
        .cs-action-bad {border-left-color:#dc2626; background:#fef2f2;}
        .cs-action-title {
            font-weight: 750;
            color: #1f2937;
            margin-bottom: 3px;
        }
        .cs-action-body {color:#4b5563; font-size:.94rem;}
        .cs-grid-note {
            color:#667085;
            font-size:.92rem;
            margin: 0 0 8px 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_navigation(active: str) -> None:
    def safe_page_link(path: str, label: str) -> None:
        try:
            st.page_link(path, label=label)
        except Exception:
            st.markdown(label)

    st.sidebar.markdown(f'<div class="cs-sidebar-title">{APP_NAME}</div>', unsafe_allow_html=True)
    with st.sidebar:
        for key, (path, label) in PAGE_LINKS.items():
            if key == active:
                st.markdown(f'<div class="cs-sidebar-active">{label}</div>', unsafe_allow_html=True)
            else:
                safe_page_link(path, label)
    st.sidebar.markdown(
        f'<div class="cs-sidebar-build">Build: {BUILD_LABEL}</div>',
        unsafe_allow_html=True,
    )


def step_status(step_key: str) -> str:
    if step_key == "workflow":
        return "done" if st.session_state.get("rating_output") else "todo"
    if step_key == "source_intake":
        candidates = st.session_state.get("source_intake_candidates")
        report = st.session_state.get("source_intake_source_report")
        if isinstance(candidates, pd.DataFrame) and not candidates.empty:
            return "done"
        if isinstance(report, pd.DataFrame) and not report.empty:
            return "done"
        return "todo"
    if step_key == "data_confirmation":
        approvals = st.session_state.get("data_confirmation_approvals")
        comparison = st.session_state.get("data_confirmation_comparison")
        if isinstance(approvals, pd.DataFrame) and not approvals.empty:
            return "done"
        if isinstance(comparison, pd.DataFrame) and not comparison.empty:
            return "done"
        return "todo"
    if step_key == "data_platform":
        required = [
            Path("config/source_registry.csv"),
            Path("config/source_priority.csv"),
            Path("config/data_dictionary.csv"),
            Path("config/field_mapping.csv"),
        ]
        return "done" if all(path.exists() for path in required) else "todo"
    if step_key == "audit_platform":
        accuracy = st.session_state.get("audit_accuracy_matrix")
        if isinstance(accuracy, pd.DataFrame) and not accuracy.empty:
            return "done"
        return "done" if Path("work/methodology_accuracy_matrix/tables.json").exists() else "todo"
    if step_key == "developer_tools":
        df = st.session_state.get("formula_results")
        return "done" if isinstance(df, pd.DataFrame) and not df.empty else "todo"
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
            prefix = "Done"
        html.append(f'<div class="{klass}"><b>{prefix}</b><br>{label}</div>')
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def page_header(title: str, subtitle: str, active: str) -> None:
    init_state()
    inject_css()
    render_sidebar_navigation(active)
    st.markdown('<div class="cs-kicker">Credit scoring workflow</div>', unsafe_allow_html=True)
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
          <div class="cs-kicker">Current context</div>
          <span class="cs-pill">{methodology}</span>
          <span class="cs-pill">{issuer}</span>
          <span class="cs-pill">FY {year}</span>
          <div class="cs-muted" style="margin-top:6px;">This context is shared by Workflow, Review & Adjust, and Audit.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_counts(df: pd.DataFrame | None, status_col: str) -> Dict[str, int]:
    if not isinstance(df, pd.DataFrame) or df.empty or status_col not in df.columns:
        return {}
    return {
        str(k): int(v)
        for k, v in df[status_col].fillna("unknown").astype(str).value_counts().to_dict().items()
    }


def clean_for_display(df: pd.DataFrame | None) -> pd.DataFrame:
    """Normalize mixed Python objects so Streamlit/Arrow can render tables reliably."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame() if df is None else df
    out = df.copy()

    def clean_value(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, float) and pd.isna(value):
            return ""
        if isinstance(value, (list, tuple, set)):
            return "; ".join(str(v) for v in value if v is not None)
        if isinstance(value, Mapping):
            return json.dumps(value, default=str, ensure_ascii=False)
        if isinstance(value, str) and value.strip().lower() in {"nan", "none", "<na>"}:
            return ""
        return value

    for col in out.columns:
        if out[col].dtype == "object":
            out[col] = out[col].map(clean_value)
            values = out[col].dropna().tolist()
            has_string = any(isinstance(value, str) for value in values)
            has_non_string = any(not isinstance(value, str) for value in values)
            if has_string and has_non_string:
                out[col] = out[col].map(lambda value: "" if value is None else str(value))
    return out


def selected_source_report(source_report: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(source_report, pd.DataFrame) or source_report.empty:
        return pd.DataFrame()
    if "selected" not in source_report.columns:
        return source_report.copy()
    return source_report[source_report["selected"].astype(bool)].copy()


def source_readiness_counts(source_report: pd.DataFrame | None) -> Dict[str, int]:
    return status_counts(selected_source_report(source_report), "readiness_status")


def render_count_metrics(items: Iterable[Tuple[str, Any]]) -> None:
    item_list = list(items)
    columns = st.columns(len(item_list) or 1)
    for column, (label, value) in zip(columns, item_list):
        column.metric(label, value)


def action_panel(title: str, body: str, tone: str = "neutral") -> None:
    tone_class = {
        "good": "cs-action-good",
        "warn": "cs-action-warn",
        "bad": "cs-action-bad",
    }.get(tone, "")
    st.markdown(
        f"""
        <div class="cs-action {tone_class}">
          <div class="cs-action-title">{title}</div>
          <div class="cs-action-body">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def readiness_action(source_report: pd.DataFrame | None) -> None:
    counts = source_readiness_counts(source_report)
    missing = counts.get("missing", 0)
    pending = counts.get("source_pending", 0) + counts.get("needs_review", 0)
    independent = counts.get("independent_ready", 0)
    if not counts:
        action_panel(
            "Start with source inputs",
            "Upload a raw workbook or fetch API candidates, then save issuer_data before running formulas.",
            "warn",
        )
    elif missing:
        action_panel(
            "Source coverage is incomplete",
            f"{independent} fields are independently ready, but {missing} required fields are still missing. Fill the missing raw fields before relying on the rating.",
            "bad",
        )
    elif pending:
        action_panel(
            "Some fields need source review",
            f"{independent} fields are independently ready. {pending} fields are present but still marked source_pending or needs_review.",
            "warn",
        )
    else:
        action_panel(
            "Source layer is ready for calculation",
            f"{independent} fields are independently ready. Continue to Calculators.",
            "good",
        )


def _unique_missing_formula_fields(formula_results: pd.DataFrame | None) -> list[str]:
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        return []
    if "missing_fields" not in formula_results.columns:
        return []
    fields: set[str] = set()
    for value in formula_results["missing_fields"].dropna().tolist():
        for part in str(value).replace("|", ";").replace(",", ";").split(";"):
            field = part.strip()
            if field and field.lower() not in {"manual", "none", "nan"}:
                fields.add(field)
    return sorted(fields)


def formula_action(summary: Mapping[str, Any], formula_results: pd.DataFrame | None = None) -> None:
    missing = int(summary.get("missing", 0) or 0)
    errors = int(summary.get("error", 0) or 0)
    manual = int(summary.get("manual", 0) or 0)
    ready = int(summary.get("ready", 0) or 0)
    if errors:
        action_panel(
            "Formula errors need attention",
            f"{errors} formulas returned errors. Check expression logic before running the Scoreboard.",
            "bad",
        )
    elif missing:
        missing_fields = _unique_missing_formula_fields(formula_results)
        if missing_fields:
            field_count = len(missing_fields)
            field_label = "raw input" if field_count == 1 else "raw inputs"
            field_list = ", ".join(missing_fields[:4])
            if len(missing_fields) > 4:
                field_list += f", +{len(missing_fields) - 4} more"
            body = (
                f"{ready} formulas are ready. {field_count} {field_label} ({field_list}) "
                f"currently blocks {missing} formula(s), and {manual} require analyst input."
            )
        else:
            body = f"{ready} formulas are ready, {missing} are missing raw fields, and {manual} require analyst input."
        action_panel(
            "Some formulas cannot calculate yet",
            body,
            "warn",
        )
    else:
        action_panel(
            "Formula layer is ready for scoring",
            f"{ready} formulas are ready. Fill any manual rating inputs in Source Data, then run the Scoreboard.",
            "good",
        )
