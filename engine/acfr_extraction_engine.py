from __future__ import annotations

from dataclasses import dataclass
import io
import re
from typing import Any, Iterable, Mapping

import pandas as pd


DEFAULT_FIELD_HINTS: dict[str, list[str]] = {
    "gov_operating_margin_3yr_avg": [
        "statement of revenues expenditures and changes in fund balances",
        "governmental funds",
        "revenues",
        "expenditures",
        "other financing sources",
        "transfers",
    ],
    "available_fund_balance_ratio_3yr_avg": [
        "balance sheet governmental funds",
        "committed fund balance",
        "assigned fund balance",
        "unassigned fund balance",
        "fund balances",
        "revenues",
    ],
    "fixed_cost_burden_ratio": [
        "debt service",
        "principal",
        "interest",
        "pension expense",
        "opeb expense",
        "governmental revenues",
    ],
    "net_direct_debt_per_capita": [
        "long-term debt",
        "debt outstanding",
        "net direct debt",
        "governmental activities",
        "population",
    ],
    "npl_per_capita": [
        "net pension liability",
        "proportionate share",
        "statement of net position",
        "pension plan",
        "population",
    ],
    "net_pension_liability": [
        "net pension liability",
        "proportionate share",
        "pension plan",
        "statement of net position",
    ],
    "debt_service": ["debt service", "principal", "interest", "maturity", "requirements"],
    "pension_cost": ["pension expense", "pension cost", "pension contributions"],
    "opeb_cost": ["opeb expense", "opeb cost", "other postemployment benefits"],
    "governmental_revenue": ["governmental funds", "revenues", "taxes", "intergovernmental"],
    "governmental_expense": ["governmental funds", "expenditures", "public safety", "general government"],
    "operating_transfers": ["transfers", "other financing sources", "other financing uses"],
    "committed_fund_balance": ["committed fund balance", "fund balances", "governmental funds"],
    "assigned_fund_balance": ["assigned fund balance", "fund balances", "governmental funds"],
    "unassigned_fund_balance": ["unassigned fund balance", "fund balances", "governmental funds"],
    "reserve_revenue": ["revenues", "governmental funds", "statement of revenues"],
}


@dataclass(frozen=True)
class PdfDocument:
    source_slot: str
    source_name: str
    file_name: str
    payload: bytes


def normalize_pdf_documents(raw_docs: Any) -> list[PdfDocument]:
    docs: list[PdfDocument] = []
    if isinstance(raw_docs, Mapping):
        iterable: Iterable[Any] = [
            item
            for values in raw_docs.values()
            for item in (values if isinstance(values, list) else [values])
        ]
    elif isinstance(raw_docs, list):
        iterable = raw_docs
    else:
        iterable = []
    for item in iterable:
        if not isinstance(item, Mapping):
            continue
        payload = item.get("payload")
        if isinstance(payload, str):
            payload = payload.encode("latin-1", errors="ignore")
        if not isinstance(payload, (bytes, bytearray)) or not payload:
            continue
        docs.append(
            PdfDocument(
                source_slot=str(item.get("source_slot", "") or ""),
                source_name=str(item.get("source_name", "") or ""),
                file_name=str(item.get("file_name", "") or "uploaded.pdf"),
                payload=bytes(payload),
            )
        )
    return docs


def extract_pdf_pages(document: PdfDocument, *, max_pages: int | None = None) -> pd.DataFrame:
    """Extract per-page text. Returns status rows instead of raising for parser/PDF failures."""
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:
        return pd.DataFrame(
            [
                {
                    "source_slot": document.source_slot,
                    "source_name": document.source_name,
                    "file_name": document.file_name,
                    "page_number": "",
                    "text": "",
                    "extraction_status": "parser_missing",
                    "error": f"pypdf is required for local PDF text extraction: {exc}",
                }
            ]
        )

    try:
        reader = PdfReader(io.BytesIO(document.payload))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                pass
        page_count = len(reader.pages)
        limit = min(page_count, max_pages) if max_pages else page_count
        rows: list[dict[str, Any]] = []
        for idx in range(limit):
            text = ""
            status = "text_ready"
            try:
                text = reader.pages[idx].extract_text() or ""
                if not text.strip():
                    status = "blank_or_scanned"
            except Exception as exc:
                status = "page_error"
                text = ""
                rows.append(
                    {
                        "source_slot": document.source_slot,
                        "source_name": document.source_name,
                        "file_name": document.file_name,
                        "page_number": idx + 1,
                        "text": text,
                        "extraction_status": status,
                        "error": str(exc),
                    }
                )
                continue
            rows.append(
                {
                    "source_slot": document.source_slot,
                    "source_name": document.source_name,
                    "file_name": document.file_name,
                    "page_number": idx + 1,
                    "text": text,
                    "extraction_status": status,
                    "error": "",
                }
            )
        return pd.DataFrame(rows)
    except Exception as exc:
        return pd.DataFrame(
            [
                {
                    "source_slot": document.source_slot,
                    "source_name": document.source_name,
                    "file_name": document.file_name,
                    "page_number": "",
                    "text": "",
                    "extraction_status": "document_error",
                    "error": str(exc),
                }
            ]
        )


def extract_all_pdf_pages(documents: Iterable[PdfDocument], *, max_pages: int | None = None) -> pd.DataFrame:
    frames = [extract_pdf_pages(document, max_pages=max_pages) for document in documents]
    frames = [frame for frame in frames if isinstance(frame, pd.DataFrame) and not frame.empty]
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _clean_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def field_search_terms(row: Mapping[str, Any]) -> list[str]:
    field = str(row.get("field_name", row.get("field_or_metric", "")) or "").strip()
    seed_text = " ".join(
        _clean_text(row.get(col, ""))
        for col in [
            "field_name",
            "factor",
            "metric",
            "expected_source",
            "suggested_search_terms",
            "suggested_document_section",
            "evidence_target",
            "definition_or_hint",
            "status_reason",
        ]
    )
    terms: list[str] = []
    terms.extend(DEFAULT_FIELD_HINTS.get(field, []))
    for phrase in re.split(r"[;,|]", seed_text):
        phrase = _clean_text(phrase)
        if len(phrase) >= 4:
            terms.append(phrase)
    for token in re.findall(r"[A-Za-z][A-Za-z_]{3,}", seed_text):
        token = token.replace("_", " ").strip()
        if len(token) >= 4:
            terms.append(token)
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        normalized = _clean_text(term).lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(term)
    return deduped[:28]


def _score_page(text: str, terms: list[str]) -> tuple[float, list[str]]:
    lower = text.lower()
    score = 0.0
    matched: list[str] = []
    for term in terms:
        normalized = term.lower().strip()
        if not normalized:
            continue
        count = lower.count(normalized)
        if not count:
            continue
        matched.append(term)
        score += min(count, 4) * (4.0 if " " in normalized else 1.0)
    return score, matched


def _best_excerpt(text: str, terms: list[str], *, char_limit: int = 2400) -> str:
    text = _clean_text(text)
    if len(text) <= char_limit:
        return text
    lower = text.lower()
    positions = [
        lower.find(term.lower())
        for term in terms
        if term and lower.find(term.lower()) >= 0
    ]
    center = min(positions) if positions else 0
    start = max(0, center - char_limit // 3)
    end = min(len(text), start + char_limit)
    return text[start:end].strip()


def rank_pdf_snippets_for_field(
    pages: pd.DataFrame,
    field_row: Mapping[str, Any],
    *,
    top_n: int = 5,
    char_limit: int = 2400,
) -> pd.DataFrame:
    if not isinstance(pages, pd.DataFrame) or pages.empty:
        return pd.DataFrame()
    terms = field_search_terms(field_row)
    rows: list[dict[str, Any]] = []
    for _, page in pages.iterrows():
        text = str(page.get("text", "") or "")
        if not text.strip():
            continue
        score, matched = _score_page(text, terms)
        if score <= 0:
            continue
        rows.append(
            {
                "field_name": field_row.get("field_name", field_row.get("field_or_metric", "")),
                "source_slot": page.get("source_slot", ""),
                "source_name": page.get("source_name", ""),
                "file_name": page.get("file_name", ""),
                "page_number": page.get("page_number", ""),
                "score": score,
                "matched_terms": "; ".join(matched[:10]),
                "snippet": _best_excerpt(text, matched or terms, char_limit=char_limit),
                "extraction_status": page.get("extraction_status", ""),
            }
        )
    if not rows:
        return pd.DataFrame()
    ranked = pd.DataFrame(rows).sort_values(["score", "file_name", "page_number"], ascending=[False, True, True])
    return ranked.head(top_n).reset_index(drop=True)


def snippets_to_evidence_text(snippets: pd.DataFrame, *, max_chars: int = 14000) -> str:
    if not isinstance(snippets, pd.DataFrame) or snippets.empty:
        return ""
    parts: list[str] = []
    for _, row in snippets.iterrows():
        header = (
            f"[{row.get('source_name') or row.get('source_slot')}] "
            f"{row.get('file_name')} | PDF page {row.get('page_number')} | "
            f"matched: {row.get('matched_terms', '')}"
        )
        parts.append(f"{header}\n{row.get('snippet', '')}")
    text = "\n\n---\n\n".join(parts)
    return text[:max_chars]
