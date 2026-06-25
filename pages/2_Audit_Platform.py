from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.ai_audit_pipeline import (
    build_deploy_sanity_check,
    build_section_b_pdf_audit,
    build_section_b_term_matrix,
    perplexity_source_recommendations,
    recommendations_to_source_candidates,
    uploaded_pdf_documents_from_payloads,
)
from engine.data_sourcing_engine import normalize_source_candidates
from engine.methodology_audit import AUDIT_METHODOLOGIES
from utils.ui_helpers import ADVANCED_PAGE_LINKS, clean_for_display, current_context_card, init_state, page_header


def _secret_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return str(value)
    try:
        for name in names:
            value = st.secrets.get(name)  # type: ignore[attr-defined]
            if value:
                return str(value)
    except Exception:
        pass
    return ""


def _download_csv(label: str, df: pd.DataFrame, file_name: str) -> None:
    if isinstance(df, pd.DataFrame) and not df.empty:
        st.download_button(
            label,
            data=df.to_csv(index=False).encode("utf-8"),
            file_name=file_name,
            mime="text/csv",
        )


def _uploaded_payloads(files: Iterable[Any], *, source_name: str, source_slot: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for uploaded in files or []:
        if uploaded is None:
            continue
        if hasattr(uploaded, "seek"):
            uploaded.seek(0)
        payload = uploaded.getvalue() if hasattr(uploaded, "getvalue") else uploaded.read()
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        if hasattr(uploaded, "seek"):
            uploaded.seek(0)
        payloads.append(
            {
                "source_slot": source_slot,
                "source_name": source_name,
                "file_name": str(getattr(uploaded, "name", "") or "uploaded.pdf"),
                "payload": bytes(payload),
            }
        )
    return payloads


def _render_advanced_links() -> None:
    with st.expander("Advanced tools", expanded=False):
        st.caption("Builder/debugging workspaces. Section B below is the normal review carrier.")
        cols = st.columns(len(ADVANCED_PAGE_LINKS))
        for idx, (_, (path, label)) in enumerate(ADVANCED_PAGE_LINKS.items()):
            with cols[idx]:
                with st.container(border=True):
                    st.markdown(f"**{label}**")
                    try:
                        st.page_link(path, label=f"Open {label}")
                    except Exception:
                        st.caption(f"Open from sidebar/page list: {label}")


def _status_badge(label: str, configured: bool, secret_name: str) -> None:
    if configured:
        st.success(f"{label} configured")
    else:
        st.info(f"{label} disabled. Add `{secret_name}` in Streamlit secrets to enable it.")


def _render_deploy_sanity_check(pubfin_key: str, llama_key: str) -> None:
    checks = build_deploy_sanity_check(
        pubfin_api_key=pubfin_key,
        llama_cloud_api_key=llama_key,
    )
    blockers = int(checks["deploy_blocker"].astype(bool).sum()) if not checks.empty else 0
    ready = int(checks["status"].astype(str).eq("ready").sum()) if not checks.empty else 0
    with st.expander("Deploy sanity check", expanded=True):
        metric_cols = st.columns(3)
        metric_cols[0].metric("Checks", len(checks))
        metric_cols[1].metric("Ready", ready)
        metric_cols[2].metric("Deploy blockers", blockers)
        if blockers:
            st.warning("Required deployment checks are missing. Fix these before relying on Review & Audit in production.")
        else:
            st.success("Required deployment checks are ready. Optional AI features may still need secrets.")
        display_cols = ["check", "status", "required", "deploy_blocker", "detail"]
        st.table(
            clean_for_display(checks[[col for col in display_cols if col in checks.columns]]),
        )


def _display_recommendations(recommendations: pd.DataFrame) -> None:
    if recommendations.empty:
        st.info("No recommended links returned yet.")
        return
    display_cols = [
        "source_type",
        "title",
        "url",
        "date_or_year",
        "related_fields",
        "concept_terms",
        "reason",
        "confidence",
    ]
    st.dataframe(
        clean_for_display(recommendations[[col for col in display_cols if col in recommendations.columns]]),
        width="stretch",
        hide_index=True,
    )
    _download_csv("Download recommended links", recommendations, "section_b_recommended_links.csv")


def _store_pdf_audit(result: dict[str, pd.DataFrame]) -> None:
    st.session_state["section_b_pdf_pages"] = result.get("pdf_pages", pd.DataFrame())
    st.session_state["section_b_pdf_evidence"] = result.get("pdf_evidence", pd.DataFrame())
    st.session_state["section_b_source_candidates"] = result.get("source_candidates", pd.DataFrame())


def _store_recommendations(recommendations: pd.DataFrame) -> None:
    st.session_state["section_b_recommended_links"] = recommendations
    st.session_state["section_b_recommended_link_candidates"] = recommendations_to_source_candidates(recommendations)


def _send_candidates_to_review(candidates: pd.DataFrame) -> int:
    if candidates is None or candidates.empty:
        return 0
    existing = st.session_state.get("source_candidates")
    frames = [candidates]
    if isinstance(existing, pd.DataFrame) and not existing.empty:
        frames.insert(0, existing)
    combined = normalize_source_candidates(pd.concat(frames, ignore_index=True, sort=False))
    dedupe_cols = [
        col
        for col in ["field_name", "value", "source_name", "source_file", "source_table", "source_cell_or_api"]
        if col in combined.columns
    ]
    if dedupe_cols:
        combined = combined.drop_duplicates(subset=dedupe_cols, keep="last").reset_index(drop=True)
    before = len(existing) if isinstance(existing, pd.DataFrame) else 0
    st.session_state["source_candidates"] = combined
    return max(0, len(combined) - before)


def _parse_cache() -> dict[str, pd.DataFrame]:
    cache = st.session_state.get("section_b_pdf_parse_cache")
    if not isinstance(cache, dict):
        cache = {}
        st.session_state["section_b_pdf_parse_cache"] = cache
    return cache


st.set_page_config(page_title="Review & Audit", layout="wide")
init_state()
page_header(
    "Review & Audit",
    "Section B: AI-assisted methodology source review, uploaded PDF evidence, and recommended source links.",
    "audit_platform",
)
current_context_card()
_render_advanced_links()

perplexity_key = _secret_value("PUBFIN_API_KEY", "PERPLEXITY_API_KEY")
llama_key = _secret_value("LLAMA_CLOUD_API_KEY")

st.subheader("Section B - Source Review Carrier")
st.caption(
    "This section carries the methodology term map, PDF evidence review, and missing-document recommendations."
)

control_cols = st.columns([1.2, 1.5, 0.8, 0.9])
with control_cols[0]:
    default_method = st.session_state.get("methodology_id", AUDIT_METHODOLOGIES[0])
    methodology_id = st.selectbox(
        "Methodology",
        AUDIT_METHODOLOGIES,
        index=AUDIT_METHODOLOGIES.index(default_method) if default_method in AUDIT_METHODOLOGIES else 0,
    )
with control_cols[1]:
    issuer_name = st.text_input("Issuer name", value=str(st.session_state.get("issuer_name", "") or ""))
with control_cols[2]:
    analysis_year = st.text_input("Fiscal / analysis year", value=str(st.session_state.get("analysis_year", "") or ""))
with control_cols[3]:
    target_limit = st.number_input("Field limit", min_value=5, max_value=80, value=25, step=5)

st.session_state["methodology_id"] = methodology_id
if issuer_name:
    st.session_state["issuer_name"] = issuer_name
if analysis_year:
    st.session_state["analysis_year"] = analysis_year

status_cols = st.columns(2)
with status_cols[0]:
    _status_badge("Perplexity/PUBFIN source discovery", bool(perplexity_key), "PUBFIN_API_KEY")
with status_cols[1]:
    _status_badge("LlamaCloud PDF parsing", bool(llama_key), "LLAMA_CLOUD_API_KEY")

_render_deploy_sanity_check(perplexity_key, llama_key)

term_matrix = build_section_b_term_matrix(methodology_id, max_fields=int(target_limit))
st.session_state["section_b_term_matrix"] = term_matrix

if term_matrix.empty:
    st.warning("No Section B field terms are available for the selected methodology.")
    st.stop()

metric_cols = st.columns(4)
metric_cols[0].metric("Fields", len(term_matrix))
metric_cols[1].metric(
    "ACFR-linked",
    int(term_matrix["expected_documents"].fillna("").astype(str).str.contains("ACFR", case=False).sum()),
)
metric_cols[2].metric(
    "OS/debt-linked",
    int(term_matrix["expected_documents"].fillna("").astype(str).str.contains("official statement|debt", case=False, regex=True).sum()),
)
metric_cols[3].metric(
    "Review blockers",
    int(pd.to_numeric(term_matrix.get("audit_field_blocking", 0), errors="coerce").fillna(0).gt(0).sum()),
)

tabs = st.tabs(["Terms & Files", "Uploaded Evidence", "Recommended Links", "Review Candidates"])

with tabs[0]:
    st.subheader("Methodology Terms And Expected Files")
    st.caption("These rows come from the methodology template, formula library, data dictionary, and source priority config.")
    display_cols = [
        "field_name",
        "field_category",
        "formula_ids",
        "metrics",
        "expected_documents",
        "priority_sources",
        "local_concept_terms",
        "audit_field_blocking",
        "audit_coverage_statuses",
    ]
    st.dataframe(
        clean_for_display(term_matrix[[col for col in display_cols if col in term_matrix.columns]]),
        width="stretch",
        hide_index=True,
    )
    _download_csv("Download term matrix", term_matrix, "section_b_term_matrix.csv")

with tabs[1]:
    st.subheader("Uploaded ACFR / OH / OS Evidence")
    st.caption(
        "Upload issuer ACFR/audited financial statements and OH/OS/debt PDFs. LlamaCloud is used first when configured; "
        "local pypdf extraction is used as fallback."
    )
    upload_cols = st.columns(2)
    with upload_cols[0]:
        acfr_files = st.file_uploader(
            "ACFR / audited financial statements PDF",
            type=["pdf"],
            accept_multiple_files=True,
            key=f"section_b_acfr_upload_{methodology_id}",
        )
    with upload_cols[1]:
        os_files = st.file_uploader(
            "OH / OS / debt support PDF",
            type=["pdf"],
            accept_multiple_files=True,
            key=f"section_b_os_upload_{methodology_id}",
        )

    payloads = [
        *_uploaded_payloads(acfr_files, source_name="ACFR", source_slot="section_b_acfr"),
        *_uploaded_payloads(os_files, source_name="OS", source_slot="section_b_os"),
    ]
    st.session_state["section_b_uploaded_pdf_payloads"] = payloads

    run_disabled = not payloads
    cache = _parse_cache()
    run_cols = st.columns([0.9, 1.1, 1.1, 1.4])
    with run_cols[0]:
        max_pages = st.number_input("Max pages / PDF", min_value=5, max_value=250, value=60, step=5)
    with run_cols[1]:
        snippets_per_field = st.number_input("Snippets / field", min_value=1, max_value=8, value=3, step=1)
    with run_cols[2]:
        st.metric("Parse cache", len(cache))
        if st.button("Clear PDF parse cache", disabled=not bool(cache)):
            st.session_state["section_b_pdf_parse_cache"] = {}
            st.success("PDF parse cache cleared.")
            st.rerun()
    with run_cols[3]:
        if st.button("Run Section B PDF audit", type="primary", disabled=run_disabled):
            documents = uploaded_pdf_documents_from_payloads(payloads)
            with st.spinner("Parsing PDFs and ranking methodology evidence..."):
                result = build_section_b_pdf_audit(
                    documents,
                    term_matrix,
                    llama_api_key=llama_key or None,
                    max_pages=int(max_pages),
                    top_n_per_field=int(snippets_per_field),
                    page_cache=cache,
                )
            _store_pdf_audit(result)
            st.success("Section B PDF evidence refreshed.")

    if run_disabled:
        st.info("No ACFR/OH/OS PDFs uploaded yet. Use Recommended Links to find likely source documents.")

    pages = st.session_state.get("section_b_pdf_pages", pd.DataFrame())
    evidence = st.session_state.get("section_b_pdf_evidence", pd.DataFrame())
    if isinstance(pages, pd.DataFrame) and not pages.empty:
        with st.expander("PDF parse status", expanded=False):
            parse_cols = ["file_name", "page_number", "extraction_status", "parser", "cache_status", "error"]
            st.dataframe(clean_for_display(pages[[col for col in parse_cols if col in pages.columns]]), width="stretch", hide_index=True)
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
        st.dataframe(
            clean_for_display(evidence[[col for col in evidence_cols if col in evidence.columns]]),
            width="stretch",
            hide_index=True,
        )
        _download_csv("Download PDF evidence", evidence, "section_b_pdf_evidence.csv")
    elif isinstance(pages, pd.DataFrame) and not pages.empty:
        st.warning("PDFs were parsed, but no term-matched evidence snippets were found.")

with tabs[2]:
    st.subheader("Recommended Source Links")
    if payloads:
        st.caption("Recommendations are still useful when uploaded PDFs do not cover every target field.")
    else:
        st.caption("Because no PDFs are uploaded, this is the fallback path: Perplexity searches for official download links.")

    if st.button("Find official links with Perplexity", disabled=not bool(perplexity_key)):
        with st.spinner("Searching for ACFR/OH/OS and related source documents..."):
            result = perplexity_source_recommendations(
                issuer_name=issuer_name,
                analysis_year=analysis_year,
                targets=term_matrix,
                api_key=perplexity_key,
            )
        status = result["status"]
        if status.ok:
            _store_recommendations(result["recommendations"])
            st.success("Recommended links refreshed.")
        else:
            st.warning(f"Perplexity search failed: {status.status}")
            if status.detail:
                st.caption(status.detail)

    if not perplexity_key:
        st.info("Add `PUBFIN_API_KEY` in Streamlit secrets to enable Perplexity source discovery.")
    recommendations = st.session_state.get("section_b_recommended_links", pd.DataFrame())
    recommendations = recommendations if isinstance(recommendations, pd.DataFrame) else pd.DataFrame()
    _display_recommendations(recommendations)
    link_candidates = st.session_state.get("section_b_recommended_link_candidates")
    if isinstance(recommendations, pd.DataFrame) and not recommendations.empty:
        if not isinstance(link_candidates, pd.DataFrame) or link_candidates.empty:
            link_candidates = recommendations_to_source_candidates(recommendations)
            st.session_state["section_b_recommended_link_candidates"] = link_candidates
        if isinstance(link_candidates, pd.DataFrame) and not link_candidates.empty:
            st.caption("Recommended links can be sent as document-pending rows. They are reminders to download/upload documents, not scoring inputs.")
            if st.button("Send recommended links to Review & Adjust queue"):
                added = _send_candidates_to_review(link_candidates)
                st.success(f"Sent {added} recommended-link candidates to Review & Adjust.")
            with st.expander("Recommended-link candidates", expanded=False):
                link_cols = [
                    "field_name",
                    "source_name",
                    "source_file",
                    "source_table",
                    "candidate_status",
                    "notes",
                ]
                st.dataframe(
                    clean_for_display(link_candidates[[col for col in link_cols if col in link_candidates.columns]]),
                    width="stretch",
                    hide_index=True,
                )

with tabs[3]:
    st.subheader("Review-Only Source Candidates")
    st.caption(
        "These candidates are not fed into scoring automatically. They are marked source_review and should be confirmed in Data Confirmation first."
    )
    candidates = st.session_state.get("section_b_source_candidates", pd.DataFrame())
    if isinstance(candidates, pd.DataFrame) and not candidates.empty:
        action_cols = st.columns([1, 2])
        with action_cols[0]:
            if st.button("Send to Review & Adjust queue"):
                added = _send_candidates_to_review(candidates)
                st.success(f"Sent {added} new/updated candidates to Review & Adjust.")
        with action_cols[1]:
            try:
                st.page_link("pages/0_Data_Confirmation.py", label="Open Review & Adjust")
            except Exception:
                st.caption("Open Review & Adjust from the sidebar to confirm these candidates.")
        candidate_cols = [
            "field_name",
            "value",
            "source_name",
            "confidence",
            "source_file",
            "source_table",
            "source_cell_or_api",
            "source_label",
            "candidate_status",
            "notes",
        ]
        st.dataframe(
            clean_for_display(candidates[[col for col in candidate_cols if col in candidates.columns]]),
            width="stretch",
            hide_index=True,
        )
        _download_csv("Download review candidates", candidates, "section_b_review_candidates.csv")
    else:
        st.info("Run the PDF audit to generate review-only candidates from uploaded documents.")
