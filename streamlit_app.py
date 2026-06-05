from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.calculator_engine import calculate_all_formulas
from engine.factor_engine import load_factor_template
from engine.rating_engine import run_rating_engine, summarize_rating_output
from utils.manual_scores import render_manual_score_editor
from utils.source_workflow import render_source_workflow
from utils.ui_helpers import (
    SCHEME_OPTIONS,
    action_panel,
    clean_for_display,
    current_context_card,
    formula_action,
    init_state,
    page_header,
    source_readiness_counts,
    status_counts,
)

st.set_page_config(page_title="Scoreboard Combo", layout="wide")
init_state()

DOWNSTREAM_STATE_KEYS = [
    "issuer_data",
    "source_report",
    "source_candidates",
    "source_readiness_summary",
    "source_match_reports",
    "uploaded_source_candidates",
    "uploaded_source_reports",
    "api_source_candidates",
    "api_source_reports",
    "manual_source_candidates",
    "manual_source_values",
    "formula_results",
    "methodology_formula_results",
    "rating_output",
]


def clear_downstream_state() -> None:
    for key in DOWNSTREAM_STATE_KEYS:
        st.session_state.pop(key, None)
    st.session_state["uploaded_source_candidates"] = {}
    st.session_state["uploaded_source_reports"] = {}
    st.session_state["api_source_candidates"] = {}
    st.session_state["api_source_reports"] = {}
    st.session_state["manual_source_values"] = {}
    st.session_state["issuer_data"] = {}
    st.session_state["formula_results"] = pd.DataFrame()
    st.session_state["methodology_formula_results"] = pd.DataFrame()
    st.session_state["rating_output"] = None


page_header(
    "Workflow",
    "A focused workspace for sourcing raw issuer data, calculating methodology formulas, and producing an indicative rating.",
    "workflow",
)
current_context_card()

source_report = st.session_state.get("source_report")
formula_results = st.session_state.get("methodology_formula_results")
if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
    formula_results = st.session_state.get("formula_results")
rating_output = st.session_state.get("rating_output")

source_counts = source_readiness_counts(source_report)
formula_counts = status_counts(formula_results, "status")
rating_result = rating_output.get("rating_result", {}) if isinstance(rating_output, dict) else {}

st.subheader("Run Status")
status_cols = st.columns(4)
status_cols[0].metric("Source Ready", source_counts.get("independent_ready", 0))
status_cols[1].metric(
    "Source Gaps",
    source_counts.get("missing", 0) + source_counts.get("source_pending", 0) + source_counts.get("needs_review", 0),
)
status_cols[2].metric("Formula Ready", formula_counts.get("ready", 0))
status_cols[3].metric("Rating", rating_result.get("indicative_rating") or "Not run")

if not source_counts:
    action_panel(
        "Next step: set up source data",
        "Use Source Data below to upload a raw workbook, fetch Census/BEA candidates, or fill true manual/source-pending fields.",
        "warn",
    )
elif source_counts.get("missing", 0):
    action_panel(
        "Next step: close source gaps",
        "Data Mapping has saved issuer_data, but required raw fields are still missing. Review the missing list before trusting downstream scores.",
        "bad",
    )
elif not formula_counts:
    action_panel(
        "Next step: run Calculators",
        "The source layer has data. Run the methodology formulas and save formula_results for the Scoreboard.",
        "good",
    )
elif formula_counts.get("missing", 0) or formula_counts.get("error", 0):
    action_panel(
        "Next step: fix formula inputs",
        "Some formulas still need raw fields or returned errors. Use Calculators to identify the exact missing fields.",
        "warn",
    )
elif not rating_result:
    action_panel(
        "Next step: run Scoreboard",
        "Formula results are available. Enter any true qualitative scores and run the rating engine.",
        "good",
    )
else:
    action_panel(
        "Workflow run is available",
        "Review the rating output below. Developer Tools holds validation, audit, and export utilities.",
        "good",
    )

st.subheader("Main Workflow")
st.caption("A normal user can stay here: confirm sources, run formulas, enter manual scores, then produce the indicative rating.")

methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")
issuer_data = st.session_state.get("issuer_data", {}) or {}

with st.container(border=True):
    st.markdown("**0. Deal Setup**")
    st.caption("Choose the rating methodology, issuer, and fiscal year before sourcing data.")
    method_ids = list(SCHEME_OPTIONS.keys())
    with st.form("deal_setup_form"):
        setup_cols = st.columns([1.2, 1.2, 0.8])
        selected_methodology = setup_cols[0].selectbox(
            "Methodology / scheme",
            method_ids,
            index=method_ids.index(methodology_id) if methodology_id in method_ids else 0,
            format_func=lambda value: SCHEME_OPTIONS.get(value, value),
        )
        selected_issuer = setup_cols[1].text_input(
            "Issuer name",
            value=st.session_state.get("issuer_name", ""),
            placeholder="e.g., Contra Costa CCD",
        )
        selected_year = setup_cols[2].text_input(
            "Analysis year / fiscal year",
            value=str(st.session_state.get("analysis_year", "2023")),
        )
        years = st.multiselect(
            "Years to include for trend formulas",
            ["Current", "1Y prior", "2Y prior", "3Y prior", "4Y prior", "5Y prior"],
            default=st.session_state.get("analysis_years_included", ["Current", "1Y prior", "2Y prior", "3Y prior"]),
        )
        saved_setup = st.form_submit_button("Save deal setup", type="primary")

    if saved_setup:
        prior_context = (
            st.session_state.get("methodology_id"),
            st.session_state.get("issuer_name"),
            str(st.session_state.get("analysis_year")),
        )
        next_context = (selected_methodology, selected_issuer.strip(), selected_year.strip())
        st.session_state["methodology_id"] = selected_methodology
        st.session_state["issuer_name"] = selected_issuer.strip()
        st.session_state["analysis_year"] = selected_year.strip()
        st.session_state["analysis_years_included"] = years
        if prior_context != next_context:
            clear_downstream_state()
            st.session_state["setup_saved_notice"] = "Deal setup saved. Downstream source, formula, and rating outputs were reset for the new context."
        else:
            st.session_state["setup_saved_notice"] = "Deal setup saved."
        st.rerun()

setup_notice = st.session_state.pop("setup_saved_notice", None)
if setup_notice:
    st.success(setup_notice)

methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")
issuer_data = st.session_state.get("issuer_data", {}) or {}

with st.container(border=True):
    st.markdown("**1. Source Data**")
    render_source_workflow(methodology_id)

with st.container(border=True):
    st.markdown("**2. Formula Calculation**")
    if issuer_data and st.button("Run formulas from current issuer_data", type="primary"):
        try:
            formula_results = calculate_all_formulas(issuer_data)
            st.session_state["formula_results"] = formula_results
            try:
                template = load_factor_template(methodology_id, templates_dir="templates")
                ids = set(template["formula_id"].dropna().astype(str))
                st.session_state["methodology_formula_results"] = formula_results[
                    formula_results["formula_id"].astype(str).isin(ids)
                ].copy()
            except Exception:
                st.session_state["methodology_formula_results"] = formula_results
            st.success(f"Saved {len(formula_results)} formula results.")
        except Exception as exc:
            st.error("Could not run formulas from issuer_data.")
            st.exception(exc)

    formula_results = st.session_state.get("methodology_formula_results")
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        formula_results = st.session_state.get("formula_results")
    formula_counts = status_counts(formula_results, "status")
    if formula_counts:
        formula_action(formula_counts)
        show_cols = ["formula_id", "formula_name", "category", "status", "value", "missing_fields", "warning", "error"]
        st.dataframe(
            clean_for_display(formula_results[[c for c in show_cols if c in formula_results.columns]]),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("Formula results have not been created yet.")

with st.container(border=True):
    st.markdown("**3. Manual Scores and Scoreboard**")
    if isinstance(formula_results, pd.DataFrame) and not formula_results.empty:
        try:
            template = load_factor_template(methodology_id, templates_dir="templates")
            manual_scores = render_manual_score_editor(methodology_id, template, formula_results, key_prefix="home_manual")
            if st.button("Run scoreboard from current results", type="primary"):
                output = run_rating_engine(
                    methodology_id=methodology_id,
                    formula_results=st.session_state.get("formula_results", formula_results),
                    manual_scores=manual_scores,
                    thresholds_path="config/scoring_thresholds.csv",
                    templates_dir="templates",
                )
                st.session_state["rating_output"] = output
                st.session_state["manual_scores"] = manual_scores
                st.success("Scoreboard output saved.")
        except Exception as exc:
            st.error("Could not prepare scoreboard controls.")
            st.exception(exc)

    rating_output = st.session_state.get("rating_output")
    rating_result = rating_output.get("rating_result", {}) if isinstance(rating_output, dict) else {}
    if rating_result:
        rating_cols = st.columns(4)
        rating_cols[0].metric("Indicative Rating", rating_result.get("indicative_rating") or "Missing")
        rating_cols[1].metric(
            "Weighted Score",
            "" if rating_result.get("overall_score") is None else f"{float(rating_result['overall_score']):.3f}",
        )
        rating_cols[2].metric("Coverage", rating_result.get("coverage_status", "unknown"))
        rating_cols[3].metric("Warnings", len(rating_result.get("warnings", []) or []))
        warnings = rating_result.get("warnings", []) or []
        for warning in warnings:
            st.warning(str(warning))
        with st.expander("Rating summary", expanded=False):
            st.dataframe(clean_for_display(summarize_rating_output(rating_output)), width="stretch", hide_index=True)
    elif not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        st.info("Run formulas before producing a scoreboard.")

st.subheader("Developer Tools")
st.caption("Validation, methodology audit, and exports live on the second page so this workflow stays focused.")
st.page_link("pages/1_Developer_Tools.py", label="Open Developer Tools")

st.subheader("Current Deal")
deal_cols = st.columns(3)
deal_cols[0].metric("Issuer", st.session_state.get("issuer_name") or "Not set")
deal_cols[1].metric("Methodology", SCHEME_OPTIONS.get(st.session_state.get("methodology_id"), "Not set"))
deal_cols[2].metric("Analysis Year", st.session_state.get("analysis_year") or "Not set")

with st.expander("Session details", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.write("Source readiness")
        if source_counts:
            st.dataframe(
                pd.DataFrame(
                    [{"readiness_status": key, "field_count": value} for key, value in source_counts.items()]
                ),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("No source_report saved yet.")
    with c2:
        st.write("Formula status")
        if formula_counts:
            st.dataframe(
                pd.DataFrame([{"status": key, "formula_count": value} for key, value in formula_counts.items()]),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("No formula_results saved yet.")
