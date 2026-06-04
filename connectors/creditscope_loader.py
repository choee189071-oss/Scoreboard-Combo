"""
CreditScope source loader.

This connector wraps the row/column mapping logic in engine.mapping_engine and
emits the unified source-candidate schema used by engine.data_sourcing_engine.
It only returns raw source fields; calculated CreditScope ratios remain out of
the formal data layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional, Union

import pandas as pd

from engine.data_sourcing_engine import (
    CANDIDATE_COLUMNS,
    mapping_report_to_source_candidates,
    normalize_source_candidates,
)
from engine.mapping_engine import map_creditscope_workbook, map_uploaded_file


def _uploaded_name(uploaded_file: Any) -> str:
    if isinstance(uploaded_file, (str, Path)):
        return Path(uploaded_file).name
    return str(getattr(uploaded_file, "name", "") or "")


def _is_excel(uploaded_file: Any) -> bool:
    return Path(_uploaded_name(uploaded_file)).suffix.lower() in {".xlsx", ".xls"}


def _seek_start(uploaded_file: Any) -> None:
    if not isinstance(uploaded_file, (str, Path)) and hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)


def load_creditscope_source_candidates(
    uploaded_file: Any,
    *,
    mapping_path: Union[str, Path] = "config/field_mapping.csv",
    row_mapping_path: Union[str, Path] = "config/creditscope_row_mapping.csv",
    sheet_name: Optional[str] = None,
    value_col: int = 2,
    required_fields: Optional[Iterable[str]] = None,
    fuzzy_threshold: float = 0.92,
) -> dict[str, Any]:
    """
    Load a CreditScope CSV/XLSX file as standardized source candidates.

    Returns a dict with:
      - issuer_data: mapped field/value dict
      - match_report: human-readable mapping report
      - source_candidates: normalized candidate rows
    """
    uploaded_name = _uploaded_name(uploaded_file)

    if _is_excel(uploaded_file):
        _seek_start(uploaded_file)
        issuer_data, match_report = map_creditscope_workbook(
            uploaded_file=uploaded_file,
            mapping_path=mapping_path,
            row_mapping_path=row_mapping_path,
            sheet_name=sheet_name,
            value_col=value_col,
            fuzzy_threshold=fuzzy_threshold,
        )
        if not issuer_data:
            _seek_start(uploaded_file)
            issuer_data, match_report = map_uploaded_file(
                uploaded_file=uploaded_file,
                source_name="CreditScope",
                mapping_path=mapping_path,
                fuzzy_threshold=0.82,
            )
    else:
        _seek_start(uploaded_file)
        issuer_data, match_report = map_uploaded_file(
            uploaded_file=uploaded_file,
            source_name="CreditScope",
            mapping_path=mapping_path,
            fuzzy_threshold=0.82,
        )

    report = match_report.copy() if match_report is not None else pd.DataFrame()
    if not report.empty:
        if "uploaded_file" not in report.columns:
            report.insert(0, "uploaded_file", uploaded_name)
        else:
            report["uploaded_file"] = report["uploaded_file"].replace("", uploaded_name).fillna(uploaded_name)
        if "source_type" not in report.columns:
            report["source_type"] = "Upload"

    candidates = mapping_report_to_source_candidates(report, uploaded_file=uploaded_name)
    if required_fields is not None and not candidates.empty:
        required = {str(field).strip() for field in required_fields if str(field).strip()}
        candidates = candidates[candidates["field_name"].isin(required)].reset_index(drop=True)

    return {
        "issuer_data": issuer_data,
        "match_report": report,
        "source_candidates": normalize_source_candidates(candidates)
        if not candidates.empty
        else pd.DataFrame(columns=CANDIDATE_COLUMNS),
    }
