"""
Mapping Engine for CreditScope / IPEDS / OS / ACFR uploads.

Purpose
-------
Convert uploaded source files with human-facing column names into canonical
issuer_data fields used by the formula engine.

Example
-------
CreditScope CSV columns:
    Population, Full Value, Net Direct Debt

field_mapping.csv:
    field_name,source_name,possible_column_names,match_type,notes
    population,CreditScope,"Population|Resident Population",fuzzy,...

Output:
    issuer_data = {
        "population": 530000,
        "full_value": 40000000000,
        "net_direct_debt": 700000000,
    }
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
import re

import pandas as pd


DEFAULT_MAPPING_PATH = Path("config/field_mapping.csv")


@dataclass
class FieldMatch:
    """One canonical field mapped from one uploaded column."""

    field_name: str
    source_name: str
    matched_column: Optional[str]
    matched_label: Optional[str]
    match_method: str  # exact_alias, normalized_alias, fuzzy_alias, not_found
    confidence: float
    value: Any = None
    notes: str = ""

    @property
    def status(self) -> str:
        if self.matched_column is None:
            return "missing"
        if self.confidence >= 0.97:
            return "ready"
        if self.confidence >= 0.78:
            return "review"
        return "low_confidence"


def normalize_label(value: Any) -> str:
    """Normalize labels for resilient matching."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_aliases(value: Any) -> List[str]:
    """Split pipe-delimited aliases from field_mapping.csv."""
    if pd.isna(value):
        return []
    return [x.strip() for x in str(value).split("|") if x.strip()]


def load_mapping_table(mapping_path: Union[str, Path] = DEFAULT_MAPPING_PATH) -> pd.DataFrame:
    """Load and validate config/field_mapping.csv."""
    path = Path(mapping_path)
    if not path.exists():
        raise FileNotFoundError(f"Mapping file not found: {path}")

    mapping = pd.read_csv(path)
    required = {"field_name", "source_name", "possible_column_names"}
    missing = required - set(mapping.columns)
    if missing:
        raise ValueError(f"field_mapping.csv missing required columns: {sorted(missing)}")

    mapping = mapping.copy()
    mapping["field_name"] = mapping["field_name"].astype(str).str.strip()
    mapping["source_name"] = mapping["source_name"].astype(str).str.strip()
    mapping["possible_column_names"] = mapping["possible_column_names"].fillna("")
    if "match_type" not in mapping.columns:
        mapping["match_type"] = "fuzzy"
    if "notes" not in mapping.columns:
        mapping["notes"] = ""
    return mapping


def read_uploaded_file(uploaded_file: Any) -> pd.DataFrame:
    """
    Read csv/xlsx from either a Streamlit UploadedFile or a local path.
    """
    if isinstance(uploaded_file, (str, Path)):
        filename = str(uploaded_file)
        suffix = Path(filename).suffix.lower()
    else:
        filename = getattr(uploaded_file, "name", "")
        suffix = Path(filename).suffix.lower()

    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(uploaded_file)
    if suffix == ".csv":
        return pd.read_csv(uploaded_file)
    raise ValueError(f"Unsupported file type: {suffix or filename}. Use CSV or Excel.")


def _best_column_match(
    aliases: Iterable[str],
    columns: Iterable[str],
    fuzzy_threshold: float = 0.82,
) -> Tuple[Optional[str], Optional[str], str, float]:
    """
    Find best uploaded column for a list of aliases.

    Returns:
        matched_column, matched_alias, match_method, confidence
    """
    columns = list(columns)
    aliases = list(aliases)

    # 1. Exact raw string match
    for alias in aliases:
        for col in columns:
            if str(col).strip() == alias.strip():
                return col, alias, "exact_alias", 1.0

    # 2. Normalized exact match
    normalized_cols = {normalize_label(col): col for col in columns}
    for alias in aliases:
        norm_alias = normalize_label(alias)
        if norm_alias in normalized_cols:
            return normalized_cols[norm_alias], alias, "normalized_alias", 0.97

    # 3. Fuzzy match against normalized strings
    best: Tuple[Optional[str], Optional[str], float] = (None, None, 0.0)
    for alias in aliases:
        norm_alias = normalize_label(alias)
        if not norm_alias:
            continue
        for col in columns:
            score = SequenceMatcher(None, norm_alias, normalize_label(col)).ratio()
            if score > best[2]:
                best = (col, alias, score)

    if best[0] is not None and best[2] >= fuzzy_threshold:
        return best[0], best[1], "fuzzy_alias", round(best[2], 3)

    return None, None, "not_found", 0.0


def clean_numeric_value(value: Any) -> Any:
    """
    Convert common financial strings to numbers when safe.

    Handles:
        "$1,234" -> 1234
        "12.5%" -> 0.125
        "(1,234)" -> -1234
        "N/A" -> None
    Leaves non-numeric strings unchanged.
    """
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return value

    raw = str(value).strip()
    if raw == "" or raw.lower() in {"na", "n/a", "none", "null", "--", "-"}:
        return None

    is_percent = raw.endswith("%")
    negative = raw.startswith("(") and raw.endswith(")")

    text = raw.replace("$", "").replace(",", "").replace("%", "")
    text = text.replace("(", "").replace(")", "").strip()

    try:
        number = float(text)
        if negative:
            number = -number
        if is_percent:
            number = number / 100.0
        return number
    except ValueError:
        return raw


def extract_value_from_column(df: pd.DataFrame, column: str, row_index: int = 0) -> Any:
    """
    Extract one value from a matched column.

    MVP assumption: one issuer / one period per uploaded file, so the first
    non-empty value is usually the target value. If that fails, fall back to
    the requested row_index.
    """
    series = df[column]
    non_empty = series.dropna()
    if len(non_empty) > 0:
        return clean_numeric_value(non_empty.iloc[row_index if row_index < len(non_empty) else 0])
    return None


def map_uploaded_dataframe(
    df: pd.DataFrame,
    source_name: str,
    mapping: Union[pd.DataFrame, str, Path] = DEFAULT_MAPPING_PATH,
    fuzzy_threshold: float = 0.82,
    row_index: int = 0,
) -> Tuple[Dict[str, Any], pd.DataFrame]:
    """
    Map uploaded dataframe columns to canonical issuer_data.

    Args:
        df: uploaded CreditScope / IPEDS / ACFR / OS dataframe.
        source_name: source label, e.g. "CreditScope", "IPEDS", "ACFR".
        mapping: mapping dataframe or path to field_mapping.csv.
        fuzzy_threshold: minimum fuzzy score accepted.
        row_index: row index used when multiple non-empty rows exist.

    Returns:
        issuer_data: dict keyed by canonical field_name.
        match_report: dataframe with status, matched column, confidence, value.
    """
    if not isinstance(mapping, pd.DataFrame):
        mapping_df = load_mapping_table(mapping)
    else:
        mapping_df = mapping.copy()

    source_norm = normalize_label(source_name)
    source_rows = mapping_df[mapping_df["source_name"].map(normalize_label) == source_norm]

    # Allow generic/common mapping rows if you later add source_name = Any/Common.
    generic_rows = mapping_df[mapping_df["source_name"].map(normalize_label).isin({"any", "common", "all"})]
    source_rows = pd.concat([source_rows, generic_rows], ignore_index=True).drop_duplicates(
        subset=["field_name", "possible_column_names"], keep="first"
    )

    issuer_data: Dict[str, Any] = {}
    matches: List[FieldMatch] = []

    for _, row in source_rows.iterrows():
        field_name = str(row["field_name"]).strip()
        aliases = split_aliases(row.get("possible_column_names", ""))
        notes = "" if pd.isna(row.get("notes", "")) else str(row.get("notes", ""))

        matched_col, matched_alias, method, confidence = _best_column_match(
            aliases=aliases,
            columns=df.columns,
            fuzzy_threshold=fuzzy_threshold,
        )

        value = None
        if matched_col is not None:
            value = extract_value_from_column(df, matched_col, row_index=row_index)
            issuer_data[field_name] = value

        matches.append(
            FieldMatch(
                field_name=field_name,
                source_name=source_name,
                matched_column=matched_col,
                matched_label=matched_alias,
                match_method=method,
                confidence=confidence,
                value=value,
                notes=notes,
            )
        )

    match_report = pd.DataFrame([asdict(m) | {"status": m.status} for m in matches])
    if not match_report.empty:
        match_report = match_report[
            [
                "field_name",
                "source_name",
                "status",
                "matched_column",
                "matched_label",
                "match_method",
                "confidence",
                "value",
                "notes",
            ]
        ]
    return issuer_data, match_report


def map_uploaded_file(
    uploaded_file: Any,
    source_name: str,
    mapping_path: Union[str, Path] = DEFAULT_MAPPING_PATH,
    fuzzy_threshold: float = 0.82,
    row_index: int = 0,
) -> Tuple[Dict[str, Any], pd.DataFrame]:
    """
    Convenience wrapper for Streamlit uploaders.
    """
    df = read_uploaded_file(uploaded_file)
    return map_uploaded_dataframe(
        df=df,
        source_name=source_name,
        mapping=mapping_path,
        fuzzy_threshold=fuzzy_threshold,
        row_index=row_index,
    )


def merge_issuer_data(
    *issuer_data_dicts: Dict[str, Any],
    overwrite: bool = False,
) -> Dict[str, Any]:
    """
    Merge mapped outputs from multiple sources.

    By default, earlier sources win. This is useful when you want source priority:
        CreditScope first, then IPEDS, then ACFR, then manual.
    Set overwrite=True if later sources should replace earlier values.
    """
    merged: Dict[str, Any] = {}
    for data in issuer_data_dicts:
        for key, value in data.items():
            if overwrite or key not in merged or merged[key] is None:
                merged[key] = value
    return merged


if __name__ == "__main__":
    # Tiny smoke test. Run from project root:
    # python engine/mapping_engine.py
    sample = pd.DataFrame(
        {
            "Population": [530000],
            "Full Value": ["$40,000,000,000"],
            "Net Direct Debt": ["700,000,000"],
        }
    )
    data, report = map_uploaded_dataframe(sample, source_name="CreditScope")
    print(data)
    print(report.head(10).to_string(index=False))
