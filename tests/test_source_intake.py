from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import pandas as pd

from engine.data_sourcing_engine import run_data_sourcing_pipeline
from engine.source_confirmation import (
    build_source_confirmation_queue,
    confirmation_queue_to_source_candidates,
)
from engine.source_intake import (
    build_formula_mismatch_review,
    build_pdf_evidence_candidates,
    build_top_blocking_fields,
    fields_for_pdf_evidence,
)


class SourceIntakeTests(unittest.TestCase):
    def test_top_blocking_fields_and_mismatch_review_build(self) -> None:
        blocking_rows = pd.DataFrame(
            [
                {
                    "fixture_key": "case_a",
                    "methodology_id": "moodys_ccd_go",
                    "field_name": field_name,
                    "formula_id": formula_id,
                    "metric": metric,
                    "coverage_status": "missing_primary_raw",
                    "suspected_cause": "fixture_source_gap",
                    "field_blocking": True,
                    "metric_blocking": True,
                }
                for field_name, formula_id, metric in [
                    ("county_gdp", "gdp_per_capita", "GDP Per Capita"),
                    ("personal_income", "income_per_capita", "Income Per Capita"),
                    ("population_current", "population_growth", "Population Growth"),
                    ("cash_and_investments", "cash_to_debt", "Cash to Debt"),
                    ("debt_service", "fixed_costs", "Fixed Costs"),
                    ("net_direct_debt", "debt_per_capita", "Debt Per Capita"),
                ]
            ]
        )
        mismatch_rows = pd.DataFrame(
            [
                {
                    "fixture_key": "case_a",
                    "methodology_id": "moodys_ccd_go",
                    "issuer_name": "Fixture Issuer",
                    "formula_id": "fund_balance_trend_5yr",
                    "factor": "Finances",
                    "metric": "Fund Balance Trend",
                    "official_value": 0.25,
                    "model_compare_value": 0.15,
                    "value_delta": -0.10,
                    "official_score": 1,
                    "model_score": 2,
                    "score_delta": 1,
                    "required_fields": "fund_balance_ratio_current;fund_balance_ratio_5yr_prior",
                    "raw_source_cells": "",
                    "warning": "current-year scalar proxy",
                    "suspected_cause": "period mismatch",
                    "workbook": "fixture.xlsx",
                    "primary_raw_sheet": "Raw",
                    "value_status": "mismatch",
                },
                {
                    "fixture_key": "case_a",
                    "methodology_id": "moodys_ccd_go",
                    "issuer_name": "Fixture Issuer",
                    "formula_id": "tax_base_size",
                    "factor": "Economy",
                    "metric": "Tax Base Size",
                    "official_value": 1000,
                    "model_compare_value": 1,
                    "value_delta": -999,
                    "official_score": 1,
                    "model_score": 2,
                    "score_delta": 1,
                    "required_fields": "full_value",
                    "raw_source_cells": "",
                    "warning": "",
                    "suspected_cause": "unit mismatch",
                    "workbook": "fixture.xlsx",
                    "primary_raw_sheet": "Raw",
                    "value_status": "mismatch",
                },
                {
                    "fixture_key": "case_a",
                    "methodology_id": "moodys_ccd_go",
                    "issuer_name": "Fixture Issuer",
                    "formula_id": "net_direct_debt_per_capita",
                    "factor": "Debt",
                    "metric": "Debt Per Capita",
                    "official_value": 200,
                    "model_compare_value": 240,
                    "value_delta": 40,
                    "official_score": 2,
                    "model_score": 3,
                    "score_delta": 1,
                    "required_fields": "net_direct_debt;population_current",
                    "raw_source_cells": "",
                    "warning": "",
                    "suspected_cause": "denominator mismatch",
                    "workbook": "fixture.xlsx",
                    "primary_raw_sheet": "Raw",
                    "value_status": "mismatch",
                },
                {
                    "fixture_key": "case_a",
                    "methodology_id": "moodys_ccd_go",
                    "issuer_name": "Fixture Issuer",
                    "formula_id": "cash_balance_ratio",
                    "factor": "Liquidity",
                    "metric": "Cash Balance",
                    "official_value": 0.5,
                    "model_compare_value": 0.7,
                    "value_delta": 0.2,
                    "official_score": 2,
                    "model_score": 3,
                    "score_delta": 1,
                    "required_fields": "cash;revenue",
                    "raw_source_cells": "",
                    "warning": "",
                    "suspected_cause": "source adjustment",
                    "workbook": "fixture.xlsx",
                    "primary_raw_sheet": "Raw",
                    "value_status": "mismatch",
                },
                {
                    "fixture_key": "case_a",
                    "methodology_id": "moodys_ccd_go",
                    "issuer_name": "Fixture Issuer",
                    "formula_id": "debt_to_revenue",
                    "factor": "Debt",
                    "metric": "Debt to Revenue",
                    "official_value": 0.4,
                    "model_compare_value": 0.6,
                    "value_delta": 0.2,
                    "official_score": 2,
                    "model_score": 3,
                    "score_delta": 1,
                    "required_fields": "debt;revenue",
                    "raw_source_cells": "",
                    "warning": "",
                    "suspected_cause": "source adjustment",
                    "workbook": "fixture.xlsx",
                    "primary_raw_sheet": "Raw",
                    "value_status": "mismatch",
                },
                {
                    "fixture_key": "case_a",
                    "methodology_id": "moodys_ccd_go",
                    "issuer_name": "Fixture Issuer",
                    "formula_id": "other_formula",
                    "factor": "Other",
                    "metric": "Other Metric",
                    "official_value": 10,
                    "model_compare_value": 12,
                    "value_delta": 2,
                    "official_score": 2,
                    "model_score": 3,
                    "score_delta": 1,
                    "required_fields": "other_field",
                    "raw_source_cells": "",
                    "warning": "",
                    "suspected_cause": "formula review",
                    "workbook": "fixture.xlsx",
                    "primary_raw_sheet": "Raw",
                    "value_status": "mismatch",
                },
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            coverage_path = temp_path / "field_coverage.csv"
            accuracy_path = temp_path / "accuracy_matrix.csv"
            blocking_rows.to_csv(coverage_path, index=False)
            mismatch_rows.to_csv(accuracy_path, index=False)

            top_fields = build_top_blocking_fields(coverage_path, top_n=10)
            mismatch_review = build_formula_mismatch_review(accuracy_path)

        self.assertGreaterEqual(len(top_fields), 5)
        self.assertIn("field_name", top_fields.columns)
        self.assertEqual(len(mismatch_review), 6)
        self.assertIn("mismatch_type", mismatch_review.columns)

    def test_pdf_evidence_extractor_returns_review_candidates(self) -> None:
        pdf_path = Path("/Users/zhouyiyi/Downloads/Ramirez 2/SP Local Government Scorecard Criteria 2024.pdf")
        if not pdf_path.exists():
            self.skipTest("Local criteria PDF is not available.")

        top_fields = build_top_blocking_fields(top_n=3)
        targets = fields_for_pdf_evidence(top_fields, limit=2)
        output = build_pdf_evidence_candidates(
            [(pdf_path.name, pdf_path.read_bytes())],
            targets,
            source_name="RatingReport",
            max_pages=3,
            top_n_per_field=1,
        )

        self.assertIn("pdf_pages", output)
        self.assertIn("pdf_evidence", output)
        self.assertIn("source_candidates", output)

    def test_source_pending_candidates_do_not_feed_issuer_data(self) -> None:
        pipeline = run_data_sourcing_pipeline(
            pd.DataFrame(
                [
                    {
                        "field_name": "ready_field",
                        "value": 100,
                        "source_name": "CreditScope",
                        "confidence": 0.95,
                        "candidate_status": "ready",
                    },
                    {
                        "field_name": "pending_field",
                        "value": 200,
                        "source_name": "ACFR",
                        "confidence": 0.72,
                        "candidate_status": "source_review",
                    },
                ]
            ),
            required_fields=["ready_field", "pending_field"],
        )

        self.assertEqual(pipeline["issuer_data"], {"ready_field": 100.0})
        selected = pipeline["source_report"][pipeline["source_report"]["selected"].astype(bool)]
        pending = selected[selected["field_name"].eq("pending_field")].iloc[0]
        self.assertEqual(pending["readiness_status"], "source_pending")
        self.assertFalse(bool(pending["model_input_eligible"]))

    def test_source_confirmation_queue_promotes_accepted_values(self) -> None:
        source_report = pd.DataFrame(
            [
                {
                    "field_name": "debt_service",
                    "selected": True,
                    "readiness_status": "source_pending",
                    "source_quality_status": "source_pending",
                    "value": 12345,
                    "source_name": "DebtReport",
                    "canonical_source": "DebtReport",
                    "confidence": 0.72,
                    "source_file": "debt.pdf",
                    "source_table": "PDF page 1",
                    "source_cell_or_api": "page:1",
                    "source_label": "Total Debt Service",
                }
            ]
        )
        evidence = pd.DataFrame(
            [
                {
                    "field_name": "debt_service",
                    "file_name": "debt.pdf",
                    "page_number": 1,
                    "snippet": "Total Debt Service 12,345",
                    "candidate_values": "12,345",
                    "citation": "debt.pdf p. 1",
                    "score": 10,
                }
            ]
        )

        queue = build_source_confirmation_queue(source_report, evidence)
        self.assertEqual(len(queue), 1)
        self.assertIn("Total Debt Service", queue.iloc[0]["snippet"])

        queue.loc[0, "decision"] = "Accept edited value"
        queue["confirmed_value"] = queue["confirmed_value"].astype(object)
        queue.loc[0, "confirmed_value"] = "12345"
        approved = confirmation_queue_to_source_candidates(queue)
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved.iloc[0]["candidate_status"], "ready")

        pipeline = run_data_sourcing_pipeline(
            approved,
            required_fields=["debt_service"],
        )
        self.assertEqual(pipeline["issuer_data"], {"debt_service": 12345.0})


if __name__ == "__main__":
    unittest.main()
