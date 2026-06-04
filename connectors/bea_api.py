"""
BEA API connector.

This connector fetches raw BEA Regional-account values and emits the unified
source-candidate schema used by engine.data_sourcing_engine. It does not import
BEA- or scorecard-calculated ratios; downstream formula code owns all per-capita
and relative-ratio calculations.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, Mapping, Optional, Union
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

from engine.data_sourcing_engine import CANDIDATE_COLUMNS, normalize_source_candidates


BEA_API_BASE_URL = "https://apps.bea.gov/api/data"
BEA_REGIONAL_DATASET = "Regional"
BEA_RESULT_FORMAT = "json"
COUNTY_GDP_TABLE = "CAGDP9"
PERSONAL_INCOME_TABLE = "CAINC1"
ALL_INDUSTRY_LINE_CODE = "1"
PERSONAL_INCOME_LINE_CODE = "1"
POPULATION_LINE_CODE = "2"
US_GEOFIPS = "00000"


class BeaApiError(RuntimeError):
    """Raised when the BEA API cannot return a usable raw value."""


BEA_STANDARD_FIELD_SPECS = {
    "county_gdp": {
        "scope": "county",
        "table_name": COUNTY_GDP_TABLE,
        "line_code": ALL_INDUSTRY_LINE_CODE,
        "unit": "dollars",
        "source_label": "County real GDP, all industries",
        "notes": "BEA Regional CAGDP9 all-industry real GDP. Converted to raw dollars using UNIT_MULT.",
    },
    "county_gdp_current": {
        "scope": "county",
        "table_name": COUNTY_GDP_TABLE,
        "line_code": ALL_INDUSTRY_LINE_CODE,
        "unit": "dollars",
        "source_label": "Current-period county real GDP, all industries",
        "notes": "BEA Regional CAGDP9 all-industry real GDP for the selected year.",
    },
    "county_gdp_prior": {
        "scope": "county_prior",
        "table_name": COUNTY_GDP_TABLE,
        "line_code": ALL_INDUSTRY_LINE_CODE,
        "unit": "dollars",
        "source_label": "Prior-period county real GDP, all industries",
        "notes": "BEA Regional CAGDP9 all-industry real GDP for the prior year.",
    },
    "us_gdp": {
        "scope": "us",
        "table_name": COUNTY_GDP_TABLE,
        "line_code": ALL_INDUSTRY_LINE_CODE,
        "unit": "dollars",
        "source_label": "U.S. real GDP, all industries",
        "notes": "BEA Regional CAGDP9 all-industry U.S. real GDP denominator.",
    },
    "us_gdp_current": {
        "scope": "us",
        "table_name": COUNTY_GDP_TABLE,
        "line_code": ALL_INDUSTRY_LINE_CODE,
        "unit": "dollars",
        "source_label": "Current-period U.S. real GDP, all industries",
        "notes": "BEA Regional CAGDP9 all-industry U.S. real GDP for the selected year.",
    },
    "us_gdp_prior": {
        "scope": "us_prior",
        "table_name": COUNTY_GDP_TABLE,
        "line_code": ALL_INDUSTRY_LINE_CODE,
        "unit": "dollars",
        "source_label": "Prior-period U.S. real GDP, all industries",
        "notes": "BEA Regional CAGDP9 all-industry U.S. real GDP for the prior year.",
    },
    "personal_income": {
        "scope": "county",
        "table_name": PERSONAL_INCOME_TABLE,
        "line_code": PERSONAL_INCOME_LINE_CODE,
        "unit": "dollars",
        "source_label": "County personal income",
        "notes": "BEA Regional CAINC1 personal income. Converted to raw dollars using UNIT_MULT.",
    },
    "us_personal_income": {
        "scope": "us",
        "table_name": PERSONAL_INCOME_TABLE,
        "line_code": PERSONAL_INCOME_LINE_CODE,
        "unit": "dollars",
        "source_label": "U.S. personal income",
        "notes": "BEA Regional CAINC1 U.S. personal income denominator.",
    },
    "population_us": {
        "scope": "us",
        "table_name": PERSONAL_INCOME_TABLE,
        "line_code": POPULATION_LINE_CODE,
        "unit": "count",
        "source_label": "U.S. population",
        "notes": "BEA Regional CAINC1 U.S. population denominator.",
    },
}


def get_bea_api_key(api_key: Optional[str] = None) -> Optional[str]:
    """Resolve the BEA API key from an argument, environment, or Streamlit secrets."""
    if api_key:
        return api_key
    for name in ("BEA_API_KEY", "BEA_KEY", "BEA_USER_ID"):
        value = os.environ.get(name)
        if value:
            return value

    try:
        import streamlit as st  # type: ignore

        for name in ("BEA_API_KEY", "bea_api_key", "BEA_KEY", "BEA_USER_ID"):
            try:
                value = st.secrets.get(name)
            except Exception:
                value = None
            if value:
                return str(value)
    except Exception:
        return None
    return None


def _redact_api_key(url: str) -> str:
    return re.sub(r"([?&]UserID=)[^&]+", r"\1<redacted>", url)


def _request_json(url: str, timeout: int = 20) -> Dict[str, Any]:
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        raise BeaApiError(f"BEA API returned HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise BeaApiError(f"Could not reach BEA API: {exc.reason}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BeaApiError("BEA API returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise BeaApiError("BEA API returned an unexpected response shape.")
    return data


def _parse_bea_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "n/a"}:
        return None
    if text.upper() in {"(D)", "(NA)", "(L)", "(T)", "(X)"}:
        return None
    text = text.replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.replace("(", "").replace(")", "")
    try:
        number = float(text)
    except ValueError as exc:
        raise BeaApiError(f"BEA DataValue is not numeric: {value!r}") from exc
    return -number if negative else number


def _unit_multiplier(row: Mapping[str, Any]) -> float:
    raw = row.get("UNIT_MULT", row.get("UnitMult", 0))
    try:
        return 10.0 ** int(float(raw))
    except (TypeError, ValueError):
        return 1.0


def _response_data_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    bea = payload.get("BEAAPI", {})
    if "Error" in bea:
        error = bea.get("Error", {})
        message = error.get("APIErrorDescription") or error.get("APIErrorCode") or str(error)
        raise BeaApiError(f"BEA API error: {message}")
    results = bea.get("Results", {})
    if "Error" in results:
        error = results.get("Error", {})
        message = error.get("APIErrorDescription") or error.get("APIErrorCode") or str(error)
        raise BeaApiError(f"BEA API error: {message}")
    data = results.get("Data")
    if data is None:
        raise BeaApiError("BEA API returned no Data rows.")
    if isinstance(data, dict):
        return [data]
    if not isinstance(data, list) or not data:
        raise BeaApiError("BEA API returned no Data rows.")
    return [row for row in data if isinstance(row, dict)]


def fetch_regional_table_value(
    *,
    table_name: str,
    line_code: Union[str, int],
    geo_fips: str,
    year: int,
    api_key: Optional[str] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    """
    Fetch a single BEA Regional table value.

    The raw DataValue is multiplied by UNIT_MULT so downstream formulas receive
    full raw dollars/counts rather than display-scaled values.
    """
    key = get_bea_api_key(api_key)
    if not key:
        raise BeaApiError("BEA_API_KEY is not configured.")

    params = {
        "UserID": key,
        "method": "GetData",
        "datasetname": BEA_REGIONAL_DATASET,
        "TableName": str(table_name),
        "LineCode": str(line_code),
        "GeoFips": str(geo_fips).zfill(5),
        "Year": str(int(year)),
        "ResultFormat": BEA_RESULT_FORMAT,
    }
    url = f"{BEA_API_BASE_URL}?{urlencode(params)}"
    payload = _request_json(url, timeout=timeout)
    rows = _response_data_rows(payload)
    row = rows[0].copy()
    value = _parse_bea_number(row.get("DataValue"))
    row["_value"] = value * _unit_multiplier(row) if value is not None else None
    row["_url"] = _redact_api_key(url)
    row["_year"] = int(year)
    row["_table_name"] = str(table_name)
    row["_line_code"] = str(line_code)
    row["_geo_fips"] = str(geo_fips).zfill(5)
    return row


def supported_bea_candidate_fields() -> list[str]:
    """Return fields that this connector can produce from BEA Regional data."""
    return sorted(BEA_STANDARD_FIELD_SPECS)


def _geo_for_scope(scope: str, state_fips: str, county_fips: str) -> str:
    if scope.startswith("us"):
        return US_GEOFIPS
    return f"{str(state_fips).zfill(2)}{str(county_fips).zfill(3)}"


def _year_for_scope(scope: str, year: int, prior_year: Optional[int]) -> int:
    if "prior" in scope:
        return int(prior_year if prior_year is not None else int(year) - 1)
    return int(year)


def _candidate_row(
    *,
    field_name: str,
    spec: Mapping[str, Any],
    row: Mapping[str, Any],
    confidence: float,
) -> dict[str, Any]:
    geo_name = str(row.get("GeoName", "") or row.get("GeoName", ""))
    table_name = str(row.get("_table_name", spec.get("table_name", "")))
    line_code = str(row.get("_line_code", spec.get("line_code", "")))
    year = str(row.get("_year", ""))
    source_cell = f"{BEA_REGIONAL_DATASET}:{table_name}:LineCode:{line_code}:GeoFips:{row.get('_geo_fips', '')}:Year:{year}"
    return {
        "field_name": field_name,
        "value": row.get("_value"),
        "unit": spec.get("unit", ""),
        "source_name": "BEA",
        "source_type": "API",
        "source_detail": "bea_regional",
        "confidence": confidence,
        "source_file": "BEA API",
        "source_table": table_name,
        "source_cell_or_api": source_cell,
        "source_label": f"{geo_name} - {spec.get('source_label', field_name)}".strip(" -"),
        "candidate_status": "ready" if row.get("_value") is not None else "missing_value",
        "notes": spec.get("notes", ""),
    }


def fetch_bea_source_candidates(
    *,
    state_fips: str,
    county_fips: str,
    year: int,
    prior_year: Optional[int] = None,
    fields: Optional[Iterable[str]] = None,
    api_key: Optional[str] = None,
    timeout: int = 20,
    confidence: float = 0.95,
) -> pd.DataFrame:
    """
    Fetch BEA Regional raw values and return standardized source candidates.

    This function returns raw county/U.S. GDP, personal income, and population
    denominator values only. It intentionally does not calculate scorecard
    ratios.
    """
    supported = set(supported_bea_candidate_fields())
    requested = [str(field).strip() for field in (fields or supported) if str(field).strip()]
    requested = [field for field in requested if field in supported]
    if not requested:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)

    rows: list[dict[str, Any]] = []
    for field in requested:
        spec = BEA_STANDARD_FIELD_SPECS[field]
        scope = str(spec["scope"])
        source_year = _year_for_scope(scope, year, prior_year)
        geo_fips = _geo_for_scope(scope, state_fips, county_fips)
        bea_row = fetch_regional_table_value(
            table_name=str(spec["table_name"]),
            line_code=str(spec["line_code"]),
            geo_fips=geo_fips,
            year=source_year,
            api_key=api_key,
            timeout=timeout,
        )
        rows.append(
            _candidate_row(
                field_name=field,
                spec=spec,
                row=bea_row,
                confidence=confidence,
            )
        )

    return normalize_source_candidates(pd.DataFrame(rows))
