from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

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

from utils.manual_scores import manual_score_candidates
from utils.source_workflow import (
    _direct_metric_debug_frame,
    _workbook_direct_metric_overrides,
    render_source_workflow,
)
from utils.ui_helpers import (
    SCHEME_OPTIONS,
    clean_for_display,
    current_context_card,
    formula_action,
    init_state,
    page_header,
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
    "uploaded_pdf_documents",
    "acfr_pdf_pages_cache",
    "last_acfr_auto_snippets",
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


def _manual_score_value(score: Any) -> Any:
    if isinstance(score, dict):
        return score.get("numeric_score")
    return score


def _missing_manual_score_ids(methodology_id: str, template: pd.DataFrame) -> list[str]:
    candidates = manual_score_candidates(methodology_id, template)
    if candidates.empty:
        return []
    stored = st.session_state.get("manual_scores", {}) or {}
    missing: list[str] = []
    for fid in candidates["formula_id"].dropna().astype(str):
        value = _manual_score_value(stored.get(fid))
        numeric = pd.to_numeric(value, errors="coerce")
        if pd.isna(numeric):
            missing.append(fid)
    return missing


def _formula_results_with_manual_scores(formula_results: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        return formula_results
    if "formula_id" not in formula_results.columns:
        return formula_results

    stored = st.session_state.get("manual_scores", {}) or {}
    if not stored:
        return formula_results

    output = formula_results.copy()
    for idx, row in output.iterrows():
        fid = str(row.get("formula_id", "") or "").strip()
        if not fid or fid not in stored:
            continue
        value = _manual_score_value(stored.get(fid))
        numeric = pd.to_numeric(value, errors="coerce")
        if pd.isna(numeric):
            continue
        output.at[idx, "value"] = float(numeric)
        if "status" in output.columns:
            output.at[idx, "status"] = "ready"
        if "missing_fields" in output.columns:
            output.at[idx, "missing_fields"] = ""
        if "error" in output.columns:
            output.at[idx, "error"] = ""
        if "warning" in output.columns:
            warning = str(output.at[idx, "warning"] or "").strip()
            note = "Manual rating input saved in Source Data."
            output.at[idx, "warning"] = f"{warning} {note}".strip() if warning else note
    return output


def _split_formula_missing_fields(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        pieces = value
    else:
        pieces = str(value).replace("|", ";").replace(",", ";").split(";")
    fields: list[str] = []
    for piece in pieces:
        field = str(piece).strip().strip("'\"[]()")
        if not field or field.lower() in {"nan", "none", "manual"}:
            continue
        fields.append(field)
    return fields


def formula_missing_raw_fields(formula_results: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        return pd.DataFrame()
    if "missing_fields" not in formula_results.columns:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, row in formula_results.iterrows():
        status = str(row.get("status", "") or "").strip().lower()
        if status not in {"missing", "error"}:
            continue
        formula_id = str(row.get("formula_id", "") or "").strip()
        for field in _split_formula_missing_fields(row.get("missing_fields")):
            key = f"{field}|{formula_id}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "field_name": field,
                    "formula_id": formula_id,
                    "category": row.get("category", ""),
                }
            )
    return pd.DataFrame(rows)


page_header(
    "Workflow",
    "A focused workspace for sourcing raw issuer data, calculating methodology formulas, and producing an indicative rating.",
    "workflow",
)
current_context_card()

methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")
if st.session_state.get("source_saved_needs_formula_run"):
    st.warning("issuer_data was updated. Run formulas again before relying on formula results or scoreboard output.")

st.subheader("Main Workflow")
st.caption("A normal user can stay here: confirm sources, fill required inputs, run formulas, then produce the indicative rating.")

issuer_data = st.session_state.get("issuer_data", {}) or {}

with st.container(border=True):
    st.markdown("**0. Deal Setup**")
    st.caption("Choose the rating methodology, issuer, and fiscal year before sourcing data.")
    preset_cols = st.columns([1, 2])
    if preset_cols[0].button("Load West Sacramento pilot settings"):
        st.session_state["methodology_id"] = "sp_local_gov_k12"
        st.session_state["issuer_name"] = "City of West Sacramento"
        st.session_state["analysis_year"] = "2026"
        st.session_state["analysis_years_included"] = ["Current", "1Y prior", "2Y prior", "3Y prior"]
        st.session_state["state_fips"] = "06"
        st.session_state["county_fips"] = "113"
        st.session_state["guided_source_mode"] = True
        clear_downstream_state()
        st.session_state["setup_saved_notice"] = "West Sacramento pilot settings loaded. Source/formula/rating outputs were reset for a clean run."
        st.rerun()
    preset_cols[1].caption("Recommended while we finish the pilot. It sets S&P Local Gov/K-12, 2026, CA/Yolo County.")
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

issuer_data = st.session_state.get("issuer_data", {}) or {}
formula_results = st.session_state.get("methodology_formula_results")
if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
    formula_results = st.session_state.get("formula_results")

with st.container(border=True):
    st.markdown("**2. Formula Calculation & Scoreboard**")
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
                    "Full debug details are in Audit & Advanced > Developer Tools > Advanced Diagnostics."
                )
            if not confirmed_formula_inputs.empty:
                with st.expander("Confirmed inputs applied to formula engine", expanded=True):
                    st.dataframe(clean_for_display(confirmed_formula_inputs), width="stretch", hide_index=True)
            st.success(f"Saved {len(formula_results)} formula results.")
            st.session_state["source_saved_needs_formula_run"] = False
        except Exception as exc:
            st.error("Could not run formulas from issuer_data.")
            st.exception(exc)

    formula_results = st.session_state.get("methodology_formula_results")
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        formula_results = st.session_state.get("formula_results")
    formula_results_for_display = _formula_results_with_manual_scores(formula_results)
    formula_counts = status_counts(formula_results_for_display, "status")
    if formula_counts:
        formula_action(formula_counts, formula_results_for_display)
        show_cols = ["formula_id", "formula_name", "category", "status", "value", "missing_fields", "error"]
        st.dataframe(
            clean_for_display(
                formula_results_for_display[[c for c in show_cols if c in formula_results_for_display.columns]]
            ),
            width="stretch",
            hide_index=True,
        )
        raw_missing_inputs = formula_missing_raw_fields(formula_results_for_display)
        raw_blockers_present = not raw_missing_inputs.empty
        if raw_blockers_present:
            st.info(
                "Resolve all missing raw inputs in Source Data > Step 3 > Missing inputs, then save all inputs and rerun formulas."
            )
        if isinstance(formula_results_for_display, pd.DataFrame) and "missing_fields" in formula_results_for_display.columns:
            missing_text = ";".join(formula_results_for_display["missing_fields"].fillna("").astype(str).tolist())
            if "issuer_population" in missing_text:
                st.info(
                    "Debt and pension per-capita formulas need `issuer_population`, not county population. "
                    "For West Sacramento this should come from the CreditScope Population row or the issuer population in ACFR/OS. "
                    "Re-save issuer_data after reuploading the CreditScope workbook, or type `issuer_population` in the value table."
                )
        if "warning" in formula_results_for_display.columns:
            warning_rows = formula_results_for_display[
                formula_results_for_display["warning"].fillna("").astype(str).str.strip().ne("")
            ]
            if not warning_rows.empty:
                with st.expander("Formula notes and source warnings", expanded=False):
                    st.caption(
                        "These notes explain source provenance or review hints. Developer-level direct metric diagnostics live in Audit & Advanced > Developer Tools."
                    )
                    note_cols = ["formula_id", "status", "value", "warning"]
                    st.dataframe(
                        clean_for_display(warning_rows[[c for c in note_cols if c in warning_rows.columns]]),
                        width="stretch",
                        hide_index=True,
                    )
        if methodology_id == "sp_local_gov_k12":
            diagnostics = local_gov_formula_diagnostics(formula_results_for_display, issuer_data)
            if not diagnostics.empty:
                st.session_state["local_gov_formula_diagnostics"] = diagnostics
                st.caption(
                    "S&P Local Gov formula diagnostics are available in Audit & Advanced > Developer Tools > Advanced Diagnostics."
                )

        st.markdown("**Scoreboard**")
        try:
            template = load_factor_template(methodology_id, templates_dir="templates")
            missing_manual = _missing_manual_score_ids(methodology_id, template)
        except Exception as exc:
            template = pd.DataFrame()
            missing_manual = []
            st.warning(f"Could not load scoreboard template: {exc}")

        if raw_blockers_present:
            st.info("The scoreboard unlocks after the unified Missing inputs table is filled and formulas rerun.")
        if missing_manual:
            st.info(
                "Fill the manual rating input(s) in Source Data before running the scoreboard: "
                + ", ".join(missing_manual)
            )
        can_run_scoreboard = not raw_blockers_present and not missing_manual
        if st.button("Run scoreboard from current results", type="primary", disabled=not can_run_scoreboard):
            try:
                output = run_rating_engine(
                    methodology_id=methodology_id,
                    formula_results=st.session_state.get("formula_results", formula_results),
                    manual_scores=st.session_state.get("manual_scores", {}) or {},
                    thresholds_path="config/scoring_thresholds.csv",
                    templates_dir="templates",
                )
                st.session_state["rating_output"] = output
                st.success("Scoreboard output saved.")
            except Exception as exc:
                st.error("Could not run the scoreboard.")
                st.exception(exc)
    else:
        st.info("Formula results have not been created yet.")

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
            rating_summary = clean_for_display(summarize_rating_output(rating_output))
            if not rating_summary.empty:
                rating_summary = rating_summary.loc[
                    :,
                    [
                        col
                        for col in rating_summary.columns
                        if rating_summary[col].fillna("").astype(str).str.strip().ne("").any()
                    ],
                ]
            st.dataframe(rating_summary, width="stretch", hide_index=True)
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
