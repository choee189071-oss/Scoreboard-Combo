"""
Validation Workspace for Scoreboard-Combo / CreditScope MVP
===========================================================

Purpose
-------
This page is the first end-to-end QA workspace for the rating model.
It lets you validate whether the model output matches a benchmark scorecard.

Supported validation modes
--------------------------
1. Quick rating check
   - Moody's: weighted score -> rating
   - S&P Local Gov: IF + ICP -> anchor/rating
   - S&P Water/Sewer or Education: Enterprise + Financial -> anchor/rating

2. Formula-results validation
   - Upload a CSV/XLSX with formula_id + value/status/numeric_score columns.
   - The rating engine will score available metrics, aggregate factors, and produce a rating.

3. Manual metric-score validation
   - Loads the selected methodology template.
   - You can paste/enter numeric scores directly for each formula_id.
   - Useful when replicating an official scorecard row-by-row.

Expected project structure
--------------------------
config/scoring_thresholds.csv
engine/factor_engine.py
engine/rating_engine.py
templates/*.csv
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import streamlit as st

# Make imports work when Streamlit runs from the project root or from pages/.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from engine.factor_engine import list_supported_schemes, load_factor_template
    from engine.rating_engine import (
        compare_to_benchmark,
        run_rating_engine,
        summarize_rating_output,
    )
    from engine.official_fixture_engine import (
        compare_metric_scores_to_fixture,
        list_official_fixture_files,
        load_fixture_catalog,
        load_official_fixture,
        official_fixture_report,
    )
except Exception as exc:  # pragma: no cover - Streamlit display path
    st.set_page_config(page_title="Validation", page_icon="🧪", layout="wide")
    st.error("Could not import engine modules. Please confirm engine/factor_engine.py and engine/rating_engine.py exist.")
    st.exception(exc)
    st.stop()


st.set_page_config(page_title="Validation", page_icon="🧪", layout="wide")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

SCHEME_LABELS = {
    "moodys_ccd_go": "Moody's CCD GO",
    "moodys_k12": "Moody's K-12",
    "sp_local_gov_k12": "S&P Local Gov / K-12 GO",
    "sp_local_gov": "S&P Local Government",
    "sp_us_government_2024": "S&P U.S. Government 2024",
    "sp_water_sewer": "S&P Water / Sewer Utility",
    "sp_community_college_go": "S&P Community College GO",
}


def _read_uploaded_table(uploaded_file) -> pd.DataFrame:
    """Read CSV/XLSX into a DataFrame."""
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file)
    raise ValueError("Please upload a .csv, .xlsx, or .xls file.")


def _clean_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"none", "nan", "na", "n/a", "-", "--"}:
        return None
    try:
        return float(text)
    except Exception:
        return None


def _show_rating_result(output: Dict[str, Any], benchmark_rating: str = "", benchmark_score: Optional[float] = None) -> None:
    """Render rating-engine output."""
    rr = output.get("rating_result", {})
    rating = rr.get("indicative_rating", "")
    overall = rr.get("overall_score")
    coverage = rr.get("coverage_status", "")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Model Rating", rating or "Missing")
    col2.metric("Model Weighted Score", "" if overall is None else f"{float(overall):.3f}")
    col3.metric("Coverage", coverage or "unknown")
    if benchmark_rating:
        cmp = compare_to_benchmark(output, benchmark_rating=benchmark_rating, benchmark_score=benchmark_score)
        col4.metric("Benchmark Match", "PASS ✅" if cmp["rating_match"] else "CHECK ⚠️")
    else:
        col4.metric("Benchmark Match", "Not provided")

    summary_df = summarize_rating_output(output)
    with st.expander("Rating summary", expanded=True):
        st.dataframe(summary_df, use_container_width=True, hide_index=True)

    if benchmark_rating:
        cmp = compare_to_benchmark(output, benchmark_rating=benchmark_rating, benchmark_score=benchmark_score)
        with st.expander("Benchmark comparison", expanded=True):
            st.dataframe(pd.DataFrame([cmp]), use_container_width=True, hide_index=True)

    warnings = rr.get("warnings", []) or []
    if warnings:
        with st.expander("Warnings / missing logic", expanded=True):
            for w in warnings:
                st.warning(str(w))

    factor_output = output.get("factor_engine_output", {}) or {}
    metric_df = factor_output.get("metric_scores", pd.DataFrame())
    factor_df = factor_output.get("factor_scores", pd.DataFrame())
    section_df = factor_output.get("section_scores", pd.DataFrame())

    tab1, tab2, tab3, tab4 = st.tabs(["Metric Scores", "Factor Scores", "Section/Profile Scores", "Downloads"])
    with tab1:
        if isinstance(metric_df, pd.DataFrame) and not metric_df.empty:
            st.dataframe(metric_df, use_container_width=True, hide_index=True)
        else:
            st.info("No metric-level table returned for this mode.")
    with tab2:
        if isinstance(factor_df, pd.DataFrame) and not factor_df.empty:
            st.dataframe(factor_df, use_container_width=True, hide_index=True)
        else:
            st.info("No factor-level table returned for this mode.")
    with tab3:
        if isinstance(section_df, pd.DataFrame) and not section_df.empty:
            st.dataframe(section_df, use_container_width=True, hide_index=True)
        else:
            st.info("No section/profile-level table returned for this mode.")
    with tab4:
        if isinstance(summary_df, pd.DataFrame) and not summary_df.empty:
            st.download_button(
                "Download rating summary CSV",
                data=summary_df.to_csv(index=False).encode("utf-8"),
                file_name="validation_rating_summary.csv",
                mime="text/csv",
            )
        if isinstance(metric_df, pd.DataFrame) and not metric_df.empty:
            st.download_button(
                "Download metric scores CSV",
                data=metric_df.to_csv(index=False).encode("utf-8"),
                file_name="validation_metric_scores.csv",
                mime="text/csv",
            )
        if isinstance(factor_df, pd.DataFrame) and not factor_df.empty:
            st.download_button(
                "Download factor scores CSV",
                data=factor_df.to_csv(index=False).encode("utf-8"),
                file_name="validation_factor_scores.csv",
                mime="text/csv",
            )


def _make_factor_output_for_quick_check(methodology_id: str, overall_score: Optional[float] = None,
                                        if_score: Optional[float] = None,
                                        icp_score: Optional[float] = None,
                                        enterprise_score: Optional[float] = None,
                                        financial_score: Optional[float] = None) -> Dict[str, Any]:
    """Create minimal factor_engine_output for rating-engine smoke checks."""
    factor_df = pd.DataFrame()
    section_df = pd.DataFrame()

    if methodology_id in {"sp_local_gov", "sp_local_gov_k12", "sp_us_government_2024"}:
        factor_df = pd.DataFrame([
            {"section": "Institutional Framework", "factor": "Institutional Framework", "factor_score": if_score, "status": "ready"}
        ])
        section_df = pd.DataFrame([
            {"section": "Individual Credit Profile Assessment", "section_score": icp_score, "status": "ready"}
        ])
        overall_score = icp_score

    elif methodology_id in {"sp_water_sewer", "sp_community_college_go"}:
        section_df = pd.DataFrame([
            {"section": "Enterprise Profile", "section_score": enterprise_score, "status": "ready"},
            {"section": "Financial Profile", "section_score": financial_score, "status": "ready"},
        ])
        if enterprise_score is not None and financial_score is not None:
            overall_score = (enterprise_score + financial_score) / 2

    return {
        "overall_score": overall_score,
        "factor_scores": factor_df,
        "section_scores": section_df,
        "metric_scores": pd.DataFrame(),
        "coverage_summary": {"metrics_missing": 0, "metrics_manual": 0, "metrics_need_score": 0, "metrics_error": 0},
    }


# -----------------------------------------------------------------------------
# Page UI
# -----------------------------------------------------------------------------

st.title("🧪 Validation Workspace")
st.caption("Use this page to compare CreditScope model outputs against official scorecards before building more UI.")

try:
    supported = list_supported_schemes()
    scheme_ids = supported["methodology_id"].tolist()
except Exception:
    scheme_ids = list(SCHEME_LABELS.keys())

left, right = st.columns([2, 1])
with left:
    methodology_id = st.selectbox(
        "Methodology / scheme",
        options=scheme_ids,
        format_func=lambda x: SCHEME_LABELS.get(x, x),
        index=scheme_ids.index("moodys_ccd_go") if "moodys_ccd_go" in scheme_ids else 0,
    )
with right:
    threshold_path = st.text_input("Threshold file", value="config/scoring_thresholds.csv")

tpl_msg = ""
try:
    tpl = load_factor_template(methodology_id, templates_dir="templates")
    tpl_msg = f"Template loaded: {len(tpl)} metrics."
    st.success(tpl_msg)
except Exception as exc:
    st.warning(f"Could not load template for {methodology_id}: {exc}")
    tpl = pd.DataFrame()

st.divider()

mode = st.radio(
    "Validation mode",
    [
        "Official fixture comparison",
        "Quick rating check",
        "Formula-results upload",
        "Manual metric-score entry",
    ],
    horizontal=True,
)

benchmark_col1, benchmark_col2, benchmark_col3 = st.columns([1, 1, 1])
with benchmark_col1:
    issuer_name = st.text_input("Issuer / test case", value="Contra Costa CCD" if methodology_id == "moodys_ccd_go" else "")
with benchmark_col2:
    benchmark_rating = st.text_input("Official / benchmark rating", value="Aa1" if methodology_id == "moodys_ccd_go" else "")
with benchmark_col3:
    benchmark_score = _clean_optional_float(st.text_input("Official weighted score optional", value="1.52" if methodology_id == "moodys_ccd_go" else ""))

st.divider()

if mode == "Official fixture comparison":
    st.subheader("Official fixture comparison")
    st.write("Load a reference scorecard fixture and compare model aggregation against official subfactor values, scores, weights, profiles, and rating.")

    fixture_dir = PROJECT_ROOT / "config" / "validation_fixtures"
    fixtures = list_official_fixture_files(fixture_dir)
    if not fixtures:
        st.info("No validation fixtures found under config/validation_fixtures.")
    else:
        catalog = load_fixture_catalog(fixture_dir)
        if not catalog.empty:
            with st.expander("Fixture catalog", expanded=True):
                st.dataframe(catalog, use_container_width=True, hide_index=True)

        def _fixture_label(key: str) -> str:
            if catalog.empty or "fixture_key" not in catalog.columns:
                return key
            row = catalog[catalog["fixture_key"] == key]
            if row.empty:
                return key
            rec = row.iloc[0]
            issuer = rec.get("issuer_name", "")
            methodology = rec.get("methodology_id", "")
            rating = rec.get("official_rating", "")
            return f"{issuer} | {methodology} | {rating}".strip(" |")

        selected_fixture = st.selectbox("Fixture", options=list(fixtures.keys()), format_func=_fixture_label)
        try:
            fixture = load_official_fixture(fixtures[selected_fixture])
            fixture_methodology = str(fixture["methodology_id"].iloc[0])
            fixture_rating = str(fixture["official_rating"].iloc[0])
            fixture_score = float(fixture["official_weighted_score"].iloc[0])

            report = official_fixture_report(
                fixture,
                thresholds_path=threshold_path,
                templates_dir="templates",
            )
            output = report["output"]

            st.session_state["validation_output"] = output
            with st.expander("Official fixture rows", expanded=False):
                st.dataframe(fixture, use_container_width=True, hide_index=True)

            _show_rating_result(output, benchmark_rating=fixture_rating, benchmark_score=fixture_score)

            with st.expander("Official summary and model comparison", expanded=True):
                c1, c2 = st.columns(2)
                c1.dataframe(report["fixture_summary"], use_container_width=True, hide_index=True)
                c2.dataframe(report["rating_comparison"], use_container_width=True, hide_index=True)

            with st.expander("Official vs model metric comparison", expanded=True):
                comparison = report["metric_comparison"]
                if comparison.empty:
                    st.info("No metric-level model output available for comparison.")
                else:
                    st.dataframe(comparison, use_container_width=True, hide_index=True)

            current_output = st.session_state.get("rating_output")
            if isinstance(current_output, dict):
                with st.expander("Current Scoreboard output vs official fixture", expanded=False):
                    current_comparison = compare_metric_scores_to_fixture(current_output, fixture)
                    if current_comparison.empty:
                        st.info("No current Scoreboard metric output available.")
                    else:
                        st.dataframe(current_comparison, use_container_width=True, hide_index=True)
        except Exception as exc:
            st.error("Could not run fixture comparison.")
            st.exception(exc)

elif mode == "Quick rating check":
    st.subheader("Quick rating check")
    st.write("Fastest way to verify the final rating mapping before testing raw formulas.")

    if methodology_id in {"moodys_ccd_go", "moodys_k12"}:
        default_score = 1.52 if methodology_id == "moodys_ccd_go" else 3.75
        weighted_score = st.number_input("Model weighted score", min_value=0.0, max_value=30.0, value=float(default_score), step=0.01)
        factor_output = _make_factor_output_for_quick_check(methodology_id, overall_score=weighted_score)

    elif methodology_id in {"sp_local_gov", "sp_local_gov_k12", "sp_us_government_2024"}:
        c1, c2 = st.columns(2)
        if_score = c1.number_input("Institutional Framework score", min_value=1.0, max_value=6.0, value=2.0, step=0.5)
        icp_score = c2.number_input("Individual Credit Profile / ICP score", min_value=1.0, max_value=6.0, value=2.5, step=0.1)
        factor_output = _make_factor_output_for_quick_check(methodology_id, if_score=if_score, icp_score=icp_score)

    else:
        c1, c2 = st.columns(2)
        enterprise_score = c1.number_input("Enterprise Profile score", min_value=1.0, max_value=6.0, value=2.0, step=0.5)
        financial_score = c2.number_input("Financial Profile score", min_value=1.0, max_value=6.0, value=2.0, step=0.5)
        factor_output = _make_factor_output_for_quick_check(methodology_id, enterprise_score=enterprise_score, financial_score=financial_score)

    if st.button("Run quick validation", type="primary"):
        output = run_rating_engine(
            methodology_id=methodology_id,
            factor_engine_output=factor_output,
            thresholds_path=threshold_path,
            templates_dir="templates",
        )
        st.session_state["validation_output"] = output
        _show_rating_result(output, benchmark_rating=benchmark_rating, benchmark_score=benchmark_score)

elif mode == "Formula-results upload":
    st.subheader("Formula-results upload")
    st.write("Upload a table with at least `formula_id` and `value`. If it already has `numeric_score`, the engine will use it directly.")

    sample = pd.DataFrame({
        "formula_id": ["full_value_per_capita", "cash_balance_ratio"],
        "status": ["ready", "ready"],
        "value": [243176, 0.28],
    })
    with st.expander("Expected upload format"):
        st.dataframe(sample, use_container_width=True, hide_index=True)
        st.download_button(
            "Download sample formula_results CSV",
            data=sample.to_csv(index=False).encode("utf-8"),
            file_name="sample_formula_results.csv",
            mime="text/csv",
        )

    uploaded = st.file_uploader("Upload formula results CSV/XLSX", type=["csv", "xlsx", "xls"])

    manual_scores: Dict[str, Any] = {}
    if methodology_id in {"sp_local_gov", "sp_local_gov_k12", "sp_us_government_2024"}:
        with st.expander("Manual S&P Local Gov fields"):
            if_score = st.number_input("Institutional Framework score", min_value=1.0, max_value=6.0, value=2.0, step=0.5)
            manual_scores["institutional_framework_rating"] = if_score

    if uploaded is not None:
        try:
            formula_df = _read_uploaded_table(uploaded)
            st.dataframe(formula_df.head(50), use_container_width=True, hide_index=True)
            if "formula_id" not in formula_df.columns:
                st.error("The uploaded file must include a formula_id column.")
            elif st.button("Run formula-results validation", type="primary"):
                output = run_rating_engine(
                    methodology_id=methodology_id,
                    formula_results=formula_df,
                    manual_scores=manual_scores,
                    thresholds_path=threshold_path,
                    templates_dir="templates",
                )
                st.session_state["validation_output"] = output
                _show_rating_result(output, benchmark_rating=benchmark_rating, benchmark_score=benchmark_score)
        except Exception as exc:
            st.error("Could not process uploaded formula results.")
            st.exception(exc)

else:
    st.subheader("Manual metric-score entry")
    st.write("Use this when you are manually replicating an official scorecard and want to test aggregation + rating only.")

    if tpl.empty:
        st.info("Template unavailable, so manual metric entry cannot be shown.")
    else:
        editable = tpl[["section", "factor", "metric", "formula_id", "factor_weight", "metric_weight"]].copy()
        editable["numeric_score"] = None
        editable["raw_value_optional"] = None
        editable["notes"] = ""

        edited = st.data_editor(
            editable,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            column_config={
                "numeric_score": st.column_config.NumberColumn("numeric_score", min_value=0.0, max_value=30.0, step=0.01),
                "raw_value_optional": st.column_config.TextColumn("raw_value_optional"),
            },
        )

        metric_scores: Dict[str, Dict[str, Any]] = {}
        formula_records = []
        for _, row in edited.iterrows():
            fid = str(row.get("formula_id", "")).strip()
            score = _clean_optional_float(row.get("numeric_score"))
            raw_value = row.get("raw_value_optional")
            if not fid:
                continue
            if score is not None:
                metric_scores[fid] = {"numeric_score": score, "score_label": "manual"}
                formula_records.append({"formula_id": fid, "status": "ready", "value": raw_value, "numeric_score": score})
            elif raw_value not in [None, ""]:
                formula_records.append({"formula_id": fid, "status": "ready", "value": raw_value})

        manual_scores = {}
        if methodology_id in {"sp_local_gov", "sp_local_gov_k12", "sp_us_government_2024"}:
            with st.expander("Manual S&P Local Gov fields"):
                manual_scores["institutional_framework_rating"] = st.number_input(
                    "Institutional Framework score", min_value=1.0, max_value=6.0, value=2.0, step=0.5
                )

        if st.button("Run manual-score validation", type="primary"):
            formula_df = pd.DataFrame(formula_records)
            output = run_rating_engine(
                methodology_id=methodology_id,
                formula_results=formula_df,
                metric_scores=metric_scores,
                manual_scores=manual_scores,
                thresholds_path=threshold_path,
                templates_dir="templates",
            )
            st.session_state["validation_output"] = output
            _show_rating_result(output, benchmark_rating=benchmark_rating, benchmark_score=benchmark_score)

st.divider()
with st.expander("What to do with failures"):
    st.markdown(
        """
        If the model does not match the official scorecard, debug in this order:

        1. **Metric Scores** — check whether each subfactor score matches the official scorecard.
        2. **Raw Values** — check units: percent vs decimal, `$000s` vs dollars, `x` ratio vs raw number.
        3. **Thresholds** — check boundary logic: `<`, `<=`, `>`, `>=`.
        4. **Factor Weights** — check whether metric weights sum correctly inside each factor.
        5. **Rating Mapping** — check weighted score bucket / S&P anchor matrix.
        """
    )
