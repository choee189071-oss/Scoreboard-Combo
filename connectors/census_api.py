"""
Census API connector.

The connector returns raw source fields only. It does not import Census- or
CreditScope-calculated ratios; downstream formula code owns those calculations.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

from engine.data_sourcing_engine import CANDIDATE_COLUMNS, normalize_source_candidates


CENSUS_API_BASE_URL = "https://api.census.gov/data"
DEFAULT_ACS5_DATASET = "acs/acs5"
ACS5_VARIABLES = {
    "population": "B01003_001E",
    "median_household_income": "B19013_001E",
    "median_family_income": "B19113_001E",
    "poverty_population": "B17001_002E",
    "poverty_universe": "B17001_001E",
}
CENSUS_STANDARD_FIELD_SPECS = {
    "population": {
        "scope": "county",
        "variables": ["population"],
        "unit": "count",
        "source_label": "Total population",
        "notes": "ACS 5-year county population.",
    },
    "tax_base_population": {
        "scope": "county",
        "variables": ["population"],
        "unit": "count",
        "source_label": "Tax-base population denominator",
        "notes": (
            "ACS 5-year county population used as a tax-base population proxy. "
            "Confirm geography before using for districts that do not match the county."
        ),
    },
    "service_area_population": {
        "scope": "county",
        "variables": ["population"],
        "unit": "count",
        "source_label": "Service-area population proxy",
        "notes": (
            "ACS 5-year county population used only as a service-area proxy. "
            "Prefer explicit utility/district service-area population when available."
        ),
    },
    "population_current": {
        "scope": "county",
        "variables": ["population"],
        "unit": "count",
        "source_label": "Current-period population",
        "notes": "ACS 5-year county population for the selected year.",
    },
    "population_prior": {
        "scope": "county_prior",
        "variables": ["population"],
        "unit": "count",
        "source_label": "Prior-period population",
        "notes": "ACS 5-year county population for the prior ACS release year.",
    },
    "population_us": {
        "scope": "us",
        "variables": ["population"],
        "unit": "count",
        "source_label": "U.S. total population",
        "notes": "ACS 5-year U.S. population denominator.",
    },
    "issuer_mfi": {
        "scope": "county",
        "variables": ["median_family_income"],
        "unit": "dollars",
        "source_label": "Issuer-area median family income",
        "notes": "ACS 5-year county median family income.",
    },
    "us_mfi": {
        "scope": "us",
        "variables": ["median_family_income"],
        "unit": "dollars",
        "source_label": "U.S. median family income",
        "notes": "ACS 5-year U.S. median family income.",
    },
    "us_median_income": {
        "scope": "us",
        "variables": ["median_household_income"],
        "unit": "dollars",
        "source_label": "U.S. median household income",
        "notes": "ACS 5-year U.S. median household income.",
    },
    "median_household_ebi": {
        "scope": "county",
        "variables": ["median_household_income"],
        "unit": "dollars",
        "source_label": "County median household income",
        "notes": (
            "ACS 5-year county median household income. This is a raw median-income "
            "denominator, not a CreditScope- or agency-calculated affordability ratio."
        ),
    },
    "poverty_rate": {
        "scope": "county",
        "variables": ["poverty_population", "poverty_universe"],
        "unit": "decimal",
        "source_label": "County poverty rate",
        "notes": "Computed from raw ACS 5-year poverty population divided by poverty universe.",
        "computed": "poverty_population/poverty_universe",
    },
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


def _redact_api_key(url: str) -> str:
    return re.sub(r"([?&]key=)[^&]+", r"\1<redacted>", url)


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
    if not key:
        raise CensusApiError("CENSUS_API_KEY is not configured.")
    params["key"] = key

    dataset = DEFAULT_ACS5_DATASET
    url = f"{CENSUS_API_BASE_URL}/{int(year)}/{dataset}?{urlencode(params)}"
    data = _request_json(url, timeout=timeout)
    header, values = data[0], data[1]
    row = dict(zip(header, values))
    row["_url"] = _redact_api_key(url)
    row["_dataset"] = dataset
    row["_year"] = int(year)
    return row


def fetch_acs5_us_fields(
    *,
    fields: Iterable[str],
    year: int,
    api_key: Optional[str] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    """Fetch raw ACS 5-year fields for the United States."""
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
        "for": "us:*",
    }
    key = get_census_api_key(api_key)
    if not key:
        raise CensusApiError("CENSUS_API_KEY is not configured.")
    params["key"] = key

    dataset = DEFAULT_ACS5_DATASET
    url = f"{CENSUS_API_BASE_URL}/{int(year)}/{dataset}?{urlencode(params)}"
    data = _request_json(url, timeout=timeout)
    header, values = data[0], data[1]
    row = dict(zip(header, values))
    row["_url"] = _redact_api_key(url)
    row["_dataset"] = dataset
    row["_year"] = int(year)
    return row


def _numeric(row: Mapping[str, Any], variable: str) -> float:
    raw_value = row.get(variable)
    try:
        return float(raw_value)
    except (TypeError, ValueError) as exc:
        raise CensusApiError(f"Census value for {variable} is not numeric: {raw_value!r}") from exc


def _variable_ids(variable_names: Iterable[str]) -> list[str]:
    return [ACS5_VARIABLES.get(name, name) for name in variable_names]


def _source_cell(row: Mapping[str, Any], variables: Iterable[str], scope: str, state_fips: str, county_fips: str) -> str:
    variable_text = "+".join(_variable_ids(variables))
    dataset = row.get("_dataset", DEFAULT_ACS5_DATASET)
    year = row.get("_year", "")
    if scope == "us":
        return f"{year}/{dataset}:{variable_text}:us:*"
    return f"{year}/{dataset}:{variable_text}:state:{state_fips.zfill(2)}:county:{county_fips.zfill(3)}"


def _candidate_row(
    *,
    field_name: str,
    spec: Mapping[str, Any],
    row: Mapping[str, Any],
    scope: str,
    state_fips: str,
    county_fips: str,
    confidence: float,
) -> Dict[str, Any]:
    variable_names = list(spec["variables"])
    variable_ids = _variable_ids(variable_names)
    if spec.get("computed") == "poverty_population/poverty_universe":
        numerator = _numeric(row, ACS5_VARIABLES["poverty_population"])
        denominator = _numeric(row, ACS5_VARIABLES["poverty_universe"])
        value = numerator / denominator if denominator else None
    else:
        value = _numeric(row, variable_ids[0])

    geography = row.get("NAME", "United States" if scope == "us" else f"state:{state_fips} county:{county_fips}")
    source_detail = "acs5_us" if scope == "us" else "acs5_county"
    return {
        "field_name": field_name,
        "value": value,
        "unit": spec.get("unit", ""),
        "source_name": "CensusACS",
        "source_type": "API",
        "source_detail": source_detail,
        "confidence": confidence,
        "source_file": "Census API",
        "source_table": str(row.get("_dataset", DEFAULT_ACS5_DATASET)),
        "source_cell_or_api": _source_cell(row, variable_names, scope, state_fips, county_fips),
        "source_label": f"{geography} - {spec.get('source_label', field_name)}",
        "candidate_status": "ready" if value is not None else "missing_value",
        "notes": spec.get("notes", ""),
    }


def supported_census_candidate_fields(include_proxy_fields: bool = False) -> list[str]:
    """
    Return fields that this connector can produce from raw ACS data.

    Proxy-only fields are intentionally excluded by default. Add them only when
    the analyst explicitly accepts the proxy in the UI or caller.
    """
    fields = sorted(CENSUS_STANDARD_FIELD_SPECS)
    if include_proxy_fields:
        return fields
    proxy_fields = {"service_area_population", "median_household_ebi"}
    return [field for field in fields if field not in proxy_fields]


def fetch_census_source_candidates(
    *,
    state_fips: str,
    county_fips: str,
    year: int,
    fields: Optional[Iterable[str]] = None,
    api_key: Optional[str] = None,
    timeout: int = 20,
    confidence: float = 0.95,
    include_proxy_fields: bool = False,
) -> pd.DataFrame:
    """
    Fetch Census ACS raw values and return standardized source candidates.

    This function returns raw ACS values only. Downstream formula code remains
    responsible for all ratio/per-capita calculations.
    """
    supported = set(supported_census_candidate_fields(include_proxy_fields=include_proxy_fields))
    requested = [str(field).strip() for field in (fields or supported) if str(field).strip()]
    requested = [field for field in requested if field in supported]
    if not requested:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)

    county_fields = set()
    us_fields = set()
    prior_fields = set()
    for field in requested:
        spec = CENSUS_STANDARD_FIELD_SPECS[field]
        scope = str(spec["scope"])
        if scope == "county":
            county_fields.update(spec["variables"])
        elif scope == "county_prior":
            prior_fields.update(spec["variables"])
        elif scope == "us":
            us_fields.update(spec["variables"])

    county_row: Dict[str, Any] = {}
    prior_row: Dict[str, Any] = {}
    us_row: Dict[str, Any] = {}
    if county_fields:
        county_row = fetch_acs5_county_fields(
            state_fips=state_fips,
            county_fips=county_fips,
            fields=county_fields,
            year=year,
            api_key=api_key,
            timeout=timeout,
        )
    if prior_fields:
        prior_row = fetch_acs5_county_fields(
            state_fips=state_fips,
            county_fips=county_fips,
            fields=prior_fields,
            year=int(year) - 1,
            api_key=api_key,
            timeout=timeout,
        )
    if us_fields:
        us_row = fetch_acs5_us_fields(
            fields=us_fields,
            year=year,
            api_key=api_key,
            timeout=timeout,
        )

    rows: list[dict[str, Any]] = []
    for field in requested:
        spec = CENSUS_STANDARD_FIELD_SPECS[field]
        scope = str(spec["scope"])
        if scope == "county":
            source_row = county_row
            source_scope = "county"
        elif scope == "county_prior":
            source_row = prior_row
            source_scope = "county"
        else:
            source_row = us_row
            source_scope = "us"
        rows.append(
            _candidate_row(
                field_name=field,
                spec=spec,
                row=source_row,
                scope=source_scope,
                state_fips=state_fips,
                county_fips=county_fips,
                confidence=confidence,
            )
        )

    return normalize_source_candidates(pd.DataFrame(rows))


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
