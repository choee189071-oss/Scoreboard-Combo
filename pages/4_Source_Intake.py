from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.data_platform import build_source_catalog
from engine.source_intake import (
    DEFAULT_PRIORITY_FIELDS,
    build_formula_mismatch_review,
    build_pdf_evidence_candidates,
    build_top_blocking_fields,
    fields_for_pdf_evidence,
    run_source_intake_pipeline,
    uploaded_payload_to_source_candidates,
)
from utils.source_confirmation_queue import render_source_confirmation_queue
from utils.ui_helpers import SCHEME_OPTIONS, clean_for_display, current_context_card, init_state, page_header


PILOT_CASE_DIR = PROJECT_ROOT.parent.parent / "outputs" / "west_sacramento_source_intake"


def _payload(uploaded_file: Any) -> tuple[str, bytes]:
    name = str(getattr(uploaded_file, "name", "") or "uploaded_file")
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    if hasattr(uploaded_file, "getvalue"):
        raw = uploaded_file.getvalue()
    else:
        raw = uploaded_file.read()
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    return name, bytes(raw)


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


def _source_options() -> list[str]:
    catalog = build_source_catalog()
    if catalog.empty or "source_name" not in catalog.columns:
        return ["CreditScope", "ACFR", "OS", "MoodysWorkbook", "RatingReport", "Manual"]
    return catalog["source_name"].dropna().astype(str).sort_values().tolist()


def _display_candidates(candidates: pd.DataFrame) -> None:
    cols = [
        "field_name",
        "value",
        "source_name",
        "canonical_source",
        "source_type",
        "source_detail",
        "confidence",
        "source_file",
        "source_table",
        "source_cell_or_api",
        "source_label",
        "candidate_status",
        "notes",
    ]
    st.dataframe(
        clean_for_display(candidates[[col for col in cols if col in candidates.columns]]),
        width="stretch",
        hide_index=True,
    )


def _load_pilot_case_into_session() -> bool:
    required = ["all_source_candidates.csv", "source_report.csv", "source_readiness_summary.csv"]
    if not all((PILOT_CASE_DIR / name).exists() for name in required):
        return False
    st.session_state["source_intake_candidates"] = pd.read_csv(PILOT_CASE_DIR / "all_source_candidates.csv")
    st.session_state["source_intake_source_report"] = pd.read_csv(PILOT_CASE_DIR / "source_report.csv")
    st.session_state["source_intake_readiness_summary"] = pd.read_csv(PILOT_CASE_DIR / "source_readiness_summary.csv")
    if (PILOT_CASE_DIR / "acfr_pdf_evidence.csv").exists() and (PILOT_CASE_DIR / "debt_report_pdf_evidence.csv").exists():
        st.session_state["source_intake_pdf_evidence"] = pd.concat(
            [
                pd.read_csv(PILOT_CASE_DIR / "acfr_pdf_evidence.csv"),
                pd.read_csv(PILOT_CASE_DIR / "debt_report_pdf_evidence.csv"),
            ],
            ignore_index=True,
            sort=False,
        )
    elif (PILOT_CASE_DIR / "acfr_pdf_evidence.csv").exists():
        st.session_state["source_intake_pdf_evidence"] = pd.read_csv(PILOT_CASE_DIR / "acfr_pdf_evidence.csv")
    selected_path = PILOT_CASE_DIR / "selected_issuer_data.csv"
    if selected_path.exists():
        selected = pd.read_csv(selected_path)
        if {"field_name", "value"}.issubset(selected.columns):
            st.session_state["source_intake_issuer_data"] = dict(zip(selected["field_name"], selected["value"]))
    return True


st.set_page_config(page_title="Source Intake Lab", layout="wide")
init_state()
page_header(
    "Source Intake Lab",
    "Advanced source-candidate tooling. Normal runs should start in Workflow and use Review & Adjust for evidence corrections.",
    "source_intake",
)
current_context_card()

methodology_ids = list(SCHEME_OPTIONS.keys())
methodology_id = st.selectbox(
    "Methodology for source selection",
    methodology_ids,
    index=methodology_ids.index(st.session_state.get("methodology_id", "moodys_ccd_go"))
    if st.session_state.get("methodology_id", "moodys_ccd_go") in methodology_ids
    else 0,
    format_func=lambda value: SCHEME_OPTIONS.get(value, value),
)
st.session_state["methodology_id"] = methodology_id

top_fields_all = build_top_blocking_fields(top_n=100)
mismatch_review = build_formula_mismatch_review()
priority_pack = (
    top_fields_all[top_fields_all["field_name"].isin(DEFAULT_PRIORITY_FIELDS)].copy()
    if not top_fields_all.empty and "field_name" in top_fields_all.columns
    else pd.DataFrame()
)

metric_cols = st.columns(5)
metric_cols[0].metric("Top Blocking Fields", len(top_fields_all))
metric_cols[1].metric("Priority Targets Found", len(priority_pack))
metric_cols[2].metric("Formula Mismatches", len(mismatch_review))
metric_cols[3].metric(
    "Session Candidates",
    len(st.session_state.get("source_intake_candidates", pd.DataFrame()))
    if isinstance(st.session_state.get("source_intake_candidates"), pd.DataFrame)
    else 0,
)
metric_cols[4].metric(
    "PDF Evidence Rows",
    len(st.session_state.get("source_intake_pdf_evidence", pd.DataFrame()))
    if isinstance(st.session_state.get("source_intake_pdf_evidence"), pd.DataFrame)
    else 0,
)

tabs = st.tabs([
    "Source Intake Pipeline",
    "Source Confirmation Queue",
    "Top Blocking Fields",
    "Formula Mismatch Review",
    "PDF Evidence Extractor",
    "What To Provide",
    "Exports",
])

with tabs[0]:
    st.subheader("Upload To Source Candidates")
    st.caption(
        "Excel/CSV/JSON uploads become source_candidates with provenance. PDF uploads become review-only evidence candidates."
    )
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        source_name = st.selectbox("Source type", _source_options(), index=0, key="source_intake_source_name")
    with c2:
        include_support_tabs = st.checkbox("CreditScope support tabs", value=False)
    with c3:
        pdf_max_pages = st.number_input("PDF max pages", min_value=1, max_value=250, value=40, step=5)

    uploads = st.file_uploader(
        "Upload source files",
        type=["xlsx", "xls", "csv", "json", "jsonl", "pdf"],
        accept_multiple_files=True,
        key="source_intake_uploads",
    )
    if st.button("Process uploaded source files", type="primary"):
        candidate_frames: list[pd.DataFrame] = []
        diagnostic_frames: list[pd.DataFrame] = []
        pdf_docs: list[tuple[str, bytes]] = []
        for uploaded in uploads or []:
            file_name, payload = _payload(uploaded)
            if Path(file_name).suffix.lower() == ".pdf":
                pdf_docs.append((file_name, payload))
                continue
            try:
                result = uploaded_payload_to_source_candidates(
                    file_name,
                    payload,
                    source_name=source_name,
                    methodology_id=methodology_id,
                    include_support_tabs=include_support_tabs,
                )
                candidates = result.get("source_candidates", pd.DataFrame())
                diagnostics = result.get("diagnostics", pd.DataFrame())
                if isinstance(candidates, pd.DataFrame) and not candidates.empty:
                    candidate_frames.append(candidates)
                if isinstance(diagnostics, pd.DataFrame) and not diagnostics.empty:
                    diagnostic_frames.append(diagnostics)
            except Exception as exc:
                st.error(f"Could not process {file_name}.")
                st.exception(exc)

        if pdf_docs:
            target_fields = fields_for_pdf_evidence(
                priority_pack if not priority_pack.empty else top_fields_all.head(10),
                limit=10,
            )
            pdf_output = build_pdf_evidence_candidates(
                pdf_docs,
                target_fields,
                source_name=source_name,
                max_pages=int(pdf_max_pages),
                top_n_per_field=3,
            )
            pdf_candidates = pdf_output.get("source_candidates", pd.DataFrame())
            if isinstance(pdf_candidates, pd.DataFrame) and not pdf_candidates.empty:
                candidate_frames.append(pdf_candidates)
            st.session_state["source_intake_pdf_pages"] = pdf_output.get("pdf_pages", pd.DataFrame())
            st.session_state["source_intake_pdf_evidence"] = pdf_output.get("pdf_evidence", pd.DataFrame())

        candidates = pd.concat(candidate_frames, ignore_index=True, sort=False) if candidate_frames else pd.DataFrame()
        diagnostics = pd.concat(diagnostic_frames, ignore_index=True, sort=False) if diagnostic_frames else pd.DataFrame()
        st.session_state["source_intake_candidates"] = candidates
        st.session_state["source_intake_diagnostics"] = diagnostics
        if candidates.empty:
            st.warning("No source candidates were created.")
        else:
            pipeline = run_source_intake_pipeline(candidates, methodology_id=methodology_id)
            st.session_state["source_intake_source_report"] = pipeline["source_report"]
            st.session_state["source_intake_readiness_summary"] = pipeline["source_readiness_summary"]
            st.session_state["source_intake_issuer_data"] = pipeline["issuer_data"]
            st.success(f"Created {len(candidates)} source candidate rows and selected {len(pipeline['issuer_data'])} issuer fields.")

    candidates = st.session_state.get("source_intake_candidates", pd.DataFrame())
    source_report = st.session_state.get("source_intake_source_report", pd.DataFrame())
    readiness = st.session_state.get("source_intake_readiness_summary", pd.DataFrame())
    if isinstance(candidates, pd.DataFrame) and not candidates.empty:
        st.write("Source candidates")
        _display_candidates(candidates)
        _download_dataframe("Download source_candidates.csv", candidates, "source_candidates.csv", "download_intake_candidates")
    if isinstance(source_report, pd.DataFrame) and not source_report.empty:
        st.write("Selected source report")
        st.dataframe(clean_for_display(source_report), width="stretch", hide_index=True)
        _download_dataframe("Download source_report.csv", source_report, "source_report.csv", "download_intake_source_report")
    if isinstance(readiness, pd.DataFrame) and not readiness.empty:
        st.write("Readiness")
        st.dataframe(clean_for_display(readiness), width="stretch", hide_index=True)

with tabs[1]:
    st.subheader("Source Confirmation Queue")
    if PILOT_CASE_DIR.exists():
        if st.button("Load West Sacramento pilot output", key="load_west_sacramento_confirmation_case"):
            if _load_pilot_case_into_session():
                st.success("Loaded West Sacramento source report, candidates, and PDF evidence into this session.")
                st.rerun()
            else:
                st.warning("Pilot output files are incomplete.")
    render_source_confirmation_queue(
        methodology_id=methodology_id,
        source_report_key="source_intake_source_report",
        source_candidates_key="source_intake_candidates",
        pdf_evidence_key="source_intake_pdf_evidence",
        approved_candidates_key="source_intake_approved_candidates",
        issuer_data_key="source_intake_issuer_data",
        source_readiness_key="source_intake_readiness_summary",
        source_candidates_output_key="source_intake_candidates",
        decision_state_key="source_intake_confirmation_decisions",
        recalculate_formulas=False,
        show_header=False,
    )

with tabs[2]:
    st.subheader("Top Blocking Fields")
    st.caption("Frequency-ranked fields that block benchmark replication in the no-cheat audit.")
    top_cols = [
        "field_name",
        "blocking_rows",
        "case_count",
        "methodology_count",
        "methodologies",
        "formula_count",
        "formulas",
        "missing_primary_raw",
        "no_primary_raw_sheet",
        "preferred_source",
        "fallback_source",
        "priority_sources",
        "recommended_action",
    ]
    st.dataframe(
        clean_for_display(top_fields_all.head(10)[[col for col in top_cols if col in top_fields_all.columns]]),
        width="stretch",
        hide_index=True,
    )
    st.subheader("Suggested Priority Pack")
    st.caption("The fields we discussed as likely first targets, shown when they appear in the benchmark gaps.")
    st.dataframe(
        clean_for_display(priority_pack[[col for col in top_cols if col in priority_pack.columns]]),
        width="stretch",
        hide_index=True,
    )

with tabs[3]:
    st.subheader("Formula Mismatch Review")
    st.caption("Rows where the model produced a value but it did not match the official benchmark value.")
    review_cols = [
        "fixture_key",
        "methodology_id",
        "formula_id",
        "metric",
        "official_value",
        "model_compare_value",
        "value_delta",
        "relative_delta",
        "mismatch_type",
        "review_status",
        "recommended_action",
        "required_fields",
        "raw_source_cells",
        "warning",
    ]
    st.dataframe(
        clean_for_display(mismatch_review[[col for col in review_cols if col in mismatch_review.columns]]),
        width="stretch",
        hide_index=True,
    )
    if not mismatch_review.empty:
        c1, c2 = st.columns(2)
        with c1:
            st.write("Mismatch type")
            st.dataframe(clean_for_display(_status_counts(mismatch_review, "mismatch_type")), width="stretch", hide_index=True)
        with c2:
            st.write("Review status")
            st.dataframe(clean_for_display(_status_counts(mismatch_review, "review_status")), width="stretch", hide_index=True)

with tabs[4]:
    st.subheader("PDF Evidence Extractor")
    st.caption(
        "Narrow extractor: it ranks pages/snippets for blocking fields and creates review-only candidates with citations."
    )
    source_for_pdf = st.selectbox(
        "PDF source type",
        ["ACFR", "OS", "DebtReport", "MoodysWorkbook", "RatingReport", "AnnualReport", "RateStudy", "CountyAssessor"],
        index=0,
        key="pdf_evidence_source",
    )
    available_fields = top_fields_all["field_name"].dropna().astype(str).tolist() if not top_fields_all.empty else []
    default_fields = [field for field in DEFAULT_PRIORITY_FIELDS if field in available_fields][:8] or available_fields[:8]
    selected_fields = st.multiselect(
        "Target fields",
        available_fields,
        default=default_fields,
        key="pdf_evidence_fields",
    )
    pdf_uploads = st.file_uploader(
        "Upload PDF evidence files",
        type=["pdf"],
        accept_multiple_files=True,
        key="pdf_evidence_uploads",
    )
    pdf_cols = st.columns(2)
    max_pages = pdf_cols[0].number_input("Max pages per PDF", min_value=1, max_value=500, value=60, step=10)
    top_n = pdf_cols[1].number_input("Snippets per field", min_value=1, max_value=10, value=3, step=1)
    if st.button("Extract PDF evidence", type="primary"):
        docs = [_payload(uploaded) for uploaded in pdf_uploads or []]
        target = top_fields_all[top_fields_all["field_name"].isin(selected_fields)].copy()
        target = fields_for_pdf_evidence(target, limit=max(len(selected_fields), 1))
        output = build_pdf_evidence_candidates(
            docs,
            target,
            source_name=source_for_pdf,
            max_pages=int(max_pages),
            top_n_per_field=int(top_n),
        )
        st.session_state["source_intake_pdf_pages"] = output["pdf_pages"]
        st.session_state["source_intake_pdf_evidence"] = output["pdf_evidence"]
        st.session_state["source_intake_pdf_candidates"] = output["source_candidates"]
        st.success(
            f"Extracted {len(output['pdf_evidence'])} evidence rows and {len(output['source_candidates'])} review candidates."
        )

    evidence = st.session_state.get("source_intake_pdf_evidence", pd.DataFrame())
    pdf_candidates = st.session_state.get("source_intake_pdf_candidates", pd.DataFrame())
    if isinstance(evidence, pd.DataFrame) and not evidence.empty:
        evidence_cols = [
            "field_name",
            "source_name",
            "file_name",
            "page_number",
            "score",
            "matched_terms",
            "candidate_values",
            "citation",
            "snippet",
        ]
        st.dataframe(clean_for_display(evidence[[col for col in evidence_cols if col in evidence.columns]]), width="stretch", hide_index=True)
        _download_dataframe("Download pdf_evidence.csv", evidence, "pdf_evidence.csv", "download_pdf_evidence")
    if isinstance(pdf_candidates, pd.DataFrame) and not pdf_candidates.empty:
        st.write("Review-only source candidates")
        _display_candidates(pdf_candidates)
        _download_dataframe("Download pdf_source_candidates.csv", pdf_candidates, "pdf_source_candidates.csv", "download_pdf_candidates")

with tabs[5]:
    st.subheader("What You Should Provide Next")
    st.write("To make this accurate beyond the v1 scaffold, the platform needs these inputs:")
    st.markdown(
        """
- For each issuer: issuer name, methodology, fiscal year, state/county FIPS, and service-area geography if different from county.
- Raw CreditScope workbook or equivalent raw data tab. Ideally not the scorecard tab.
- ACFR/CAFR PDFs for financial statement fields like fund balance, cash, revenues, debt service, pension/OPEB.
- Official Statement PDFs for MADS, debt schedule, utility bill/rate covenants, and bond-specific fields.
- Rating reports or Moody's/S&P support workbooks only where the methodology uses adjusted/source-specific values.
- A quick note saying which PDF belongs to which source type: ACFR, OS, RatingReport, MoodysWorkbook, RateStudy, CountyAssessor.
        """.strip()
    )

with tabs[6]:
    st.subheader("Exports")
    _download_dataframe("Download top_blocking_fields.csv", top_fields_all, "top_blocking_fields.csv", "download_top_blocking")
    _download_dataframe("Download formula_mismatch_review.csv", mismatch_review, "formula_mismatch_review.csv", "download_mismatch_review")
    candidates = st.session_state.get("source_intake_candidates", pd.DataFrame())
    source_report = st.session_state.get("source_intake_source_report", pd.DataFrame())
    evidence = st.session_state.get("source_intake_pdf_evidence", pd.DataFrame())
    _download_dataframe("Download session_source_candidates.csv", candidates, "session_source_candidates.csv", "download_session_candidates")
    _download_dataframe("Download session_source_report.csv", source_report, "session_source_report.csv", "download_session_source_report")
    _download_dataframe("Download session_pdf_evidence.csv", evidence, "session_pdf_evidence.csv", "download_session_pdf_evidence")
