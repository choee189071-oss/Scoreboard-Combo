from __future__ import annotations

import re
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


WEST_SACRAMENTO_SOURCE_MAP: List[Dict[str, str]] = [
    {
        "fiscal_year": "2024",
        "document_type": "ACFR",
        "pdf_page": "49",
        "report_page": "27",
        "section": "Balance Sheet - Governmental Funds",
        "confirms": "committed_fund_balance; assigned_fund_balance; unassigned_fund_balance; fund balance",
        "review_focus": "Use governmental funds. Confirm restricted/nonspendable treatment before reserve ratio approval.",
    },
    {
        "fiscal_year": "2024",
        "document_type": "ACFR",
        "pdf_page": "50",
        "report_page": "28",
        "section": "Reconciliation to Statement of Net Position",
        "confirms": "net_pension_liability; net_opeb_liability; long-term liabilities",
        "review_focus": "Use governmental activities values and confirm units are dollars.",
    },
    {
        "fiscal_year": "2024",
        "document_type": "ACFR",
        "pdf_page": "51",
        "report_page": "29",
        "section": "Statement of Revenues, Expenditures, and Changes in Fund Balances",
        "confirms": "governmental_revenue; governmental_expense; operating_transfers; debt_service",
        "review_focus": "Use total governmental funds or agreed analytical fund scope consistently across years.",
    },
    {
        "fiscal_year": "2023",
        "document_type": "ACFR",
        "pdf_page": "49-50",
        "report_page": "28",
        "section": "Balance Sheet - Governmental Funds",
        "confirms": "committed_fund_balance; assigned_fund_balance; unassigned_fund_balance; fund balance",
        "review_focus": "Second observation for three-year reserve calculation.",
    },
    {
        "fiscal_year": "2023",
        "document_type": "ACFR",
        "pdf_page": "52",
        "report_page": "30",
        "section": "Reconciliation to Statement of Net Position",
        "confirms": "net_pension_liability; net_opeb_liability; long-term liabilities",
        "review_focus": "Supports pension and OPEB check; do not mix business-type activities into governmental metrics.",
    },
    {
        "fiscal_year": "2023",
        "document_type": "ACFR",
        "pdf_page": "53-54",
        "report_page": "31-32",
        "section": "Statement of Revenues, Expenditures, and Changes in Fund Balances",
        "confirms": "governmental_revenue; governmental_expense; operating_transfers; debt_service",
        "review_focus": "Second observation for three-year operating-result calculation.",
    },
    {
        "fiscal_year": "2023",
        "document_type": "ACFR",
        "pdf_page": "88+",
        "report_page": "66+",
        "section": "Pension Plans note",
        "confirms": "net_pension_liability; pension cost context",
        "review_focus": "Use as support when reconciliation value needs note-level confirmation.",
    },
    {
        "fiscal_year": "2023",
        "document_type": "ACFR",
        "pdf_page": "108+",
        "report_page": "86+",
        "section": "Debt service schedule / long-term debt note",
        "confirms": "debt service; long-term debt; maturity schedule",
        "review_focus": "Supports debt service flow; may not equal S&P net direct debt adjustments.",
    },
    {
        "fiscal_year": "2022",
        "document_type": "ACFR",
        "pdf_page": "47",
        "report_page": "24",
        "section": "Balance Sheet - Governmental Funds",
        "confirms": "committed_fund_balance; assigned_fund_balance; unassigned_fund_balance; fund balance",
        "review_focus": "Secured PDF; use rendered page evidence if text extraction is blocked.",
    },
    {
        "fiscal_year": "2022",
        "document_type": "ACFR",
        "pdf_page": "48",
        "report_page": "25",
        "section": "Reconciliation to Statement of Net Position",
        "confirms": "net_pension_liability; net_opeb_liability; long-term liabilities",
        "review_focus": "Secured PDF; verify visually against the rendered page.",
    },
    {
        "fiscal_year": "2022",
        "document_type": "ACFR",
        "pdf_page": "49",
        "report_page": "26",
        "section": "Statement of Revenues, Expenditures, and Changes in Fund Balances",
        "confirms": "governmental_revenue; governmental_expense; operating_transfers; debt_service",
        "review_focus": "Third observation for three-year operating-result calculation.",
    },
    {
        "fiscal_year": "2022",
        "document_type": "ACFR",
        "pdf_page": "87-101",
        "report_page": "64-78",
        "section": "Long-term liabilities / pension / OPEB notes",
        "confirms": "net_pension_liability; net_opeb_liability; debt context",
        "review_focus": "Use only cited line items; secured PDF text may be unreliable.",
    },
    {
        "fiscal_year": "2022",
        "document_type": "Debt support",
        "pdf_page": "239-276",
        "report_page": "",
        "section": "West Sacramento Financing Authority refunding packet",
        "confirms": "refunding amount; final maturity; debt service flow; reserve mechanics",
        "review_focus": "Supporting evidence only; not a complete official statement for net direct debt.",
    },
]

FIELD_TOLERANCES: Dict[str, float] = {
    "gdp_per_capita_ratio": 0.005,
    "personal_income_ratio": 0.005,
    "gov_operating_margin_3yr_avg": 0.0025,
    "available_fund_balance_ratio_3yr_avg": 0.0025,
    "fixed_cost_burden_ratio": 0.0025,
    "net_direct_debt_per_capita": 1.0,
    "npl_per_capita": 1.0,
}

APPROVAL_DECISIONS = [
    "Needs review",
    "Accept CreditScope",
    "Accept independent source",
    "Manual override",
    "Exclude source",
]


def _frame(rows: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _split_file_names(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").split(";")
    return [str(item).strip() for item in raw if str(item).strip()]


def _detect_fiscal_year(file_name: str) -> str:
    lower = file_name.lower()
    if "4124" in lower:
        return "2023"
    match = re.search(r"(20[0-9]{2})", lower)
    if match:
        year = match.group(1)
        if year in {"2022", "2023", "2024", "2025"}:
            return year
    return ""


def _classify_file(source_slot: str, file_name: str, issuer_name: str) -> dict[str, Any]:
    lower = file_name.lower()
    issuer_lower = str(issuer_name or "").lower()
    issuer_mismatch = "city of sacramento" in lower or ("sacramento" in lower and "west sacramento" not in lower and "west_sacramento" not in lower)
    if issuer_mismatch and "west sacramento" in issuer_lower:
        include_status = "exclude"
        reason = "Issuer mismatch: document appears to be City of Sacramento, not City of West Sacramento."
    else:
        include_status = "include"
        reason = "Issuer match or needs human confirmation."

    if source_slot == "creditscope":
        doc_type = "CreditScope workbook"
    elif source_slot == "acfr" or "acfr" in lower or "annual comprehensive financial report" in lower:
        doc_type = "ACFR"
    elif source_slot == "os":
        doc_type = "Debt support / official statement"
    elif source_slot == "ipeds":
        doc_type = "IPEDS"
    else:
        doc_type = "Source document"

    return {
        "source_slot": source_slot,
        "file_name": file_name,
        "document_type": doc_type,
        "fiscal_year": _detect_fiscal_year(file_name),
        "include_status": include_status,
        "review_reason": reason,
        "next_review": "Confirm issuer, fiscal year, and whether this document supports current deal fields.",
    }


def _current_source_registry() -> pd.DataFrame:
    uploaded = st.session_state.get("uploaded_sources", {}) or {}
    rows: list[dict[str, Any]] = []
    issuer_name = st.session_state.get("issuer_name", "")
    for source_key, file_names in uploaded.items():
        for file_name in _split_file_names(file_names):
            rows.append(_classify_file(source_key, file_name, issuer_name))
    return pd.DataFrame(rows)


def _source_map_frame(registry: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(registry, pd.DataFrame) or registry.empty:
        return pd.DataFrame(WEST_SACRAMENTO_SOURCE_MAP)
    years = set(registry["fiscal_year"].dropna().astype(str)) - {""}
    doc_types = set(registry["document_type"].dropna().astype(str))
    rows = []
    for item in WEST_SACRAMENTO_SOURCE_MAP:
        if years and item["fiscal_year"] not in years:
            continue
        if item["document_type"] == "Debt support" and not any("Debt" in doc for doc in doc_types):
            continue
        rows.append(item)
    return pd.DataFrame(rows or WEST_SACRAMENTO_SOURCE_MAP)


def _selected_source_lookup() -> dict[str, str]:
    source_report = st.session_state.get("source_report", pd.DataFrame())
    if not isinstance(source_report, pd.DataFrame) or source_report.empty:
        return {}
    selected = source_report
    if "selected" in selected.columns:
        selected = selected[selected["selected"].astype(bool)].copy()
    lookup: dict[str, str] = {}
    for _, row in selected.iterrows():
        field = str(row.get("field_name", "") or "").strip()
        if not field:
            continue
        source = str(row.get("source_name") or row.get("canonical_source") or "").strip()
        detail = str(row.get("source_cell_or_api") or row.get("source_detail") or "").strip()
        lookup[field] = f"{source}: {detail}" if detail else source
    return lookup


def _formula_value_lookup() -> dict[str, Any]:
    formula_results = st.session_state.get("methodology_formula_results")
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        formula_results = st.session_state.get("formula_results", pd.DataFrame())
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty or "formula_id" not in formula_results.columns:
        return {}
    values: dict[str, Any] = {}
    for _, row in formula_results.iterrows():
        formula_id = str(row.get("formula_id", "") or "").strip()
        if formula_id:
            values[formula_id] = row.get("value")
    return values


def _selected_value(field: str, formula_values: dict[str, Any]) -> Any:
    issuer_data = st.session_state.get("issuer_data", {}) or {}
    if field in formula_values:
        return formula_values[field]
    return issuer_data.get(field)


def _base_check_rows(methodology_id: str) -> list[dict[str, Any]]:
    checklist = SP_LOCAL_GOV_FIELD_CHECKLIST if methodology_id == "sp_local_gov_k12" else []
    formula_values = _formula_value_lookup()
    source_lookup = _selected_source_lookup()
    rows: list[dict[str, Any]] = []
    for item in checklist:
        field = item["field_or_metric"]
        if " / " in field:
            continue
        rows.append(
            {
                "factor": item["factor"],
                "field_or_metric": field,
                "selected_value": _selected_value(field, formula_values),
                "selected_source": source_lookup.get(field, "Formula / current issuer_data"),
                "independent_value": "",
                "independent_source": "",
                "citation": "",
                "review_note": "",
            }
        )
    return rows


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _comparison_status(field: str, selected_value: Any, independent_value: Any) -> tuple[str, Any, Any]:
    selected = _parse_float(selected_value)
    independent = _parse_float(independent_value)
    if independent is None:
        return "missing_source", "", ""
    if selected is None:
        return "source_pending", "", ""
    diff = independent - selected
    abs_diff = abs(diff)
    tolerance = FIELD_TOLERANCES.get(field, max(abs(selected) * 0.01, 0.01))
    if abs_diff <= tolerance:
        status = "matched"
    elif abs_diff <= max(tolerance * 3, abs(selected) * 0.02):
        status = "minor_difference"
    else:
        status = "material_difference"
    return status, diff, abs_diff


def _confirmation_checks(methodology_id: str) -> pd.DataFrame:
    saved = st.session_state.get("data_confirmation_checks")
    base = pd.DataFrame(_base_check_rows(methodology_id))
    if isinstance(saved, pd.DataFrame) and not saved.empty:
        editable_cols = ["field_or_metric", "independent_value", "independent_source", "citation", "review_note"]
        saved_editable = saved[[col for col in editable_cols if col in saved.columns]].copy()
        base = base.drop(columns=[col for col in ["independent_value", "independent_source", "citation", "review_note"] if col in base.columns])
        base = base.merge(saved_editable, on="field_or_metric", how="left")
    for col in ["independent_value", "independent_source", "citation", "review_note"]:
        if col not in base.columns:
            base[col] = ""
        base[col] = base[col].fillna("")
    return base


def _comparison_frame(methodology_id: str) -> pd.DataFrame:
    checks = _confirmation_checks(methodology_id).copy()
    if checks.empty:
        return checks
    rows = []
    for _, row in checks.iterrows():
        status, diff, abs_diff = _comparison_status(
            str(row.get("field_or_metric", "")),
            row.get("selected_value"),
            row.get("independent_value"),
        )
        item = row.to_dict()
        item.update(
            {
                "difference": diff,
                "absolute_difference": abs_diff,
                "qa_status": status,
            }
        )
        rows.append(item)
    return pd.DataFrame(rows)


def _save_confirmation_checks(edited: pd.DataFrame) -> None:
    st.session_state["data_confirmation_checks"] = edited.copy()


def _render_step_1_context() -> None:
    cols = st.columns(3)
    cols[0].metric("Issuer", st.session_state.get("issuer_name") or "Not set")
    cols[1].metric("Methodology", st.session_state.get("methodology_id") or "Not set")
    cols[2].metric("Fiscal Year", st.session_state.get("analysis_year") or "Not set")


def _render_step_2_file_registry() -> pd.DataFrame:
    registry = _current_source_registry()
    if registry.empty:
        st.info("No source files uploaded yet. Use Source Data below to upload CreditScope, ACFR, and debt support files.")
    else:
        st.dataframe(clean_for_display(registry), width="stretch", hide_index=True)
    return registry


def _render_step_3_source_map(registry: pd.DataFrame) -> None:
    source_map = _source_map_frame(registry)
    st.dataframe(clean_for_display(source_map), width="stretch", hide_index=True)


def _render_step_4_candidates(methodology_id: str) -> None:
    checks = _confirmation_checks(methodology_id)
    if checks.empty:
        st.info("This methodology does not have a source-QA checklist yet.")
        return
    editable_cols = [
        "factor",
        "field_or_metric",
        "selected_value",
        "selected_source",
        "independent_value",
        "independent_source",
        "citation",
        "review_note",
    ]
    with st.form("data_confirmation_candidate_form"):
        edited = st.data_editor(
            clean_for_display(checks[[col for col in editable_cols if col in checks.columns]]),
            width="stretch",
            hide_index=True,
            num_rows="fixed",
            key="data_confirmation_candidate_editor",
            column_config={
                "factor": st.column_config.TextColumn("factor", disabled=True),
                "field_or_metric": st.column_config.TextColumn("field_or_metric", disabled=True),
                "selected_value": st.column_config.TextColumn("selected_value", disabled=True),
                "selected_source": st.column_config.TextColumn("selected_source", disabled=True),
                "independent_value": st.column_config.TextColumn("independent_value"),
                "independent_source": st.column_config.TextColumn("independent_source"),
                "citation": st.column_config.TextColumn("citation"),
                "review_note": st.column_config.TextColumn("review_note"),
            },
        )
        if st.form_submit_button("Save independent source candidates", type="primary"):
            _save_confirmation_checks(edited)
            st.success("Independent source candidates saved.")


def _render_step_5_comparison(methodology_id: str) -> pd.DataFrame:
    comparison = _comparison_frame(methodology_id)
    if comparison.empty:
        st.info("No comparison rows available yet.")
        return comparison
    cols = st.columns(4)
    counts = comparison["qa_status"].value_counts().to_dict()
    cols[0].metric("Matched", int(counts.get("matched", 0)))
    cols[1].metric("Minor", int(counts.get("minor_difference", 0)))
    cols[2].metric("Material", int(counts.get("material_difference", 0)))
    cols[3].metric("Missing source", int(counts.get("missing_source", 0)))
    show_cols = [
        "factor",
        "field_or_metric",
        "selected_value",
        "independent_value",
        "difference",
        "absolute_difference",
        "qa_status",
        "citation",
    ]
    st.dataframe(clean_for_display(comparison[[col for col in show_cols if col in comparison.columns]]), width="stretch", hide_index=True)
    st.session_state["data_confirmation_comparison"] = comparison
    return comparison


def _review_prompt(row: pd.Series) -> str:
    return (
        "Review this source QA item using only the cited page/table evidence.\n\n"
        f"Field: {row.get('field_or_metric', '')}\n"
        f"Factor: {row.get('factor', '')}\n"
        f"Current selected value: {row.get('selected_value', '')}\n"
        f"Selected source: {row.get('selected_source', '')}\n"
        f"Independent candidate value: {row.get('independent_value', '')}\n"
        f"Independent source: {row.get('independent_source', '')}\n"
        f"Citation: {row.get('citation', '')}\n"
        f"Current QA status: {row.get('qa_status', '')}\n\n"
        "Confirm whether the independent source line item supports this field, explain any mismatch, "
        "and assign confidence as high / medium / low."
    )


def _render_step_6_ai_review(comparison: pd.DataFrame) -> None:
    if not isinstance(comparison, pd.DataFrame) or comparison.empty:
        st.info("Run comparison first, then generate a bounded AI review prompt.")
        return
    options = comparison["field_or_metric"].dropna().astype(str).tolist()
    selected = st.selectbox("Select field for bounded AI review prompt", options, key="data_confirmation_review_field")
    row = comparison[comparison["field_or_metric"].astype(str).eq(selected)].iloc[0]
    st.text_area("Review prompt", value=_review_prompt(row), height=230)


def _render_step_7_approval(comparison: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(comparison, pd.DataFrame) or comparison.empty:
        st.info("No comparison rows to approve yet.")
        return pd.DataFrame()
    saved = st.session_state.get("data_confirmation_approvals")
    approvals = comparison.copy()
    if isinstance(saved, pd.DataFrame) and not saved.empty:
        saved_cols = ["field_or_metric", "approval_decision", "approved_value", "approval_note"]
        approvals = approvals.merge(saved[[col for col in saved_cols if col in saved.columns]], on="field_or_metric", how="left")
    for col in ["approval_decision", "approved_value", "approval_note"]:
        if col not in approvals.columns:
            approvals[col] = ""
        approvals[col] = approvals[col].fillna("")
    approvals["approval_decision"] = approvals["approval_decision"].replace("", "Needs review")

    with st.form("data_confirmation_approval_form"):
        edited = st.data_editor(
            clean_for_display(
                approvals[
                    [
                        "factor",
                        "field_or_metric",
                        "selected_value",
                        "independent_value",
                        "qa_status",
                        "approval_decision",
                        "approved_value",
                        "approval_note",
                    ]
                ]
            ),
            width="stretch",
            hide_index=True,
            num_rows="fixed",
            key="data_confirmation_approval_editor",
            column_config={
                "factor": st.column_config.TextColumn("factor", disabled=True),
                "field_or_metric": st.column_config.TextColumn("field_or_metric", disabled=True),
                "selected_value": st.column_config.TextColumn("selected_value", disabled=True),
                "independent_value": st.column_config.TextColumn("independent_value", disabled=True),
                "qa_status": st.column_config.TextColumn("qa_status", disabled=True),
                "approval_decision": st.column_config.SelectboxColumn(
                    "approval_decision",
                    options=APPROVAL_DECISIONS,
                ),
                "approved_value": st.column_config.TextColumn("approved_value"),
                "approval_note": st.column_config.TextColumn("approval_note"),
            },
        )
        if st.form_submit_button("Save approvals", type="primary"):
            st.session_state["data_confirmation_approvals"] = edited.copy()
            st.success("Approvals saved.")
            return edited.copy()
    return approvals


def _render_step_8_publish(comparison: pd.DataFrame, approvals: pd.DataFrame) -> None:
    approved = approvals if isinstance(approvals, pd.DataFrame) and not approvals.empty else st.session_state.get("data_confirmation_approvals")
    if not isinstance(approved, pd.DataFrame) or approved.empty:
        st.info("No approvals saved yet.")
    else:
        decisions = approved["approval_decision"].value_counts().to_dict() if "approval_decision" in approved.columns else {}
        cols = st.columns(3)
        cols[0].metric("Approved rows", len(approved[approved.get("approval_decision", "").astype(str).ne("Needs review")]) if "approval_decision" in approved.columns else 0)
        cols[1].metric("Needs review", int(decisions.get("Needs review", 0)))
        cols[2].metric("Material differences", int((comparison.get("qa_status", pd.Series(dtype=str)).astype(str) == "material_difference").sum()) if isinstance(comparison, pd.DataFrame) and not comparison.empty else 0)

    export_frames = []
    if isinstance(comparison, pd.DataFrame) and not comparison.empty:
        export_frames.append(comparison.assign(export_section="comparison"))
    if isinstance(approved, pd.DataFrame) and not approved.empty:
        export_frames.append(approved.assign(export_section="approvals"))
    export_df = pd.concat(export_frames, ignore_index=True, sort=False) if export_frames else pd.DataFrame()
    if not export_df.empty:
        st.download_button(
            "Download source_qa_workpaper.csv",
            data=export_df.to_csv(index=False).encode("utf-8"),
            file_name="source_qa_workpaper.csv",
            mime="text/csv",
        )


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
    methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")
    registry = pd.DataFrame()
    comparison = pd.DataFrame()
    approvals = pd.DataFrame()

    with st.expander("1. Lock deal context", expanded=True):
        _render_step_1_context()

    with st.expander("2. Register files", expanded=True):
        registry = _render_step_2_file_registry()

    with st.expander("3. Locate evidence", expanded=True):
        _render_step_3_source_map(registry)

    with st.expander("4. Extract candidates", expanded=True):
        _render_step_4_candidates(methodology_id)

    with st.expander("5. Compare sources", expanded=True):
        comparison = _render_step_5_comparison(methodology_id)

    with st.expander("6. AI review", expanded=False):
        _render_step_6_ai_review(comparison)

    with st.expander("7. Approve value", expanded=False):
        approvals = _render_step_7_approval(comparison)

    with st.expander("8. Publish outputs", expanded=False):
        _render_step_8_publish(comparison, approvals)


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
