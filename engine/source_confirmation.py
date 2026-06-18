"""Human confirmation helpers for source-pending data candidates."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from engine.data_sourcing_engine import CANDIDATE_COLUMNS, normalize_source_candidates


PENDING_READINESS_STATUSES = {"source_pending", "needs_review"}
QUEUE_DECISIONS = ["Pending", "Accept", "Accept edited value", "Reject", "Needs more review"]


def _clean(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _has_value(value: Any) -> bool:
    return bool(_clean(value))


def _stable_row_id(parts: list[Any]) -> str:
    raw = "|".join(_clean(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _selected_rows(source_report: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(source_report, pd.DataFrame) or source_report.empty:
        return pd.DataFrame()
    out = source_report.copy()
    if "selected" in out.columns:
        out = out[out["selected"].astype(str).str.lower().isin({"true", "1", "yes"})].copy()
    return out


def _page_from_source(row: pd.Series) -> str:
    for col in ["source_cell_or_api", "source_table"]:
        text = _clean(row.get(col))
        if "page:" in text.lower():
            return text.split(":", 1)[-1].strip()
        lower = text.lower()
        if lower.startswith("pdf page"):
            return text.split()[-1].strip()
    return ""


def _evidence_match(row: pd.Series, evidence: pd.DataFrame) -> dict[str, Any]:
    if not isinstance(evidence, pd.DataFrame) or evidence.empty:
        return {}
    if "field_name" not in evidence.columns:
        return {}

    field = _clean(row.get("field_name"))
    source_file = _clean(row.get("source_file"))
    page = _page_from_source(row)

    matches = evidence[evidence["field_name"].fillna("").astype(str).eq(field)].copy()
    if matches.empty:
        return {}
    if source_file and "file_name" in matches.columns:
        file_matches = matches[matches["file_name"].fillna("").astype(str).eq(source_file)].copy()
        if not file_matches.empty:
            matches = file_matches
    if page and "page_number" in matches.columns:
        page_text = matches["page_number"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
        page_matches = matches[page_text.eq(page)].copy()
        if not page_matches.empty:
            matches = page_matches
    if "score" in matches.columns:
        matches["_score_sort"] = pd.to_numeric(matches["score"], errors="coerce").fillna(0)
        matches = matches.sort_values("_score_sort", ascending=False)
    return matches.iloc[0].to_dict()


def build_source_confirmation_queue(
    source_report: pd.DataFrame,
    pdf_evidence: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Create a human review queue from selected source-pending rows."""
    selected = _selected_rows(source_report)
    if selected.empty:
        return pd.DataFrame()

    readiness = selected.get("readiness_status", "").fillna("").astype(str)
    quality = selected.get("source_quality_status", "").fillna("").astype(str)
    pending = selected[
        readiness.isin(PENDING_READINESS_STATUSES)
        | quality.isin({"source_pending", "review"})
    ].copy()
    if pending.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    evidence = pdf_evidence if isinstance(pdf_evidence, pd.DataFrame) else pd.DataFrame()
    for _, row in pending.iterrows():
        ev = _evidence_match(row, evidence)
        field = _clean(row.get("field_name"))
        candidate_value = row.get("value")
        source_file = _clean(row.get("source_file")) or _clean(ev.get("file_name"))
        page_or_cell = _clean(row.get("source_cell_or_api")) or _clean(row.get("source_table"))
        if not page_or_cell and _clean(ev.get("page_number")):
            page_or_cell = f"page:{_clean(ev.get('page_number'))}"
        rows.append(
            {
                "row_id": _stable_row_id(
                    [
                        field,
                        candidate_value,
                        row.get("canonical_source") or row.get("source_name"),
                        source_file,
                        page_or_cell,
                    ]
                ),
                "decision": "Pending",
                "field_name": field,
                "candidate_value": candidate_value,
                "confirmed_value": "" if candidate_value is None else candidate_value,
                "source_name": _clean(row.get("canonical_source")) or _clean(row.get("source_name")),
                "source_file": source_file,
                "page_or_cell": page_or_cell,
                "source_table": _clean(row.get("source_table")),
                "source_label": _clean(row.get("source_label")),
                "confidence": row.get("confidence"),
                "readiness_status": _clean(row.get("readiness_status")),
                "source_quality_status": _clean(row.get("source_quality_status")),
                "citation": _clean(ev.get("citation")),
                "candidate_values_from_snippet": _clean(ev.get("candidate_values")),
                "snippet": _clean(ev.get("snippet")),
                "review_note": "",
            }
        )
    return pd.DataFrame(rows)


def merge_saved_queue_decisions(queue: pd.DataFrame, saved: pd.DataFrame | None) -> pd.DataFrame:
    """Overlay saved decisions onto a freshly rebuilt queue."""
    if queue.empty or not isinstance(saved, pd.DataFrame) or saved.empty or "row_id" not in saved.columns:
        return queue
    keep_cols = ["row_id", "decision", "confirmed_value", "review_note"]
    saved_small = saved[[col for col in keep_cols if col in saved.columns]].copy()
    out = queue.drop(columns=[col for col in keep_cols if col in queue.columns and col != "row_id"])
    out = out.merge(saved_small, on="row_id", how="left")
    for col, default in [("decision", "Pending"), ("confirmed_value", ""), ("review_note", "")]:
        if col not in out.columns:
            out[col] = default
        out[col] = out[col].fillna(default)
    return out


def confirmation_queue_to_source_candidates(queue: pd.DataFrame) -> pd.DataFrame:
    """Convert accepted queue rows into formula-eligible source candidates."""
    if not isinstance(queue, pd.DataFrame) or queue.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)

    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()
    for _, row in queue.iterrows():
        decision = _clean(row.get("decision"))
        if decision not in {"Accept", "Accept edited value"}:
            continue
        value = row.get("confirmed_value") if decision == "Accept edited value" else row.get("candidate_value")
        if not _has_value(value):
            value = row.get("confirmed_value")
        if not _has_value(value):
            continue
        source_name = _clean(row.get("source_name")) or "Manual"
        confidence = pd.to_numeric(row.get("confidence"), errors="coerce")
        confidence = 0.0 if pd.isna(confidence) else float(confidence)
        note_bits = [
            f"Source confirmation decision: {decision}.",
            _clean(row.get("review_note")),
            _clean(row.get("citation")),
        ]
        rows.append(
            {
                "field_name": _clean(row.get("field_name")),
                "value": value,
                "unit": "",
                "source_name": source_name,
                "source_type": "Document" if source_name not in {"Manual", "CreditScope", "BEA", "CensusACS"} else "",
                "source_detail": "human_confirmed_source_candidate",
                "confidence": max(confidence, 0.95),
                "source_file": _clean(row.get("source_file")),
                "source_table": _clean(row.get("source_table")),
                "source_cell_or_api": _clean(row.get("page_or_cell")),
                "source_label": _clean(row.get("source_label")),
                "candidate_status": "ready",
                "notes": " ".join(bit for bit in note_bits if bit) + f" confirmed_at={now}",
            }
        )
    return normalize_source_candidates(pd.DataFrame(rows))
