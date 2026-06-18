from __future__ import annotations

from typing import Any, Iterable

import pandas as pd
import streamlit as st

from engine.calculator_engine import calculate_all_formulas
from engine.data_sourcing_engine import (
    normalize_source_candidates,
    required_fields_for_methodology,
    run_data_sourcing_pipeline,
)
from engine.source_confirmation import (
    QUEUE_DECISIONS,
    build_source_confirmation_queue,
    confirmation_queue_to_source_candidates,
    merge_saved_queue_decisions,
)
from engine.factor_engine import load_factor_template
from utils.ui_helpers import clean_for_display


def _frame_from_state(key: str) -> pd.DataFrame:
    frame = st.session_state.get(key)
    return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()


def _base_candidate_frame(source_candidates_key: str) -> pd.DataFrame:
    candidates = _frame_from_state(source_candidates_key)
    if candidates.empty:
        return candidates
    if "source_detail" in candidates.columns:
        candidates = candidates[
            ~candidates["source_detail"].fillna("").astype(str).eq("human_confirmed_source_candidate")
        ].copy()
    return candidates


def _methodology_formula_results(methodology_id: str, formula_results: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        return pd.DataFrame()
    try:
        template = load_factor_template(methodology_id, templates_dir="templates")
    except Exception:
        return formula_results.copy()
    if template.empty or "formula_id" not in template.columns or "formula_id" not in formula_results.columns:
        return formula_results.copy()
    ids = set(template["formula_id"].dropna().astype(str).str.strip())
    return formula_results[formula_results["formula_id"].astype(str).isin(ids)].copy()


def _run_confirmation_pipeline(
    *,
    methodology_id: str,
    source_candidates_key: str,
    approved_candidates_key: str,
    source_report_key: str,
    issuer_data_key: str,
    source_readiness_key: str,
    source_candidates_output_key: str | None,
    extra_candidate_frames: Iterable[pd.DataFrame] | None,
    recalculate_formulas: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    base = _base_candidate_frame(source_candidates_key)
    approved = _frame_from_state(approved_candidates_key)
    frames = [frame for frame in [base, approved, *(extra_candidate_frames or [])] if isinstance(frame, pd.DataFrame) and not frame.empty]
    required = required_fields_for_methodology(methodology_id)
    result = run_data_sourcing_pipeline(frames, methodology_id=methodology_id, required_fields=required)
    st.session_state[source_report_key] = result["source_report"]
    st.session_state[issuer_data_key] = result["issuer_data"]
    st.session_state[source_readiness_key] = result["source_readiness_summary"]
    if source_candidates_output_key:
        st.session_state[source_candidates_output_key] = result["source_candidates"]
    if recalculate_formulas:
        formula_results = calculate_all_formulas(result["issuer_data"])
        st.session_state["formula_results"] = formula_results
        st.session_state["methodology_formula_results"] = _methodology_formula_results(methodology_id, formula_results)
        st.session_state["rating_output"] = None
    return result["source_report"], result["issuer_data"]


def render_source_confirmation_queue(
    *,
    methodology_id: str,
    source_report_key: str = "source_report",
    source_candidates_key: str = "source_candidates",
    pdf_evidence_key: str = "source_intake_pdf_evidence",
    approved_candidates_key: str = "approved_source_candidates",
    issuer_data_key: str = "issuer_data",
    source_readiness_key: str = "source_readiness_summary",
    source_candidates_output_key: str | None = "source_candidates",
    decision_state_key: str = "source_confirmation_decisions",
    extra_candidate_frames: Iterable[pd.DataFrame] | None = None,
    recalculate_formulas: bool = False,
    show_header: bool = True,
) -> pd.DataFrame:
    """Render an Accept/Reject/Edit queue for source-pending rows."""
    source_report = _frame_from_state(source_report_key)
    evidence = _frame_from_state(pdf_evidence_key)
    saved = _frame_from_state(decision_state_key)
    queue = build_source_confirmation_queue(source_report, evidence)
    queue = merge_saved_queue_decisions(queue, saved)

    if show_header:
        st.markdown("**Source Confirmation Queue**")
        st.caption(
            "Accepting a row converts it into a ready source candidate. Rejected and pending rows stay out of issuer_data."
        )

    approved_existing = _frame_from_state(approved_candidates_key)
    metric_cols = st.columns(4)
    metric_cols[0].metric("Queue Rows", len(queue))
    metric_cols[1].metric("Accepted Candidates", len(approved_existing))
    metric_cols[2].metric(
        "Model Inputs",
        len(st.session_state.get(issuer_data_key, {}) or {})
        if isinstance(st.session_state.get(issuer_data_key, {}), dict)
        else 0,
    )
    readiness = _frame_from_state(source_readiness_key)
    pending_count = 0
    if not readiness.empty and {"readiness_status", "field_count"}.issubset(readiness.columns):
        pending = readiness[readiness["readiness_status"].astype(str).eq("source_pending")]
        pending_count = int(pd.to_numeric(pending["field_count"], errors="coerce").fillna(0).sum()) if not pending.empty else 0
    metric_cols[3].metric("Still Pending", pending_count)

    if queue.empty:
        if source_report.empty:
            st.info("No source report is available yet. Process/upload sources and save issuer_data first.")
        else:
            st.success("No source-pending rows need confirmation right now.")
        return queue

    editable_cols = [
        "decision",
        "field_name",
        "candidate_value",
        "confirmed_value",
        "source_name",
        "source_file",
        "page_or_cell",
        "citation",
        "candidate_values_from_snippet",
        "snippet",
        "review_note",
        "row_id",
    ]
    with st.form(f"{decision_state_key}_form"):
        edited = st.data_editor(
            clean_for_display(queue[[col for col in editable_cols if col in queue.columns]]),
            width="stretch",
            hide_index=True,
            num_rows="fixed",
            key=f"{decision_state_key}_editor",
            column_config={
                "decision": st.column_config.SelectboxColumn("Action", options=QUEUE_DECISIONS),
                "field_name": st.column_config.TextColumn("Field", disabled=True),
                "candidate_value": st.column_config.TextColumn("Candidate Value", disabled=True),
                "confirmed_value": st.column_config.TextColumn("Confirmed / Edited Value"),
                "source_name": st.column_config.TextColumn("Source", disabled=True),
                "source_file": st.column_config.TextColumn("File", disabled=True),
                "page_or_cell": st.column_config.TextColumn("Page / Cell", disabled=True),
                "citation": st.column_config.TextColumn("Citation", disabled=True),
                "candidate_values_from_snippet": st.column_config.TextColumn("Snippet Values", disabled=True),
                "snippet": st.column_config.TextColumn("Snippet", disabled=True),
                "review_note": st.column_config.TextColumn("Review Note"),
                "row_id": st.column_config.TextColumn("Row ID", disabled=True),
            },
        )
        button_cols = st.columns(3)
        save_only = button_cols[0].form_submit_button("Save decisions")
        apply = button_cols[1].form_submit_button("Apply accepted values", type="primary")
        clear = button_cols[2].form_submit_button("Clear saved decisions")

    if clear:
        st.session_state.pop(decision_state_key, None)
        st.success("Saved source-confirmation decisions cleared.")
        st.rerun()

    if save_only or apply:
        st.session_state[decision_state_key] = edited.copy()
        approved = confirmation_queue_to_source_candidates(edited)
        st.session_state[approved_candidates_key] = approved
        if save_only:
            st.success(f"Saved decisions. {len(approved)} accepted candidate(s) are ready to apply.")
        if apply:
            _run_confirmation_pipeline(
                methodology_id=methodology_id,
                source_candidates_key=source_candidates_key,
                approved_candidates_key=approved_candidates_key,
                source_report_key=source_report_key,
                issuer_data_key=issuer_data_key,
                source_readiness_key=source_readiness_key,
                source_candidates_output_key=source_candidates_output_key,
                extra_candidate_frames=extra_candidate_frames,
                recalculate_formulas=recalculate_formulas,
            )
            note = f"Applied {len(approved)} accepted source confirmation(s) to issuer_data."
            if recalculate_formulas:
                note += " Formula results were refreshed and the stale rating output was cleared."
            st.success(note)
            st.rerun()

    decisions = st.session_state.get(decision_state_key)
    if isinstance(decisions, pd.DataFrame) and not decisions.empty:
        st.download_button(
            "Download source_confirmation_decisions.csv",
            data=decisions.to_csv(index=False).encode("utf-8"),
            file_name="source_confirmation_decisions.csv",
            mime="text/csv",
            key=f"{decision_state_key}_download_decisions",
        )
    approved = _frame_from_state(approved_candidates_key)
    if not approved.empty:
        with st.expander("Accepted source candidates", expanded=False):
            st.dataframe(clean_for_display(approved), width="stretch", hide_index=True)
            st.download_button(
                "Download accepted_source_candidates.csv",
                data=approved.to_csv(index=False).encode("utf-8"),
                file_name="accepted_source_candidates.csv",
                mime="text/csv",
                key=f"{decision_state_key}_download_accepted",
            )
    return queue
