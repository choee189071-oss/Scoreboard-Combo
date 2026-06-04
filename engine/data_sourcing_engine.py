"""
Unified data sourcing engine.

This layer turns many source candidates into one canonical issuer_data dict
plus a transparent source_report. It does not calculate scorecard ratios and
does not tune values to fit sample workbooks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

import pandas as pd

from engine.calculator_engine import clean_numeric, load_formula_library, parse_required_fields
from engine.factor_engine import load_factor_template
from engine.source_registry import (
    canonical_source_name,
    coerce_bool,
    load_source_registry,
    source_metadata,
    split_pipe,
)


DEFAULT_SOURCE_PRIORITY_PATH = Path("config/source_priority.csv")
DEFAULT_DATA_DICTIONARY_PATH = Path("config/data_dictionary.csv")
DEFAULT_FORMULA_LIBRARY_PATH = Path("config/formula_library.csv")
DEFAULT_THRESHOLDS_PATH = Path("config/scoring_thresholds.csv")
DEFAULT_TEMPLATES_DIR = Path("templates")

SOURCE_PENDING_TYPES = {
    "manual_source_pending",
    "manual_pending",
    "source_pending",
    "source_review",
}
SCORECARD_IMPLIED_TYPES = {
    "scorecard_implied",
    "official_implied",
    "fixture_implied",
}
MISSING_STATUSES = {
    "missing",
    "missing_value",
    "not_found",
    "model_missing",
}

CANDIDATE_COLUMNS = [
    "field_name",
    "value",
    "unit",
    "source_name",
    "canonical_source",
    "source_type",
    "source_detail",
    "confidence",
    "source_file",
    "source_table",
    "source_cell_or_api",
    "source_label",
    "candidate_status",
    "notes",
]


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _parse_float(value: Any, default: float) -> float:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return float(value)
    except Exception:
        return default


def _normalize_field(value: Any) -> str:
    return str(value or "").strip()


def _priority_sources_from_dictionary(row: pd.Series) -> str:
    sources: list[str] = []
    for col in ["preferred_source", "fallback_source"]:
        sources.extend(split_pipe(row.get(col, "")))
    if coerce_bool(row.get("manual_allowed", True)) and "Manual" not in sources:
        sources.append("Manual")
    return "|".join(dict.fromkeys(sources))


def _formula_required_fields(
    formula_library_path: str | Path = DEFAULT_FORMULA_LIBRARY_PATH,
) -> list[str]:
    formula_library = load_formula_library(formula_library_path)
    fields: list[str] = []
    for raw in formula_library["required_data"].tolist():
        for field in parse_required_fields(raw):
            if field != "manual":
                fields.append(field)
    return sorted(set(fields))


def _default_source_priority_from_dictionary(
    data_dictionary_path: str | Path = DEFAULT_DATA_DICTIONARY_PATH,
    formula_library_path: str | Path = DEFAULT_FORMULA_LIBRARY_PATH,
) -> pd.DataFrame:
    path = Path(data_dictionary_path)
    rows: list[dict[str, Any]] = []
    known_fields: set[str] = set()
    if path.exists():
        dictionary = pd.read_csv(path)
        for _, row in dictionary.iterrows():
            field = _normalize_field(row.get("field_name", ""))
            if not field:
                continue
            known_fields.add(field)
            rows.append(
                {
                    "field_name": field,
                    "methodology_id": "default",
                    "priority_sources": _priority_sources_from_dictionary(row),
                    "min_confidence": 0.80,
                    "manual_allowed": True,
                    "notes": str(row.get("notes", "") or ""),
                }
            )

    for field in _formula_required_fields(formula_library_path):
        if field not in known_fields:
            rows.append(
                {
                    "field_name": field,
                    "methodology_id": "default",
                    "priority_sources": "Manual",
                    "min_confidence": 0.50,
                    "manual_allowed": True,
                    "notes": "Fallback priority generated from formula_library.csv.",
                }
            )
    return pd.DataFrame(rows)


def load_source_priority(
    path: str | Path = DEFAULT_SOURCE_PRIORITY_PATH,
    data_dictionary_path: str | Path = DEFAULT_DATA_DICTIONARY_PATH,
    formula_library_path: str | Path = DEFAULT_FORMULA_LIBRARY_PATH,
) -> pd.DataFrame:
    """
    Load field-level source priority.

    The preferred schema is one row per field with pipe-delimited
    priority_sources. If the file is missing/empty, this function builds a
    conservative fallback from data_dictionary.csv.
    """
    priority_path = Path(path)
    if not priority_path.exists() or priority_path.stat().st_size == 0:
        df = _default_source_priority_from_dictionary(data_dictionary_path, formula_library_path)
    else:
        df = pd.read_csv(priority_path)

    if df.empty:
        return _default_source_priority_from_dictionary(data_dictionary_path, formula_library_path)

    if "priority_sources" not in df.columns and "source_name" not in df.columns:
        raise ValueError("source_priority.csv must include priority_sources or source_name.")

    df = df.copy()
    df["field_name"] = df["field_name"].fillna("").astype(str).str.strip()
    if "methodology_id" not in df.columns:
        df["methodology_id"] = "default"
    df["methodology_id"] = df["methodology_id"].fillna("default").astype(str).str.strip()
    if "priority_sources" not in df.columns:
        df["priority_sources"] = df["source_name"].fillna("").astype(str)
    if "min_confidence" not in df.columns:
        df["min_confidence"] = 0.80
    df["min_confidence"] = pd.to_numeric(df["min_confidence"], errors="coerce").fillna(0.80)
    if "manual_allowed" not in df.columns:
        df["manual_allowed"] = True
    df["manual_allowed"] = df["manual_allowed"].map(coerce_bool)
    if "notes" not in df.columns:
        df["notes"] = ""
    df["notes"] = df["notes"].fillna("").astype(str)
    return df[df["field_name"] != ""].reset_index(drop=True)


def expand_source_priority(
    priority: pd.DataFrame,
    registry: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Expand pipe-delimited priority_sources into one row per source/rank."""
    if registry is None:
        registry = load_source_registry()

    rows: list[dict[str, Any]] = []
    for _, row in priority.iterrows():
        sources = split_pipe(row.get("priority_sources", ""))
        if not sources:
            continue
        for rank, source in enumerate(sources, start=1):
            canonical = canonical_source_name(source, registry=registry)
            rows.append(
                {
                    "field_name": _normalize_field(row.get("field_name", "")),
                    "methodology_id": str(row.get("methodology_id", "default") or "default").strip(),
                    "source_name": canonical,
                    "priority_rank": rank,
                    "min_confidence": float(row.get("min_confidence", 0.80)),
                    "manual_allowed": coerce_bool(row.get("manual_allowed", True)),
                    "priority_notes": str(row.get("notes", "") or ""),
                }
            )
    return pd.DataFrame(rows)


def required_fields_for_methodology(
    methodology_id: str,
    formula_library_path: str | Path = DEFAULT_FORMULA_LIBRARY_PATH,
    templates_dir: str | Path = DEFAULT_TEMPLATES_DIR,
    thresholds_path: str | Path = DEFAULT_THRESHOLDS_PATH,
) -> list[str]:
    """Return raw fields required by a methodology's template and secondary thresholds."""
    formula_library = load_formula_library(formula_library_path)
    template = load_factor_template(methodology_id, templates_dir=templates_dir)
    formula_ids = set(template["formula_id"].dropna().astype(str).str.strip())

    path = Path(thresholds_path)
    if path.exists():
        thresholds = pd.read_csv(path)
        if {"methodology_id", "secondary_formula_id"}.issubset(thresholds.columns):
            rows = thresholds[thresholds["methodology_id"].astype(str).str.strip().eq(str(methodology_id))]
            formula_ids.update(
                fid
                for fid in rows["secondary_formula_id"].dropna().astype(str).str.strip()
                if fid and fid.lower() != "nan"
            )

    fields: list[str] = []
    rows = formula_library[formula_library["formula_id"].astype(str).str.strip().isin(formula_ids)]
    for _, row in rows.iterrows():
        for field in parse_required_fields(row.get("required_data", "")):
            if field != "manual":
                fields.append(field)
    return sorted(set(fields))


def normalize_source_candidates(
    candidates: pd.DataFrame | Sequence[Mapping[str, Any]] | Mapping[str, Any],
    registry: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Normalize arbitrary candidate rows to the standard candidate schema."""
    if registry is None:
        registry = load_source_registry()

    if isinstance(candidates, pd.DataFrame):
        df = candidates.copy()
    elif isinstance(candidates, Mapping):
        df = pd.DataFrame(
            [
                {"field_name": key, "value": value, "source_name": "Manual"}
                for key, value in candidates.items()
            ]
        )
    else:
        df = pd.DataFrame(list(candidates))

    if df.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)

    for col in CANDIDATE_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df["field_name"] = df["field_name"].fillna("").astype(str).str.strip()
    df["source_name"] = df["source_name"].replace("", "Manual").fillna("Manual").astype(str).str.strip()
    df["canonical_source"] = df["source_name"].map(lambda src: canonical_source_name(src, registry=registry))

    def metadata_value(row: pd.Series, attr: str) -> Any:
        metadata = source_metadata(row.get("canonical_source", ""), registry=registry)
        return getattr(metadata, attr)

    df["source_type"] = df.apply(
        lambda row: row.get("source_type") or metadata_value(row, "source_type"),
        axis=1,
    )
    df["confidence"] = df.apply(
        lambda row: _parse_float(row.get("confidence"), metadata_value(row, "default_confidence")),
        axis=1,
    )
    df["candidate_status"] = df["candidate_status"].replace("", "ready").fillna("ready")
    for col in ["unit", "source_detail", "source_file", "source_table", "source_cell_or_api", "source_label", "notes"]:
        df[col] = df[col].fillna("").astype(str)
    df["value"] = df["value"].map(clean_numeric)
    return df[CANDIDATE_COLUMNS].reset_index(drop=True)


def mapping_report_to_source_candidates(
    match_report: pd.DataFrame,
    uploaded_file: str = "",
    default_source_type: str = "Upload",
) -> pd.DataFrame:
    """Convert mapping_engine match_report rows into source candidates."""
    if match_report is None or match_report.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)

    rows: list[dict[str, Any]] = []
    for _, row in match_report.iterrows():
        rows.append(
            {
                "field_name": row.get("field_name", ""),
                "value": row.get("value"),
                "unit": row.get("unit", ""),
                "source_name": row.get("source_name", ""),
                "source_type": row.get("source_type", default_source_type),
                "source_detail": row.get("match_method", ""),
                "confidence": row.get("confidence"),
                "source_file": row.get("uploaded_file", uploaded_file),
                "source_table": row.get("sheet_name", ""),
                "source_cell_or_api": row.get("matched_column", ""),
                "source_label": row.get("matched_label", ""),
                "candidate_status": row.get("status", ""),
                "notes": row.get("notes", ""),
            }
        )
    return normalize_source_candidates(pd.DataFrame(rows))


def manual_data_to_source_candidates(
    manual_data: Mapping[str, Any],
    source_name: str = "Manual",
    confidence: float = 0.55,
) -> pd.DataFrame:
    """Convert manually entered canonical fields into source candidates."""
    rows = []
    for field, value in manual_data.items():
        if _is_missing_value(value):
            continue
        rows.append(
            {
                "field_name": field,
                "value": value,
                "source_name": source_name,
                "source_type": "Manual",
                "source_detail": "user_input",
                "confidence": confidence,
                "candidate_status": "ready",
                "notes": "Manual user-entered canonical field.",
            }
        )
    return normalize_source_candidates(pd.DataFrame(rows))


def raw_fixture_to_source_candidates(raw_fixture: pd.DataFrame) -> pd.DataFrame:
    """Convert validation raw fixture rows into source candidates."""
    if raw_fixture is None or raw_fixture.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)

    def infer_source_name(row: pd.Series) -> str:
        detail = str(row.get("source_type", "") or "").lower()
        workbook = str(row.get("source_workbook", "") or "")
        if "census" in detail or "census" in workbook.lower():
            return "CensusACS"
        if "bea" in detail or "bea" in workbook.lower():
            return "BEA"
        if "creditscope" in detail or "credit scope" in workbook.lower():
            return "CreditScope"
        if "manual" in detail:
            return "Manual"
        return workbook or "Manual"

    rows: list[dict[str, Any]] = []
    for _, row in raw_fixture.iterrows():
        rows.append(
            {
                "field_name": row.get("field_name", ""),
                "value": row.get("value"),
                "unit": row.get("unit", ""),
                "source_name": infer_source_name(row),
                "source_type": row.get("source_type", ""),
                "source_detail": row.get("source_type", ""),
                "confidence": row.get("confidence", ""),
                "source_file": row.get("source_workbook", ""),
                "source_table": row.get("source_sheet", ""),
                "source_cell_or_api": row.get("source_cell", ""),
                "source_label": row.get("source_label", ""),
                "candidate_status": "ready",
                "notes": row.get("notes", ""),
            }
        )
    return normalize_source_candidates(pd.DataFrame(rows))


def _source_quality_status(candidate: Mapping[str, Any], min_confidence: float) -> str:
    status = str(candidate.get("candidate_status", "") or "").strip().lower()
    detail = str(candidate.get("source_detail", "") or "").strip().lower()
    source_type = str(candidate.get("source_type", "") or "").strip().lower()
    source_name = str(candidate.get("canonical_source", "") or candidate.get("source_name", "")).strip().lower()
    confidence = _parse_float(candidate.get("confidence"), 0.0)

    if status in MISSING_STATUSES or _is_missing_value(candidate.get("value")):
        return "missing"
    if status in SOURCE_PENDING_TYPES or detail in SOURCE_PENDING_TYPES or source_type in SOURCE_PENDING_TYPES or "pending" in status:
        return "source_pending"
    if detail in SCORECARD_IMPLIED_TYPES or source_type in SCORECARD_IMPLIED_TYPES:
        return "scorecard_implied"
    if source_name == "manual":
        return "manual"
    if confidence < min_confidence:
        return "review"
    return "independent_source"


def _readiness_status(source_quality_status: str) -> str:
    return {
        "independent_source": "independent_ready",
        "manual": "manual_input",
        "review": "needs_review",
        "source_pending": "source_pending",
        "scorecard_implied": "scorecard_implied",
        "missing": "missing",
    }.get(source_quality_status, "needs_review")


def _quality_penalty(source_quality_status: str) -> int:
    return {
        "independent_source": 0,
        "review": 25,
        "manual": 50,
        "scorecard_implied": 100,
        "source_pending": 200,
        "missing": 500,
    }.get(source_quality_status, 300)


def _field_priorities(
    expanded_priority: pd.DataFrame,
    methodology_id: Optional[str],
    field_name: str,
) -> pd.DataFrame:
    if expanded_priority.empty:
        return expanded_priority
    field_rows = expanded_priority[expanded_priority["field_name"].astype(str).eq(field_name)].copy()
    if field_rows.empty:
        return field_rows
    methodology = str(methodology_id or "").strip()
    exact = field_rows[field_rows["methodology_id"].astype(str).eq(methodology)] if methodology else pd.DataFrame()
    if not exact.empty:
        return exact
    return field_rows[field_rows["methodology_id"].astype(str).isin({"default", "all", ""})].copy()


def select_issuer_sources(
    candidates: pd.DataFrame | Sequence[Mapping[str, Any]] | Mapping[str, Any],
    methodology_id: Optional[str] = None,
    required_fields: Optional[Iterable[str]] = None,
    source_priority_path: str | Path = DEFAULT_SOURCE_PRIORITY_PATH,
    data_dictionary_path: str | Path = DEFAULT_DATA_DICTIONARY_PATH,
    formula_library_path: str | Path = DEFAULT_FORMULA_LIBRARY_PATH,
) -> pd.DataFrame:
    """Select one source candidate per field according to source priority."""
    registry = load_source_registry()
    candidate_df = normalize_source_candidates(candidates, registry=registry)
    priority = load_source_priority(
        source_priority_path,
        data_dictionary_path=data_dictionary_path,
        formula_library_path=formula_library_path,
    )
    expanded_priority = expand_source_priority(priority, registry=registry)

    fields = set(required_fields or [])
    if not candidate_df.empty:
        fields.update(candidate_df["field_name"].dropna().astype(str).str.strip())
    fields = {field for field in fields if field}

    selected_rows: list[dict[str, Any]] = []
    for field in sorted(fields):
        field_candidates = candidate_df[candidate_df["field_name"].astype(str).eq(field)].copy()
        priorities = _field_priorities(expanded_priority, methodology_id, field)
        min_confidence_default = (
            float(priorities["min_confidence"].min())
            if not priorities.empty and "min_confidence" in priorities.columns
            else 0.80
        )

        if field_candidates.empty:
            selected_rows.append(
                {
                    "field_name": field,
                    "selected": True,
                    "value": None,
                    "unit": "",
                    "source_name": "",
                    "canonical_source": "",
                    "source_type": "",
                    "source_detail": "",
                    "confidence": None,
                    "priority_rank": None,
                    "min_confidence": min_confidence_default,
                    "readiness_status": "missing",
                    "source_quality_status": "missing",
                    "selection_reason": "no_candidate_source",
                    "source_file": "",
                    "source_table": "",
                    "source_cell_or_api": "",
                    "source_label": "",
                    "candidate_status": "missing",
                    "notes": "",
                }
            )
            continue

        priority_lookup = {
            str(row["source_name"]): int(row["priority_rank"])
            for _, row in priorities.iterrows()
        }
        min_lookup = {
            str(row["source_name"]): float(row["min_confidence"])
            for _, row in priorities.iterrows()
        }

        ranked_rows: list[dict[str, Any]] = []
        for _, candidate in field_candidates.iterrows():
            canonical = str(candidate.get("canonical_source", "") or "").strip()
            priority_rank = priority_lookup.get(canonical, 999)
            min_confidence = min_lookup.get(canonical, min_confidence_default)
            quality_status = _source_quality_status(candidate, min_confidence=min_confidence)
            confidence = _parse_float(candidate.get("confidence"), 0.0)
            rank_score = priority_rank + _quality_penalty(quality_status)
            record = candidate.to_dict()
            record.update(
                {
                    "priority_rank": priority_rank if priority_rank != 999 else None,
                    "min_confidence": min_confidence,
                    "source_quality_status": quality_status,
                    "readiness_status": _readiness_status(quality_status),
                    "_rank_score": rank_score,
                    "_confidence_sort": -confidence,
                }
            )
            ranked_rows.append(record)

        ranked = pd.DataFrame(ranked_rows).sort_values(
            ["_rank_score", "_confidence_sort", "source_name"],
            na_position="last",
        )
        winner = ranked.iloc[0].to_dict()
        winner["selected"] = True
        if winner.get("priority_rank") is None:
            winner["selection_reason"] = "selected_without_configured_priority"
        elif winner.get("readiness_status") == "independent_ready":
            winner["selection_reason"] = "selected_by_source_priority"
        else:
            winner["selection_reason"] = f"selected_but_{winner.get('readiness_status')}"
        selected_rows.append(winner)

        for _, loser in ranked.iloc[1:].iterrows():
            row = loser.to_dict()
            row["selected"] = False
            row["selection_reason"] = "lower_priority_or_confidence"
            selected_rows.append(row)

    out = pd.DataFrame(selected_rows)
    if out.empty:
        return pd.DataFrame()
    out = out.drop(columns=[col for col in ["_rank_score", "_confidence_sort"] if col in out.columns])
    preferred = [
        "field_name",
        "selected",
        "readiness_status",
        "source_quality_status",
        "value",
        "unit",
        "source_name",
        "canonical_source",
        "priority_rank",
        "confidence",
        "min_confidence",
        "source_type",
        "source_detail",
        "source_file",
        "source_table",
        "source_cell_or_api",
        "source_label",
        "candidate_status",
        "selection_reason",
        "notes",
    ]
    return out[[col for col in preferred if col in out.columns] + [col for col in out.columns if col not in preferred]]


def issuer_data_from_source_report(source_report: pd.DataFrame) -> Dict[str, Any]:
    """Build issuer_data from selected non-missing source report rows."""
    issuer_data: Dict[str, Any] = {}
    if source_report is None or source_report.empty:
        return issuer_data
    selected = source_report[source_report["selected"].astype(bool)].copy()
    for _, row in selected.iterrows():
        field = _normalize_field(row.get("field_name", ""))
        value = row.get("value")
        if not field or _is_missing_value(value):
            continue
        issuer_data[field] = value
    return issuer_data


def source_readiness_summary(source_report: pd.DataFrame) -> pd.DataFrame:
    """Summarize selected source readiness counts."""
    if source_report is None or source_report.empty:
        return pd.DataFrame(columns=["readiness_status", "field_count"])
    selected = source_report[source_report["selected"].astype(bool)].copy()
    if selected.empty:
        return pd.DataFrame(columns=["readiness_status", "field_count"])
    return (
        selected.groupby("readiness_status", as_index=False)
        .agg(field_count=("field_name", "nunique"))
        .sort_values("readiness_status")
        .reset_index(drop=True)
    )


def run_data_sourcing_pipeline(
    candidate_frames: Sequence[pd.DataFrame] | pd.DataFrame,
    methodology_id: Optional[str] = None,
    required_fields: Optional[Iterable[str]] = None,
    source_priority_path: str | Path = DEFAULT_SOURCE_PRIORITY_PATH,
) -> Dict[str, Any]:
    """
    Select sources and return issuer_data + reports.

    candidate_frames can be a single DataFrame or a sequence of candidate
    DataFrames from mapping reports, APIs, raw fixtures, or manual input.
    """
    if isinstance(candidate_frames, pd.DataFrame):
        candidates = candidate_frames
    else:
        frames = [frame for frame in candidate_frames if frame is not None and not frame.empty]
        candidates = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=CANDIDATE_COLUMNS)

    source_report = select_issuer_sources(
        candidates,
        methodology_id=methodology_id,
        required_fields=required_fields,
        source_priority_path=source_priority_path,
    )
    issuer_data = issuer_data_from_source_report(source_report)
    return {
        "issuer_data": issuer_data,
        "source_candidates": normalize_source_candidates(candidates),
        "source_report": source_report,
        "source_readiness_summary": source_readiness_summary(source_report),
    }
