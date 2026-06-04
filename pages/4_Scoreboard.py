from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import streamlit as st
from utils.ui_helpers import action_panel, current_context_card, init_state, page_header, status_counts

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
        st.dataframe(summarize_rating_output(output), width="stretch", hide_index=True)

    warnings = rr.get("warnings", []) or []
    if warnings:
        with st.expander("Warnings / missing logic", expanded=True):
            for warning in warnings:
                st.warning(str(warning))

    fe = output.get("factor_engine_output", {}) or {}
    tabs = st.tabs(["Metric Scores", "Factor Scores", "Section Scores", "Auto Scores"])
    with tabs[0]:
        df = fe.get("metric_scores", pd.DataFrame())
        st.dataframe(df, width="stretch", hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No metric table returned.")
    with tabs[1]:
        df = fe.get("factor_scores", pd.DataFrame())
        st.dataframe(df, width="stretch", hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No factor table returned.")
    with tabs[2]:
        df = fe.get("section_scores", pd.DataFrame())
        st.dataframe(df, width="stretch", hide_index=True) if isinstance(df, pd.DataFrame) and not df.empty else st.info("No section table returned.")
    with tabs[3]:
        auto_scores = output.get("scored_metric_overrides", {}) or {}
        if auto_scores:
            auto_df = pd.DataFrame.from_dict(auto_scores, orient="index")
            auto_df.insert(0, "formula_id", auto_df.index)
            st.dataframe(auto_df.reset_index(drop=True), width="stretch", hide_index=True)
        else:
            st.info("No automatic threshold scores were produced.")

with st.expander("Input formula results", expanded=False):
    st.caption("Only template formula_id values are shown here. Full formula_results stay available to the rating engine.")
    st.dataframe(method_formula_df, width="stretch", hide_index=True)

st.divider()
st.subheader("Manual Qualitative Scores")
st.caption("Only true qualitative fields should be entered here. Numeric threshold scoring is handled by rating_engine.")

manual_candidates = template[
    template["source_priority"].astype(str).str.contains("Manual", case=False, na=False)
].copy()
formula_status = formula_df.set_index("formula_id")["status"].to_dict() if "status" in formula_df.columns else {}
manual_candidates["formula_status"] = manual_candidates["formula_id"].map(formula_status).fillna("missing")

manual_scores: Dict[str, Any] = {}
if manual_candidates.empty:
    st.info("No manual qualitative fields found in this template.")
else:
    manual_rows = manual_candidates[
        ["section", "factor", "metric", "formula_id", "formula_status"]
    ].drop_duplicates("formula_id")
    manual_rows["numeric_score"] = None
    manual_rows["score_label"] = ""

    edited_manual = st.data_editor(
        manual_rows,
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        column_config={
            "numeric_score": st.column_config.NumberColumn(
                "numeric_score", min_value=0.0, max_value=30.0, step=0.01
            ),
            "score_label": st.column_config.TextColumn("score_label"),
            "section": st.column_config.TextColumn("section", disabled=True),
            "factor": st.column_config.TextColumn("factor", disabled=True),
            "metric": st.column_config.TextColumn("metric", disabled=True),
            "formula_id": st.column_config.TextColumn("formula_id", disabled=True),
            "formula_status": st.column_config.TextColumn("formula_status", disabled=True),
        },
    )

    for _, row in edited_manual.iterrows():
        fid = str(row.get("formula_id", "")).strip()
        if not fid:
            continue
        numeric = pd.to_numeric(row.get("numeric_score"), errors="coerce")
        label = str(row.get("score_label", "") or "").strip()
        if pd.notna(numeric):
            manual_scores[fid] = {"numeric_score": float(numeric), "score_label": label}
        elif label:
            manual_scores[fid] = {"score_label": label}

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
