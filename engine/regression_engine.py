"""
Regression helpers for official scorecard fixtures.

Use this module to run raw-value validation across all fixture pairs after
changes to formula, sourcing, mapping, threshold, or rating logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from engine.official_fixture_engine import list_official_fixture_files, load_official_fixture
from engine.rating_engine import compare_to_benchmark
from engine.raw_value_validation import (
    list_raw_input_fixture_files,
    load_raw_input_fixture,
    raw_value_validation_report,
)


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


if __name__ == "__main__":
    print(run_raw_validation_regression().to_string(index=False))
