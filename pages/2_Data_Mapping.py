from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st
from openpyxl import load_workbook
from utils.ui_helpers import page_header, current_context_card, init_state

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from engine.calculator_engine import load_formula_library, parse_required_fields
    from engine.factor_engine import load_factor_template
    from engine.mapping_engine import map_uploaded_file
    from engine.data_sourcing_engine import (
        mapping_report_to_source_candidates,
        manual_data_to_source_candidates,
        run_data_sourcing_pipeline,
    )
    from connectors.census_api import (
        CensusApiError,
        fetch_census_source_candidates,
        supported_census_candidate_fields,
    )
    from connectors.bea_api import (
        BeaApiError,
        fetch_bea_source_candidates,
        supported_bea_candidate_fields,
    )
    from connectors.creditscope_loader import load_creditscope_source_candidates
except Exception as exc:  # pragma: no cover - Streamlit display path
    st.set_page_config(page_title="Data Mapping", page_icon="②", layout="wide")
    st.error("Could not import mapping/calculator engines.")
    st.exception(exc)
    st.stop()


st.set_page_config(page_title="Data Mapping", page_icon="②", layout="wide")
init_state()
page_header(
    "② Data Mapping",
    "Upload source files or enter canonical raw fields. This page builds the issuer_data dictionary used by Calculators.",
    "data_mapping",
)
current_context_card()

methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")
st.session_state.setdefault("api_source_candidates", {})
st.session_state.setdefault("api_source_reports", {})
if st.session_state.get("api_source_methodology_id") != methodology_id:
    st.session_state["api_source_candidates"] = {}
    st.session_state["api_source_reports"] = {}
    st.session_state["api_source_methodology_id"] = methodology_id


SOURCE_SESSION_KEYS = {
    "issuer_data",
    "source_report",
    "source_candidates",
    "source_readiness_summary",
    "source_match_reports",
    "uploaded_sources",
    "api_source_candidates",
    "api_source_reports",
}
SOURCE_WIDGET_PREFIXES = (
    "upload_",
    "sheet_",
    "manual_source_editor_",
)


def _reset_source_session() -> None:
    """Clear source-selection state and source-input widgets for a clean rerun."""
    for key in SOURCE_SESSION_KEYS:
        st.session_state.pop(key, None)
    for key in list(st.session_state.keys()):
        if any(str(key).startswith(prefix) for prefix in SOURCE_WIDGET_PREFIXES):
            st.session_state.pop(key, None)
    st.session_state["api_source_candidates"] = {}
    st.session_state["api_source_reports"] = {}
    st.session_state["api_source_methodology_id"] = methodology_id
    st.session_state["source_reset_notice"] = "Source session reset. Upload/select sources again before saving issuer_data."


def _default_value_for_field(field_name: str) -> Any:
    defaults = {
        "population": 100000.0,
        "population_us": 330000000.0,
        "full_value": 12000000000.0,
        "assessed_value": 10000000000.0,
        "assessed_value_prior": 9500000000.0,
        "operating_revenue": 100000000.0,
        "operating_expense": 98000000.0,
        "governmental_revenue": 100000000.0,
        "governmental_expense": 98000000.0,
        "general_fund_balance": 25000000.0,
        "available_fund_balance": 25000000.0,
        "cash_balance": 30000000.0,
        "cash_and_investments": 30000000.0,
        "unrestricted_reserves": 30000000.0,
        "net_direct_debt": 20000000.0,
        "direct_debt": 20000000.0,
        "total_go_debt": 20000000.0,
        "net_pension_liability": 35000000.0,
        "mads": 6000000.0,
        "mads_requirement": 6000000.0,
        "issuer_mfi": 120000.0,
        "us_mfi": 100000.0,
        "mhi_adjusted_rpp": 120000.0,
        "us_median_income": 80000.0,
        "county_ebi": 120.0,
        "us_ebi": 100.0,
        "county_gdp_current": 105.0,
        "county_gdp_prior": 100.0,
        "us_gdp_current": 103.0,
        "us_gdp_prior": 100.0,
    }
    return defaults.get(field_name, None)


def _required_fields_for_methodology(methodology_id: str) -> pd.DataFrame:
    formulas = load_formula_library("config/formula_library.csv")
    template = load_factor_template(methodology_id, templates_dir="templates")
    formula_ids = set(template["formula_id"].dropna().astype(str))
    try:
        thresholds = pd.read_csv("config/scoring_thresholds.csv")
        related_thresholds = thresholds[thresholds["methodology_id"].astype(str) == str(methodology_id)]
        secondary_ids = set(related_thresholds["secondary_formula_id"].dropna().astype(str))
        secondary_ids.discard("")
        secondary_ids.discard("nan")
        formula_ids |= secondary_ids
    except Exception:
        pass
    rows: List[Dict[str, Any]] = []
    for _, formula in formulas[formulas["formula_id"].astype(str).isin(formula_ids)].iterrows():
        for field in parse_required_fields(formula.get("required_data", "")):
            if field == "manual":
                continue
            rows.append(
                {
                    "field_name": field,
                    "formula_id": formula["formula_id"],
                    "formula_name": formula["formula_name"],
                    "category": formula.get("category", ""),
                }
            )
    if not rows:
        return pd.DataFrame(columns=["field_name", "used_by", "category"])
    df = pd.DataFrame(rows)
    return (
        df.groupby("field_name", as_index=False)
        .agg(
            used_by=("formula_id", lambda x: "; ".join(sorted(set(map(str, x))))),
            category=("category", lambda x: "; ".join(sorted(set(str(v) for v in x if str(v))))),
        )
        .sort_values("field_name")
        .reset_index(drop=True)
    )


def _clean_edited_values(df: pd.DataFrame) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for _, row in df.iterrows():
        key = str(row.get("field_name", "")).strip()
        raw_value = row.get("value")
        if not key or raw_value in [None, ""]:
            continue
        try:
            data[key] = float(raw_value)
        except Exception:
            data[key] = raw_value
    return data


def _is_excel_upload(uploaded_file: Any) -> bool:
    name = str(getattr(uploaded_file, "name", "") or uploaded_file)
    return Path(name).suffix.lower() in {".xlsx", ".xls"}


def _excel_sheet_names(uploaded_file: Any) -> List[str]:
    if not _is_excel_upload(uploaded_file):
        return []
    workbook = None
    try:
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
        workbook = load_workbook(uploaded_file, read_only=True, data_only=True)
        return list(workbook.sheetnames)
    except Exception:
        return []
    finally:
        if workbook is not None:
            workbook.close()
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)


def _name_tokens(value: str) -> set[str]:
    ignored = {
        "scorecard",
        "moodys",
        "moody",
        "higher",
        "local",
        "gov",
        "water",
        "sewer",
        "utility",
        "xlsx",
        "xls",
        "2023",
        "2024",
        "2025",
        "2026",
        "fin",
        "ccd",
        "go",
        "sp",
        "k12",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value).lower())
        if len(token) > 2 and token not in ignored
    }


def _sheet_match_score(sheet_name: str, uploaded_name: str) -> int:
    upload_tokens = _name_tokens(uploaded_name)
    if not upload_tokens:
        return 0
    sheet_tokens = _name_tokens(sheet_name)
    return len(upload_tokens & sheet_tokens)


def _preferred_sheet_index(sheet_names: List[str], uploaded_name: str) -> int:
    if not sheet_names:
        return 0
    scored = [
        (
            _sheet_match_score(sheet, uploaded_name),
            0 if "backup" not in sheet.lower() else -1,
            -idx,
        )
        for idx, sheet in enumerate(sheet_names)
    ]
    best_idx = -max(scored)[2]
    return max(0, min(best_idx, len(sheet_names) - 1))


try:
    required_fields = _required_fields_for_methodology(methodology_id)
except Exception as exc:
    st.warning(f"Could not load required fields for {methodology_id}: {exc}")
    required_fields = pd.DataFrame(columns=["field_name", "used_by", "category"])

required_field_names = required_fields["field_name"].dropna().astype(str).str.strip().tolist()


reset_notice = st.session_state.pop("source_reset_notice", None)
if reset_notice:
    st.success(reset_notice)

reset_col, reset_note_col = st.columns([1, 4])
with reset_col:
    if st.button("Reset source session"):
        _reset_source_session()
        st.rerun()
with reset_note_col:
    st.caption("Use reset before switching workbook, worksheet, issuer, or methodology source inputs.")

st.divider()
st.subheader("Mapped Source Files")
st.caption("CSV/XLSX uploads are mapped through engine.mapping_engine. PDF parsing is intentionally left for the next parser layer.")

source_options = {
    "creditscope": ("CreditScope", "CreditScope CSV/XLSX"),
    "ipeds": ("IPEDS_Excel", "IPEDS Excel"),
    "os": ("OS", "Official Statement table extract"),
    "acfr": ("ACFR", "ACFR / Audit table extract"),
}

mapped_candidate_frames: List[pd.DataFrame] = []
match_reports: List[pd.DataFrame] = []
cols = st.columns(4)
for i, (key, (source_name, label)) in enumerate(source_options.items()):
    with cols[i]:
        uploaded = st.file_uploader(label, type=["csv", "xlsx", "xls"], key=f"upload_{key}")
        if uploaded is None:
            continue
        selected_sheet_name = None
        if source_name == "CreditScope" and _is_excel_upload(uploaded):
            sheet_names = _excel_sheet_names(uploaded)
            if sheet_names:
                default_sheet_idx = _preferred_sheet_index(sheet_names, uploaded.name)
                selected_sheet_name = st.selectbox(
                    "CreditScope worksheet",
                    options=sheet_names,
                    index=default_sheet_idx,
                    key=f"sheet_{key}_{uploaded.name}",
                )
                if selected_sheet_name and _sheet_match_score(selected_sheet_name, uploaded.name) == 0:
                    st.warning("Selected worksheet name does not appear to match the uploaded file name.")
        try:
            if source_name == "CreditScope":
                loader_output = load_creditscope_source_candidates(
                    uploaded_file=uploaded,
                    mapping_path="config/field_mapping.csv",
                    row_mapping_path="config/creditscope_row_mapping.csv",
                    sheet_name=selected_sheet_name,
                    value_col=2,
                    required_fields=required_field_names,
                )
                source_data = loader_output["issuer_data"]
                report = loader_output["match_report"]
                source_candidates = loader_output["source_candidates"]
            else:
                source_data, report = map_uploaded_file(
                    uploaded_file=uploaded,
                    source_name=source_name,
                    mapping_path="config/field_mapping.csv",
                )
                source_candidates = mapping_report_to_source_candidates(report, uploaded_file=uploaded.name)
            st.session_state["uploaded_sources"][key] = uploaded.name
            st.success(f"{len(source_data)} fields mapped")
            if not report.empty:
                if "uploaded_file" not in report.columns:
                    report.insert(0, "uploaded_file", uploaded.name)
                match_reports.append(report)
                mapped_candidate_frames.append(source_candidates)
        except Exception as exc:
            st.error(f"Could not map {uploaded.name}.")
            st.exception(exc)

if match_reports:
    with st.expander("Source mapping reports", expanded=True):
        st.dataframe(pd.concat(match_reports, ignore_index=True), use_container_width=True, hide_index=True)

st.divider()
st.subheader("API Source Candidates")
st.caption("API loaders fetch raw source values only. Formula calculations remain in calculator_engine.")

census_default_fields = [field for field in required_field_names if field in supported_census_candidate_fields()]
with st.expander("Census ACS", expanded=False):
    geo_cols = st.columns(4)
    with geo_cols[0]:
        census_year = st.number_input("ACS year", min_value=2009, max_value=2026, value=2024, step=1)
    with geo_cols[1]:
        state_fips = st.text_input("State FIPS", value="06", max_chars=2)
    with geo_cols[2]:
        county_fips = st.text_input("County FIPS", value="013", max_chars=3)
    with geo_cols[3]:
        include_proxy_fields = st.checkbox("include proxy fields", value=False)

    selectable_census_fields = supported_census_candidate_fields(include_proxy_fields=include_proxy_fields)
    selected_census_fields = st.multiselect(
        "Census fields",
        options=selectable_census_fields,
        default=[field for field in census_default_fields if field in selectable_census_fields],
    )
    if st.button("Fetch Census candidates"):
        try:
            census_candidates = fetch_census_source_candidates(
                state_fips=state_fips,
                county_fips=county_fips,
                year=int(census_year),
                fields=selected_census_fields,
                include_proxy_fields=include_proxy_fields,
            )
            st.session_state["api_source_candidates"]["census"] = census_candidates
            st.session_state["api_source_reports"]["census"] = census_candidates
            st.success(f"Fetched {len(census_candidates)} Census candidate fields")
        except CensusApiError as exc:
            st.error("Could not fetch Census ACS data.")
            st.exception(exc)

bea_default_fields = [field for field in required_field_names if field in supported_bea_candidate_fields()]
with st.expander("BEA Regional", expanded=False):
    bea_geo_cols = st.columns(4)
    with bea_geo_cols[0]:
        bea_year = st.number_input("BEA year", min_value=2001, max_value=2026, value=2024, step=1)
    with bea_geo_cols[1]:
        bea_prior_year = st.number_input("BEA prior year", min_value=2000, max_value=2025, value=2023, step=1)
    with bea_geo_cols[2]:
        bea_state_fips = st.text_input("BEA State FIPS", value=state_fips or "06", max_chars=2)
    with bea_geo_cols[3]:
        bea_county_fips = st.text_input("BEA County FIPS", value=county_fips or "013", max_chars=3)

    selected_bea_fields = st.multiselect(
        "BEA fields",
        options=supported_bea_candidate_fields(),
        default=bea_default_fields,
    )
    if st.button("Fetch BEA candidates"):
        try:
            bea_candidates = fetch_bea_source_candidates(
                state_fips=bea_state_fips,
                county_fips=bea_county_fips,
                year=int(bea_year),
                prior_year=int(bea_prior_year),
                fields=selected_bea_fields,
            )
            st.session_state["api_source_candidates"]["bea"] = bea_candidates
            st.session_state["api_source_reports"]["bea"] = bea_candidates
            st.success(f"Fetched {len(bea_candidates)} BEA candidate fields")
        except BeaApiError as exc:
            st.error("Could not fetch BEA Regional data.")
            st.exception(exc)

api_candidate_frames = [
    frame
    for frame in st.session_state.get("api_source_candidates", {}).values()
    if isinstance(frame, pd.DataFrame) and not frame.empty
]
if api_candidate_frames:
    st.dataframe(pd.concat(api_candidate_frames, ignore_index=True), use_container_width=True, hide_index=True)

st.divider()
st.subheader("Manual Canonical Fields")
st.caption(
    "Manual values are blank by default so stale source data does not re-enter the model. "
    "Only type values here for truly manual/source-pending fields."
)

prefill_manual = st.checkbox(
    "Pre-fill manual values from current issuer_data",
    value=False,
    help="Use only when you intentionally want the currently saved issuer_data to become editable manual input.",
)
existing = (st.session_state.get("issuer_data", {}) or {}) if prefill_manual else {}
rows = []
for _, row in required_fields.iterrows():
    field_name = str(row["field_name"])
    rows.append(
        {
            "field_name": field_name,
            "value": existing.get(field_name, ""),
            "used_by": row.get("used_by", ""),
            "category": row.get("category", ""),
        }
    )

manual_df = pd.DataFrame(rows)
if not manual_df.empty:
    manual_df["value"] = manual_df["value"].apply(lambda x: "" if x is None else str(x))
if manual_df.empty:
    st.info("No formula-driven raw fields found for the selected methodology.")
    edited = manual_df
else:
    edited = st.data_editor(
        manual_df,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key=f"manual_source_editor_{methodology_id}",
        column_config={
            "value": st.column_config.TextColumn("value"),
            "used_by": st.column_config.TextColumn("used_by", disabled=True),
            "category": st.column_config.TextColumn("category", disabled=True),
        },
    )

if st.button("Save issuer_data", type="primary"):
    manual_data = _clean_edited_values(edited)
    manual_candidates = manual_data_to_source_candidates(manual_data)
    sourcing_output = run_data_sourcing_pipeline(
        [*mapped_candidate_frames, *api_candidate_frames, manual_candidates],
        methodology_id=methodology_id,
        required_fields=required_field_names,
        source_priority_path="config/source_priority.csv",
    )
    st.session_state["issuer_data"] = sourcing_output["issuer_data"]
    st.session_state["source_report"] = sourcing_output["source_report"]
    st.session_state["source_candidates"] = sourcing_output["source_candidates"]
    st.session_state["source_readiness_summary"] = sourcing_output["source_readiness_summary"]
    st.session_state["source_match_reports"] = pd.concat(match_reports, ignore_index=True) if match_reports else pd.DataFrame()
    st.success(f"Saved {len(sourcing_output['issuer_data'])} canonical fields. Go to Calculators next.")

with st.expander("Source readiness summary", expanded=True):
    readiness = st.session_state.get("source_readiness_summary", pd.DataFrame())
    source_report = st.session_state.get("source_report", pd.DataFrame())
    if isinstance(readiness, pd.DataFrame) and not readiness.empty:
        st.dataframe(readiness, use_container_width=True, hide_index=True)
    else:
        st.info("No source selection has been saved yet.")
    if isinstance(source_report, pd.DataFrame) and not source_report.empty:
        st.dataframe(source_report, use_container_width=True, hide_index=True)

with st.expander("Current issuer_data", expanded=False):
    data = st.session_state.get("issuer_data", {}) or {}
    if data:
        st.json(data)
    else:
        st.info("No issuer_data saved yet.")
