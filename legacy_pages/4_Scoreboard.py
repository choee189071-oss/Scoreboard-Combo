from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.manual_scores import render_manual_score_editor
from utils.ui_helpers import action_panel, clean_for_display, current_context_card, init_state, page_header, status_counts

try:
    from engine.factor_engine import load_factor_template
    from engine.rating_engine import run_rating_engine, summarize_rating_output
except Exception as exc:  # pragma: no cover - Streamlit display path
    st.set_page_config(page_title="Scoreboard", layout="wide")
    st.error("Could not import rating/factor engines.")
    st.exception(exc)
    st.stop()


st.set_page_config(page_title="Scoreboard", layout="wide")
init_state()
page_header(
    "Scoreboard",
    "Run thresholds, qualitative inputs, factor aggregation, and rating anchor logic from the engine.",
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
formula_counts = status_counts(method_formula_df, "status")

status_cols = st.columns(4)
status_cols[0].metric("Ready formulas", formula_counts.get("ready", 0))
status_cols[1].metric("Missing formulas", formula_counts.get("missing", 0))
status_cols[2].metric("Manual formulas", formula_counts.get("manual", 0))
status_cols[3].metric("Formula errors", formula_counts.get("error", 0))

if formula_counts.get("missing", 0) or formula_counts.get("error", 0):
    action_panel(
        "Scoreboard can run, but coverage is incomplete",
        "Missing formula values will reduce reliability. Use Calculators to review missing raw fields before treating the rating as final.",
        "warn",
    )
else:
    action_panel(
        "Formula inputs are ready for scoring",
        "Enter any qualitative scores below, then run the Scoreboard.",
        "good",
    )


def _render_rating_output(output: Dict[str, Any], benchmark_rating: str) -> None:
    rr = output.get("rating_result", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Indicative Rating", rr.get("indicative_rating", "Missing") or "Missing")
    overall = rr.get("overall_score")
    c2.metric("Weighted Score", "" if overall is None else f"{float(overall):.3f}")
    c3.metric("Coverage", rr.get("coverage_status", "unknown"))
    c4.metric("Benchmark", benchmark_rating or "Not provided")

    with st.expander("Rating summary", expanded=True):
        st.dataframe(clean_for_display(summarize_rating_output(output)), width="stretch", hide_index=True)

    warnings = rr.get("warnings", []) or []
    if warnings:
        with st.expander("Warnings / missing logic", expanded=True):
            for warning in warnings:
                st.warning(str(warning))

    fe = output.get("factor_engine_output", {}) or {}
    tabs = st.tabs(["Metric Scores", "Factor Scores", "Section Scores", "Auto Scores"])
    with tabs[0]:
        df = fe.get("metric_scores", pd.DataFrame())
        st.dataframe(clean_for_display(df), width="stretch", hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No metric table returned.")
    with tabs[1]:
        df = fe.get("factor_scores", pd.DataFrame())
        st.dataframe(clean_for_display(df), width="stretch", hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No factor table returned.")
    with tabs[2]:
        df = fe.get("section_scores", pd.DataFrame())
        st.dataframe(clean_for_display(df), width="stretch", hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No section table returned.")
    with tabs[3]:
        auto_scores = output.get("scored_metric_overrides", {}) or {}
        if auto_scores:
            auto_df = pd.DataFrame.from_dict(auto_scores, orient="index")
            auto_df.insert(0, "formula_id", auto_df.index)
            st.dataframe(clean_for_display(auto_df.reset_index(drop=True)), width="stretch", hide_index=True)
        else:
            st.info("No automatic threshold scores were produced.")

with st.expander("Input formula results", expanded=False):
    st.caption("Only template formula_id values are shown here. Full formula_results stay available to the rating engine.")
    st.dataframe(clean_for_display(method_formula_df), width="stretch", hide_index=True)

st.divider()
st.subheader("Manual Qualitative Scores")
st.caption(
    "Enter analyst-only scores here. S&P Local Government also needs Institutional Framework Rating for the final anchor matrix."
)
manual_scores: Dict[str, Any] = render_manual_score_editor(methodology_id, template, formula_df, key_prefix="scoreboard_manual")

benchmark_rating = st.text_input(
    "Benchmark rating optional",
    value="Aa1" if methodology_id == "moodys_ccd_go" else "",
)

if st.button("Run scoreboard", type="primary"):
    try:
        output = run_rating_engine(
            methodology_id=methodology_id,
            formula_results=formula_df,
            manual_scores=manual_scores,
            thresholds_path="config/scoring_thresholds.csv",
            templates_dir="templates",
        )
        st.session_state["rating_output"] = output
        st.session_state["manual_scores"] = manual_scores
    except Exception as exc:
        st.error("Rating engine failed.")
        st.exception(exc)
        output = None

    if output:
        st.subheader("Scoreboard Output")
        _render_rating_output(output, benchmark_rating)
elif st.session_state.get("rating_output"):
    st.subheader("Scoreboard Output")
    _render_rating_output(st.session_state["rating_output"], benchmark_rating)

action_panel(
    "Next step: Validation",
    "Compare the metric, factor, section, and final rating outputs against official fixtures before using the result externally.",
    "neutral",
)
