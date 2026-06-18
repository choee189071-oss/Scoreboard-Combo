"""Source intake helpers for uploads, benchmark gaps, and evidence review."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from connectors.creditscope_loader import load_creditscope_source_candidates
from engine.acfr_extraction_engine import PdfDocument, extract_all_pdf_pages, rank_pdf_snippets_for_field
from engine.data_platform import build_field_dictionary_catalog
from engine.data_sourcing_engine import (
    CANDIDATE_COLUMNS,
    mapping_report_to_source_candidates,
    normalize_source_candidates,
    required_fields_for_methodology,
    run_data_sourcing_pipeline,
)
from engine.mapping_engine import map_uploaded_file


DEFAULT_AUDIT_DIR = Path("work/methodology_accuracy_matrix")
DEFAULT_PRIORITY_FIELDS = [
    "county_gdp",
    "personal_income",
    "population_current",
    "population_prior",
    "cash_and_investments",
    "debt_service",
    "net_direct_debt",
    "adjusted_npl",
    "mads",
]


def _buffer(file_name: str, payload: bytes) -> io.BytesIO:
    handle = io.BytesIO(payload)
    handle.name = file_name
    return handle


def _read_csv(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _clean_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except Exception:
        return None


def _join_unique(values: Iterable[Any], sep: str = "|") -> str:
    items: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text.lower() != "nan" and text not in items:
            items.append(text)
    return sep.join(items)


def _read_tabular_payload(file_name: str, payload: bytes) -> pd.DataFrame:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(_buffer(file_name, payload))
    if suffix in {".json", ".jsonl"}:
        raw = payload.decode("utf-8")
        if suffix == ".jsonl":
            return pd.DataFrame([json.loads(line) for line in raw.splitlines() if line.strip()])
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return pd.DataFrame(parsed)
        if isinstance(parsed, dict):
            if "source_candidates" in parsed and isinstance(parsed["source_candidates"], list):
                return pd.DataFrame(parsed["source_candidates"])
            if "rows" in parsed and isinstance(parsed["rows"], list):
                return pd.DataFrame(parsed["rows"])
            return pd.DataFrame([parsed])
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(_buffer(file_name, payload))
    raise ValueError(f"Unsupported tabular upload: {file_name}")


def tabular_payload_to_source_candidates(
    file_name: str,
    payload: bytes,
    *,
    source_name: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convert CSV/JSON/XLSX upload to source candidates and a diagnostics frame."""
    df = _read_tabular_payload(file_name, payload)
    if df.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS), pd.DataFrame()

    normalized_columns = {str(col).strip().lower(): col for col in df.columns}
    if {"field_name", "value"} <= set(normalized_columns):
        candidates = df.copy()
        if "source_name" not in candidates.columns:
            candidates["source_name"] = source_name
        if "source_file" not in candidates.columns:
            candidates["source_file"] = file_name
        if "candidate_status" not in candidates.columns:
            candidates["candidate_status"] = "ready"
        return normalize_source_candidates(candidates), candidates

    mapped_data, report = map_uploaded_file(
        uploaded_file=_buffer(file_name, payload),
        source_name=source_name,
        mapping_path="config/field_mapping.csv",
    )
    _ = mapped_data
    if not report.empty and "uploaded_file" not in report.columns:
        report.insert(0, "uploaded_file", file_name)
    candidates = mapping_report_to_source_candidates(report, uploaded_file=file_name)
    return candidates, report


def creditscope_payload_to_source_candidates(
    file_name: str,
    payload: bytes,
    *,
    methodology_id: str,
    sheet_name: str | None = None,
    include_support_tabs: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    required = required_fields_for_methodology(methodology_id)
    output = load_creditscope_source_candidates(
        uploaded_file=_buffer(file_name, payload),
        sheet_name=sheet_name,
        required_fields=required,
        include_support_tabs=include_support_tabs,
    )
    return output["source_candidates"], output["match_report"], output.get("issuer_data", {})


def uploaded_payload_to_source_candidates(
    file_name: str,
    payload: bytes,
    *,
    source_name: str,
    methodology_id: str,
    sheet_name: str | None = None,
    include_support_tabs: bool = False,
) -> dict[str, Any]:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        return {
            "source_candidates": pd.DataFrame(columns=CANDIDATE_COLUMNS),
            "diagnostics": pd.DataFrame(),
            "issuer_data": {},
            "registered_pdf": True,
        }
    if source_name == "CreditScope":
        candidates, diagnostics, issuer_data = creditscope_payload_to_source_candidates(
            file_name,
            payload,
            methodology_id=methodology_id,
            sheet_name=sheet_name,
            include_support_tabs=include_support_tabs,
        )
    else:
        candidates, diagnostics = tabular_payload_to_source_candidates(
            file_name,
            payload,
            source_name=source_name,
        )
        issuer_data = {}
    return {
        "source_candidates": candidates,
        "diagnostics": diagnostics,
        "issuer_data": issuer_data,
        "registered_pdf": False,
    }


def run_source_intake_pipeline(
    candidates: pd.DataFrame | Sequence[pd.DataFrame],
    *,
    methodology_id: str,
) -> dict[str, Any]:
    required = required_fields_for_methodology(methodology_id)
    return run_data_sourcing_pipeline(
        candidates,
        methodology_id=methodology_id,
        required_fields=required,
    )


def build_top_blocking_fields(
    field_coverage_path: str | Path = DEFAULT_AUDIT_DIR / "field_coverage.csv",
    *,
    top_n: int = 10,
    priority_fields: Sequence[str] = tuple(DEFAULT_PRIORITY_FIELDS),
) -> pd.DataFrame:
    coverage = _read_csv(field_coverage_path)
    dictionary = build_field_dictionary_catalog()
    if coverage.empty:
        return pd.DataFrame()

    coverage = coverage.copy()
    coverage["field_blocking_bool"] = coverage.get("field_blocking", False).map(_as_bool)
    coverage["metric_blocking_bool"] = coverage.get("metric_blocking", False).map(_as_bool)
    blocking = coverage[
        coverage["field_blocking_bool"]
        & ~coverage["field_name"].fillna("").astype(str).isin({"manual_score", "no_raw_field_required"})
    ].copy()
    if blocking.empty:
        return pd.DataFrame()

    grouped = blocking.groupby("field_name", as_index=False).agg(
        blocking_rows=("field_name", "size"),
        case_count=("fixture_key", lambda s: len(set(_clean_text(v) for v in s if _clean_text(v)))),
        methodology_count=("methodology_id", lambda s: len(set(_clean_text(v) for v in s if _clean_text(v)))),
        methodologies=("methodology_id", lambda s: _join_unique(sorted(set(s.astype(str))))),
        formula_count=("formula_id", lambda s: len(set(_clean_text(v) for v in s if _clean_text(v)))),
        formulas=("formula_id", lambda s: _join_unique(sorted(set(s.astype(str))))),
        metrics=("metric", lambda s: _join_unique(s, sep="; ")),
        missing_primary_raw=("coverage_status", lambda s: int(s.astype(str).eq("missing_primary_raw").sum())),
        no_primary_raw_sheet=("coverage_status", lambda s: int(s.astype(str).eq("no_primary_raw_sheet").sum())),
        available_primary_raw=("coverage_status", lambda s: int(s.astype(str).eq("available_primary_raw").sum())),
        suspected_causes=("suspected_cause", lambda s: _join_unique(s, sep="; ")),
    )
    grouped["priority_target"] = grouped["field_name"].isin(priority_fields)
    priority_rank = {field: idx + 1 for idx, field in enumerate(priority_fields)}
    grouped["priority_target_rank"] = grouped["field_name"].map(priority_rank)
    merged = grouped.merge(
        dictionary[
            [
                "field_name",
                "field_category",
                "preferred_source",
                "fallback_source",
                "priority_sources",
                "automation_level",
                "alias_count",
                "mapped_sources",
                "aliases",
            ]
        ],
        on="field_name",
        how="left",
    )
    merged["recommended_action"] = merged.apply(_blocking_field_action, axis=1)
    return merged.sort_values(
        ["blocking_rows", "methodology_count", "case_count", "priority_target"],
        ascending=[False, False, False, False],
    ).head(top_n).reset_index(drop=True)


def _blocking_field_action(row: pd.Series) -> str:
    sources = str(row.get("priority_sources", "") or "")
    field = str(row.get("field_name", "") or "")
    if "BEA" in sources or field in {"county_gdp", "personal_income", "us_gdp", "us_personal_income"}:
        return "Add or verify BEA/API geography and year mapping; keep denominators explicit."
    if "CensusACS" in sources or "population" in field or field in {"poverty_rate", "issuer_mfi"}:
        return "Add or verify Census/API geography mapping and current/prior period selection."
    if "CreditScope" in sources:
        return "Improve CreditScope row aliases or workbook-sheet selection for this raw field."
    if any(source in sources for source in ["OS", "ACFR", "MoodysWorkbook", "RatingReport"]):
        return "Use PDF evidence extractor and add reviewed source candidate with page citation."
    return "Review source priority and add a structured source or manual evidence workflow."


def build_formula_mismatch_review(
    accuracy_matrix_path: str | Path = DEFAULT_AUDIT_DIR / "accuracy_matrix.csv",
) -> pd.DataFrame:
    accuracy = _read_csv(accuracy_matrix_path)
    if accuracy.empty:
        return pd.DataFrame()
    rows = accuracy[
        accuracy.get("value_status", pd.Series(dtype=str)).fillna("").astype(str).eq("mismatch")
    ].copy()
    if rows.empty:
        return pd.DataFrame()
    rows["official_numeric"] = rows.get("official_value", pd.Series(dtype=object)).map(_safe_float)
    rows["model_numeric"] = rows.get("model_compare_value", pd.Series(dtype=object)).map(_safe_float)
    rows["abs_delta"] = (rows["model_numeric"] - rows["official_numeric"]).abs()
    rows["relative_delta"] = rows.apply(
        lambda row: None
        if row["official_numeric"] in {None, 0} or pd.isna(row["official_numeric"])
        else abs(row["model_numeric"] - row["official_numeric"]) / abs(row["official_numeric"]),
        axis=1,
    )
    classifications = rows.apply(_classify_mismatch, axis=1, result_type="expand")
    rows = pd.concat([rows.reset_index(drop=True), classifications.reset_index(drop=True)], axis=1)
    preferred = [
        "fixture_key",
        "methodology_id",
        "issuer_name",
        "formula_id",
        "factor",
        "metric",
        "official_value",
        "model_compare_value",
        "value_delta",
        "abs_delta",
        "relative_delta",
        "official_score",
        "model_score",
        "score_delta",
        "mismatch_type",
        "review_status",
        "recommended_action",
        "required_fields",
        "raw_source_cells",
        "warning",
        "suspected_cause",
        "workbook",
        "primary_raw_sheet",
    ]
    return rows[[col for col in preferred if col in rows.columns] + [col for col in rows.columns if col not in preferred]]


def _classify_mismatch(row: pd.Series) -> dict[str, str]:
    formula_id = str(row.get("formula_id", "") or "")
    warning = str(row.get("warning", "") or "").lower()
    required = str(row.get("required_fields", "") or "").lower()
    official = row.get("official_numeric")
    model = row.get("model_numeric")
    ratio = None
    if official not in {None, 0} and model not in {None, 0} and not pd.isna(official) and not pd.isna(model):
        ratio = abs(float(model) / float(official))

    if "avg_5yr" in warning or "current-year scalar proxy" in warning or "trend" in formula_id:
        return {
            "mismatch_type": "period_or_time_series",
            "review_status": "needs_multi_year_source",
            "recommended_action": "Source explicit multi-year history instead of using current-year scalar proxy.",
        }
    if ratio is not None and any(abs(ratio - target) / target <= 0.05 for target in [0.001, 0.01, 100, 1000]):
        return {
            "mismatch_type": "unit_or_scale",
            "review_status": "needs_unit_normalization",
            "recommended_action": "Check dollars vs thousands/millions and add unit normalization before formula comparison.",
        }
    if "population" in required or "per_capita" in formula_id:
        return {
            "mismatch_type": "denominator_or_geography",
            "review_status": "needs_denominator_review",
            "recommended_action": "Verify issuer/service-area/county denominator and period against the official workbook.",
        }
    if any(token in formula_id for token in ["cash", "margin", "revenue", "debt"]):
        return {
            "mismatch_type": "source_or_adjustment",
            "review_status": "needs_source_reconciliation",
            "recommended_action": "Check adjusted vs unadjusted source values, fiscal period, and numerator/denominator definition.",
        }
    return {
        "mismatch_type": "formula_review",
        "review_status": "needs_formula_review",
        "recommended_action": "Review formula expression, source fields, and methodology threshold mapping.",
    }


VALUE_PATTERN = re.compile(
    r"(?P<value>\(?\$?\s*-?\d[\d,]*(?:\.\d+)?\s*\)?\s*(?:%|percent|million|billion|thousand|m|bn)?)",
    re.IGNORECASE,
)


def extract_value_candidates(text: str, *, limit: int = 8) -> str:
    values: list[str] = []
    for match in VALUE_PATTERN.finditer(text or ""):
        raw = re.sub(r"\s+", " ", match.group("value")).strip()
        if len(raw) < 2 or raw in values:
            continue
        values.append(raw)
        if len(values) >= limit:
            break
    return "; ".join(values)


def build_pdf_evidence_candidates(
    pdf_documents: Sequence[tuple[str, bytes]],
    target_fields: pd.DataFrame,
    *,
    source_name: str = "ACFR",
    source_slot: str = "pdf_evidence",
    max_pages: int | None = 40,
    top_n_per_field: int = 3,
) -> dict[str, pd.DataFrame]:
    docs = [
        PdfDocument(source_slot=source_slot, source_name=source_name, file_name=file_name, payload=payload)
        for file_name, payload in pdf_documents
        if payload
    ]
    pages = extract_all_pdf_pages(docs, max_pages=max_pages)
    if pages.empty or target_fields.empty:
        return {
            "pdf_pages": pages,
            "pdf_evidence": pd.DataFrame(),
            "source_candidates": pd.DataFrame(columns=CANDIDATE_COLUMNS),
        }

    evidence_frames: list[pd.DataFrame] = []
    for _, field_row in target_fields.iterrows():
        snippets = rank_pdf_snippets_for_field(pages, field_row.to_dict(), top_n=top_n_per_field, char_limit=1800)
        if not snippets.empty:
            evidence_frames.append(snippets)
    evidence = pd.concat(evidence_frames, ignore_index=True) if evidence_frames else pd.DataFrame()
    if evidence.empty:
        return {
            "pdf_pages": pages,
            "pdf_evidence": evidence,
            "source_candidates": pd.DataFrame(columns=CANDIDATE_COLUMNS),
        }

    evidence = evidence.copy()
    evidence["candidate_values"] = evidence["snippet"].map(extract_value_candidates)
    evidence["citation"] = evidence.apply(
        lambda row: f"{row.get('file_name', '')} p. {row.get('page_number', '')}",
        axis=1,
    )
    candidate_rows: list[dict[str, Any]] = []
    for _, row in evidence.iterrows():
        values = str(row.get("candidate_values", "") or "")
        first_value = values.split(";")[0].strip() if values else ""
        confidence = min(0.72, 0.35 + float(row.get("score", 0) or 0) / 100.0)
        candidate_rows.append(
            {
                "field_name": row.get("field_name", ""),
                "value": first_value,
                "unit": "",
                "source_name": source_name,
                "source_type": "Document",
                "source_detail": "pdf_evidence_review",
                "confidence": confidence,
                "source_file": row.get("file_name", ""),
                "source_table": f"PDF page {row.get('page_number', '')}",
                "source_cell_or_api": f"page:{row.get('page_number', '')}",
                "source_label": row.get("matched_terms", ""),
                "candidate_status": "source_review",
                "notes": "PDF evidence candidate; analyst review required before use.",
            }
        )
    candidates = normalize_source_candidates(pd.DataFrame(candidate_rows))
    return {
        "pdf_pages": pages,
        "pdf_evidence": evidence,
        "source_candidates": candidates,
    }


def fields_for_pdf_evidence(
    top_blocking_fields: pd.DataFrame,
    *,
    limit: int = 10,
) -> pd.DataFrame:
    if top_blocking_fields.empty:
        return pd.DataFrame(columns=["field_name"])
    rows = top_blocking_fields.head(limit).copy()
    rows["expected_source"] = rows.get("priority_sources", "")
    rows["suggested_search_terms"] = rows.apply(
        lambda row: "; ".join(
            part
            for part in [
                row.get("field_name", ""),
                row.get("metrics", ""),
                row.get("aliases", ""),
                row.get("preferred_source", ""),
            ]
            if _clean_text(part)
        ),
        axis=1,
    )
    rows["status_reason"] = rows.get("suspected_causes", "")
    return rows
