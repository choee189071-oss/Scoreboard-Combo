"""AI-assisted source discovery and document audit helpers.

This module powers the Section B review workflow:

* derive methodology field terms from existing templates/config;
* recommend source links with Perplexity/Sonar using ``PUBFIN_API_KEY``;
* parse uploaded PDFs with LlamaCloud when available, with local ``pypdf``
  extraction as a fallback;
* turn ranked evidence snippets into review-only source candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence
from urllib import error as urllib_error
from urllib import request as urllib_request

import pandas as pd

from engine.acfr_extraction_engine import (
    PdfDocument,
    extract_pdf_pages,
    field_search_terms,
    rank_pdf_snippets_for_field,
)
from engine.data_platform import build_methodology_field_matrix
from engine.data_sourcing_engine import CANDIDATE_COLUMNS, normalize_source_candidates


PERPLEXITY_SEARCH_URL = "https://api.perplexity.ai/search"
PERPLEXITY_CHAT_COMPLETIONS_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_PERPLEXITY_MODEL = "sonar"

DOCUMENT_SOURCE_LABELS = {
    "ACFR": "ACFR / audited financial statements",
    "OS": "Official statement / offering document",
    "DebtReport": "Debt schedule / official statement",
    "AnnualReport": "Annual report",
    "RateStudy": "Rate study",
    "CountyAssessor": "County assessor / tax base report",
    "MoodysWorkbook": "Moody's workbook or rating support",
    "MoodysReport": "Moody's rating report",
    "RatingReport": "Rating report",
    "CreditScope": "CreditScope workbook",
    "CensusACS": "Census ACS",
    "BEA": "BEA data",
    "IPEDS_Excel": "IPEDS workbook",
    "Manual": "Analyst review",
}

DOCUMENT_PRIORITY = {
    "ACFR / audited financial statements": 1,
    "Official statement / offering document": 2,
    "Debt schedule / official statement": 3,
    "Annual report": 4,
    "Rate study": 5,
    "County assessor / tax base report": 6,
    "Moody's workbook or rating support": 7,
    "Moody's rating report": 8,
    "Rating report": 9,
    "CreditScope workbook": 10,
    "Census ACS": 20,
    "BEA data": 21,
    "IPEDS workbook": 22,
    "Analyst review": 99,
}


@dataclass(frozen=True)
class ApiCallStatus:
    ok: bool
    status: str
    detail: str = ""


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def build_deploy_sanity_check(
    *,
    pubfin_api_key: str | None = None,
    llama_cloud_api_key: str | None = None,
) -> pd.DataFrame:
    """Return lightweight deployment readiness checks for Review & Audit."""
    llama_installed = _module_available("llama_cloud")
    pypdf_installed = _module_available("pypdf")
    rows = [
        {
            "check": "pypdf fallback available",
            "status": "ready" if pypdf_installed else "missing_dependency",
            "required": True,
            "detail": "Local PDF text extraction fallback is available."
            if pypdf_installed
            else "Install pypdf from requirements.txt so uploaded PDFs can still be parsed without LlamaCloud.",
        },
        {
            "check": "llama-cloud installed",
            "status": "ready" if llama_installed else "missing_dependency",
            "required": False,
            "detail": "LlamaCloud SDK is importable."
            if llama_installed
            else "Install llama-cloud from requirements.txt to enable agentic PDF parsing.",
        },
        {
            "check": "PUBFIN_API_KEY configured",
            "status": "ready" if _clean_text(pubfin_api_key) else "missing_config",
            "required": False,
            "detail": "Perplexity source discovery is enabled."
            if _clean_text(pubfin_api_key)
            else "Add PUBFIN_API_KEY or PERPLEXITY_API_KEY in Streamlit secrets to enable recommended links.",
        },
        {
            "check": "LLAMA_CLOUD_API_KEY configured",
            "status": "ready" if _clean_text(llama_cloud_api_key) else "missing_config",
            "required": False,
            "detail": "LlamaCloud parsing is enabled."
            if _clean_text(llama_cloud_api_key)
            else "Add LLAMA_CLOUD_API_KEY in Streamlit secrets to enable LlamaCloud parsing; pypdf fallback can still run.",
        },
    ]
    out = pd.DataFrame(rows)
    out["deploy_blocker"] = out["required"].astype(bool) & out["status"].ne("ready")
    return out


def _clean_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _split_pipe(value: Any) -> list[str]:
    parts: list[str] = []
    for part in re.split(r"[|;,]", _clean_text(value)):
        part = part.strip()
        if part and part.lower() != "nan" and part not in parts:
            parts.append(part)
    return parts


def _join_unique(values: Iterable[Any], sep: str = "; ") -> str:
    out: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text.lower() != "nan" and text not in out:
            out.append(text)
    return sep.join(out)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return int(float(value))
    except Exception:
        return default


def _expected_documents_from_sources(*sources: Any) -> str:
    labels: list[str] = []
    for source_blob in sources:
        for source in _split_pipe(source_blob):
            labels.append(DOCUMENT_SOURCE_LABELS.get(source, source))
    return _join_unique(labels)


def _primary_document_rank(expected_documents: str) -> int:
    ranks = [DOCUMENT_PRIORITY.get(part, 50) for part in _split_pipe(expected_documents)]
    return min(ranks) if ranks else 50


def _local_concepts(row: Mapping[str, Any]) -> str:
    seed = {
        "field_name": row.get("field_name", ""),
        "factor": row.get("factors", row.get("factor", "")),
        "metric": row.get("metrics", row.get("metric", "")),
        "expected_source": row.get("expected_documents", ""),
        "suggested_search_terms": " ".join(
            [
                _clean_text(row.get("priority_sources", "")),
                _clean_text(row.get("preferred_source", "")),
                _clean_text(row.get("fallback_source", "")),
                _clean_text(row.get("template_source_priority", "")),
            ]
        ),
        "definition_or_hint": row.get("priority_notes", row.get("notes", "")),
    }
    terms = field_search_terms(seed)
    field_name = _clean_text(row.get("field_name", ""))
    if field_name:
        terms.insert(0, field_name.replace("_", " "))
    return _join_unique(terms[:16])


def build_section_b_term_matrix(
    methodology_id: str,
    *,
    include_manual: bool = False,
    max_fields: int | None = None,
) -> pd.DataFrame:
    """Return one row per methodology/raw-field with concepts and expected files."""
    matrix = build_methodology_field_matrix()
    if matrix.empty:
        return pd.DataFrame()

    out = matrix[matrix["methodology_id"].astype(str).eq(str(methodology_id))].copy()
    if not include_manual:
        out = out[~out["field_name"].astype(str).isin(["manual_score", "no_raw_field_required"])]
    if out.empty:
        return out

    out["expected_documents"] = out.apply(
        lambda row: _expected_documents_from_sources(
            row.get("priority_sources", ""),
            row.get("template_source_priority", ""),
            row.get("preferred_source", ""),
            row.get("fallback_source", ""),
        ),
        axis=1,
    )
    out["local_concept_terms"] = out.apply(_local_concepts, axis=1)
    out["document_rank"] = out["expected_documents"].map(_primary_document_rank)
    out["audit_priority_score"] = out.apply(
        lambda row: (
            (100 if _as_int(row.get("audit_field_blocking")) > 0 else 0)
            + (30 if "ACFR" in _clean_text(row.get("priority_sources")) else 0)
            + (25 if "OS" in _clean_text(row.get("priority_sources")) else 0)
            + (10 * _as_int(row.get("formula_count")))
            - _as_int(row.get("document_rank"))
        ),
        axis=1,
    )
    out["search_query_hint"] = out.apply(
        lambda row: _join_unique(
            [
                row.get("field_name", ""),
                row.get("metrics", ""),
                row.get("expected_documents", ""),
                row.get("local_concept_terms", ""),
            ],
            sep=" | ",
        ),
        axis=1,
    )
    out = out.sort_values(
        ["audit_priority_score", "document_rank", "field_name"],
        ascending=[False, True, True],
    ).reset_index(drop=True)
    if max_fields is not None:
        out = out.head(max_fields).copy()
    preferred = [
        "methodology_id",
        "field_name",
        "field_category",
        "formula_count",
        "formula_ids",
        "factors",
        "metrics",
        "expected_documents",
        "priority_sources",
        "template_source_priority",
        "preferred_source",
        "fallback_source",
        "local_concept_terms",
        "search_query_hint",
        "audit_field_blocking",
        "audit_coverage_statuses",
        "audit_priority_score",
    ]
    return out[[col for col in preferred if col in out.columns] + [col for col in out.columns if col not in preferred]]


def _targets_for_prompt(targets: pd.DataFrame, *, max_rows: int = 12) -> str:
    if targets is None or targets.empty:
        return ""
    lines: list[str] = []
    for _, row in targets.head(max_rows).iterrows():
        lines.append(
            json.dumps(
                {
                    "field_name": row.get("field_name", ""),
                    "metrics": row.get("metrics", ""),
                    "formula_ids": row.get("formula_ids", ""),
                    "expected_documents": row.get("expected_documents", ""),
                    "concept_terms": row.get("local_concept_terms", ""),
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


def build_source_discovery_prompt(
    issuer_name: str,
    analysis_year: str | int,
    targets: pd.DataFrame,
) -> str:
    """Build a compact Perplexity prompt for source links and concept aliases."""
    target_text = _targets_for_prompt(targets)
    return f"""
Find public-finance source documents for this issuer and scoring audit.

Issuer: {issuer_name or "unknown issuer"}
Analysis/fiscal year: {analysis_year or "latest available"}

Target fields and concepts:
{target_text}

Return JSON only. The JSON must be an array of objects with these keys:
source_type, title, url, date_or_year, related_fields, concept_terms, reason, confidence.

Prioritize official issuer ACFR/audited financial statements, official statements,
debt schedules, rating reports, and issuer-hosted PDFs. Include download links
when available. Do not include generic explanatory pages unless no source PDF is
available.
""".strip()


def _document_query_fragments(targets: pd.DataFrame, *, limit: int = 4) -> list[str]:
    fragments: list[str] = []
    if targets is None or targets.empty:
        return fragments
    for _, row in targets.head(limit).iterrows():
        document = _clean_text(row.get("expected_documents", ""))
        metrics = _clean_text(row.get("metrics", ""))
        terms = _clean_text(row.get("local_concept_terms", ""))
        fragment = _join_unique([document, metrics, terms], sep=" ")
        if fragment:
            fragments.append(fragment[:220])
    return fragments


def build_source_search_queries(
    issuer_name: str,
    analysis_year: str | int,
    targets: pd.DataFrame,
    *,
    max_queries: int = 5,
) -> list[str]:
    """Build Search API queries for official source-document discovery."""
    issuer = _clean_text(issuer_name) or "public finance issuer"
    year = _clean_text(analysis_year) or "latest"
    base_queries = [
        f'{issuer} {year} ACFR audited financial statements PDF',
        f'{issuer} {year} annual comprehensive financial report PDF',
        f'{issuer} official statement bonds PDF debt service',
    ]
    field_queries = [
        f"{issuer} {year} {fragment} PDF"
        for fragment in _document_query_fragments(targets)
    ]
    queries = []
    for query in [*base_queries, *field_queries]:
        cleaned = _clean_text(query)
        if cleaned and cleaned not in queries:
            queries.append(cleaned)
        if len(queries) >= max_queries:
            break
    return queries


def _infer_source_type(title: Any, url: Any, snippet: Any) -> str:
    text = f"{title} {url} {snippet}".lower()
    if any(token in text for token in ["acfr", "annual comprehensive financial", "audited financial"]):
        return "ACFR"
    if any(token in text for token in ["official statement", "offering document", "preliminary official"]):
        return "OS"
    if "debt service" in text or "continuing disclosure" in text:
        return "DebtReport"
    if "rating" in text or "moody" in text or "s&p" in text or "fitch" in text:
        return "RatingReport"
    if "rate study" in text:
        return "RateStudy"
    if "assessor" in text or "tax roll" in text:
        return "CountyAssessor"
    return "SourceLink"


def _target_matches_for_result(result: Mapping[str, Any], targets: pd.DataFrame) -> tuple[str, str]:
    if targets is None or targets.empty:
        return "", ""
    haystack = " ".join(
        _clean_text(result.get(col, ""))
        for col in ["title", "url", "snippet", "source_type"]
    ).lower()
    fields: list[str] = []
    concepts: list[str] = []
    for _, target in targets.iterrows():
        field = _clean_text(target.get("field_name", ""))
        terms = [
            field.replace("_", " "),
            *_split_pipe(target.get("metrics", "")),
            *_split_pipe(target.get("local_concept_terms", "")),
            *_split_pipe(target.get("expected_documents", "")),
        ]
        matched = [term for term in terms if term and term.lower() in haystack]
        if matched:
            if field:
                fields.append(field)
            concepts.extend(matched[:4])
    return _join_unique(fields), _join_unique(concepts)


def _recommendation_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    for col in [
        "source_type",
        "title",
        "url",
        "date_or_year",
        "related_fields",
        "concept_terms",
        "reason",
        "confidence",
        "discovery_method",
    ]:
        if col not in out.columns:
            out[col] = ""
    return out[
        [
            "source_type",
            "title",
            "url",
            "date_or_year",
            "related_fields",
            "concept_terms",
            "reason",
            "confidence",
            "discovery_method",
        ]
    ]


def _search_results_to_recommendations(
    search_response: Mapping[str, Any],
    targets: pd.DataFrame,
) -> pd.DataFrame:
    raw_results = search_response.get("results", []) if isinstance(search_response, Mapping) else []
    flattened: list[Mapping[str, Any]] = []
    if isinstance(raw_results, list):
        for item in raw_results:
            if isinstance(item, Mapping):
                flattened.append(item)
            elif isinstance(item, list):
                flattened.extend(sub for sub in item if isinstance(sub, Mapping))

    rows: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for result in flattened:
        url = _clean_text(result.get("url", ""))
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        source_type = _infer_source_type(result.get("title"), url, result.get("snippet"))
        related_fields, concept_terms = _target_matches_for_result(
            {**dict(result), "source_type": source_type},
            targets,
        )
        has_pdf = ".pdf" in url.lower() or "pdf" in _clean_text(result.get("title", "")).lower()
        confidence = 0.72
        if source_type in {"ACFR", "OS", "DebtReport"}:
            confidence += 0.12
        if related_fields:
            confidence += 0.08
        if has_pdf:
            confidence += 0.05
        rows.append(
            {
                "source_type": source_type,
                "title": result.get("title", ""),
                "url": url,
                "date_or_year": result.get("date", "") or result.get("last_updated", ""),
                "related_fields": related_fields,
                "concept_terms": concept_terms,
                "reason": result.get("snippet", ""),
                "confidence": round(min(confidence, 0.95), 2),
                "discovery_method": "perplexity_search",
            }
        )
    return _recommendation_columns(pd.DataFrame(rows))


def perplexity_search_recommendations(
    issuer_name: str,
    analysis_year: str | int,
    targets: pd.DataFrame,
    *,
    api_key: str | None = None,
    timeout: int = 45,
    urlopen: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Use Perplexity Search API to return structured source-document links."""
    if not api_key:
        return {
            "status": ApiCallStatus(False, "missing_api_key", "Set PUBFIN_API_KEY or PERPLEXITY_API_KEY."),
            "recommendations": pd.DataFrame(),
            "raw_response": {},
        }

    queries = build_source_search_queries(issuer_name, analysis_year, targets)
    payload: dict[str, Any] = {
        "query": queries if len(queries) > 1 else (queries[0] if queries else ""),
        "max_results": 8,
        "search_context_size": "low",
    }
    opener = urlopen or urllib_request.urlopen
    request = urllib_request.Request(
        PERPLEXITY_SEARCH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return {
            "status": ApiCallStatus(False, "search_http_error", f"{exc.code}: {detail[:500]}"),
            "recommendations": pd.DataFrame(),
            "raw_response": {},
        }
    except Exception as exc:
        return {
            "status": ApiCallStatus(False, "search_request_error", str(exc)),
            "recommendations": pd.DataFrame(),
            "raw_response": {},
        }

    try:
        parsed_response = json.loads(raw)
    except Exception:
        parsed_response = {"raw": raw}
    recommendations = (
        _search_results_to_recommendations(parsed_response, targets)
        if isinstance(parsed_response, Mapping)
        else pd.DataFrame()
    )
    return {
        "status": ApiCallStatus(True, "ok", ""),
        "recommendations": recommendations,
        "raw_response": parsed_response,
        "queries": queries,
    }


def _strip_code_fence(text: str) -> str:
    text = _clean_text(text)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    return text


def _parse_jsonish_array(text: str) -> list[dict[str, Any]]:
    cleaned = _strip_code_fence(text)
    try:
        parsed = json.loads(cleaned)
    except Exception:
        match = re.search(r"\[[\s\S]*\]", cleaned)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return []
    if isinstance(parsed, dict):
        parsed = parsed.get("results", parsed.get("sources", []))
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def perplexity_source_recommendations(
    issuer_name: str,
    analysis_year: str | int,
    targets: pd.DataFrame,
    *,
    api_key: str | None = None,
    model: str = DEFAULT_PERPLEXITY_MODEL,
    timeout: int = 60,
    urlopen: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Recommend source documents with Search API first and Sonar as fallback."""
    if not api_key:
        return {
            "status": ApiCallStatus(False, "missing_api_key", "Set PUBFIN_API_KEY or PERPLEXITY_API_KEY."),
            "recommendations": pd.DataFrame(),
            "raw_response": {},
        }

    search_result = perplexity_search_recommendations(
        issuer_name,
        analysis_year,
        targets,
        api_key=api_key,
        timeout=min(timeout, 45),
        urlopen=urlopen,
    )
    search_recommendations = search_result.get("recommendations", pd.DataFrame())
    if isinstance(search_recommendations, pd.DataFrame) and not search_recommendations.empty:
        return {
            "status": search_result["status"],
            "recommendations": search_recommendations,
            "raw_response": {
                "search": search_result.get("raw_response", {}),
                "queries": search_result.get("queries", []),
            },
        }

    prompt = build_source_discovery_prompt(issuer_name, analysis_year, targets)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a public-finance source discovery assistant. Return valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    opener = urlopen or urllib_request.urlopen
    request = urllib_request.Request(
        PERPLEXITY_CHAT_COMPLETIONS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return {
            "status": ApiCallStatus(False, "http_error", f"{exc.code}: {detail[:500]}"),
            "recommendations": pd.DataFrame(),
            "raw_response": {},
        }
    except Exception as exc:
        return {
            "status": ApiCallStatus(False, "request_error", str(exc)),
            "recommendations": pd.DataFrame(),
            "raw_response": {},
        }

    try:
        parsed_response = json.loads(raw)
    except Exception:
        parsed_response = {"raw": raw}

    message = (
        parsed_response.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        if isinstance(parsed_response, dict)
        else ""
    )
    rows = _parse_jsonish_array(message)
    if not rows and isinstance(parsed_response, dict):
        citations = parsed_response.get("citations") or parsed_response.get("search_results") or []
        for citation in citations:
            if isinstance(citation, str):
                rows.append({"source_type": "Perplexity citation", "title": citation, "url": citation})
            elif isinstance(citation, Mapping):
                rows.append(
                    {
                        "source_type": citation.get("source_type", "Perplexity citation"),
                        "title": citation.get("title", citation.get("url", "")),
                        "url": citation.get("url", ""),
                        "date_or_year": citation.get("date", citation.get("published_date", "")),
                        "reason": citation.get("snippet", ""),
                    }
                )

    recommendations = pd.DataFrame(rows)
    if not recommendations.empty:
        recommendations["discovery_method"] = "perplexity_sonar"
        recommendations = _recommendation_columns(recommendations)
    return {
        "status": ApiCallStatus(True, "ok", ""),
        "recommendations": recommendations,
        "raw_response": {
            "search": search_result.get("raw_response", {}),
            "search_status": search_result["status"].status,
            "sonar": parsed_response,
        },
    }


def _llamacloud_page_rows(file_name: str, result: Any) -> list[dict[str, Any]]:
    markdown = getattr(result, "markdown", None)
    pages = getattr(markdown, "pages", None) if markdown is not None else None
    rows: list[dict[str, Any]] = []
    if pages:
        for idx, page in enumerate(pages, start=1):
            text = getattr(page, "markdown", None) or getattr(page, "text", None) or ""
            page_number = getattr(page, "page", None) or getattr(page, "page_number", None) or idx
            rows.append(
                {
                    "source_slot": "ai_upload",
                    "source_name": "",
                    "file_name": file_name,
                    "page_number": page_number,
                    "text": text,
                    "extraction_status": "llamacloud_markdown_ready" if _clean_text(text) else "llamacloud_blank_page",
                    "error": "",
                    "parser": "llamacloud",
                }
            )
    if not rows:
        text = getattr(result, "markdown", None)
        if isinstance(text, str) and _clean_text(text):
            rows.append(
                {
                    "source_slot": "ai_upload",
                    "source_name": "",
                    "file_name": file_name,
                    "page_number": 1,
                    "text": text,
                    "extraction_status": "llamacloud_markdown_ready",
                    "error": "",
                    "parser": "llamacloud",
                }
            )
    return rows


def parse_pdf_with_llamacloud(
    document: PdfDocument,
    *,
    api_key: str | None = None,
    max_pages: int | None = None,
) -> pd.DataFrame:
    """Parse one PDF with LlamaCloud. Returns status rows instead of raising."""
    if not api_key:
        return pd.DataFrame(
            [
                {
                    "source_slot": document.source_slot,
                    "source_name": document.source_name,
                    "file_name": document.file_name,
                    "page_number": "",
                    "text": "",
                    "extraction_status": "llamacloud_disabled",
                    "error": "LLAMA_CLOUD_API_KEY is not configured.",
                    "parser": "llamacloud",
                }
            ]
        )
    try:
        from llama_cloud import LlamaCloud  # type: ignore
    except Exception as exc:
        return pd.DataFrame(
            [
                {
                    "source_slot": document.source_slot,
                    "source_name": document.source_name,
                    "file_name": document.file_name,
                    "page_number": "",
                    "text": "",
                    "extraction_status": "llamacloud_sdk_missing",
                    "error": f"Install llama-cloud to enable LlamaCloud parsing: {exc}",
                    "parser": "llamacloud",
                }
            ]
        )

    old_key = os.environ.get("LLAMA_CLOUD_API_KEY")
    os.environ["LLAMA_CLOUD_API_KEY"] = api_key
    temp_path = ""
    try:
        suffix = Path(document.file_name).suffix or ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(document.payload)
            temp_path = handle.name
        client = LlamaCloud()
        uploaded = client.files.create(file=temp_path, purpose="parse")
        result = client.parsing.parse(
            file_id=uploaded.id,
            tier="agentic",
            version="latest",
            expand=["markdown"],
        )
        rows = _llamacloud_page_rows(document.file_name, result)
        if max_pages is not None:
            rows = rows[:max_pages]
        for row in rows:
            row["source_slot"] = document.source_slot
            row["source_name"] = document.source_name
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception as exc:
        return pd.DataFrame(
            [
                {
                    "source_slot": document.source_slot,
                    "source_name": document.source_name,
                    "file_name": document.file_name,
                    "page_number": "",
                    "text": "",
                    "extraction_status": "llamacloud_error",
                    "error": str(exc),
                    "parser": "llamacloud",
                }
            ]
        )
    finally:
        if old_key is None:
            os.environ.pop("LLAMA_CLOUD_API_KEY", None)
        else:
            os.environ["LLAMA_CLOUD_API_KEY"] = old_key
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass


def pdf_parse_cache_key(
    document: PdfDocument,
    *,
    max_pages: int | None,
    prefer_llama: bool,
    llama_enabled: bool,
) -> str:
    digest = hashlib.sha256(document.payload).hexdigest()[:20]
    parser_mode = "llama" if prefer_llama and llama_enabled else "pypdf"
    return "|".join(
        [
            parser_mode,
            str(max_pages or "all"),
            document.source_slot,
            document.source_name,
            document.file_name,
            str(len(document.payload)),
            digest,
        ]
    )


def parse_pdf_documents(
    documents: Sequence[PdfDocument],
    *,
    llama_api_key: str | None = None,
    max_pages: int | None = 60,
    prefer_llama: bool = True,
    page_cache: MutableMapping[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Parse PDFs with LlamaCloud when possible, falling back to local pypdf."""
    frames: list[pd.DataFrame] = []
    for document in documents:
        cache_key = pdf_parse_cache_key(
            document,
            max_pages=max_pages,
            prefer_llama=prefer_llama,
            llama_enabled=bool(llama_api_key),
        )
        if page_cache is not None and cache_key in page_cache:
            cached = page_cache[cache_key]
            if isinstance(cached, pd.DataFrame) and not cached.empty:
                cached = cached.copy()
                cached["cache_status"] = "cache_hit"
                frames.append(cached)
                continue

        parsed = pd.DataFrame()
        if prefer_llama and llama_api_key:
            parsed = parse_pdf_with_llamacloud(document, api_key=llama_api_key, max_pages=max_pages)
            ready = (
                not parsed.empty
                and parsed.get("text", pd.Series(dtype=str)).fillna("").astype(str).str.strip().ne("").any()
            )
            if ready:
                parsed = parsed.copy()
                parsed["cache_status"] = "cache_miss"
                if page_cache is not None:
                    page_cache[cache_key] = parsed.copy()
                frames.append(parsed)
                continue
        fallback = extract_pdf_pages(document, max_pages=max_pages)
        if not fallback.empty:
            fallback = fallback.copy()
            fallback["parser"] = "pypdf"
            fallback["cache_status"] = "cache_miss"
        if not parsed.empty and not fallback.empty:
            parsed_status = parsed[parsed.get("text", pd.Series(dtype=str)).fillna("").astype(str).str.strip().eq("")]
            combined = pd.concat([fallback, parsed_status], ignore_index=True, sort=False)
            if page_cache is not None:
                page_cache[cache_key] = combined.copy()
            frames.append(combined)
        elif not fallback.empty:
            if page_cache is not None:
                page_cache[cache_key] = fallback.copy()
            frames.append(fallback)
        elif not parsed.empty:
            parsed = parsed.copy()
            parsed["cache_status"] = "cache_miss"
            if page_cache is not None:
                page_cache[cache_key] = parsed.copy()
            frames.append(parsed)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _value_candidates(text: str, *, limit: int = 8) -> str:
    values: list[str] = []
    pattern = re.compile(
        r"(?P<value>\(?\$?\s*-?\d[\d,]*(?:\.\d+)?\s*\)?\s*(?:%|percent|million|billion|thousand|m|bn)?)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text or ""):
        raw = re.sub(r"\s+", " ", match.group("value")).strip()
        if len(raw) < 2 or raw in values:
            continue
        values.append(raw)
        if len(values) >= limit:
            break
    return "; ".join(values)


def _candidate_rows_from_evidence(evidence: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if evidence is None or evidence.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)
    for _, row in evidence.iterrows():
        values = _clean_text(row.get("candidate_values", ""))
        first_value = values.split(";")[0].strip() if values else ""
        confidence = min(0.78, 0.40 + float(row.get("score", 0) or 0) / 100.0)
        rows.append(
            {
                "field_name": row.get("field_name", ""),
                "value": first_value,
                "unit": "",
                "source_name": row.get("source_name", "") or "UploadedPDF",
                "source_type": "Document",
                "source_detail": "section_b_ai_pdf_evidence",
                "confidence": confidence,
                "source_file": row.get("file_name", ""),
                "source_table": f"PDF page {row.get('page_number', '')}",
                "source_cell_or_api": f"page:{row.get('page_number', '')}",
                "source_label": row.get("matched_terms", ""),
                "candidate_status": "source_review",
                "notes": "Section B evidence candidate; analyst approval required before model use.",
            }
        )
    return normalize_source_candidates(pd.DataFrame(rows))


def recommendations_to_source_candidates(recommendations: pd.DataFrame) -> pd.DataFrame:
    """Convert recommended links to document-pending review candidates."""
    if recommendations is None or recommendations.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)

    rows: list[dict[str, Any]] = []
    for _, rec in recommendations.iterrows():
        related_fields = _split_pipe(rec.get("related_fields", ""))
        if not related_fields:
            related_fields = ["document_link"]
        for field in related_fields:
            rows.append(
                {
                    "field_name": field,
                    "value": "",
                    "unit": "",
                    "source_name": rec.get("source_type", "") or "SourceLink",
                    "source_type": "Document",
                    "source_detail": "section_b_recommended_link",
                    "confidence": rec.get("confidence", 0.65),
                    "source_file": rec.get("url", ""),
                    "source_table": rec.get("title", ""),
                    "source_cell_or_api": rec.get("date_or_year", ""),
                    "source_label": rec.get("concept_terms", ""),
                    "candidate_status": "document_pending",
                    "notes": _join_unique(
                        [
                            "Recommended source link; download/upload the source document before using values.",
                            rec.get("reason", ""),
                            rec.get("discovery_method", ""),
                        ],
                        sep=" ",
                    ),
                }
            )
    return normalize_source_candidates(pd.DataFrame(rows))


def build_section_b_pdf_audit(
    pdf_documents: Sequence[PdfDocument],
    targets: pd.DataFrame,
    *,
    llama_api_key: str | None = None,
    max_pages: int | None = 60,
    top_n_per_field: int = 3,
    page_cache: MutableMapping[str, pd.DataFrame] | None = None,
) -> dict[str, pd.DataFrame]:
    """Parse uploaded PDFs and rank snippets against Section B targets."""
    pages = parse_pdf_documents(
        pdf_documents,
        llama_api_key=llama_api_key,
        max_pages=max_pages,
        page_cache=page_cache,
    )
    if pages.empty or targets is None or targets.empty:
        return {
            "pdf_pages": pages,
            "pdf_evidence": pd.DataFrame(),
            "source_candidates": pd.DataFrame(columns=CANDIDATE_COLUMNS),
        }

    frames: list[pd.DataFrame] = []
    for _, target in targets.iterrows():
        snippets = rank_pdf_snippets_for_field(
            pages,
            target.to_dict(),
            top_n=top_n_per_field,
            char_limit=1800,
        )
        if not snippets.empty:
            frames.append(snippets)
    evidence = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    if evidence.empty:
        return {
            "pdf_pages": pages,
            "pdf_evidence": evidence,
            "source_candidates": pd.DataFrame(columns=CANDIDATE_COLUMNS),
        }

    evidence = evidence.copy()
    evidence["candidate_values"] = evidence["snippet"].map(_value_candidates)
    evidence["citation"] = evidence.apply(
        lambda row: f"{row.get('file_name', '')} p. {row.get('page_number', '')}",
        axis=1,
    )
    source_by_file = {
        document.file_name: document.source_name
        for document in pdf_documents
    }
    evidence["source_name"] = evidence["file_name"].map(source_by_file).fillna(evidence.get("source_name", ""))
    return {
        "pdf_pages": pages,
        "pdf_evidence": evidence,
        "source_candidates": _candidate_rows_from_evidence(evidence),
    }


def uploaded_pdf_documents_from_payloads(payloads: Sequence[Mapping[str, Any]]) -> list[PdfDocument]:
    """Normalize UI upload payloads to PdfDocument instances."""
    documents: list[PdfDocument] = []
    for item in payloads:
        payload = item.get("payload")
        if not isinstance(payload, (bytes, bytearray)) or not payload:
            continue
        documents.append(
            PdfDocument(
                source_slot=_clean_text(item.get("source_slot")) or "section_b_upload",
                source_name=_clean_text(item.get("source_name")) or "UploadedPDF",
                file_name=_clean_text(item.get("file_name")) or "uploaded.pdf",
                payload=bytes(payload),
            )
        )
    return documents
