from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import streamlit as st
from utils.ui_helpers import page_header, current_context_card, init_state

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from engine.factor_engine import load_factor_template
    from engine.rating_engine import run_rating_engine, summarize_rating_output
except Exception as exc:  # pragma: no cover - Streamlit display path
    st.set_page_config(page_title="Scoreboard", page_icon="④", layout="wide")
    st.error("Could not import rating/factor engines.")
    st.exception(exc)
    st.stop()


st.set_page_config(page_title="Scoreboard", page_icon="④", layout="wide")
init_state()
page_header(
    "④ Scoreboard",
    "Use thresholds, manual qualitative inputs, factor aggregation, and rating anchor logic from the engine.",
    "scoreboard",
)
current_context_card()

formula_df = st.session_state.get("formula_results")
if not isinstance(formula_df, pd.DataFrame) or formula_df.empty:
    st.warning("No formula results found. Go to Calculators first.")
    st.stop()

methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")

try:
    template = load_factor_template(methodology_id, templates_dir="templates")
except Exception as exc:
    st.error("Could not load methodology template.")
    st.exception(exc)
    st.stop()

template_formula_ids = set(template["formula_id"].dropna().astype(str))
method_formula_df = formula_df[formula_df["formula_id"].astype(str).isin(template_formula_ids)].copy()


def _render_rating_output(output: Dict[str, Any], benchmark_rating: str) -> None:
    rr = output.get("rating_result", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Indicative Rating", rr.get("indicative_rating", "Missing") or "Missing")
    overall = rr.get("overall_score")
    c2.metric("Weighted Score", "" if overall is None else f"{float(overall):.3f}")
    c3.metric("Coverage", rr.get("coverage_status", "unknown"))
    c4.metric("Benchmark", benchmark_rating or "Not provided")

    with st.expander("Rating summary", expanded=True):
        st.dataframe(summarize_rating_output(output), use_container_width=True, hide_index=True)

    warnings = rr.get("warnings", []) or []
    if warnings:
        with st.expander("Warnings / missing logic", expanded=True):
            for warning in warnings:
                st.warning(str(warning))

    fe = output.get("factor_engine_output", {}) or {}
    tabs = st.tabs(["Metric Scores", "Factor Scores", "Section Scores", "Auto Scores"])
    with tabs[0]:
        df = fe.get("metric_scores", pd.DataFrame())
        st.dataframe(df, use_container_width=True, hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No metric table returned.")
    with tabs[1]:
        df = fe.get("factor_scores", pd.DataFrame())
        st.dataframe(df, use_container_width=True, hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No factor table returned.")
    with tabs[2]:
        df = fe.get("section_scores", pd.DataFrame())
        st.dataframe(df, use_container_width=True, hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No section table returned.")
    with tabs[3]:
        auto_scores = output.get("scored_metric_overrides", {}) or {}
        if auto_scores:
            auto_df = pd.DataFrame.from_dict(auto_scores, orient="index")
            auto_df.insert(0, "formula_id", auto_df.index)
            st.dataframe(auto_df.reset_index(drop=True), use_container_width=True, hide_index=True)
        else:
            st.info("No automatic threshold scores were produced.")

st.subheader("Input Formula Results")
st.caption("Only template formula_id values are shown here. Full formula_results stay available to the rating engine.")
st.dataframe(method_formula_df, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Manual Qualitative Scores")
st.caption("Only true manual/template qualitative fields should be entered here. Numeric thresholds are handled by rating_engine.")

manual_candidates = template[
    template["source_priority"].astype(str).str.contains("Manual", case=False, na=False)
].copy()
