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
from engine.rating_audit import build_rating_audit_trail
from engine.rating_engine import run_rating_engine, summarize_rating_output
try:
    from utils.data_confirmation import (
        apply_confirmed_inputs_to_issuer_data,
        evidence_confidence_metrics,
        rating_readiness_metrics,
        render_rating_readiness_overview,
    )
except ImportError:
    from utils import data_confirmation as _data_confirmation

    def apply_confirmed_inputs_to_issuer_data(issuer_data: dict | None, methodology_id: str | None = None):
        helper = getattr(_data_confirmation, "apply_confirmed_inputs_to_issuer_data", None)
        if callable(helper):
            return helper(issuer_data, methodology_id)
        return dict(issuer_data or {}), pd.DataFrame()

    def evidence_confidence_metrics(methodology_id: str | None = None):
        helper = getattr(_data_confirmation, "evidence_confidence_metrics", None)
        if callable(helper):
            return helper(methodology_id)
        return {
            "data_completeness_pct": 0.0,
            "evidence_coverage_pct": 0.0,
            "verified_fields": 0,
            "verified_denominator": 0,
        }

    def rating_readiness_metrics(methodology_id: str | None = None):
        helper = getattr(_data_confirmation, "rating_readiness_metrics", None)
        if callable(helper):
            return helper(methodology_id)
        return {
            "stage": "Unknown",
            "next_action": "Open Data Confirmation for details.",
            "raw_source_missing": 0,
            "formula_blocking_missing": 0,
            "manual_score_missing": 0,
            "rating_label": "",
            "rating_ready": False,
        }

    def render_rating_readiness_overview(methodology_id: str | None = None, *, expanded: bool = True):
        _ = expanded
        metrics = rating_readiness_metrics(methodology_id)
        st.info(metrics.get("next_action", "Open Data Confirmation for details."))
        return metrics
from utils.manual_scores import render_manual_score_editor
from utils.source_workflow import (
    _direct_metric_debug_frame,
    _workbook_direct_metric_overrides,
    render_source_workflow,
)
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
st.sidebar.caption("Build: direct-metrics-cache-v3")

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
    "approved_source_candidates",
    "formula_results",
    "methodology_formula_results",
    "rating_output",
]

LOCAL_GOV_DIAGNOSTIC_NOTES = {
    "gdp_per_capita_ratio": (
        "Source/denominator check: uses county_population with county_gdp, then compares against "
        "U.S. GDP per capita. A variance from the workbook usually means Census/BEA live geography "
        "differs from the scorecard denominator, not that the formula arithmetic is broken."
    ),
    "npl_per_capita": (
        "Unit/denominator check: uses net_pension_liability divided by issuer_population. If this "
        "looks far from the sample, verify whether NPL is stored in dollars vs. thousands and whether "
        "issuer_population is the actual issuer/service-area denominator."
    ),
    "fixed_cost_burden_ratio": (
        "Source component check: needs debt_service, pension_cost, opeb_cost, and governmental_revenue. "
        "Missing pension/OPEB costs will understate the burden even when debt_service is present."
    ),
}


def _direct_metric_source_label(formula_id: str, result: pd.Series, issuer_data: dict) -> str:
    warning = str(result.get("warning", "") or "")
    if formula_id in issuer_data and issuer_data.get(formula_id) not in (None, ""):
        if "Direct source metric value supplied" in warning:
            return "Direct metric override"
        return "Direct metric in issuer_data"
    return "Formula derived from raw components"


def local_gov_formula_diagnostics(formula_results: pd.DataFrame, issuer_data: dict) -> pd.DataFrame:
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        return pd.DataFrame()
    if "formula_id" not in formula_results.columns:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    indexed = formula_results.drop_duplicates(subset=["formula_id"], keep="first").set_index("formula_id")
    for formula_id, note in LOCAL_GOV_DIAGNOSTIC_NOTES.items():
        if formula_id not in indexed.index:
            continue
        result = indexed.loc[formula_id]
        missing_fields = str(result.get("missing_fields", "") or "")
        status = str(result.get("status", "") or "")
        value = result.get("value")
        source_used = _direct_metric_source_label(formula_id, result, issuer_data)
        uses_direct_metric = source_used != "Formula derived from raw components"

        if status == "missing":
            diagnosis = f"Missing required field(s): {missing_fields or 'unknown'}."
        elif formula_id == "npl_per_capita":
            has_npl = issuer_data.get("net_pension_liability") not in (None, "")
            has_population = issuer_data.get("issuer_population") not in (None, "")
            if uses_direct_metric:
                missing_support = [
                    field
                    for field, present in [
                        ("net_pension_liability", has_npl),
                        ("issuer_population", has_population),
                    ]
                    if not present
                ]
                diagnosis = (
                    "Ready via direct metric override. "
                    + (
                        f"Raw support component(s) not separately sourced: {', '.join(missing_support)}."
                        if missing_support
                        else "Raw support components are also available."
                    )
                )
            else:
                diagnosis = "Ready. Check NPL unit and issuer_population denominator if sample variance remains."
                if not has_npl or not has_population:
                    diagnosis = "Needs net_pension_liability and issuer_population; do not substitute county population silently."
        elif formula_id == "fixed_cost_burden_ratio":
            cost_fields = ["debt_service", "pension_cost", "opeb_cost", "governmental_revenue"]
            missing_cost_fields = [field for field in cost_fields if issuer_data.get(field) in (None, "")]
            if uses_direct_metric:
                diagnosis = (
                    "Ready via direct metric override. "
                    + (
                        f"Raw support component(s) not separately sourced: {', '.join(missing_cost_fields)}."
                        if missing_cost_fields
                        else "Raw support components are also available."
                    )
                )
            else:
                diagnosis = (
                    "Ready. Verify pension_cost and opeb_cost are included in the same dollar unit as debt_service."
                    if not missing_cost_fields
                    else f"Missing/blank component(s): {', '.join(missing_cost_fields)}."
                )
        else:
            diagnosis = "Ready. Compare live BEA/Census geography against the official workbook denominator."

        rows.append(
            {
                "formula_id": formula_id,
                "status": status,
                "value": value,
                "source_used": source_used,
                "missing_fields": missing_fields,
                "diagnosis": diagnosis,
                "why_it_matters": note,
            }
        )
    return pd.DataFrame(rows)


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
methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")
readiness = rating_readiness_metrics(methodology_id)

st.subheader("Run Status")
status_cols = st.columns(4)
status_cols[0].metric("Current Stage", readiness.get("stage", "Not started"))
status_cols[1].metric("Formula Blocking Missing", readiness.get("formula_blocking_missing", 0))
status_cols[2].metric("Manual Score Missing", readiness.get("manual_score_missing", 0))
status_cols[3].metric("Rating", readiness.get("rating_label") or "Not run")
panel_kind = "good" if readiness.get("rating_ready") or readiness.get("rating_produced") else "warn"
action_panel("Next step", str(readiness.get("next_action", "Continue workflow.")), panel_kind)

st.subheader("Main Workflow")
st.caption("A normal user can stay here: confirm sources, run formulas, enter manual scores, then produce the indicative rating.")

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

render_rating_readiness_overview(methodology_id, expanded=False)

with st.container(border=True):
    st.markdown("**Evidence Confidence**")
    st.caption(
        "Use Data Confirmation after the rating path is clear. ACFR/API/OS evidence validates rating-driving values; "
        "it is not the same as raw source extraction readiness."
    )
    st.page_link("pages/0_Data_Confirmation.py", label="Open Data Confirmation")

issuer_data = st.session_state.get("issuer_data", {}) or {}
formula_results = st.session_state.get("methodology_formula_results")
if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
    formula_results = st.session_state.get("formula_results")

with st.container(border=True):
    st.markdown("**2. Formula Calculation**")
    if issuer_data and st.button("Run formulas from current issuer_data", type="primary"):
        try:
            formula_issuer_data = dict(issuer_data)
            direct_metric_overrides = _workbook_direct_metric_overrides(methodology_id)
            for field, item in direct_metric_overrides.items():
                formula_issuer_data[field] = item.get("workbook_value")
            formula_issuer_data, confirmed_formula_inputs = apply_confirmed_inputs_to_issuer_data(
                formula_issuer_data,
                methodology_id,
            )
            if direct_metric_overrides or not confirmed_formula_inputs.empty:
                st.session_state["issuer_data"] = formula_issuer_data
                st.session_state["workbook_direct_metric_debug"] = _direct_metric_debug_frame(
                    direct_metric_overrides,
                    formula_issuer_data,
                )
            formula_results = calculate_all_formulas(formula_issuer_data)
            st.session_state["formula_results"] = formula_results
            try:
                template = load_factor_template(methodology_id, templates_dir="templates")
                ids = set(template["formula_id"].dropna().astype(str))
                st.session_state["methodology_formula_results"] = formula_results[
                    formula_results["formula_id"].astype(str).isin(ids)
                ].copy()
            except Exception:
                st.session_state["methodology_formula_results"] = formula_results
            if direct_metric_overrides:
                st.caption(
                    f"{len(direct_metric_overrides)} workbook direct metric(s) applied to formula inputs. "
                    "Full debug details are in Developer Tools > Advanced Diagnostics."
                )
            if not confirmed_formula_inputs.empty:
                with st.expander("Confirmed inputs applied to formula engine", expanded=True):
                    st.dataframe(clean_for_display(confirmed_formula_inputs), width="stretch", hide_index=True)
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
        if methodology_id == "sp_local_gov_k12":
            diagnostics = local_gov_formula_diagnostics(formula_results, issuer_data)
            if not diagnostics.empty:
                st.session_state["local_gov_formula_diagnostics"] = diagnostics
                st.caption(
                    "S&P Local Gov formula diagnostics are available in Developer Tools > Advanced Diagnostics."
                )
    else:
        st.info("Formula results have not been created yet.")

with st.container(border=True):
    st.markdown("**3. Manual Scores and Scoreboard**")
    if isinstance(formula_results, pd.DataFrame) and not formula_results.empty:
        try:
            template = load_factor_template(methodology_id, templates_dir="templates")
            with st.form(f"scoreboard_manual_form_{methodology_id}"):
                manual_scores = render_manual_score_editor(
                    methodology_id,
                    template,
                    formula_results,
                    key_prefix="home_manual",
                )
                run_scoreboard = st.form_submit_button("Run scoreboard from current results", type="primary")
            if run_scoreboard:
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
        confidence = evidence_confidence_metrics(methodology_id)
        confidence_cols = st.columns(3)
        confidence_cols[0].metric("Blocking Completion", f"{confidence['data_completeness_pct']:.0f}%")
        confidence_cols[1].metric("Evidence Coverage", f"{confidence['evidence_coverage_pct']:.0f}%")
        confidence_cols[2].metric("Verified Fields", f"{confidence['verified_fields']} / {confidence['verified_denominator']}")
        warnings = rating_result.get("warnings", []) or []
        for warning in warnings:
            st.warning(str(warning))
        with st.expander("Rating summary", expanded=False):
            st.dataframe(clean_for_display(summarize_rating_output(rating_output)), width="stretch", hide_index=True)
        with st.expander("Show Rating Audit Trail", expanded=False):
            audit = build_rating_audit_trail(
                methodology_id=methodology_id,
                rating_output=rating_output,
                formula_results=st.session_state.get("formula_results", formula_results),
                source_report=st.session_state.get("source_report"),
                issuer_data=st.session_state.get("issuer_data", {}) or {},
                manual_scores=st.session_state.get("manual_scores", {}) or {},
            )
            final_trace = audit.get("final_trace", pd.DataFrame())
            factor_trace = audit.get("factor_trace", pd.DataFrame())
            metric_trace = audit.get("metric_trace", pd.DataFrame())
            if not final_trace.empty:
                st.write("Final calculation")
                st.dataframe(clean_for_display(final_trace), width="stretch", hide_index=True)
            if not factor_trace.empty:
                st.write("Factor contributions")
                factor_cols = [
                    "section",
                    "factor",
                    "factor_score",
                    "factor_weight",
                    "weighted_contribution",
                    "coverage_pct",
                    "status",
                    "calculation",
                ]
                st.dataframe(
                    clean_for_display(factor_trace[[c for c in factor_cols if c in factor_trace.columns]]),
                    width="stretch",
                    hide_index=True,
                )
            if not metric_trace.empty:
                st.write("Metric trace")
                metric_cols = [
                    "section",
                    "factor",
                    "metric",
                    "raw_metric_value",
                    "bucket_used",
                    "numeric_score",
                    "metric_weight",
                    "factor_weight",
                    "weighted_contribution",
                    "source_used",
                    "threshold_source",
                    "score_status",
                ]
                st.dataframe(
                    clean_for_display(metric_trace[[c for c in metric_cols if c in metric_trace.columns]]),
                    width="stretch",
                    hide_index=True,
                )
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
        st.write("Source inventory readiness")
        st.caption("Extraction-level status only. Rating Readiness shows what actually blocks scoring.")
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
