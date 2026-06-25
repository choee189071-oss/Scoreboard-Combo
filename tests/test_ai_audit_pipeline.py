from __future__ import annotations

import json
import unittest

import pandas as pd

from engine.acfr_extraction_engine import PdfDocument
from engine.ai_audit_pipeline import (
    build_deploy_sanity_check,
    build_section_b_pdf_audit,
    build_section_b_term_matrix,
    parse_pdf_documents,
    perplexity_search_recommendations,
    perplexity_source_recommendations,
    recommendations_to_source_candidates,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class AiAuditPipelineTests(unittest.TestCase):
    def test_deploy_sanity_check_reports_required_components(self) -> None:
        checks = build_deploy_sanity_check(
            pubfin_api_key="test-pubfin",
            llama_cloud_api_key="test-llama",
        )

        self.assertEqual(len(checks), 4)
        self.assertIn("pypdf fallback available", set(checks["check"]))
        self.assertIn("llama-cloud installed", set(checks["check"]))
        pubfin = checks[checks["check"].eq("PUBFIN_API_KEY configured")].iloc[0]
        llama = checks[checks["check"].eq("LLAMA_CLOUD_API_KEY configured")].iloc[0]
        self.assertEqual(pubfin["status"], "ready")
        self.assertEqual(llama["status"], "ready")

    def test_section_b_term_matrix_includes_expected_documents_and_terms(self) -> None:
        matrix = build_section_b_term_matrix("moodys_ccd_go", max_fields=20)

        self.assertFalse(matrix.empty)
        self.assertIn("local_concept_terms", matrix.columns)
        self.assertIn("expected_documents", matrix.columns)
        self.assertTrue(
            matrix["expected_documents"].astype(str).str.contains("ACFR|CreditScope|Census|BEA", regex=True).any()
        )

    def test_perplexity_recommendations_parse_json_response(self) -> None:
        targets = pd.DataFrame(
            [
                {
                    "field_name": "cash_and_investments",
                    "metrics": "Cash Balance Ratio",
                    "expected_documents": "ACFR / audited financial statements",
                    "local_concept_terms": "cash and investments; statement of net position",
                }
            ]
        )
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            [
                                {
                                    "source_type": "ACFR",
                                    "title": "Issuer ACFR 2024",
                                    "url": "https://example.com/acfr.pdf",
                                    "related_fields": "cash_and_investments",
                                    "concept_terms": "cash and investments",
                                    "reason": "Official audited statements",
                                    "confidence": 0.92,
                                }
                            ]
                        )
                    }
                }
            ]
        }

        def fake_urlopen(request: object, timeout: int = 60) -> _FakeResponse:
            return _FakeResponse(payload)

        result = perplexity_source_recommendations(
            "Example Issuer",
            2024,
            targets,
            api_key="test-key",
            urlopen=fake_urlopen,
        )

        self.assertTrue(result["status"].ok)
        recommendations = result["recommendations"]
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations.iloc[0]["url"], "https://example.com/acfr.pdf")

    def test_perplexity_search_recommendations_parse_results(self) -> None:
        targets = pd.DataFrame(
            [
                {
                    "field_name": "debt_service",
                    "metrics": "Debt Service",
                    "expected_documents": "Official statement / offering document",
                    "local_concept_terms": "debt service; principal; interest",
                }
            ]
        )
        payload = {
            "results": [
                {
                    "title": "Example Issuer Official Statement 2024 PDF",
                    "url": "https://example.com/os.pdf",
                    "snippet": "Debt service schedule with principal and interest.",
                    "date": "2024-01-01",
                }
            ],
            "id": "search-id",
        }

        def fake_urlopen(request: object, timeout: int = 45) -> _FakeResponse:
            self.assertTrue(getattr(request, "full_url", "").endswith("/search"))
            return _FakeResponse(payload)

        result = perplexity_search_recommendations(
            "Example Issuer",
            2024,
            targets,
            api_key="test-key",
            urlopen=fake_urlopen,
        )

        self.assertTrue(result["status"].ok)
        recommendations = result["recommendations"]
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations.iloc[0]["source_type"], "OS")
        self.assertEqual(recommendations.iloc[0]["related_fields"], "debt_service")

    def test_recommendations_to_source_candidates_are_document_pending(self) -> None:
        recommendations = pd.DataFrame(
            [
                {
                    "source_type": "ACFR",
                    "title": "Issuer ACFR",
                    "url": "https://example.com/acfr.pdf",
                    "related_fields": "cash_and_investments",
                    "concept_terms": "cash and investments",
                    "reason": "Official PDF",
                    "confidence": 0.91,
                    "discovery_method": "perplexity_search",
                }
            ]
        )

        candidates = recommendations_to_source_candidates(recommendations)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates.iloc[0]["field_name"], "cash_and_investments")
        self.assertEqual(candidates.iloc[0]["candidate_status"], "document_pending")
        self.assertEqual(candidates.iloc[0]["source_file"], "https://example.com/acfr.pdf")

    def test_perplexity_recommendations_without_key_is_disabled(self) -> None:
        result = perplexity_source_recommendations(
            "Example Issuer",
            2024,
            pd.DataFrame(),
            api_key="",
        )

        self.assertFalse(result["status"].ok)
        self.assertEqual(result["status"].status, "missing_api_key")
        self.assertTrue(result["recommendations"].empty)

    def test_parse_pdf_documents_falls_back_to_local_status_rows(self) -> None:
        document = PdfDocument(
            source_slot="section_b_acfr",
            source_name="ACFR",
            file_name="bad.pdf",
            payload=b"not a pdf",
        )

        pages = parse_pdf_documents([document], llama_api_key=None, max_pages=2)

        self.assertFalse(pages.empty)
        self.assertIn("parser", pages.columns)
        self.assertEqual(pages.iloc[0]["parser"], "pypdf")

    def test_parse_pdf_documents_uses_cache(self) -> None:
        document = PdfDocument(
            source_slot="section_b_acfr",
            source_name="ACFR",
            file_name="bad.pdf",
            payload=b"not a pdf",
        )
        cache: dict[str, pd.DataFrame] = {}

        first = parse_pdf_documents([document], llama_api_key=None, max_pages=2, page_cache=cache)
        second = parse_pdf_documents([document], llama_api_key=None, max_pages=2, page_cache=cache)

        self.assertFalse(first.empty)
        self.assertEqual(len(cache), 1)
        self.assertIn("cache_status", second.columns)
        self.assertEqual(second.iloc[0]["cache_status"], "cache_hit")

    def test_pdf_audit_empty_inputs_return_expected_tables(self) -> None:
        output = build_section_b_pdf_audit([], pd.DataFrame())

        self.assertIn("pdf_pages", output)
        self.assertIn("pdf_evidence", output)
        self.assertIn("source_candidates", output)
        self.assertTrue(output["source_candidates"].empty)


if __name__ == "__main__":
    unittest.main()
