"""
Source retrieval helpers.

These helpers keep API-specific fetch logic out of Streamlit pages and return
rows that match the raw validation fixture schema.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

import pandas as pd

from connectors.census_api import CensusSourceValue, fetch_county_population


def fetch_census_tax_base_population(
    *,
    state_fips: str,
    county_fips: str,
    year: int,
    api_key: Optional[str] = None,
    timeout: int = 20,
) -> CensusSourceValue:
    """Fetch a Census population source row for tax-base per-capita formulas."""
    return fetch_county_population(
        state_fips=state_fips,
        county_fips=county_fips,
        year=year,
        field_name="tax_base_population",
        api_key=api_key,
        timeout=timeout,
    )


def upsert_raw_input_source_row(
    raw_fixture: pd.DataFrame,
    source_row: Mapping[str, Any],
) -> pd.DataFrame:
    """Update or append a source row in a raw input fixture DataFrame."""
    out = raw_fixture.copy()
    field_name = str(source_row.get("field_name", "")).strip()
    if not field_name:
        return out

    if out.empty:
        base = {}
    else:
        first = out.iloc[0].to_dict()
        base = {
            "test_case": first.get("test_case", ""),
            "methodology_id": first.get("methodology_id", ""),
            "issuer_name": first.get("issuer_name", ""),
        }
    row = {**base, **dict(source_row)}
    for col in out.columns:
        row.setdefault(col, "")
    if field_name in set(out.get("field_name", pd.Series(dtype=str)).astype(str)):
        mask = out["field_name"].astype(str).str.strip().eq(field_name)
        for col, value in row.items():
            if col in out.columns:
                out.loc[mask, col] = value
        return out

    return pd.concat([out, pd.DataFrame([row], columns=out.columns)], ignore_index=True)
