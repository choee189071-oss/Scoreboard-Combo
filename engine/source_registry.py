"""
Source registry helpers.

The registry normalizes source names such as Census/ACS/CensusACS to one
canonical source and provides metadata used by the data sourcing engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
import re

import pandas as pd


DEFAULT_SOURCE_REGISTRY_PATH = Path("config/source_registry.csv")


def normalize_source_key(value: Any) -> str:
    """Normalize source names and aliases for matching."""
    text = str(value or "").strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_pipe(value: Any) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    return [part.strip() for part in str(value).replace(";", "|").split("|") if part.strip()]


def coerce_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


@dataclass(frozen=True)
class SourceMetadata:
    source_name: str
    source_type: str
    access_method: str
    structured: bool
    requires_upload: bool
    requires_api_key: bool
    default_confidence: float
    preferred_for: str = ""
    notes: str = ""


def load_source_registry(
    path: str | Path = DEFAULT_SOURCE_REGISTRY_PATH,
) -> pd.DataFrame:
    """Load config/source_registry.csv and normalize optional columns."""
    registry_path = Path(path)
    if not registry_path.exists():
        raise FileNotFoundError(f"Source registry not found: {registry_path}")

    df = pd.read_csv(registry_path)
    required = {"source_name", "source_type", "access_method"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"source_registry.csv missing required columns: {sorted(missing)}")

    df = df.copy()
    for col in ["source_name", "source_type", "access_method", "preferred_for", "notes", "aliases"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip()
    for col in ["structured", "requires_upload", "requires_api_key"]:
        if col not in df.columns:
            df[col] = False
        df[col] = df[col].map(coerce_bool)
    if "default_confidence" not in df.columns:
        df["default_confidence"] = None
    df["default_confidence"] = pd.to_numeric(df["default_confidence"], errors="coerce")
    defaults = {
        "api": 0.95,
        "upload": 0.88,
        "document": 0.72,
        "manual": 0.55,
    }
    df["default_confidence"] = df.apply(
        lambda row: (
            float(row["default_confidence"])
            if pd.notna(row["default_confidence"])
            else defaults.get(normalize_source_key(row["source_type"]), 0.70)
        ),
        axis=1,
    )
    return df


def source_alias_map(registry: pd.DataFrame) -> Dict[str, str]:
    """Build normalized alias -> canonical source mapping."""
    aliases: Dict[str, str] = {}
    for _, row in registry.iterrows():
        canonical = str(row.get("source_name", "")).strip()
        if not canonical:
            continue
        for alias in [canonical, *split_pipe(row.get("aliases", ""))]:
            key = normalize_source_key(alias)
            if key:
                aliases[key] = canonical
    return aliases


def canonical_source_name(
    source_name: Any,
    registry: Optional[pd.DataFrame] = None,
    registry_path: str | Path = DEFAULT_SOURCE_REGISTRY_PATH,
) -> str:
    """Return the canonical source name for a label or alias."""
    raw = str(source_name or "").strip()
    if not raw:
        return ""
    if registry is None:
        registry = load_source_registry(registry_path)
    aliases = source_alias_map(registry)
    return aliases.get(normalize_source_key(raw), raw)


def source_metadata(
    source_name: Any,
    registry: Optional[pd.DataFrame] = None,
    registry_path: str | Path = DEFAULT_SOURCE_REGISTRY_PATH,
) -> SourceMetadata:
    """Return registry metadata for a source, with a conservative fallback."""
    if registry is None:
        registry = load_source_registry(registry_path)
    canonical = canonical_source_name(source_name, registry=registry)
    rows = registry[registry["source_name"].astype(str).str.strip().eq(canonical)]
    if rows.empty:
        return SourceMetadata(
            source_name=canonical,
            source_type="Unknown",
            access_method="unknown",
            structured=False,
            requires_upload=False,
            requires_api_key=False,
            default_confidence=0.50,
        )
    row = rows.iloc[0]
    return SourceMetadata(
        source_name=canonical,
        source_type=str(row.get("source_type", "")).strip(),
        access_method=str(row.get("access_method", "")).strip(),
        structured=coerce_bool(row.get("structured", False)),
        requires_upload=coerce_bool(row.get("requires_upload", False)),
        requires_api_key=coerce_bool(row.get("requires_api_key", False)),
        default_confidence=float(row.get("default_confidence", 0.50)),
        preferred_for=str(row.get("preferred_for", "") or ""),
        notes=str(row.get("notes", "") or ""),
    )


def known_sources(
    registry: Optional[pd.DataFrame] = None,
    registry_path: str | Path = DEFAULT_SOURCE_REGISTRY_PATH,
) -> Iterable[str]:
    if registry is None:
        registry = load_source_registry(registry_path)
    return registry["source_name"].dropna().astype(str).str.strip().tolist()
