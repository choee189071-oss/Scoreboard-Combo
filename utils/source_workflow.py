from __future__ import annotations

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
    "api_source_candidates",
    "api_source_reports",
    "manual_source_candidates",
    "manual_source_values",
}


def _reset_source_session(methodology_id: str) -> None:
    for key in SOURCE_SESSION_KEYS:
        st.session_state.pop(key, None)
    for key in list(st.session_state.keys()):
        if str(key).startswith(("upload_", "sheet_", "manual_source_editor_", "api_fetch_")):
            st.session_state.pop(key, None)
    st.session_state["uploaded_sources"] = {}
    st.session_state["uploaded_source_candidates"] = {}
    st.session_state["uploaded_source_reports"] = {}
    st.session_state["api_source_candidates"] = {}
    st.session_state["api_source_reports"] = {}
    st.session_state["manual_source_values"] = {}
    st.session_state["source_reset_notice"] = "Source session reset. Upload/fetch sources again before saving issuer_data."
    st.session_state["source_methodology_id"] = methodology_id


def _excel_sheet_names(uploaded_file: Any) -> List[str]:
    name = str(getattr(uploaded_file, "name", "") or "")
    if Path(name).suffix.lower() not in {".xlsx", ".xls"}:
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
    if any(token in lowered for token in ["fin", "raw", "creditscope", "credit scope", "financial"]):
        score += 2
    if any(token in lowered for token in ["scorecard", "public", "summary", "validation"]):
        score -= 3
    return score


def _auto_sheet(sheet_names: Iterable[str], uploaded_name: str) -> str | None:
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
    generic = [sheet for sheet in sheet_names if _raw_hint_score(sheet) > 0 and not _tokens(sheet)]
    return generic[0] if len(generic) == 1 else None


def _required_field_frame(methodology_id: str) -> pd.DataFrame:
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


def _manual_fields(required_fields: pd.DataFrame, source_report: pd.DataFrame | None) -> pd.DataFrame:
    manual_df = required_fields.copy()
    if isinstance(source_report, pd.DataFrame) and not source_report.empty and "field_name" in source_report.columns:
        selected = selected_source_report(source_report)
        if "readiness_status" in selected.columns:
            gap_fields = set(
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
    st.dataframe(
        pd.DataFrame([{"readiness_status": key, "field_count": value} for key, value in counts.items()]),
        width="stretch",
        hide_index=True,
    )
    missing = selected[selected["readiness_status"].astype(str).eq("missing")]
    ready = selected[selected["readiness_status"].astype(str).eq("independent_ready")]
    review = selected[selected["readiness_status"].astype(str).isin(["source_pending", "needs_review"])]
    tabs = st.tabs(["Missing", "Ready", "Review", "All Selected"])
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
        st.session_state["source_methodology_id"] = methodology_id
    st.session_state.setdefault("uploaded_sources", {})
    st.session_state.setdefault("uploaded_source_candidates", {})
    st.session_state.setdefault("uploaded_source_reports", {})
    st.session_state.setdefault("api_source_candidates", {})
    st.session_state.setdefault("api_source_reports", {})
    st.session_state.setdefault("manual_source_values", {})

    required_df = _required_field_frame(methodology_id)
    required_names = required_fields_for_methodology(methodology_id)

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
        cols = st.columns(4)
        source_options = {
            "creditscope": ("CreditScope", "CreditScope raw workbook"),
            "ipeds": ("IPEDS_Excel", "IPEDS Excel"),
            "os": ("OS", "Official Statement extract"),
            "acfr": ("ACFR", "ACFR / Audit extract"),
        }
        for i, (key, (source_name, label)) in enumerate(source_options.items()):
            with cols[i]:
                uploaded = st.file_uploader(label, type=["csv", "xlsx", "xls"], key=f"upload_{key}")
                if uploaded is None:
                    continue
                try:
                    if source_name == "CreditScope":
                        sheet_names = _excel_sheet_names(uploaded)
                        selected_sheet = _auto_sheet(sheet_names, uploaded.name)
                        if sheet_names and selected_sheet is None:
                            st.error(
                                "No matching CreditScope raw worksheet found. Use this workbook for validation only, "
                                "or upload a raw workbook whose FIN/raw sheet matches the issuer."
                            )
                            st.caption(f"Detected worksheets: {', '.join(sheet_names)}")
                            continue
                        if selected_sheet:
                            st.success(f"Auto-selected: {selected_sheet}")
                        loader_output = load_creditscope_source_candidates(
                            uploaded_file=uploaded,
                            mapping_path="config/field_mapping.csv",
                            row_mapping_path="config/creditscope_row_mapping.csv",
                            sheet_name=selected_sheet,
                            value_col=2,
                            required_fields=required_names,
                        )
                        report = loader_output["match_report"]
                        candidates = loader_output["source_candidates"]
                        mapped_count = len(loader_output["issuer_data"])
                    else:
                        source_data, report = map_uploaded_file(
                            uploaded_file=uploaded,
                            source_name=source_name,
                            mapping_path="config/field_mapping.csv",
                        )
                        candidates = mapping_report_to_source_candidates(report, uploaded_file=uploaded.name)
                        mapped_count = len(source_data)
                    if not report.empty and "uploaded_file" not in report.columns:
                        report.insert(0, "uploaded_file", uploaded.name)
                    st.session_state["uploaded_sources"][key] = uploaded.name
                    st.session_state["uploaded_source_candidates"][key] = candidates
                    st.session_state["uploaded_source_reports"][key] = report
                    st.success(f"{mapped_count} fields mapped")
                except Exception as exc:
                    st.error(f"Could not map {uploaded.name}.")
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
                    census = fetch_census_source_candidates(
                        state_fips=state_fips,
                        county_fips=county_fips,
                        year=int(census_year),
                        fields=census_fields,
                        include_proxy_fields=include_proxy,
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
                    bea = fetch_bea_source_candidates(
                        state_fips=bea_state,
                        county_fips=bea_county,
                        year=int(bea_year),
                        prior_year=int(bea_prior),
                        fields=bea_fields,
                    )
                    st.session_state["api_source_candidates"]["bea"] = bea
                    st.session_state["api_source_reports"]["bea"] = bea
                    st.success(f"Fetched {len(bea)} BEA candidate fields.")
                except BeaApiError as exc:
                    st.error("Could not fetch BEA data.")
                    st.exception(exc)

    with st.container(border=True):
        st.markdown("**Manual / source-pending inputs**")
        st.caption("Only typed values are saved. Blank cells do not overwrite uploaded or API source data.")
        manual_base = _manual_fields(required_df, st.session_state.get("source_report"))
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

        if st.button("Save issuer_data", type="primary"):
            manual_values = _clean_manual_values(edited_manual)
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
            if not frames:
                st.warning("No source candidates are available yet.")
            else:
                result = run_data_sourcing_pipeline(
                    frames,
                    methodology_id=methodology_id,
                    required_fields=required_names,
                )
                st.session_state["issuer_data"] = result["issuer_data"]
                st.session_state["source_report"] = result["source_report"]
                st.session_state["source_candidates"] = result["source_candidates"]
                st.session_state["source_readiness_summary"] = result["source_readiness_summary"]
                reports = []
                reports.extend(st.session_state.get("uploaded_source_reports", {}).values())
                reports.extend(st.session_state.get("api_source_reports", {}).values())
                if reports:
                    st.session_state["source_match_reports"] = pd.concat(
                        [r for r in reports if isinstance(r, pd.DataFrame) and not r.empty],
                        ignore_index=True,
                    )
                st.success(f"Saved issuer_data with {len(result['issuer_data'])} selected fields.")

    with st.container(border=True):
        st.markdown("**Source readiness**")
        _readiness_tabs(st.session_state.get("source_report", pd.DataFrame()))
