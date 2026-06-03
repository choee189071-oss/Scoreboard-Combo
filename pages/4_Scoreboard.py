from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd
import streamlit as st
from utils.ui_helpers import page_header, current_context_card, init_state

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from engine.rating_engine import run_rating_engine, summarize_rating_output
except Exception:
    run_rating_engine = None
    summarize_rating_output = None

st.set_page_config(page_title="Scoreboard", page_icon="④", layout="wide")
init_state()
page_header("④ Scoreboard", "Run factor aggregation and indicative rating. This is the first full visual scoreboard layer.", "scoreboard")
current_context_card()

formula_df = st.session_state.get("formula_results")
if not isinstance(formula_df, pd.DataFrame) or formula_df.empty:
    st.warning("No formula results found. Go to Calculators first.")
    st.stop()

methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")

st.subheader("Input formula results")
st.dataframe(formula_df, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Manual metric scores for MVP visualization")
st.caption("This bridges formula values to Moody/S&P scorecard scores while threshold QA continues.")

score_rows = formula_df[["formula_id", "formula_name", "value", "status"]].copy()
score_rows["numeric_score"] = None
score_rows["score_label"] = ""

# Practical defaults for Contra Costa-style Moody CCD demo.
default_scores = {
    "full_value_per_capita": 1.0,
    "mfi_pct_us": 1.0,
    "available_fund_balance_ratio": 1.67,
    "cash_balance_ratio": 1.0,
    "net_direct_debt_to_full_value": 1.0,
    "net_direct_debt_to_revenue": 3.0,
    "net_pension_liability_to_full_value": 1.0,
    "net_pension_liability_to_revenue": 3.0,
    "operating_history": 1.0,
}
for idx, row in score_rows.iterrows():
    fid = row["formula_id"]
    if fid in default_scores:
        score_rows.at[idx, "numeric_score"] = default_scores[fid]

edited = st.data_editor(
    score_rows,
    use_container_width=True,
    hide_index=True,
    column_config={"numeric_score": st.column_config.NumberColumn("numeric_score", min_value=0.0, max_value=30.0, step=0.01)},
)

benchmark_rating = st.text_input("Benchmark rating optional", value="Aa1" if methodology_id == "moodys_ccd_go" else "")

if st.button("Run scoreboard", type="primary"):
    formula_scored = edited.copy()
    # Ensure numeric score column is respected by factor/rating engine.
    formula_scored["numeric_score"] = pd.to_numeric(formula_scored["numeric_score"], errors="coerce")

    if run_rating_engine is not None:
        try:
            output = run_rating_engine(
                methodology_id=methodology_id,
                formula_results=formula_scored,
                thresholds_path="config/scoring_thresholds.csv",
                templates_dir="templates",
            )
            st.session_state["rating_output"] = output
        except Exception as exc:
            st.error("Rating engine failed. Showing fallback weighted average instead.")
            st.exception(exc)
            output = None
    else:
        output = None

    if output:
        rr = output.get("rating_result", {})
        c1, c2, c3 = st.columns(3)
        c1.metric("Indicative Rating", rr.get("indicative_rating", "Missing"))
        overall = rr.get("overall_score")
        c2.metric("Weighted Score", "" if overall is None else f"{float(overall):.3f}")
        c3.metric("Coverage", rr.get("coverage_status", "unknown"))

        if summarize_rating_output:
            with st.expander("Rating summary", expanded=True):
                st.dataframe(summarize_rating_output(output), use_container_width=True, hide_index=True)

        fe = output.get("factor_engine_output", {}) or {}
        tabs = st.tabs(["Metric Scores", "Factor Scores", "Section Scores"])
        with tabs[0]:
            df = fe.get("metric_scores", pd.DataFrame())
            st.dataframe(df, use_container_width=True, hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No metric table returned.")
        with tabs[1]:
            df = fe.get("factor_scores", pd.DataFrame())
            st.dataframe(df, use_container_width=True, hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No factor table returned.")
        with tabs[2]:
            df = fe.get("section_scores", pd.DataFrame())
            st.dataframe(df, use_container_width=True, hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No section table returned.")
    else:
        ready = edited.dropna(subset=["numeric_score"])
        fallback_score = ready["numeric_score"].mean() if not ready.empty else None
        st.metric("Fallback average score", "Missing" if fallback_score is None else f"{fallback_score:.3f}")

st.info("After this page produces a stable result, use Validation to compare it against the official scorecard.")
