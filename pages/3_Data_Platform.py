from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.data_platform import build_data_platform_tables
from utils.ui_helpers import clean_for_display, current_context_card, init_state, page_header


def _download_dataframe(label: str, df: pd.DataFrame, file_name: str, key: str) -> None:
    if isinstance(df, pd.DataFrame) and not df.empty:
        st.download_button(
            label,
            data=df.to_csv(index=False).encode("utf-8"),
            file_name=file_name,
            mime="text/csv",
            key=key,
        )
    else:
        st.info(f"No {file_name} data available.")


def _status_counts(df: pd.DataFrame, column: str) -> pd.DataFrame:
    if df.empty or column not in df.columns:
        return pd.DataFrame(columns=[column, "count"])
    return (
        df[column]
        .fillna("blank")
        .astype(str)
        .value_counts()
        .rename_axis(column)
        .reset_index(name="count")
    )


def _select_filter(label: str, df: pd.DataFrame, column: str, key: str) -> list[str]:
    if df.empty or column not in df.columns:
        return []
    options = sorted(
        {
            str(value)
            for value in df[column].dropna().astype(str)
            if str(value).strip() and str(value).lower() != "nan"
        }
    )
    return st.multiselect(label, options, default=[], key=key)


def _text_filter(df: pd.DataFrame, text: str, columns: list[str]) -> pd.DataFrame:
    if df.empty or not text.strip():
        return df
    needle = text.strip().lower()
    mask = pd.Series(False, index=df.index)
    for column in columns:
        if column in df.columns:
            mask = mask | df[column].fillna("").astype(str).str.lower().str.contains(needle, regex=False)
    return df[mask].copy()


st.set_page_config(page_title="Data Platform", layout="wide")
init_state()
page_header(
    "Data Platform",
    "Advanced source registry, field dictionary, and canonical data-lineage workspace.",
    "data_platform",
)
current_context_card()

try:
    tables = build_data_platform_tables()
except Exception as exc:
    st.error("Could not build data platform tables.")
    st.exception(exc)
    st.stop()

source_catalog = tables["source_catalog"]
source_field_matrix = tables["source_field_matrix"]
field_dictionary = tables["field_dictionary"]
methodology_field_matrix = tables["methodology_field_matrix"]
gap_report = tables["gap_report"]

st.session_state["data_platform_source_catalog"] = source_catalog
st.session_state["data_platform_field_dictionary"] = field_dictionary
st.session_state["data_platform_gap_report"] = gap_report

metric_cols = st.columns(5)
metric_cols[0].metric("Sources", len(source_catalog))
metric_cols[1].metric("Canonical Fields", len(field_dictionary))
metric_cols[2].metric("Source-Field Rules", len(source_field_matrix))
metric_cols[3].metric("Methodology Fields", len(methodology_field_matrix))
blocking_gaps = 0
if not gap_report.empty and "severity" in gap_report.columns:
    blocking_gaps = int(gap_report["severity"].fillna("").astype(str).str.lower().eq("blocking").sum())
metric_cols[4].metric("Blocking Gaps", blocking_gaps)

tabs = st.tabs([
    "Data Source Layer",
    "Field Dictionary",
    "Methodology Needs",
    "Gap Report",
    "Exports",
])

with tabs[0]:
    st.subheader("Source Catalog")
    st.caption("Each source has a canonical name, access method, confidence default, and field coverage summary.")
    source_type_filter = _select_filter("Source type", source_catalog, "source_type", "source_catalog_type")
    source_view = source_catalog.copy()
    if source_type_filter:
        source_view = source_view[source_view["source_type"].astype(str).isin(source_type_filter)]
    source_cols = [
        "source_name",
        "source_type",
        "access_method",
        "structured",
        "requires_upload",
        "requires_api_key",
        "default_confidence",
        "priority_field_count",
        "mapped_field_count",
        "methodology_field_count",
        "benchmark_blocking_field_count",
        "preferred_for",
        "notes",
    ]
    st.dataframe(
        clean_for_display(source_view[[col for col in source_cols if col in source_view.columns]]),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Source-Field Priority Matrix")
    filters = st.columns(3)
    with filters[0]:
        source_filter = _select_filter("Source", source_field_matrix, "source_name", "source_field_source")
    with filters[1]:
        methodology_filter = _select_filter("Methodology", source_field_matrix, "methodology_id", "source_field_methodology")
    with filters[2]:
        category_filter = _select_filter("Field category", source_field_matrix, "field_category", "source_field_category")

    source_field_view = source_field_matrix.copy()
    if source_filter:
        source_field_view = source_field_view[source_field_view["source_name"].astype(str).isin(source_filter)]
    if methodology_filter:
        source_field_view = source_field_view[source_field_view["methodology_id"].astype(str).isin(methodology_filter)]
    if category_filter:
        source_field_view = source_field_view[source_field_view["field_category"].astype(str).isin(category_filter)]
    matrix_cols = [
        "source_name",
        "source_type",
        "access_method",
        "field_name",
        "field_category",
        "methodology_id",
        "priority_rank",
        "min_confidence",
        "manual_allowed",
        "structured",
        "requires_upload",
        "requires_api_key",
        "automation_level",
        "priority_notes",
    ]
    st.dataframe(
        clean_for_display(source_field_view[[col for col in matrix_cols if col in source_field_view.columns]]),
        width="stretch",
        hide_index=True,
    )
    _download_dataframe(
        "Download source_field_matrix.csv",
        source_field_view,
        "source_field_matrix.csv",
        "download_source_field_matrix_filtered",
    )

with tabs[1]:
    st.subheader("Canonical Field Dictionary")
    st.caption("This is the standard field vocabulary that formulas, source loaders, and audit reports should all use.")
    filters = st.columns(4)
    with filters[0]:
        readiness_filter = _select_filter("Readiness", field_dictionary, "readiness_status", "field_readiness")
    with filters[1]:
        field_category_filter = _select_filter("Category", field_dictionary, "field_category", "field_category")
    with filters[2]:
        automation_filter = _select_filter("Automation", field_dictionary, "automation_level", "field_automation")
    with filters[3]:
        search_text = st.text_input("Search field / alias / metric", key="field_search")

    field_view = field_dictionary.copy()
    if readiness_filter:
        field_view = field_view[field_view["readiness_status"].astype(str).isin(readiness_filter)]
    if field_category_filter:
        field_view = field_view[field_view["field_category"].astype(str).isin(field_category_filter)]
    if automation_filter:
        field_view = field_view[field_view["automation_level"].astype(str).isin(automation_filter)]
    field_view = _text_filter(field_view, search_text, ["field_name", "aliases", "formula_ids", "metrics", "notes"])

    field_cols = [
        "readiness_status",
        "field_name",
        "field_category",
        "preferred_source",
        "fallback_source",
        "priority_sources",
        "automation_level",
        "dictionary_status",
        "source_priority_status",
        "alias_status",
        "alias_count",
        "mapped_sources",
        "methodology_count",
        "methodologies",
        "formula_count",
        "formula_ids",
        "audit_available_primary_raw",
        "audit_missing_primary_raw",
        "audit_field_blocking",
        "aliases",
        "notes",
    ]
    st.dataframe(
        clean_for_display(field_view[[col for col in field_cols if col in field_view.columns]]),
        width="stretch",
        hide_index=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        st.write("Readiness status")
        st.dataframe(clean_for_display(_status_counts(field_dictionary, "readiness_status")), width="stretch", hide_index=True)
    with c2:
        st.write("Automation level")
        st.dataframe(clean_for_display(_status_counts(field_dictionary, "automation_level")), width="stretch", hide_index=True)
    _download_dataframe(
        "Download field_dictionary.csv",
        field_view,
        "field_dictionary.csv",
        "download_field_dictionary_filtered",
    )

with tabs[2]:
    st.subheader("Methodology Field Needs")
    st.caption("One row per methodology-field dependency, merged with dictionary/source/audit status.")
    filters = st.columns(3)
    with filters[0]:
        methodology_filter = _select_filter("Methodology", methodology_field_matrix, "methodology_id", "needs_methodology")
    with filters[1]:
        gap_filter = _select_filter("Gap status", methodology_field_matrix, "methodology_gap_status", "needs_gap")
    with filters[2]:
        search_text = st.text_input("Search formula / field / metric", key="needs_search")

    needs_view = methodology_field_matrix.copy()
    if methodology_filter:
        needs_view = needs_view[needs_view["methodology_id"].astype(str).isin(methodology_filter)]
    if gap_filter:
        needs_view = needs_view[needs_view["methodology_gap_status"].astype(str).isin(gap_filter)]
    needs_view = _text_filter(needs_view, search_text, ["field_name", "formula_ids", "metrics", "factors"])

    needs_cols = [
        "methodology_gap_status",
        "methodology_id",
        "field_name",
        "field_category",
        "formula_count",
        "formula_ids",
        "factors",
        "metrics",
        "preferred_source",
        "fallback_source",
        "priority_sources",
        "template_source_priority",
        "automation_level",
        "dictionary_status",
        "source_priority_status",
        "alias_status",
        "alias_count",
        "mapped_sources",
        "audit_missing_primary_raw",
        "audit_field_blocking",
    ]
    st.dataframe(
        clean_for_display(needs_view[[col for col in needs_cols if col in needs_view.columns]]),
        width="stretch",
        hide_index=True,
    )
    _download_dataframe(
        "Download methodology_field_matrix.csv",
        needs_view,
        "methodology_field_matrix.csv",
        "download_methodology_field_matrix_filtered",
    )

with tabs[3]:
    st.subheader("Data Platform Gap Report")
    st.caption("Configuration and benchmark gaps that should guide the next source-loader and PDF-ingestion work.")
    filters = st.columns(3)
    with filters[0]:
        severity_filter = _select_filter("Severity", gap_report, "severity", "gap_severity")
    with filters[1]:
        gap_type_filter = _select_filter("Gap type", gap_report, "gap_type", "gap_type")
    with filters[2]:
        search_text = st.text_input("Search gap", key="gap_search")

    gap_view = gap_report.copy()
    if severity_filter:
        gap_view = gap_view[gap_view["severity"].astype(str).isin(severity_filter)]
    if gap_type_filter:
        gap_view = gap_view[gap_view["gap_type"].astype(str).isin(gap_type_filter)]
    gap_view = _text_filter(gap_view, search_text, ["field_name", "methodology_id", "source_name", "details"])
    st.dataframe(clean_for_display(gap_view), width="stretch", hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        st.write("Gap type")
        st.dataframe(clean_for_display(_status_counts(gap_report, "gap_type")), width="stretch", hide_index=True)
    with c2:
        st.write("Severity")
        st.dataframe(clean_for_display(_status_counts(gap_report, "severity")), width="stretch", hide_index=True)
    _download_dataframe("Download gap_report.csv", gap_view, "gap_report.csv", "download_gap_report_filtered")

with tabs[4]:
    st.subheader("Downloads")
    c1, c2 = st.columns(2)
    with c1:
        _download_dataframe("Download source_catalog.csv", source_catalog, "source_catalog.csv", "download_source_catalog_full")
        _download_dataframe(
            "Download source_field_matrix.csv",
            source_field_matrix,
            "source_field_matrix.csv",
            "download_source_field_matrix_full",
        )
        _download_dataframe(
            "Download field_dictionary.csv",
            field_dictionary,
            "field_dictionary.csv",
            "download_field_dictionary_full",
        )
    with c2:
        _download_dataframe(
            "Download methodology_field_matrix.csv",
            methodology_field_matrix,
            "methodology_field_matrix.csv",
            "download_methodology_field_matrix_full",
        )
        _download_dataframe("Download gap_report.csv", gap_report, "gap_report.csv", "download_gap_report_full")
