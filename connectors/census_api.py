"""
Census API connector.

The connector returns raw source fields only. It does not import Census- or
CreditScope-calculated ratios; downstream formula code owns those calculations.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


CENSUS_API_BASE_URL = "https://api.census.gov/data"
DEFAULT_ACS5_DATASET = "acs/acs5"
ACS5_VARIABLES = {
    "population": "B01003_001E",
    "median_household_income": "B19013_001E",
    "median_family_income": "B19113_001E",
    "poverty_population": "B17001_002E",
    "poverty_universe": "B17001_001E",
}


class CensusApiError(RuntimeError):
    """Raised when the Census API cannot return a usable raw value."""


@dataclass(frozen=True)
class CensusSourceValue:
    field_name: str
    value: float
    unit: str
    source_name: str
    source_type: str
    source_cell: str
    source_label: str
    notes: str
    source_payload: Dict[str, Any]

    def to_raw_input_row(self) -> Dict[str, Any]:
        return {
            "field_name": self.field_name,
            "value": self.value,
            "unit": self.unit,
            "source_workbook": "Census API",
            "source_sheet": self.source_payload.get("dataset", ""),
            "source_cell": self.source_cell,
            "source_label": self.source_label,
            "source_type": self.source_type,
            "notes": self.notes,
        }


def get_census_api_key(api_key: Optional[str] = None) -> Optional[str]:
    """Resolve the Census API key from an argument, environment, or Streamlit secrets."""
    if api_key:
        return api_key
    for name in ("CENSUS_API_KEY", "CENSUS_KEY"):
        value = os.environ.get(name)
        if value:
            return value

    try:
        import streamlit as st  # type: ignore

        for name in ("CENSUS_API_KEY", "census_api_key", "CENSUS_KEY"):
            try:
                value = st.secrets.get(name)
            except Exception:
                value = None
            if value:
                return str(value)
    except Exception:
        return None
    return None


def _request_json(url: str, timeout: int = 20) -> list[list[str]]:
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        raise CensusApiError(f"Census API returned HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise CensusApiError(f"Could not reach Census API: {exc.reason}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CensusApiError("Census API returned invalid JSON.") from exc
    if not isinstance(data, list) or len(data) < 2:
        raise CensusApiError("Census API returned no data rows.")
    return data


def fetch_acs5_county_fields(
    *,
    state_fips: str,
    county_fips: str,
    fields: Iterable[str],
    year: int,
    api_key: Optional[str] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    """
    Fetch raw ACS 5-year fields for a county.

    `fields` may contain canonical names from ACS5_VARIABLES or raw Census
    variable IDs. The returned dict includes NAME, state, county, and each
    requested variable.
    """
    variables = []
    for field in fields:
        field = str(field).strip()
        if not field:
            continue
        variables.append(ACS5_VARIABLES.get(field, field))
    if not variables:
        raise CensusApiError("At least one Census field must be requested.")

    params = {
        "get": ",".join(["NAME", *variables]),
        "for": f"county:{county_fips.zfill(3)}",
        "in": f"state:{state_fips.zfill(2)}",
    }
    key = get_census_api_key(api_key)
    if key:
        params["key"] = key

    dataset = DEFAULT_ACS5_DATASET
    url = f"{CENSUS_API_BASE_URL}/{int(year)}/{dataset}?{urlencode(params)}"
    data = _request_json(url, timeout=timeout)
    header, values = data[0], data[1]
    row = dict(zip(header, values))
    row["_url"] = url
    row["_dataset"] = dataset
    row["_year"] = int(year)
    return row


def fetch_county_population(
    *,
    state_fips: str,
    county_fips: str,
    year: int,
    field_name: str = "population",
    api_key: Optional[str] = None,
    timeout: int = 20,
) -> CensusSourceValue:
    """Fetch ACS 5-year county population as a raw source value."""
    variable = ACS5_VARIABLES["population"]
    row = fetch_acs5_county_fields(
        state_fips=state_fips,
        county_fips=county_fips,
        fields=[variable],
        year=year,
        api_key=api_key,
        timeout=timeout,
    )
    raw_value = row.get(variable)
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise CensusApiError(f"Census population value is not numeric: {raw_value!r}") from exc

    geography = row.get("NAME", f"state:{state_fips} county:{county_fips}")
    dataset = row.get("_dataset", DEFAULT_ACS5_DATASET)
    return CensusSourceValue(
        field_name=field_name,
        value=value,
        unit="count",
        source_name="CensusACS",
        source_type="census_api",
        source_cell=f"{row.get('_year')}/{dataset}:{variable}:state:{state_fips.zfill(2)}:county:{county_fips.zfill(3)}",
        source_label=f"{geography} total population",
        notes=(
            "ACS 5-year county population. Use as a tax-base population proxy "
            "only when the methodology geography matches or the analyst accepts the county proxy."
        ),
        source_payload={
            "dataset": dataset,
            "year": row.get("_year"),
            "variable": variable,
            "geography_name": geography,
            "state_fips": state_fips.zfill(2),
            "county_fips": county_fips.zfill(3),
            "url": row.get("_url"),
        },
    )
