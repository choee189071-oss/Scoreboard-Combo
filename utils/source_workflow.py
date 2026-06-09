from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd
import streamlit as st
from openpyxl import load_workbook

from connectors.bea_api import BeaApiError, fetch_bea_source_candidates, supported_bea_candidate_fields
from connectors.census_api import CensusApiError, fetch_census_source_candidates, supported_census_candidate_fields
from connectors.creditscope_loader import load_creditscope_source_candidates
from engine.calculator_engine import load_formula_library, parse_required_fields
from engine.data_sourcing_engine import (
    mapping_report_to_source_candidates,
    manual_data_to_source_candidates,
    required_fields_for_methodology,
    run_data_sourcing_pipeline,
)
from engine.factor_engine import load_factor_template
from engine.mapping_engine import map_uploaded_file
from utils.ui_helpers import clean_for_display, selected_source_report, source_readiness_counts


SOURCE_SESSION_KEYS = {
    "issuer_data",
    "source_report",
    "source_candidates",
    "source_readiness_summary",
    "source_match_reports",
    "uploaded_source_candidates",
    "uploaded_source_reports",
    "uploaded_sources",
    "uploaded_issuer_data",
    "api_source_candidates",
    "api_source_reports",
    "manual_source_candidates",
    "manual_source_values",
    "approved_source_candidates",
    "workbook_direct_metric_debug",
}

SOURCE_WORKFLOW_CACHE_VERSION = "direct-metrics-cache-v3"


def _uploaded_file_payload(uploaded_file: Any) -> tuple[str, bytes]:
    name = str(getattr(uploaded_file, "name", "") or "uploaded_file")
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    if hasattr(uploaded_file, "getvalue"):
        payload = uploaded_file.getvalue()
    else:
        payload = uploaded_file.read()
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    return name, bytes(payload)


def _upload_buffer(file_name: str, payload: bytes) -> io.BytesIO:
    buffer = io.BytesIO(payload)
    buffer.name = file_name
    return buffer


@st.cache_data(show_spinner=False)
def _cached_required_names(methodology_id: str) -> tuple[str, ...]:
    return tuple(required_fields_for_methodology(methodology_id))


@st.cache_data(show_spinner=False)
def _cached_excel_sheet_names(file_name: str, payload: bytes) -> List[str]:
    if Path(file_name).suffix.lower() not in {".xlsx", ".xls"}:
        return []
    workbook = None
    try:
        workbook = load_workbook(_upload_buffer(file_name, payload), read_only=True, data_only=True)
        return list(workbook.sheetnames)
    except Exception:
        return []
    finally:
        if workbook is not None:
            workbook.close()


@st.cache_data(show_spinner=False)
def _cached_creditscope_mapping(
    file_name: str,
    payload: bytes,
    sheet_name: str | None,
    required_fields: tuple[str, ...],
    cache_version: str,
) -> Dict[str, Any]:
    return load_creditscope_source_candidates(
        uploaded_file=_upload_buffer(file_name, payload),
        mapping_path="config/field_mapping.csv",
        row_mapping_path="config/creditscope_row_mapping.csv",
        sheet_name=sheet_name,
        value_col=2,
        required_fields=list(required_fields),
    )


@st.cache_data(show_spinner=False)
def _cached_mapped_upload(
    file_name: str,
    payload: bytes,
    source_name: str,
) -> tuple[Dict[str, Any], pd.DataFrame, pd.DataFrame]:
    source_data, report = map_uploaded_file(
        uploaded_file=_upload_buffer(file_name, payload),
        source_name=source_name,
        mapping_path="config/field_mapping.csv",
    )
    candidates = mapping_report_to_source_candidates(report, uploaded_file=file_name)
    return source_data, report, candidates


@st.cache_data(ttl=86400, show_spinner=False)
def _cached_census_source_candidates(
    state_fips: str,
    county_fips: str,
    year: int,
    fields: tuple[str, ...],
    include_proxy_fields: bool,
) -> pd.DataFrame:
    return fetch_census_source_candidates(
        state_fips=state_fips,
        county_fips=county_fips,
        year=year,
        fields=list(fields),
        include_proxy_fields=include_proxy_fields,
    )


@st.cache_data(ttl=86400, show_spinner=False)
def _cached_bea_source_candidates(
    state_fips: str,
    county_fips: str,
    year: int,
    prior_year: int,
    fields: tuple[str, ...],
) -> pd.DataFrame:
    return fetch_bea_source_candidates(
        state_fips=state_fips,
        county_fips=county_fips,
        year=year,
        prior_year=prior_year,
        fields=list(fields),
    )


def _reset_source_session(methodology_id: str) -> None:
    for key in SOURCE_SESSION_KEYS:
        st.session_state.pop(key, None)
    for key in list(st.session_state.keys()):
        if str(key).startswith(("upload_", "sheet_", "manual_source_editor_", "api_fetch_")):
            st.session_state.pop(key, None)
    st.session_state["uploaded_sources"] = {}
    st.session_state["uploaded_source_candidates"] = {}
    st.session_state["uploaded_source_reports"] = {}
    st.session_state["uploaded_issuer_data"] = {}
    st.session_state["api_source_candidates"] = {}
    st.session_state["api_source_reports"] = {}
    st.session_state["manual_source_values"] = {}
    st.session_state["approved_source_candidates"] = pd.DataFrame()
    st.session_state["source_reset_notice"] = "Source session reset. Upload/fetch sources again before saving issuer_data."
    st.session_state["source_methodology_id"] = methodology_id


def _excel_sheet_names(uploaded_file: Any) -> List[str]:
    name, payload = _uploaded_file_payload(uploaded_file)
    return _cached_excel_sheet_names(name, payload)


def _tokens(value: str) -> set[str]:
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
        "raw",
        "credit",
        "scope",
        "creditscope",
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


def _raw_hint_score(sheet_name: str) -> int:
    lowered = str(sheet_name).lower()
    score = 0
    if any(token in lowered for token in ["fin", "raw", "creditscope", "credit scope"]):
        score += 2
    if any(token in lowered for token in ["scorecard", "public", "summary", "validation"]):
        score -= 3
    return score


def _is_generic_raw_sheet(sheet_name: str) -> bool:
    lowered = re.sub(r"\s+", " ", str(sheet_name).strip().lower())
    return lowered in {"creditscope", "credit scope", "raw", "fin", "issuer data"}


def _auto_sheet(sheet_names: Iterable[str], uploaded_name: str) -> str | None:
    sheet_names = list(sheet_names)
    upload_tokens = _tokens(uploaded_name)
    ranked: list[tuple[int, int, int, str]] = []
    for idx, sheet in enumerate(sheet_names):
        sheet_tokens = _tokens(sheet)
        overlap = len(upload_tokens & sheet_tokens)
        hint = _raw_hint_score(sheet)
        if overlap > 0 and hint >= 0:
            ranked.append((overlap, hint, -idx, sheet))
    if ranked:
        return max(ranked)[3]
    generic_exact = [sheet for sheet in sheet_names if _is_generic_raw_sheet(sheet)]
    if len(generic_exact) == 1:
        return generic_exact[0]
    generic = [sheet for sheet in sheet_names if _raw_hint_score(sheet) > 0 and not _tokens(sheet)]
    return generic[0] if len(generic) == 1 else None


def _build_required_field_frame(methodology_id: str) -> pd.DataFrame:
    formulas = load_formula_library("config/formula_library.csv")
    template = load_factor_template(methodology_id, templates_dir="templates")
    formula_ids = set(template["formula_id"].dropna().astype(str))
    rows: list[dict[str, Any]] = []
    for _, formula in formulas[formulas["formula_id"].astype(str).isin(formula_ids)].iterrows():
        for field in parse_required_fields(formula.get("required_data", "")):
            if field == "manual":
                continue
            rows.append(
                {
                    "field_name": field,
                    "used_by": str(formula.get("formula_id", "")),
                    "category": str(formula.get("category", "")),
                }
            )
    if not rows:
        return pd.DataFrame(columns=["field_name", "used_by", "category"])
    return (
        pd.DataFrame(rows)
        .groupby("field_name", as_index=False)
        .agg(
            used_by=("used_by", lambda x: "; ".join(sorted(set(v for v in x if v)))),
            category=("category", lambda x: "; ".join(sorted(set(v for v in x if v)))),
        )
        .sort_values("field_name")
        .reset_index(drop=True)
    )


@st.cache_data(show_spinner=False)
def _required_field_frame(methodology_id: str) -> pd.DataFrame:
    return _build_required_field_frame(methodology_id)


@st.cache_data(show_spinner=False)
def _cached_methodology_formula_ids(methodology_id: str) -> tuple[str, ...]:
    template = load_factor_template(methodology_id, templates_dir="templates")
    return tuple(sorted(set(template["formula_id"].dropna().astype(str).str.strip()) - {""}))


def _has_source_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    try:
        return not bool(pd.isna(value))
    except (TypeError, ValueError):
        return True


def _workbook_direct_metric_overrides(methodology_id: str) -> Dict[str, Dict[str, Any]]:
    formula_ids = set(_cached_methodology_formula_ids(methodology_id))
    uploads = st.session_state.get("uploaded_issuer_data", {}) or {}
    candidate_uploads = st.session_state.get("uploaded_source_candidates", {}) or {}
    uploaded_sources = st.session_state.get("uploaded_sources", {}) or {}
    overrides: Dict[str, Dict[str, Any]] = {}
    for upload_key, payload in uploads.items():
        if not isinstance(payload, dict):
            continue
        if str(payload.get("source_name", "")).strip() != "CreditScope":
            continue
        workbook_data = payload.get("issuer_data", {}) or {}
        if not isinstance(workbook_data, dict):
            continue
        source_name = str(payload.get("source_name") or upload_key)
        file_name = str(payload.get("file_name") or "").strip()
        source_used = f"{source_name}: {file_name}" if file_name else source_name
        for field, value in workbook_data.items():
            field_name = str(field).strip()
            if field_name not in formula_ids or not _has_source_value(value):
                continue
            overrides[field_name] = {
                "source_used": source_used,
                "workbook_value": value,
            }
        candidates = candidate_uploads.get(upload_key)
        if not isinstance(candidates, pd.DataFrame) or candidates.empty:
            continue
        for _, row in candidates.iterrows():
            field_name = str(row.get("field_name", "") or "").strip()
            value = row.get("value")
            if field_name not in formula_ids or not _has_source_value(value):
                continue
            row_source = str(row.get("source_name") or row.get("canonical_source") or source_used).strip()
            row_detail = str(row.get("source_cell_or_api") or row.get("source_detail") or "").strip()
            overrides[field_name] = {
                "source_used": f"{row_source}: {row_detail}" if row_detail else row_source,
                "workbook_value": value,
            }
    for upload_key, candidates in candidate_uploads.items():
        if upload_key in uploads:
            continue
        if str(upload_key).strip().lower() != "creditscope":
            continue
        if not isinstance(candidates, pd.DataFrame) or candidates.empty:
            continue
        file_name = str(uploaded_sources.get(upload_key, "") or "").strip()
        fallback_source = f"CreditScope: {file_name}" if file_name else "CreditScope"
        for _, row in candidates.iterrows():
            field_name = str(row.get("field_name", "") or "").strip()
            value = row.get("value")
            if field_name not in formula_ids or not _has_source_value(value):
                continue
            row_source = str(row.get("source_name") or row.get("canonical_source") or fallback_source).strip()
            row_detail = str(row.get("source_cell_or_api") or row.get("source_detail") or "").strip()
            overrides[field_name] = {
                "source_used": f"{row_source}: {row_detail}" if row_detail else row_source,
                "workbook_value": value,
            }
    return overrides


def _direct_metric_debug_frame(
    overrides: Dict[str, Dict[str, Any]],
    issuer_data: Dict[str, Any],
) -> pd.DataFrame:
    rows = [
        {
            "field_name": field,
            "source_used": item.get("source_used", "workbook_direct_metric"),
            "workbook_value": item.get("workbook_value"),
            "final_formula_input": issuer_data.get(field),
        }
        for field, item in sorted(overrides.items())
    ]
    return pd.DataFrame(rows, columns=["field_name", "source_used", "workbook_value", "final_formula_input"])


def _clear_formula_rating_outputs() -> None:
    for key in ["formula_results", "methodology_formula_results", "rating_output"]:
        st.session_state.pop(key, None)
    st.session_state["formula_results"] = pd.DataFrame()
    st.session_state["methodology_formula_results"] = pd.DataFrame()
    st.session_state["rating_output"] = None


def _manual_fields(required_fields: pd.DataFrame, source_report: pd.DataFrame | None) -> pd.DataFrame:
    manual_df = required_fields.copy()
    if isinstance(source_report, pd.DataFrame) and not source_report.empty and "field_name" in source_report.columns:
        selected = selected_source_report(source_report)
        reported_fields = set(selected["field_name"].dropna().astype(str))
        required_names = set(manual_df["field_name"].dropna().astype(str))
        gap_fields = required_names - reported_fields
        if "readiness_status" in selected.columns:
            gap_fields.update(
                selected[
                    selected["readiness_status"].astype(str).isin(["missing", "source_pending", "needs_review"])
                ]["field_name"]
                .dropna()
                .astype(str)
            )
        if gap_fields:
            manual_df = manual_df[manual_df["field_name"].astype(str).isin(gap_fields)].copy()
    manual_values = st.session_state.get("manual_source_values", {}) or {}
    manual_df["value"] = manual_df["field_name"].map(lambda field: "" if field not in manual_values else str(manual_values[field]))
    return manual_df[["field_name", "value", "used_by", "category"]]


def _complete_required_field_frame(required_fields: pd.DataFrame, required_names: Iterable[str]) -> pd.DataFrame:
    existing = set(required_fields["field_name"].dropna().astype(str)) if not required_fields.empty else set()
    missing = sorted({str(field) for field in required_names if str(field)} - existing)
    if not missing:
        return required_fields
    additions = pd.DataFrame(
        [{"field_name": field, "used_by": "", "category": ""} for field in missing]
    )
    return (
        pd.concat([required_fields, additions], ignore_index=True)
        .drop_duplicates(subset=["field_name"], keep="first")
        .sort_values("field_name")
        .reset_index(drop=True)
    )


def _clean_manual_values(df: pd.DataFrame) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(df, pd.DataFrame) or df.empty:
        return out
    for _, row in df.iterrows():
        field = str(row.get("field_name", "") or "").strip()
        value = row.get("value")
        if not field or value is None or str(value).strip() == "":
            continue
        try:
            out[field] = float(value)
        except Exception:
            out[field] = str(value).strip()
    return out


def _readiness_tabs(source_report: pd.DataFrame) -> None:
    if not isinstance(source_report, pd.DataFrame) or source_report.empty:
        st.info("No source report saved yet.")
        return
    selected = selected_source_report(source_report)
    counts = source_readiness_counts(source_report)
    st.caption("Raw source readiness is extraction-level status. A raw missing field does not always block rating if a direct metric or approved value already exists.")
    st.dataframe(
        pd.DataFrame([{"readiness_status": key, "field_count": value} for key, value in counts.items()]),
        width="stretch",
        hide_index=True,
    )
    missing = selected[selected["readiness_status"].astype(str).eq("missing")]
    ready = selected[selected["readiness_status"].astype(str).eq("independent_ready")]
    review = selected[selected["readiness_status"].astype(str).isin(["source_pending", "needs_review"])]
    tabs = st.tabs(["Raw Missing", "Raw Ready", "Raw Review", "All Selected"])
    for tab, frame, empty in [
        (tabs[0], missing, "No missing selected fields."),
        (tabs[1], ready, "No independently ready fields yet."),
        (tabs[2], review, "No source-pending or review fields."),
        (tabs[3], selected, "No selected source rows."),
    ]:
        with tab:
            if frame.empty:
                st.info(empty)
            else:
                st.dataframe(clean_for_display(frame), width="stretch", hide_index=True)


def render_source_workflow(methodology_id: str) -> None:
    if st.session_state.get("source_methodology_id") != methodology_id:
        st.session_state["api_source_candidates"] = {}
        st.session_state["api_source_reports"] = {}
        st.session_state["uploaded_source_candidates"] = {}
        st.session_state["uploaded_source_reports"] = {}
        st.session_state["approved_source_candidates"] = pd.DataFrame()
        st.session_state["source_methodology_id"] = methodology_id
    st.session_state.setdefault("uploaded_sources", {})
    st.session_state.setdefault("uploaded_source_candidates", {})
    st.session_state.setdefault("uploaded_source_reports", {})
    st.session_state.setdefault("uploaded_issuer_data", {})
    st.session_state.setdefault("api_source_candidates", {})
    st.session_state.setdefault("api_source_reports", {})
    st.session_state.setdefault("manual_source_values", {})
    st.session_state.setdefault("approved_source_candidates", pd.DataFrame())

    required_names = list(_cached_required_names(methodology_id))
    required_df = _complete_required_field_frame(_required_field_frame(methodology_id), required_names)

    notice = st.session_state.pop("source_reset_notice", None)
    if notice:
        st.success(notice)

    top_cols = st.columns([1, 3])
    with top_cols[0]:
        if st.button("Reset source session"):
            _reset_source_session(methodology_id)
            st.rerun()
    with top_cols[1]:
        st.caption("Reset only when changing issuer, methodology, or source workbook. Saved uploads stay in session until reset.")

    with st.container(border=True):
        st.markdown("**Source uploads**")
        st.caption("Upload source files for mapping or source QA. PDF support is available for ACFR and debt-support review.")
        source_options = [
            {
                "key": "creditscope",
                "source_name": "CreditScope",
                "label": "CreditScope raw workbook",
                "types": ["csv", "xlsx", "xls"],
                "caption": "CSV/XLSX/XLS up to 200MB per file.",
                "multiple": False,
            },
            {
                "key": "ipeds",
                "source_name": "IPEDS_Excel",
                "label": "IPEDS Excel",
                "types": ["csv", "xlsx", "xls"],
                "caption": "CSV/XLSX/XLS up to 200MB per file.",
                "multiple": False,
            },
            {
                "key": "os",
                "source_name": "OS",
                "label": "Official Statement / debt support",
                "types": ["pdf", "csv", "xlsx", "xls"],
                "caption": "Upload one or more PDF/CSV/XLSX/XLS files. PDFs are registered for Source QA.",
                "multiple": True,
            },
            {
                "key": "acfr",
                "source_name": "ACFR",
                "label": "ACFR / Audit extract",
                "types": ["pdf", "csv", "xlsx", "xls"],
                "caption": "Upload one or more PDF/CSV/XLSX/XLS files. PDFs are registered for Source QA.",
                "multiple": True,
            },
        ]
        for row_start in range(0, len(source_options), 2):
            cols = st.columns(2)
            for col, option in zip(cols, source_options[row_start : row_start + 2]):
                key = option["key"]
                source_name = option["source_name"]
                label = option["label"]
                with col:
                    uploaded = st.file_uploader(
                        label,
                        type=option["types"],
                        key=f"upload_{key}",
                        help=option["caption"],
                        accept_multiple_files=bool(option.get("multiple", False)),
                    )
                    st.caption(option["caption"])
                    uploaded_files = uploaded if isinstance(uploaded, list) else ([uploaded] if uploaded is not None else [])
                    if not uploaded_files:
                        continue
                    try:
                        file_names: list[str] = []
                        source_data: dict[str, Any] = {}
                        report_frames: list[pd.DataFrame] = []
                        candidate_frames: list[pd.DataFrame] = []
                        pdf_count = 0

                        for uploaded_file in uploaded_files:
                            file_name, payload = _uploaded_file_payload(uploaded_file)
                            file_names.append(file_name)
                            if Path(file_name).suffix.lower() == ".pdf":
                                pdf_count += 1
                                continue

                            if source_name == "CreditScope":
                                sheet_names = _cached_excel_sheet_names(file_name, payload)
                                selected_sheet = _auto_sheet(sheet_names, file_name)
                                if sheet_names and selected_sheet is None:
                                    st.error(
                                        "No matching CreditScope raw worksheet found. Use this workbook for validation only, "
                                        "or upload a raw workbook whose FIN/raw sheet matches the issuer."
                                    )
                                    st.caption(f"Detected worksheets: {', '.join(sheet_names)}")
                                    continue
                                if selected_sheet:
                                    st.success(f"Auto-selected: {selected_sheet}")
                                loader_output = _cached_creditscope_mapping(
                                    file_name,
                                    payload,
                                    selected_sheet,
                                    tuple(required_names),
                                    SOURCE_WORKFLOW_CACHE_VERSION,
                                )
                                report = loader_output["match_report"]
                                candidates = loader_output["source_candidates"]
                                mapped_data = dict(loader_output.get("issuer_data", {}) or {})
                            else:
                                mapped_data, report, candidates = _cached_mapped_upload(file_name, payload, source_name)

                            source_data.update(mapped_data)
                            if not report.empty and "uploaded_file" not in report.columns:
                                report.insert(0, "uploaded_file", file_name)
                            if isinstance(report, pd.DataFrame) and not report.empty:
                                report_frames.append(report)
                            if isinstance(candidates, pd.DataFrame) and not candidates.empty:
                                candidate_frames.append(candidates)

                        report = pd.concat(report_frames, ignore_index=True) if report_frames else pd.DataFrame()
                        candidates = (
                            pd.concat(candidate_frames, ignore_index=True)
                            if candidate_frames
                            else pd.DataFrame()
                        )
                        file_label = "; ".join(file_names)
                        st.session_state["uploaded_sources"][key] = file_label
                        st.session_state["uploaded_source_candidates"][key] = candidates
                        st.session_state["uploaded_source_reports"][key] = report
                        st.session_state["uploaded_issuer_data"][key] = {
                            "source_name": source_name,
                            "file_name": file_label,
                            "issuer_data": source_data,
                        }
                        if source_data and pdf_count:
                            st.success(f"{len(source_data)} fields mapped; {pdf_count} PDF file(s) registered for Source QA.")
                        elif source_data:
                            st.success(f"{len(source_data)} fields mapped across {len(file_names)} file(s).")
                        elif pdf_count:
                            st.success(f"{pdf_count} PDF file(s) registered for Source QA.")
                        else:
                            st.info("Files uploaded, but no mappable fields were found.")
                    except Exception as exc:
                        st.error("Could not map uploaded file(s).")
                        st.exception(exc)

        upload_reports = [
            frame for frame in st.session_state.get("uploaded_source_reports", {}).values() if isinstance(frame, pd.DataFrame) and not frame.empty
        ]
        if upload_reports:
            with st.expander("Upload diagnostics", expanded=False):
                st.caption("This shows what each upload could and could not map. It is not the final source readiness list.")
                st.dataframe(clean_for_display(pd.concat(upload_reports, ignore_index=True)), width="stretch", hide_index=True)

    with st.container(border=True):
        st.markdown("**API candidates**")
        st.caption("Census and BEA fields are always fetched as a full supported set to avoid slow select/unselect loops.")
        c1, c2 = st.columns(2)
        with c1:
            st.write("Census ACS")
            census_cols = st.columns(3)
            census_year = census_cols[0].number_input("ACS year", min_value=2009, max_value=2026, value=2024, step=1)
            state_fips = census_cols[1].text_input("State FIPS", value="06", max_chars=2)
            county_fips = census_cols[2].text_input("County FIPS", value="013", max_chars=3)
            include_proxy = st.checkbox("Include proxy fields", value=False)
            census_fields = supported_census_candidate_fields(include_proxy_fields=include_proxy)
            st.caption(f"{len(census_fields)} Census fields selected automatically.")
            if st.button("Fetch Census", key="api_fetch_census"):
                try:
                    census = _cached_census_source_candidates(
                        str(state_fips),
                        str(county_fips),
                        int(census_year),
                        tuple(census_fields),
                        bool(include_proxy),
                    )
                    st.session_state["api_source_candidates"]["census"] = census
                    st.session_state["api_source_reports"]["census"] = census
                    st.success(f"Fetched {len(census)} Census candidate fields.")
                except CensusApiError as exc:
                    st.error("Could not fetch Census ACS data.")
                    st.exception(exc)
        with c2:
            st.write("BEA Regional")
            bea_cols = st.columns(4)
            bea_year = bea_cols[0].number_input("BEA year", min_value=2001, max_value=2026, value=2024, step=1)
            bea_prior = bea_cols[1].number_input("BEA prior", min_value=2000, max_value=2025, value=2023, step=1)
            bea_state = bea_cols[2].text_input("BEA State", value=state_fips or "06", max_chars=2)
            bea_county = bea_cols[3].text_input("BEA County", value=county_fips or "013", max_chars=3)
            bea_fields = supported_bea_candidate_fields()
            st.caption(f"{len(bea_fields)} BEA fields selected automatically.")
            if st.button("Fetch BEA", key="api_fetch_bea"):
                try:
                    bea = _cached_bea_source_candidates(
                        str(bea_state),
                        str(bea_county),
                        int(bea_year),
                        int(bea_prior),
                        tuple(bea_fields),
                    )
                    st.session_state["api_source_candidates"]["bea"] = bea
                    st.session_state["api_source_reports"]["bea"] = bea
                    st.success(f"Fetched {len(bea)} BEA candidate fields.")
                except BeaApiError as exc:
                    st.error("Could not fetch BEA data.")
                    st.exception(exc)
        if st.button("Fetch Census + BEA", key="api_fetch_all", type="primary"):
            fetched: list[str] = []
            try:
                census = _cached_census_source_candidates(
                    str(state_fips),
                    str(county_fips),
                    int(census_year),
                    tuple(census_fields),
                    bool(include_proxy),
                )
                st.session_state["api_source_candidates"]["census"] = census
                st.session_state["api_source_reports"]["census"] = census
                fetched.append(f"{len(census)} Census")
            except CensusApiError as exc:
                st.error("Could not fetch Census ACS data.")
                st.exception(exc)
            try:
                bea = _cached_bea_source_candidates(
                    str(bea_state),
                    str(bea_county),
                    int(bea_year),
                    int(bea_prior),
                    tuple(bea_fields),
                )
                st.session_state["api_source_candidates"]["bea"] = bea
                st.session_state["api_source_reports"]["bea"] = bea
                fetched.append(f"{len(bea)} BEA")
            except BeaApiError as exc:
                st.error("Could not fetch BEA data.")
                st.exception(exc)
            if fetched:
                st.success(f"Fetched {' and '.join(fetched)} candidate fields.")

    with st.container(border=True):
        st.markdown("**Manual / source-pending inputs**")
        st.caption("Only typed values are saved. Blank cells do not overwrite uploaded or API source data.")
        manual_base = _manual_fields(required_df, st.session_state.get("source_report"))
        with st.form(f"manual_source_form_{methodology_id}"):
            if manual_base.empty:
                st.info("No manual/source-pending fields currently need input.")
                edited_manual = manual_base
            else:
                edited_manual = st.data_editor(
                    manual_base,
                    width="stretch",
                    hide_index=True,
                    num_rows="fixed",
                    key=f"manual_source_editor_{methodology_id}",
                    column_config={
                        "field_name": st.column_config.TextColumn("field_name", disabled=True),
                        "value": st.column_config.TextColumn("value"),
                        "used_by": st.column_config.TextColumn("used_by", disabled=True),
                        "category": st.column_config.TextColumn("category", disabled=True),
                    },
                )
            save_sources = st.form_submit_button("Save issuer_data", type="primary")

        if save_sources:
            manual_values = dict(st.session_state.get("manual_source_values", {}) or {})
            manual_values.update(_clean_manual_values(edited_manual))
            st.session_state["manual_source_values"] = manual_values
            frames: list[pd.DataFrame] = []
            frames.extend(
                frame
                for frame in st.session_state.get("uploaded_source_candidates", {}).values()
                if isinstance(frame, pd.DataFrame) and not frame.empty
            )
            frames.extend(
                frame
                for frame in st.session_state.get("api_source_candidates", {}).values()
                if isinstance(frame, pd.DataFrame) and not frame.empty
            )
            manual_candidates = manual_data_to_source_candidates(manual_values)
            if not manual_candidates.empty:
                frames.append(manual_candidates)
                st.session_state["manual_source_candidates"] = manual_candidates
            approved_candidates = st.session_state.get("approved_source_candidates")
            if isinstance(approved_candidates, pd.DataFrame) and not approved_candidates.empty:
                frames.append(approved_candidates)
            if not frames:
                st.warning("No source candidates are available yet.")
            else:
                result = run_data_sourcing_pipeline(
                    frames,
                    methodology_id=methodology_id,
                    required_fields=required_names,
                )
                issuer_data = dict(result["issuer_data"])
                direct_metric_overrides = _workbook_direct_metric_overrides(methodology_id)
                for field, item in direct_metric_overrides.items():
                    issuer_data[field] = item.get("workbook_value")
                direct_metric_debug = _direct_metric_debug_frame(direct_metric_overrides, issuer_data)
                st.session_state["issuer_data"] = issuer_data
                st.session_state["source_report"] = result["source_report"]
                st.session_state["source_candidates"] = result["source_candidates"]
                st.session_state["source_readiness_summary"] = result["source_readiness_summary"]
                st.session_state["workbook_direct_metric_debug"] = direct_metric_debug
                _clear_formula_rating_outputs()
                reports = []
                reports.extend(st.session_state.get("uploaded_source_reports", {}).values())
                reports.extend(st.session_state.get("api_source_reports", {}).values())
                if reports:
                    st.session_state["source_match_reports"] = pd.concat(
                        [r for r in reports if isinstance(r, pd.DataFrame) and not r.empty],
                        ignore_index=True,
                    )
                st.success(f"Saved issuer_data with {len(issuer_data)} selected fields.")
                if not direct_metric_debug.empty:
                    with st.expander("Workbook direct metric debug", expanded=True):
                        st.dataframe(clean_for_display(direct_metric_debug), width="stretch", hide_index=True)

    with st.container(border=True):
        st.markdown("**Raw source readiness**")
        _readiness_tabs(st.session_state.get("source_report", pd.DataFrame()))
