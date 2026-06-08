from __future__ import annotations

from typing import Any, Dict, Iterable, List

import pandas as pd
import streamlit as st

from utils.ui_helpers import clean_for_display


HUMAN_WORKFLOW_STEPS: List[Dict[str, str]] = [
    {
        "step": "1. Lock deal context",
        "human_action": "Confirm issuer, methodology, and fiscal year.",
        "system_action": "Use one context across source QA, formulas, rating, and exports.",
        "decision_output": "Context locked",
    },
    {
        "step": "2. Register files",
        "human_action": "Upload CreditScope workbook, ACFRs, and debt support documents.",
        "system_action": "Classify issuer, document type, fiscal year, and include/exclude status.",
        "decision_output": "File registry",
    },
    {
        "step": "3. Locate evidence",
        "human_action": "Review source-map pages and tables before extraction.",
        "system_action": "Locate candidate ACFR sections deterministically by year and table name.",
        "decision_output": "Source map",
    },
    {
        "step": "4. Extract candidates",
        "human_action": "Check candidate values against the visible table/page.",
        "system_action": "Pull values only from located pages or marked evidence regions.",
        "decision_output": "Candidate values",
    },
    {
        "step": "5. Compare sources",
        "human_action": "Review CreditScope vs. ACFR/API/debt-support differences.",
        "system_action": "Compute variance, materiality flag, and affected formula/factor.",
        "decision_output": "Difference review",
    },
    {
        "step": "6. AI review",
        "human_action": "Use AI only on cited pages or selected evidence snippets.",
        "system_action": "Explain line-item fit, likely mismatch reason, and confidence.",
        "decision_output": "Reviewer note",
    },
    {
        "step": "7. Approve value",
        "human_action": "Accept CreditScope, accept ACFR, override manually, or mark needs review.",
        "system_action": "Store selected value, selected source, approval note, and audit timestamp.",
        "decision_output": "Approved source",
    },
    {
        "step": "8. Publish outputs",
        "human_action": "Run formulas, rating audit trail, report, and presentation exports.",
        "system_action": "Carry approved source labels into rating/report/slides.",
        "decision_output": "Source-backed output",
    },
]


SP_LOCAL_GOV_FILE_REGISTRY: List[Dict[str, str]] = [
    {
        "document_role": "CreditScope workbook",
        "required": "Required",
        "example_file": "City of West Sacramento Local Govt Scorecard workbook",
        "confirmation_use": "Starting point for scorecard values and workbook direct metrics.",
        "include_rule": "Include when issuer and methodology match the current deal.",
    },
    {
        "document_role": "Current-year ACFR",
        "required": "Required",
        "example_file": "FY2024 City of West Sacramento ACFR",
        "confirmation_use": "Independent check for governmental funds, reconciliation, pension, OPEB, and debt notes.",
        "include_rule": "Include when fiscal year matches the analysis year.",
    },
    {
        "document_role": "Prior-year ACFR",
        "required": "Required for 3Y averages",
        "example_file": "FY2023 City of West Sacramento ACFR",
        "confirmation_use": "Trend-year support for operating margin and available fund balance ratios.",
        "include_rule": "Include when used by a trend formula denominator or numerator.",
    },
    {
        "document_role": "Second prior-year ACFR",
        "required": "Required for 3Y averages",
        "example_file": "FY2022 City of West Sacramento ACFR",
        "confirmation_use": "Third observation for 3Y average checks; secured PDFs should use rendered page evidence.",
        "include_rule": "Include even when text extraction is limited if page rendering is available.",
    },
    {
        "document_role": "Debt support / agenda packet",
        "required": "Optional support",
        "example_file": "West Sacramento Financing Authority 2022 refunding packet",
        "confirmation_use": "Supporting evidence for refunding, maturity, debt service flow, and reserve mechanics.",
        "include_rule": "Include as debt support only; do not treat as a complete official statement.",
    },
    {
        "document_role": "Unrelated issuer document",
        "required": "Exclude",
        "example_file": "City of Sacramento Water Revenue Bonds packet",
        "confirmation_use": "None for West Sacramento local government QA.",
        "include_rule": "Exclude when issuer or credit pledge does not match the current deal.",
    },
]


SP_LOCAL_GOV_FIELD_CHECKLIST: List[Dict[str, str]] = [
    {
        "factor": "Economy",
        "field_or_metric": "gdp_per_capita_ratio",
        "primary_check": "CreditScope Economy support tab vs. BEA source geography.",
        "preferred_evidence": "Workbook direct metric plus BEA page/API record.",
        "approval_note": "Confirm geography before replacing workbook ratio.",
    },
    {
        "factor": "Economy",
        "field_or_metric": "personal_income_ratio",
        "primary_check": "CreditScope Economy support tab vs. BEA PCPI source geography.",
        "preferred_evidence": "Workbook direct metric plus BEA page/API record.",
        "approval_note": "Confirm local and U.S. denominators use the same year.",
    },
    {
        "factor": "Financial Performance",
        "field_or_metric": "gov_operating_margin_3yr_avg",
        "primary_check": "ACFR governmental funds revenues, expenditures, and transfers for three fiscal years.",
        "preferred_evidence": "Statement of Revenues, Expenditures, and Changes in Fund Balances.",
        "approval_note": "Recompute the 3Y average before approval.",
    },
    {
        "factor": "Reserves and Liquidity",
        "field_or_metric": "available_fund_balance_ratio_3yr_avg",
        "primary_check": "ACFR committed, assigned, unassigned fund balance and revenue denominator.",
        "preferred_evidence": "Balance Sheet - Governmental Funds and revenue statement.",
        "approval_note": "Confirm restricted/nonspendable exclusions.",
    },
    {
        "factor": "Debt & Liabilities",
        "field_or_metric": "fixed_cost_burden_ratio",
        "primary_check": "CreditScope direct metric vs. ACFR debt service and governmental revenue.",
        "preferred_evidence": "Debt service rows, pension/OPEB cost notes, and revenue denominator.",
        "approval_note": "Do not treat missing pension/OPEB costs as zero without review.",
    },
    {
        "factor": "Debt & Liabilities",
        "field_or_metric": "net_direct_debt_per_capita",
        "primary_check": "CreditScope direct metric vs. debt support/official statement where available.",
        "preferred_evidence": "Debt support schedules, ACFR long-term debt notes, and issuer population.",
        "approval_note": "ACFR alone may not capture overlapping/direct debt adjustments.",
    },
    {
        "factor": "Debt & Liabilities",
        "field_or_metric": "npl_per_capita",
        "primary_check": "CreditScope direct metric vs. ACFR net pension liability and issuer population.",
        "preferred_evidence": "Reconciliation to Statement of Net Position and pension note.",
        "approval_note": "Confirm dollar units and population denominator.",
    },
    {
        "factor": "Manual",
        "field_or_metric": "management_assessment / institutional_framework_rating",
        "primary_check": "Analyst-entered qualitative score.",
        "preferred_evidence": "Rating report, committee note, or explicit user approval.",
        "approval_note": "Manual scores are complete only when numeric score is present.",
    },
]


APPROVAL_STATUS_RULES: List[Dict[str, str]] = [
    {
        "status": "matched",
        "meaning": "Independent source supports the selected value within rounding tolerance.",
        "next_action": "Approve selected value.",
    },
    {
        "status": "minor_difference",
        "meaning": "Difference appears attributable to rounding, fiscal-year presentation, or table formatting.",
        "next_action": "Approve with note if rating bucket is unchanged.",
    },
    {
        "status": "material_difference",
        "meaning": "Difference could change a metric score, factor score, or final rating.",
        "next_action": "Do not publish until reviewed.",
    },
    {
        "status": "missing_source",
        "meaning": "Required independent support has not been located.",
        "next_action": "Keep CreditScope as unverified or request another source.",
    },
    {
        "status": "issuer_mismatch",
        "meaning": "Document issuer, pledge, or credit does not match the current deal.",
        "next_action": "Exclude from source QA.",
    },
]


def _frame(rows: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _current_source_registry() -> pd.DataFrame:
    uploaded = st.session_state.get("uploaded_sources", {}) or {}
    rows: list[dict[str, Any]] = []
    for source_key, file_name in uploaded.items():
        if not file_name:
            continue
        rows.append(
            {
                "source_slot": source_key,
                "file_name": file_name,
                "registry_status": "uploaded",
                "next_review": "Classify issuer, fiscal year, document type, and include/exclude status.",
            }
        )
    return pd.DataFrame(rows)


def data_confirmation_export() -> pd.DataFrame:
    """Flat process export for docs, reports, or future page downloads."""
    sections = [
        ("human_workflow", HUMAN_WORKFLOW_STEPS),
        ("file_registry_template", SP_LOCAL_GOV_FILE_REGISTRY),
        ("field_checklist", SP_LOCAL_GOV_FIELD_CHECKLIST),
        ("approval_rules", APPROVAL_STATUS_RULES),
    ]
    rows: list[dict[str, Any]] = []
    for section, items in sections:
        for item in items:
            rows.append({"section": section, **item})
    return pd.DataFrame(rows)


def _render_human_workflow_cards() -> None:
    for item in HUMAN_WORKFLOW_STEPS:
        with st.container(border=True):
            st.markdown(f"**{item['step']}**")
            st.markdown(f"**Human action:** {item['human_action']}")
            st.markdown(f"**System action:** {item['system_action']}")
            st.markdown(f"**Decision output:** {item['decision_output']}")


def render_data_confirmation_workflow(methodology_id: str) -> None:
    st.caption("Source QA keeps workbook values, independent documents, AI review, and human approval separate.")

    tabs = st.tabs(["Human workflow", "File registry", "Field checklist", "Approval rules"])
    with tabs[0]:
        _render_human_workflow_cards()
        with st.expander("View workflow as table", expanded=False):
            st.dataframe(
                clean_for_display(_frame(HUMAN_WORKFLOW_STEPS)),
                width="stretch",
                hide_index=True,
            )
    with tabs[1]:
        current_registry = _current_source_registry()
        if not current_registry.empty:
            st.write("Current session uploads")
            st.dataframe(clean_for_display(current_registry), width="stretch", hide_index=True)
        st.write("West Sacramento pilot registry")
        st.dataframe(
            clean_for_display(_frame(SP_LOCAL_GOV_FILE_REGISTRY)),
            width="stretch",
            hide_index=True,
        )
    with tabs[2]:
        if methodology_id == "sp_local_gov_k12":
            checklist = _frame(SP_LOCAL_GOV_FIELD_CHECKLIST)
        else:
            checklist = pd.DataFrame(
                [
                    {
                        "factor": "All",
                        "field_or_metric": "Methodology fields",
                        "primary_check": "Use source_report gaps and formula_results to identify fields needing independent support.",
                        "preferred_evidence": "Issuer-specific source document, API record, or approved manual input.",
                        "approval_note": "Build a methodology-specific checklist before production use.",
                    }
                ]
            )
        st.dataframe(clean_for_display(checklist), width="stretch", hide_index=True)
    with tabs[3]:
        st.dataframe(
            clean_for_display(_frame(APPROVAL_STATUS_RULES)),
            width="stretch",
            hide_index=True,
        )
        st.download_button(
            "Download data_confirmation_plan.csv",
            data=data_confirmation_export().to_csv(index=False).encode("utf-8"),
            file_name="data_confirmation_plan.csv",
            mime="text/csv",
        )
