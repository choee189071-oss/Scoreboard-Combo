from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.source_intake import (
    build_formula_mismatch_review,
    build_pdf_evidence_candidates,
    build_top_blocking_fields,
    creditscope_payload_to_source_candidates,
    fields_for_pdf_evidence,
    run_source_intake_pipeline,
)


OUTPUT_DIR = Path(
    "/Users/zhouyiyi/Documents/Codex/2026-06-17/"
    "https-combinationofscoreboard-streamlit-app-streamlit-app/outputs/"
    "west_sacramento_source_intake"
)


def _first_existing(paths: list[Path]) -> Path:
    for path in paths:
        if path.exists():
            return path
    return paths[0]


ISSUER_PROFILE: dict[str, Any] = {
    "issuer_name": "City of West Sacramento",
    "methodology_id": "sp_local_gov_k12",
    "methodology_label": "S&P Gov and K-12",
    "analysis_year": 2026,
    "state": "California",
    "city": "West Sacramento",
    "county": "Yolo County",
    "state_fips": "06",
    "county_fips": "113",
    "geography_status": "confirmed_by_user",
    "financial_period": "FYE 2025 / 6/30/2025",
    "financial_period_status": "confirmed_by_user",
    "os_status": "not_provided",
    "debt_report_os_substitute": "accepted_by_user",
}


FINANCIALS = _first_existing(
    [
        Path("/Users/zhouyiyi/Desktop/Financials_ West Sacramento.xlsx"),
        Path("/Users/zhouyiyi/Downloads/Financials_ West Sacramento.xlsx"),
    ]
)
SCORECARD_EXPORT = Path("/Users/zhouyiyi/Downloads/Std Scoring Scorecard (exported on 2026-06-18).xls")
ACFR_PDFS = [
    Path("/Users/zhouyiyi/Desktop/West Sacramento ACFR 21126.pdf"),
    Path("/Users/zhouyiyi/Desktop/ACFR Final 2024.pdf"),
    Path("/Users/zhouyiyi/Desktop/City of West Sacramento ACFR Final 4124.pdf"),
]
DEBT_REPORT_PDFS = [
    Path("/Users/zhouyiyi/Downloads/CombinedDebtService_City_of_West_Sacramento_CA.pdf"),
    Path("/Users/zhouyiyi/Downloads/RemainingDebtServiceByType_City_of_West_Sacramento_CA.pdf"),
    Path("/Users/zhouyiyi/Downloads/BondedIndebtedness_City_of_West_Sacramento_CA.pdf"),
]


def _write_frame(name: str, frame: pd.DataFrame) -> Path:
    path = OUTPUT_DIR / f"{name}.csv"
    frame.to_csv(path, index=False)
    return path


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes() if path.exists() else b""


def _existing_pdf_payloads(paths: list[Path]) -> list[tuple[str, bytes]]:
    return [(path.name, _read_bytes(path)) for path in paths if path.exists()]


def _source_manifest() -> pd.DataFrame:
    rows = []
    for source_type, paths in [
        ("CreditScope", [FINANCIALS]),
        ("ACFR", ACFR_PDFS),
        ("DebtReport", DEBT_REPORT_PDFS),
        ("ScorecardExportReference", [SCORECARD_EXPORT]),
    ]:
        for path in paths:
            rows.append(
                {
                    "source_type": source_type,
                    "file_name": path.name,
                    "path": str(path),
                    "exists": path.exists(),
                    "used_for_model_input": source_type in {"CreditScope"},
                    "used_for_evidence": source_type in {"ACFR", "DebtReport"},
                    "notes": _source_note(source_type),
                }
            )
    return pd.DataFrame(rows)


def _source_note(source_type: str) -> str:
    if source_type == "DebtReport":
        return "Accepted by user as debt evidence substitute because OS is not available; extracted values still require field-level review."
    if source_type == "ScorecardExportReference":
        return "Benchmark/reference only. Not used as raw source input."
    return ""


def build_case_outputs() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest = _source_manifest()
    top_fields = build_top_blocking_fields(top_n=25)
    mismatch_review = build_formula_mismatch_review()

    financial_candidates = pd.DataFrame()
    financial_diagnostics = pd.DataFrame()
    financial_issuer_data: dict[str, Any] = {}
    if FINANCIALS.exists():
        financial_candidates, financial_diagnostics, financial_issuer_data = creditscope_payload_to_source_candidates(
            FINANCIALS.name,
            FINANCIALS.read_bytes(),
            methodology_id=ISSUER_PROFILE["methodology_id"],
            sheet_name="Sheet1",
            include_support_tabs=False,
        )

    acfr_targets = fields_for_pdf_evidence(top_fields.head(15), limit=15)
    acfr_output = build_pdf_evidence_candidates(
        _existing_pdf_payloads(ACFR_PDFS),
        acfr_targets,
        source_name="ACFR",
        source_slot="acfr_pdf",
        max_pages=80,
        top_n_per_field=3,
    )

    debt_targets = top_fields[top_fields["field_name"].isin(["debt", "long_term_debt", "net_direct_debt", "debt_service", "mads"])].copy()
    if debt_targets.empty:
        debt_targets = pd.DataFrame(
            [
                {"field_name": field, "metrics": "Debt service and liabilities", "priority_sources": "DebtReport|OS|ACFR"}
                for field in ["debt", "long_term_debt", "net_direct_debt", "debt_service", "mads"]
            ]
        )
    debt_output = build_pdf_evidence_candidates(
        _existing_pdf_payloads(DEBT_REPORT_PDFS),
        fields_for_pdf_evidence(debt_targets, limit=10),
        source_name="DebtReport",
        source_slot="debt_report_pdf",
        max_pages=40,
        top_n_per_field=4,
    )

    frames = [
        frame
        for frame in [
            financial_candidates,
            acfr_output.get("source_candidates", pd.DataFrame()),
            debt_output.get("source_candidates", pd.DataFrame()),
        ]
        if isinstance(frame, pd.DataFrame) and not frame.empty
    ]
    all_candidates = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    pipeline = run_source_intake_pipeline(
        all_candidates,
        methodology_id=ISSUER_PROFILE["methodology_id"],
    )

    summary = pd.DataFrame(
        [
            {"item": "issuer", "value": ISSUER_PROFILE["issuer_name"]},
            {"item": "methodology_id", "value": ISSUER_PROFILE["methodology_id"]},
            {"item": "analysis_year", "value": ISSUER_PROFILE["analysis_year"]},
            {"item": "credit_scope_candidate_rows", "value": len(financial_candidates)},
            {"item": "credit_scope_selected_raw_fields", "value": len(financial_issuer_data)},
            {"item": "acfr_pdf_evidence_rows", "value": len(acfr_output.get("pdf_evidence", pd.DataFrame()))},
            {"item": "debt_report_pdf_evidence_rows", "value": len(debt_output.get("pdf_evidence", pd.DataFrame()))},
            {"item": "combined_source_candidate_rows", "value": len(all_candidates)},
            {"item": "selected_source_report_rows", "value": len(pipeline.get("source_report", pd.DataFrame()))},
            {"item": "selected_issuer_data_fields", "value": len(pipeline.get("issuer_data", {}))},
            {"item": "os_status", "value": ISSUER_PROFILE["os_status"]},
        ]
    )

    outputs = {
        "issuer_profile": OUTPUT_DIR / "issuer_profile.json",
        "summary": _write_frame("summary", summary),
        "source_manifest": _write_frame("source_manifest", manifest),
        "financial_source_candidates": _write_frame("financial_source_candidates", financial_candidates),
        "financial_mapping_diagnostics": _write_frame("financial_mapping_diagnostics", financial_diagnostics),
        "acfr_pdf_pages": _write_frame("acfr_pdf_pages", acfr_output.get("pdf_pages", pd.DataFrame())),
        "acfr_pdf_evidence": _write_frame("acfr_pdf_evidence", acfr_output.get("pdf_evidence", pd.DataFrame())),
        "debt_report_pdf_pages": _write_frame("debt_report_pdf_pages", debt_output.get("pdf_pages", pd.DataFrame())),
        "debt_report_pdf_evidence": _write_frame("debt_report_pdf_evidence", debt_output.get("pdf_evidence", pd.DataFrame())),
        "all_source_candidates": _write_frame("all_source_candidates", all_candidates),
        "source_report": _write_frame("source_report", pipeline.get("source_report", pd.DataFrame())),
        "source_readiness_summary": _write_frame(
            "source_readiness_summary",
            pipeline.get("source_readiness_summary", pd.DataFrame()),
        ),
        "selected_issuer_data": _write_frame(
            "selected_issuer_data",
            pd.DataFrame(
                [
                    {"field_name": key, "value": value}
                    for key, value in sorted(pipeline.get("issuer_data", {}).items())
                ]
            ),
        ),
        "top_blocking_fields": _write_frame("top_blocking_fields", top_fields),
        "formula_mismatch_review": _write_frame("formula_mismatch_review", mismatch_review),
    }
    outputs["issuer_profile"].write_text(json.dumps(ISSUER_PROFILE, indent=2), encoding="utf-8")
    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps({key: str(value) for key, value in outputs.items()}, indent=2),
        encoding="utf-8",
    )
    return {
        "outputs": outputs,
        "summary": summary,
        "source_readiness_summary": pipeline.get("source_readiness_summary", pd.DataFrame()),
    }


def main() -> None:
    result = build_case_outputs()
    print(result["summary"].to_string(index=False))
    readiness = result["source_readiness_summary"]
    if isinstance(readiness, pd.DataFrame) and not readiness.empty:
        print()
        print(readiness.to_string(index=False))
    print()
    print(OUTPUT_DIR)


if __name__ == "__main__":
    main()
