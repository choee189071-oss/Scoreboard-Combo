"""
Regression helpers for official scorecard fixtures.

Use this module to run raw-value validation across all fixture pairs after
changes to formula, sourcing, mapping, threshold, or rating logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from engine.calculator_engine import calculate_all_formulas
from engine.factor_engine import load_factor_template
from engine.official_fixture_engine import list_official_fixture_files, load_official_fixture
from engine.rating_engine import run_rating_engine
from engine.rating_engine import compare_to_benchmark
from engine.raw_value_validation import (
    list_raw_input_fixture_files,
    load_raw_input_fixture,
    raw_value_validation_report,
)


CLEAN_DATA_METHODOLOGIES: tuple[tuple[str, str], ...] = (
    ("moodys_ccd_go", "Moody's CCD GO"),
    ("moodys_k12", "Moody's K-12"),
    ("sp_local_gov_k12", "S&P Local Gov / K-12 GO"),
    ("sp_water_sewer", "S&P Water / Sewer Utility"),
    ("sp_community_college_go", "S&P Community College GO"),
)


SYNTHETIC_CLEAN_ISSUER_DATA: dict[str, object] = {
    "county_ebi": 95_000.0,
    "us_ebi": 90_000.0,
    "county_gdp": 17_200_000_000.0,
    "county_gdp_current": 17_200_000_000.0,
    "county_gdp_prior": 16_600_000_000.0,
    "us_gdp": 28_000_000_000_000.0,
    "us_gdp_current": 28_000_000_000_000.0,
    "us_gdp_prior": 27_200_000_000_000.0,
    "county_population": 205_000.0,
    "population_us": 335_000_000.0,
    "personal_income": 15_500_000_000.0,
    "us_personal_income": 24_000_000_000_000.0,
    "mhi_adjusted_rpp": 88_000.0,
    "us_median_income": 76_000.0,
    "issuer_mfi": 92_000.0,
    "us_mfi": 80_000.0,
    "population_current": 205_000.0,
    "population_prior": 200_000.0,
    "assessed_value_current": 21_000_000_000.0,
    "assessed_value_prior": 20_000_000_000.0,
    "service_area_population": 205_000.0,
    "poverty_rate": 0.12,
    "annual_utility_bill": 900.0,
    "median_household_ebi": 90_000.0,
    "full_value": 20_000_000_000.0,
    "tax_base_population": 205_000.0,
    "fte_enrollment": 15_000.0,
    "enrollment_current": 15_000.0,
    "enrollment_3yr_prior": 14_100.0,
    "operating_revenue": 110_000_000.0,
    "operating_expense": 95_000_000.0,
    "governmental_revenue": [110_000_000.0, 106_000_000.0, 102_000_000.0],
    "governmental_expense": [106_000_000.0, 102_500_000.0, 99_000_000.0],
    "operating_transfers": [0.0, 0.0, 0.0],
    "committed_fund_balance": [10_000_000.0, 9_500_000.0, 9_000_000.0],
    "assigned_fund_balance": [6_000_000.0, 5_500_000.0, 5_000_000.0],
    "unassigned_fund_balance": [26_000_000.0, 24_000_000.0, 22_000_000.0],
    "reserve_revenue": [110_000_000.0, 106_000_000.0, 102_000_000.0],
    "fund_balance": 42_000_000.0,
    "revenue": 110_000_000.0,
    "fund_balance_ratio_current": 0.38,
    "fund_balance_ratio_5yr_prior": 0.34,
    "cash": 50_000_000.0,
    "cash_ratio_current": 0.45,
    "cash_ratio_5yr_prior": 0.41,
    "cash_and_investments": 60_000_000.0,
    "debt": 80_000_000.0,
    "net_assets": 220_000_000.0,
    "mads": 6_000_000.0,
    "long_term_debt": 80_000_000.0,
    "net_direct_debt": 60_000_000.0,
    "issuer_population": 205_000.0,
    "adjusted_operating_revenue": 110_000_000.0,
    "debt_service": 6_000_000.0,
    "pension_cost": 1_250_000.0,
    "opeb_cost": 500_000.0,
    "net_pension_liability": 35_000_000.0,
    "adjusted_npl": 35_000_000.0,
    "adjusted_opeb": 8_000_000.0,
}


def _count_status(frame: pd.DataFrame, column: str, status: str) -> int:
    if frame is None or frame.empty or column not in frame.columns:
        return 0
    return int(frame[column].astype(str).eq(status).sum())


def _fixture_benchmark(official_fixture: pd.DataFrame) -> tuple[str, float | None]:
    if official_fixture.empty:
        return "", None
    rating = str(official_fixture.get("official_rating", pd.Series([""])).iloc[0])
    score_raw = official_fixture.get("official_weighted_score", pd.Series([None])).iloc[0]
    try:
        score = float(score_raw)
    except Exception:
        score = None
    return rating, score


def run_raw_validation_regression(
    raw_fixture_dir: str | Path = "config/validation_raw_inputs",
    official_fixture_dir: str | Path = "config/validation_fixtures",
    scoring_modes: Iterable[str] = ("official_assisted", "independent"),
) -> pd.DataFrame:
    """Run raw validation for every raw/official fixture key pair."""
    raw_fixtures = list_raw_input_fixture_files(raw_fixture_dir)
    official_fixtures = list_official_fixture_files(official_fixture_dir)
    rows: list[dict[str, object]] = []

    for fixture_key, raw_path in raw_fixtures.items():
        if fixture_key not in official_fixtures:
            rows.append(
                {
                    "fixture_key": fixture_key,
                    "scoring_mode": "",
                    "status": "missing_official_fixture",
                    "error": "",
                }
            )
            continue

        raw_fixture = load_raw_input_fixture(raw_path)
        official_fixture = load_official_fixture(official_fixtures[fixture_key])
        benchmark_rating, benchmark_score = _fixture_benchmark(official_fixture)

        for scoring_mode in scoring_modes:
            try:
                report = raw_value_validation_report(
                    raw_fixture,
                    official_fixture,
                    scoring_mode=scoring_mode,
                )
                value_comparison = report.get("value_comparison", pd.DataFrame())
                score_comparison = report.get("score_comparison", pd.DataFrame())
                rating_summary = report.get("rating_summary", pd.DataFrame())
                output = report.get("output", {})
                cmp = compare_to_benchmark(
                    output,
                    benchmark_rating=benchmark_rating,
                    benchmark_score=benchmark_score,
                )
                rating_result = output.get("rating_result", {}) if isinstance(output, dict) else {}
                rows.append(
                    {
                        "fixture_key": fixture_key,
                        "scoring_mode": scoring_mode,
                        "status": "ok",
                        "methodology_id": raw_fixture["methodology_id"].iloc[0],
                        "issuer_name": raw_fixture["issuer_name"].iloc[0],
                        "model_rating": cmp.get("model_rating"),
                        "benchmark_rating": benchmark_rating,
                        "rating_match": cmp.get("rating_match"),
                        "model_score": cmp.get("model_score"),
                        "benchmark_score": benchmark_score,
                        "score_match": cmp.get("score_match"),
                        "coverage_status": rating_result.get("coverage_status"),
                        "value_matches": _count_status(value_comparison, "value_status", "match"),
                        "value_mismatches": _count_status(value_comparison, "value_status", "mismatch"),
                        "manual_skip": _count_status(value_comparison, "value_status", "manual_skip"),
                        "source_pending": _count_status(value_comparison, "value_status", "source_pending"),
                        "model_missing": _count_status(value_comparison, "value_status", "model_missing"),
                        "score_mismatches": _count_status(score_comparison, "score_match", "False"),
                        "official_score_overrides": len(report.get("official_score_overrides", pd.DataFrame())),
                        "rating_rows": 0 if rating_summary is None else len(rating_summary),
                        "error": "",
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "fixture_key": fixture_key,
                        "scoring_mode": scoring_mode,
                        "status": "error",
                        "error": str(exc),
                    }
                )

    return pd.DataFrame(rows)


def _manual_scores_for_methodology(
    methodology_id: str,
    template: pd.DataFrame,
    thresholds_path: str | Path,
) -> dict[str, dict[str, float | str]]:
    formula_ids: set[str] = set()
    if isinstance(template, pd.DataFrame) and not template.empty and {"formula_id", "source_priority"}.issubset(template.columns):
        manual_mask = template["source_priority"].astype(str).str.contains("Manual", case=False, na=False)
        formula_ids.update(template.loc[manual_mask, "formula_id"].dropna().astype(str))

    path = Path(thresholds_path)
    if path.exists():
        thresholds = pd.read_csv(path)
        required_cols = {"methodology_id", "formula_id", "rule_type"}
        if required_cols.issubset(thresholds.columns):
            manual_mask = (
                thresholds["methodology_id"].astype(str).eq(str(methodology_id))
                & thresholds["rule_type"].astype(str).str.contains("manual", case=False, na=False)
            )
            formula_ids.update(thresholds.loc[manual_mask, "formula_id"].dropna().astype(str))

    scores: dict[str, dict[str, float | str]] = {}
    for formula_id in formula_ids:
        scores[formula_id] = {"numeric_score": 2.0, "score_label": "synthetic clean-data score"}
    return scores


def _template_formula_results(methodology_id: str, formula_results: pd.DataFrame, template: pd.DataFrame) -> pd.DataFrame:
    if formula_results.empty or template.empty or "formula_id" not in template.columns:
        return pd.DataFrame()
    ids = set(template["formula_id"].dropna().astype(str))
    return formula_results[formula_results["formula_id"].astype(str).isin(ids)].copy()


def run_synthetic_clean_data_regression(
    methodologies: Iterable[tuple[str, str]] = CLEAN_DATA_METHODOLOGIES,
    issuer_data: dict[str, object] | None = None,
    thresholds_path: str | Path = "config/scoring_thresholds.csv",
    templates_dir: str | Path = "templates",
) -> pd.DataFrame:
    """
    Run every active methodology against complete synthetic issuer data.

    This is intentionally not an official-scorecard validation. It answers a
    narrower engineering question: if the data is complete and coherent, do the
    formula, factor, coverage, and rating paths complete without structural
    missing/error states?
    """
    clean_data = dict(SYNTHETIC_CLEAN_ISSUER_DATA if issuer_data is None else issuer_data)
    all_formula_results = calculate_all_formulas(clean_data)
    rows: list[dict[str, object]] = []

    for methodology_id, bond_type in methodologies:
        try:
            template = load_factor_template(methodology_id, templates_dir=templates_dir)
            methodology_results = _template_formula_results(methodology_id, all_formula_results, template)
            raw_counts = (
                methodology_results["status"].fillna("").astype(str).str.lower().value_counts().to_dict()
                if not methodology_results.empty and "status" in methodology_results.columns
                else {}
            )
            raw_bad = methodology_results[
                methodology_results["status"].fillna("").astype(str).str.lower().isin(["missing", "error"])
            ].copy()
            manual_scores = _manual_scores_for_methodology(methodology_id, template, thresholds_path)
            output = run_rating_engine(
                methodology_id=methodology_id,
                formula_results=all_formula_results,
                manual_scores=manual_scores,
                thresholds_path=thresholds_path,
                templates_dir=templates_dir,
            )
            rating_result = output.get("rating_result", {}) if isinstance(output, dict) else {}
            factor_output = output.get("factor_engine_output", {}) if isinstance(output, dict) else {}
            metric_scores = (
                factor_output.get("metric_scores", pd.DataFrame())
                if isinstance(factor_output, dict)
                else pd.DataFrame()
            )
            if isinstance(metric_scores, pd.DataFrame) and not metric_scores.empty:
                metric_status_source = metric_scores.get("score_status", metric_scores.get("status", pd.Series(dtype=str)))
                metric_status = metric_status_source.fillna("").astype(str).str.lower()
                coverage_status = metric_scores.get("coverage_status", pd.Series(dtype=str)).fillna("").astype(str).str.lower()
                metric_bad = metric_scores[
                    metric_status.isin(["missing", "error", "need_score"]) | coverage_status.isin(["missing", "error"])
                ].copy()
                metrics_ready = int(coverage_status.eq("ready").sum())
                metrics_missing = int(coverage_status.eq("missing").sum())
                metrics_error = int(coverage_status.eq("error").sum())
                metrics_need_score = int(metric_status.eq("need_score").sum())
            else:
                metric_bad = pd.DataFrame()
                metrics_ready = metrics_missing = metrics_error = metrics_need_score = 0

            pass_status = (
                raw_bad.empty
                and metric_bad.empty
                and rating_result.get("coverage_status") == "ready"
                and bool(rating_result.get("indicative_rating"))
            )
            rows.append(
                {
                    "status": "PASS" if pass_status else "FAIL",
                    "methodology_id": methodology_id,
                    "bond_type": bond_type,
                    "raw_formula_rows": len(methodology_results),
                    "raw_ready": int(raw_counts.get("ready", 0)),
                    "raw_manual": int(raw_counts.get("manual", 0)),
                    "raw_missing": int(raw_counts.get("missing", 0)),
                    "raw_error": int(raw_counts.get("error", 0)),
                    "rating": rating_result.get("indicative_rating", ""),
                    "weighted_score": rating_result.get("overall_score"),
                    "coverage": rating_result.get("coverage_status", ""),
                    "warnings": len(rating_result.get("warnings", []) or []),
                    "metric_rows": len(metric_scores) if isinstance(metric_scores, pd.DataFrame) else 0,
                    "metrics_ready": metrics_ready,
                    "metrics_missing": metrics_missing,
                    "metrics_error": metrics_error,
                    "metrics_need_score": metrics_need_score,
                    "manual_scores_supplied": len(manual_scores),
                    "bad_formula_ids": "; ".join(raw_bad["formula_id"].astype(str)) if not raw_bad.empty else "",
                    "bad_metric_ids": "; ".join(metric_bad["formula_id"].astype(str)) if not metric_bad.empty and "formula_id" in metric_bad.columns else "",
                    "error": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "status": "ERROR",
                    "methodology_id": methodology_id,
                    "bond_type": bond_type,
                    "error": str(exc),
                }
            )

    return pd.DataFrame(rows)


if __name__ == "__main__":
    print(run_raw_validation_regression().to_string(index=False))
