from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_accuracy_matrix_workbook import build_workbook
from scripts.methodology_accuracy_matrix import CASES, build_accuracy_package
from utils.ui_helpers import clean_for_display, current_context_card, init_state, page_header


DATA_DIR = PROJECT_ROOT / "work" / "methodology_accuracy_matrix"
WORKBOOK_PATH = DATA_DIR / "methodology_accuracy_matrix.xlsx"
TABLES_PATH = DATA_DIR / "tables.json"


def _load_tables(data_dir: Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    tables_path = data_dir / "tables.json"
    if not tables_path.exists():
        return {}
    raw = json.loads(tables_path.read_text(encoding="utf-8"))
    return {name: pd.DataFrame(rows) for name, rows in raw.items()}


def _download_csv(label: str, df: pd.DataFrame, file_name: str) -> None:
    if isinstance(df, pd.DataFrame) and not df.empty:
        st.download_button(
            label,
            data=df.to_csv(index=False).encode("utf-8"),
            file_name=file_name,
            mime="text/csv",
        )
    else:
        st.info(f"No {file_name} data available.")


def _download_excel(path: Path = WORKBOOK_PATH) -> None:
    if path.exists():
        st.download_button(
            "Download audit workbook",
            data=path.read_bytes(),
            file_name=path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.info("Excel workbook has not been generated yet.")


def _selected_values(label: str, values: Iterable[str], key: str) -> list[str]:
    options = sorted({str(value) for value in values if str(value).strip() and str(value).lower() != "nan"})
    return st.multiselect(label, options, default=[], key=key)


def _apply_common_filters(df: pd.DataFrame, *, key_prefix: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    filter_cols = st.columns(3)
    with filter_cols[0]:
        cases = _selected_values("Case", out.get("fixture_key", pd.Series(dtype=str)), f"{key_prefix}_cases")
    with filter_cols[1]:
        methodologies = _selected_values(
            "Methodology",
            out.get("methodology_id", pd.Series(dtype=str)),
            f"{key_prefix}_methodologies",
        )
    with filter_cols[2]:
        statuses = _selected_values(
            "Status",
            out.get("value_status", out.get("coverage_status", out.get("candidate_status", pd.Series(dtype=str)))),
            f"{key_prefix}_statuses",
        )
    if cases and "fixture_key" in out.columns:
        out = out[out["fixture_key"].astype(str).isin(cases)]
    if methodologies and "methodology_id" in out.columns:
        out = out[out["methodology_id"].astype(str).isin(methodologies)]
    status_col = next((col for col in ["value_status", "coverage_status", "candidate_status"] if col in out.columns), "")
    if statuses and status_col:
        out = out[out[status_col].astype(str).isin(statuses)]
    return out


def _metric_count(df: pd.DataFrame, column: str, value: str) -> int:
    if df.empty or column not in df.columns:
        return 0
    return int(df[column].fillna("").astype(str).str.lower().eq(value.lower()).sum())


def _truthy_count(df: pd.DataFrame, column: str) -> int:
    if df.empty or column not in df.columns:
        return 0
    return int(df[column].fillna(False).astype(str).str.lower().isin(["true", "1", "yes"]).sum())


def _status_table(df: pd.DataFrame, column: str) -> pd.DataFrame:
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


def _missing_workbooks() -> list[Path]:
    return [case.workbook_path for case in CASES if not case.workbook_path.exists()]


st.set_page_config(page_title="Audit", layout="wide")
init_state()
page_header(
    "Audit",
    "No-cheat benchmark audit for methodology accuracy, raw field coverage, and workbook page consistency.",
    "audit_platform",
)
current_context_card()

missing_workbooks = _missing_workbooks()
if missing_workbooks:
    with st.expander("Missing local benchmark workbooks", expanded=not TABLES_PATH.exists()):
        st.warning("Some configured local benchmark workbooks are not available in this environment.")
        st.dataframe(
            pd.DataFrame({"missing_workbook": [str(path) for path in missing_workbooks]}),
            width="stretch",
            hide_index=True,
        )

button_cols = st.columns([1.1, 1, 1.4])
with button_cols[0]:
    run_disabled = bool(missing_workbooks)
    if st.button("Run no-cheat benchmark audit", type="primary", disabled=run_disabled):
        try:
            with st.spinner("Building accuracy matrix, field coverage, and page consistency checks..."):
                build_accuracy_package(DATA_DIR)
                build_workbook(DATA_DIR, WORKBOOK_PATH)
            st.success("Audit package refreshed.")
            st.rerun()
        except Exception as exc:
            st.error("Audit run failed.")
            st.exception(exc)
with button_cols[1]:
    if st.button("Build Excel export", disabled=not TABLES_PATH.exists()):
        try:
            build_workbook(DATA_DIR, WORKBOOK_PATH)
            st.success("Excel workbook generated.")
            st.rerun()
        except Exception as exc:
            st.error("Excel export failed.")
            st.exception(exc)
with button_cols[2]:
    _download_excel()

tables = _load_tables()
if not tables:
    st.info("No audit tables are available yet. Run the benchmark audit once local workbooks are available.")
    st.stop()

summary = tables.get("summary", pd.DataFrame())
accuracy = tables.get("accuracy_matrix", pd.DataFrame())
coverage = tables.get("field_coverage", pd.DataFrame())
page_consistency = tables.get("page_consistency", pd.DataFrame())
primary_raw = tables.get("primary_raw_inputs", pd.DataFrame())
source_quality = tables.get("source_quality", pd.DataFrame())

st.session_state["audit_accuracy_matrix"] = accuracy
st.session_state["audit_field_coverage"] = coverage

metric_cols = st.columns(5)
metric_cols[0].metric("Cases", len(summary))
metric_cols[1].metric("Accuracy Rows", len(accuracy))
metric_cols[2].metric("Blocking Metrics", _truthy_count(accuracy, "blocking"))
metric_cols[3].metric("Missing Fields", _metric_count(coverage, "coverage_status", "missing_primary_raw"))
metric_cols[4].metric("Page Checks", len(page_consistency))

tabs = st.tabs([
    "Overview",
    "Accuracy Matrix",
    "Field Coverage",
    "Page Consistency",
    "Raw Inputs",
    "Exports",
])

with tabs[0]:
    st.subheader("Case Summary")
    summary_cols = [
        "fixture_key",
        "methodology_id",
        "issuer_name",
        "primary_raw_sheet",
        "raw_status",
        "primary_input_fields",
        "value_matches",
        "value_mismatches",
        "model_missing",
        "manual_skips",
        "model_rating",
        "official_rating",
        "notes",
    ]
    st.dataframe(
        clean_for_display(summary[[col for col in summary_cols if col in summary.columns]]),
        width="stretch",
        hide_index=True,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.write("Value status")
        st.dataframe(clean_for_display(_status_table(accuracy, "value_status")), width="stretch", hide_index=True)
    with c2:
        st.write("Field coverage")
        st.dataframe(clean_for_display(_status_table(coverage, "coverage_status")), width="stretch", hide_index=True)
    with c3:
        st.write("Page consistency")
        st.dataframe(
            clean_for_display(_status_table(page_consistency, "candidate_status")),
            width="stretch",
            hide_index=True,
        )

    blocking = accuracy[
        accuracy.get("blocking", pd.Series(dtype=object)).fillna(False).astype(str).str.lower().eq("true")
    ].copy() if not accuracy.empty else pd.DataFrame()
    st.subheader("Blocking Issues")
    blocking_cols = [
        "fixture_key",
        "methodology_id",
        "factor",
        "metric",
        "formula_id",
        "official_value",
        "model_compare_value",
        "value_status",
        "missing_fields",
        "suspected_cause",
    ]
    st.dataframe(
        clean_for_display(blocking[[col for col in blocking_cols if col in blocking.columns]]),
        width="stretch",
        hide_index=True,
    )

with tabs[1]:
    st.subheader("Methodology Accuracy Matrix")
    filtered = _apply_common_filters(accuracy, key_prefix="accuracy")
    blocking_only = st.checkbox("Blocking only", value=False, key="accuracy_blocking_only")
    if blocking_only and "blocking" in filtered.columns:
        filtered = filtered[filtered["blocking"].fillna(False).astype(str).str.lower().eq("true")]
    display_cols = [
        "fixture_key",
        "methodology_id",
        "issuer_name",
        "factor",
        "metric",
        "formula_id",
        "official_weight",
        "official_value",
        "model_compare_value",
        "value_delta",
        "value_status",
        "official_score",
        "model_score",
        "score_delta",
        "score_match",
        "blocking",
        "missing_fields",
        "suspected_cause",
    ]
    st.dataframe(
        clean_for_display(filtered[[col for col in display_cols if col in filtered.columns]]),
        width="stretch",
        hide_index=True,
    )
    _download_csv("Download filtered accuracy matrix", filtered, "accuracy_matrix_filtered.csv")

with tabs[2]:
    st.subheader("Field Coverage Matrix")
    filtered = _apply_common_filters(coverage, key_prefix="coverage")
    field_blocking_only = st.checkbox("Blocking fields only", value=False, key="coverage_blocking_only")
    if field_blocking_only and "field_blocking" in filtered.columns:
        filtered = filtered[filtered["field_blocking"].fillna(False).astype(str).str.lower().eq("true")]
    display_cols = [
        "fixture_key",
        "methodology_id",
        "formula_id",
        "metric",
        "formula_value_status",
        "field_name",
        "field_category",
        "coverage_status",
        "primary_value",
        "source_sheet",
        "source_cell",
        "source_label",
        "source_type",
        "no_cheat_allowed",
        "preferred_source",
        "fallback_source",
        "likely_sources",
        "other_page_candidate_statuses",
        "other_page_evidence_sheets",
        "other_page_official_match",
        "field_blocking",
        "suspected_cause",
    ]
    st.dataframe(
        clean_for_display(filtered[[col for col in display_cols if col in filtered.columns]]),
        width="stretch",
        hide_index=True,
    )

    if not coverage.empty:
        grouped = (
            coverage.groupby(["methodology_id", "coverage_status"], dropna=False)
            .size()
            .reset_index(name="field_count")
            .sort_values(["methodology_id", "coverage_status"])
        )
        with st.expander("Coverage summary by methodology", expanded=False):
            st.dataframe(clean_for_display(grouped), width="stretch", hide_index=True)
    _download_csv("Download field coverage matrix", filtered, "field_coverage_filtered.csv")

with tabs[3]:
    st.subheader("Workbook Page Consistency")
    filtered = _apply_common_filters(page_consistency, key_prefix="page")
    mismatches_only = st.checkbox("Mismatches only", value=False, key="page_mismatches_only")
    if mismatches_only and "candidate_status" in filtered.columns:
        filtered = filtered[filtered["candidate_status"].fillna("").astype(str).str.contains("mismatch", case=False)]
    display_cols = [
        "fixture_key",
        "methodology_id",
        "issuer_name",
        "sheet_name",
        "field_name",
        "matched_label",
        "candidate_cell",
        "candidate_value",
        "primary_value",
        "delta_to_primary",
        "official_value",
        "delta_to_official",
        "official_match",
        "candidate_status",
        "notes",
    ]
    st.dataframe(
        clean_for_display(filtered[[col for col in display_cols if col in filtered.columns]]),
        width="stretch",
        hide_index=True,
    )
    _download_csv("Download page consistency", filtered, "page_consistency_filtered.csv")

with tabs[4]:
    st.subheader("Primary Raw Inputs")
    raw_cols = [
        "test_case",
        "methodology_id",
        "issuer_name",
        "field_name",
        "value",
        "source_workbook",
        "source_sheet",
        "source_cell",
        "source_label",
        "source_type",
        "notes",
    ]
    st.dataframe(
        clean_for_display(primary_raw[[col for col in raw_cols if col in primary_raw.columns]]),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Source Quality")
    st.dataframe(clean_for_display(source_quality), width="stretch", hide_index=True)

with tabs[5]:
    st.subheader("Downloads")
    export_cols = st.columns(2)
    with export_cols[0]:
        _download_excel()
        _download_csv("Download summary.csv", summary, "summary.csv")
        _download_csv("Download accuracy_matrix.csv", accuracy, "accuracy_matrix.csv")
    with export_cols[1]:
        _download_csv("Download field_coverage.csv", coverage, "field_coverage.csv")
        _download_csv("Download page_consistency.csv", page_consistency, "page_consistency.csv")
        _download_csv("Download primary_raw_inputs.csv", primary_raw, "primary_raw_inputs.csv")
