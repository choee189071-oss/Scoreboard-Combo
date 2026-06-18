from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd
import streamlit as st

from engine.calculator_engine import calculate_all_formulas, clean_numeric
from engine.acfr_extraction_engine import (
    extract_all_pdf_pages,
    normalize_pdf_documents,
    rank_pdf_snippets_for_field,
    snippets_to_evidence_text,
)
from engine.data_sourcing_engine import normalize_source_candidates, required_fields_for_methodology
from engine.factor_engine import load_factor_template
from utils.source_confirmation_queue import render_source_confirmation_queue
from utils.manual_scores import manual_score_candidates
from utils.ui_helpers import clean_for_display, selected_source_report


HUMAN_WORKFLOW_STEPS: List[Dict[str, str]] = [
    {
        "step": "1. Data Collection",
        "human_action": "Upload the CreditScope workbook for scoring inputs; upload ACFR/OS files for evidence support; fetch API candidates when needed.",
        "system_action": "Register source candidates without letting evidence files overwrite scoring inputs automatically.",
        "decision_output": "Source inventory",
    },
    {
        "step": "2. Data Completeness Review",
        "human_action": "Resolve Blocking Required fields first; use ACFR support to validate values that already feed scoring.",
        "system_action": "Classify fields as Blocking Required, Validation Support, or Optional / Contextual before status review.",
        "decision_output": "Priority queue",
    },
    {
        "step": "3. Metric Calculation",
        "human_action": "Run formulas only after required fields have acceptable coverage.",
        "system_action": "Calculate metrics from approved system values.",
        "decision_output": "System values",
    },
    {
        "step": "4. Evidence Workbench",
        "human_action": "Add independent evidence only for fields you want to validate or replace, or send rows to AI Evidence Assist.",
        "system_action": "Keep blank evidence as Awaiting Evidence, not as a review problem.",
        "decision_output": "Evidence result",
    },
    {
        "step": "5. AI Evidence Assist",
        "human_action": "Paste located ACFR/API/OS snippets and run AI extraction for selected fields.",
        "system_action": "Return candidate evidence values, citations, confidence, and suggested actions without approving them.",
        "decision_output": "AI evidence candidate",
    },
    {
        "step": "6. Approval Decisions",
        "human_action": "Approve only rows with entered evidence or a real variance/review issue.",
        "system_action": "Carry approved values forward and leave untouched rows out of the review queue.",
        "decision_output": "Approved value",
    },
    {
        "step": "7. Publish Outputs",
        "human_action": "Run rating, report, and presentation exports with trust metrics attached.",
        "system_action": "Carry completeness, evidence coverage, and approval labels into outputs.",
        "decision_output": "Auditable rating",
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
        "status": "Verified",
        "meaning": "System value matches supporting evidence within tolerance.",
        "next_action": "Accept system value.",
    },
    {
        "status": "Supported",
        "meaning": "Evidence supports the value, but minor rounding or presentation differences exist.",
        "next_action": "Accept with review note when rating impact is immaterial.",
    },
    {
        "status": "Needs Review",
        "meaning": "Material difference exists or line-item fit is uncertain.",
        "next_action": "Send to analyst review before publishing.",
    },
    {
        "status": "Awaiting Evidence",
        "meaning": "No independent evidence has been entered yet; this is a work queue state, not an error.",
        "next_action": "Enter ACFR/API/OS support if this field needs validation.",
    },
    {
        "status": "Unverified",
        "meaning": "Legacy label for a row with no supporting evidence.",
        "next_action": "Treat as Awaiting Evidence unless an analyst explicitly sends it to review.",
    },
    {
        "status": "Strong",
        "meaning": "Evidence comes from primary audited/source documentation such as ACFR notes or statements.",
        "next_action": "Use as high-confidence support.",
    },
    {
        "status": "Medium",
        "meaning": "Evidence comes from credible but secondary support such as an OS appendix.",
        "next_action": "Use with citation and scope note.",
    },
    {
        "status": "Weak",
        "meaning": "Evidence is narrative, AI-assisted, manual, or otherwise not directly tied to an official table.",
        "next_action": "Use only when stronger evidence is unavailable.",
    },
]

REQUIREMENT_CLASS_RULES: List[Dict[str, str]] = [
    {
        "priority_class": "Blocking Required",
        "meaning": "The current formula path cannot produce a ready result without this value.",
        "rating_impact": "Blocks formula readiness and must be resolved before relying on the rating.",
        "next_action": "Find a source value, approve a manual value, or mark it unavailable with review note.",
    },
    {
        "priority_class": "Validation Support",
        "meaning": "A direct metric, API value, workbook value, or formula-ready value already feeds scoring.",
        "rating_impact": "Does not block scoring; used to double-check accuracy and strengthen audit support.",
        "next_action": "Use ACFR/API/debt support to verify, support, or replace the system value if needed.",
    },
    {
        "priority_class": "Optional / Contextual",
        "meaning": "The current bond type or methodology path does not require this field.",
        "rating_impact": "Does not affect this rating run unless the deal context changes.",
        "next_action": "Leave for context or future methodology paths; do not prioritize for current scoring.",
    },
]

DATA_CONFIRMATION_LANES: List[Dict[str, str]] = [
    {
        "lane": "A. Rating Path",
        "purpose": "The only required path for producing or refreshing a rating.",
        "user_question": "Can I produce a rating yet?",
        "where_to_work": "Workflow page, Rating Readiness, Blocking Required, and Manual Scores",
    },
    {
        "lane": "B. Evidence Path",
        "purpose": "Optional validation for rating-driving values that already exist.",
        "user_question": "Can I trust the values that produced the rating?",
        "where_to_work": "Rating Inputs, Evidence Workbench, and AI Evidence Assist",
    },
    {
        "lane": "C. Publish Path",
        "purpose": "Approved replacements, review notes, evidence labels, and exportable audit support.",
        "user_question": "What value/source should flow into reports and decks?",
        "where_to_work": "Approval Decisions and Publish Outputs",
    },
]


ACFR_AUTOMATION_PLAN: List[Dict[str, str]] = [
    {
        "stage": "1. Register PDFs",
        "system_behavior": "Store uploaded ACFR files with issuer, fiscal year, document type, and source slot.",
        "human_control": "Confirm that the file belongs to the current issuer and fiscal year.",
    },
    {
        "stage": "2. Locate candidate pages",
        "system_behavior": "Search ACFR text/tables for field-specific targets such as fund balance, governmental revenues, debt service, NPL, and pension/OPEB cost.",
        "human_control": "Review the cited page/table before extraction is trusted.",
    },
    {
        "stage": "3. AI extract structured candidates",
        "system_behavior": "Call an AI model with the PDF page/table context and return a JSON candidate: field, value, unit, page, line item, confidence, and reasoning.",
        "human_control": "AI output is candidate evidence only; it cannot approve or change the rating by itself.",
    },
    {
        "stage": "4. Reconcile against system value",
        "system_behavior": "Compare ACFR candidate vs. current issuer_data/workbook/API value and classify match, minor difference, material variance, or no evidence.",
        "human_control": "Approve system value, replace with evidence value, or send to review.",
    },
    {
        "stage": "5. Apply approved values",
        "system_behavior": "Only approved values write back to issuer_data, then formulas rerun and prior scoreboard output is cleared.",
        "human_control": "Run scoreboard after formula refresh and inspect the audit trail.",
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

DIRECT_METRIC_DEPENDENCIES: Dict[str, list[str]] = {
    "gdp_per_capita_ratio": ["county_gdp", "county_population", "us_gdp", "population_us"],
    "personal_income_ratio": ["personal_income", "county_population", "us_personal_income", "population_us"],
    "gov_operating_margin_3yr_avg": [
        "governmental_revenue",
        "governmental_expense",
        "operating_transfers",
    ],
    "available_fund_balance_ratio_3yr_avg": [
        "committed_fund_balance",
        "assigned_fund_balance",
        "unassigned_fund_balance",
        "reserve_revenue",
    ],
    "fixed_cost_burden_ratio": ["debt_service", "pension_cost", "opeb_cost", "governmental_revenue"],
    "net_direct_debt_per_capita": ["net_direct_debt", "issuer_population"],
    "npl_per_capita": ["net_pension_liability", "issuer_population"],
}

DIRECT_METRIC_FIELDS = set(DIRECT_METRIC_DEPENDENCIES)

FIELD_FACTOR_OVERRIDES: Dict[str, str] = {
    "county_gdp": "Economy",
    "county_population": "Economy",
    "us_gdp": "Economy",
    "population_us": "Economy",
    "personal_income": "Economy",
    "us_personal_income": "Economy",
    "governmental_revenue": "Financial Performance",
    "governmental_expense": "Financial Performance",
    "operating_transfers": "Financial Performance",
    "committed_fund_balance": "Reserves and Liquidity",
    "assigned_fund_balance": "Reserves and Liquidity",
    "unassigned_fund_balance": "Reserves and Liquidity",
    "reserve_revenue": "Reserves and Liquidity",
    "debt_service": "Debt & Liabilities",
    "pension_cost": "Debt & Liabilities",
    "opeb_cost": "Debt & Liabilities",
    "net_direct_debt": "Debt & Liabilities",
    "issuer_population": "Debt & Liabilities",
    "net_pension_liability": "Debt & Liabilities",
    "gdp_per_capita_ratio": "Economy",
    "personal_income_ratio": "Economy",
    "gov_operating_margin_3yr_avg": "Financial Performance",
    "available_fund_balance_ratio_3yr_avg": "Reserves and Liquidity",
    "fixed_cost_burden_ratio": "Debt & Liabilities",
    "net_direct_debt_per_capita": "Debt & Liabilities",
    "npl_per_capita": "Debt & Liabilities",
}

FIELD_EVIDENCE_HINTS: Dict[str, str] = {
    "county_gdp": "BEA county GDP table/API record. Confirm county geography and fiscal year.",
    "us_gdp": "BEA U.S. GDP benchmark for the same year as county GDP.",
    "personal_income": "BEA PCPI/personal income table/API record for the selected geography.",
    "us_personal_income": "BEA U.S. personal income benchmark for the same year.",
    "county_population": "BEA/Census denominator used by the economy ratio; do not mix with issuer population.",
    "population_us": "U.S. population denominator for per-capita benchmark calculations.",
    "governmental_revenue": "ACFR Statement of Revenues, Expenditures, and Changes in Fund Balances.",
    "governmental_expense": "ACFR Statement of Revenues, Expenditures, and Changes in Fund Balances.",
    "operating_transfers": "ACFR other financing sources/uses or transfers line; keep sign convention explicit.",
    "committed_fund_balance": "ACFR governmental funds balance sheet.",
    "assigned_fund_balance": "ACFR governmental funds balance sheet.",
    "unassigned_fund_balance": "ACFR governmental funds balance sheet.",
    "reserve_revenue": "Revenue denominator used for reserves; tie to ACFR governmental revenue scope.",
    "debt_service": "ACFR debt service rows or debt-support schedule.",
    "pension_cost": "ACFR pension note; confirm expense/cost definition before using zero.",
    "opeb_cost": "ACFR OPEB note; confirm expense/cost definition before using zero.",
    "net_direct_debt": "Debt support/OS, ACFR long-term debt note, and any S&P direct debt adjustments.",
    "issuer_population": "Issuer/service-area population; do not silently substitute county population.",
    "net_pension_liability": "ACFR reconciliation to statement of net position plus pension note.",
    "gdp_per_capita_ratio": "CreditScope workbook direct metric; confirm against raw BEA components when needed.",
    "personal_income_ratio": "CreditScope workbook direct metric; confirm against raw BEA components when needed.",
    "fixed_cost_burden_ratio": "CreditScope workbook direct metric; confirm with ACFR debt service, pension/OPEB costs, and revenue.",
    "net_direct_debt_per_capita": "CreditScope workbook direct metric; confirm against debt support and issuer population.",
    "npl_per_capita": "CreditScope workbook direct metric; confirm against ACFR NPL and issuer population.",
}

APPROVAL_DECISIONS = [
    "Accept System Value",
    "Replace With Evidence Value",
    "Send To Review",
]

COMPLETENESS_STATUS_ORDER = {
    "Missing": 0,
    "Needs Review": 1,
    "Verified": 2,
    "Optional": 3,
}
REQUIREMENT_CLASS_ORDER = {
    "Blocking Required": 0,
    "Validation Support": 1,
    "Optional / Contextual": 2,
}

VALIDATION_STATUS_OPTIONS = ["Awaiting Evidence", "Verified", "Supported", "Needs Review", "Unverified"]
EVIDENCE_STRENGTH_OPTIONS = ["Not Entered", "Strong", "Medium", "Weak"]
RECONCILIATION_ACTIONS = ["Await Evidence", "Accept System Value", "Replace With Evidence Value", "Send To Review"]
FIELD_REVIEW_ACTIONS = [
    "Accept current value",
    "Replace with evidence value",
    "Manually override",
    "Mark as unavailable",
    "Send to review later",
]
CONFIDENCE_OPTIONS = ["High", "Medium", "Low"]
AI_EVIDENCE_ACTIONS = ["Accept System Value", "Replace With Evidence Value", "Send To Review", "Await Evidence"]
AI_EVIDENCE_RESULT_COLUMNS = [
    "selected",
    "field_name",
    "factor",
    "system_value",
    "extracted_value",
    "unit",
    "evidence_source",
    "evidence_page",
    "evidence_line_item",
    "citation",
    "confidence",
    "suggested_action",
    "reasoning",
    "extraction_status",
    "model",
    "last_updated",
]
CONFIRMED_INPUT_COLUMNS = [
    "issuer",
    "fiscal_year",
    "field_name",
    "factor",
    "confirmed_value",
    "original_value",
    "confirmed_source",
    "source_type",
    "evidence_note",
    "confidence_score",
    "status",
    "status_reason",
    "last_updated",
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
    elif source_slot in {"os", "debt_report"}:
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


def _project_path(*parts: str) -> Path:
    return Path(__file__).resolve().parents[1].joinpath(*parts)


def _confirmed_inputs_path() -> Path:
    return _project_path("data", "confirmed_inputs.csv")


def _current_issuer() -> str:
    return str(st.session_state.get("issuer_name") or "").strip()


def _current_fiscal_year() -> str:
    return str(st.session_state.get("analysis_year") or "").strip()


def _load_confirmed_inputs_file() -> pd.DataFrame:
    path = _confirmed_inputs_path()
    if not path.exists():
        return pd.DataFrame(columns=CONFIRMED_INPUT_COLUMNS)
    try:
        frame = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=CONFIRMED_INPUT_COLUMNS)
    for col in CONFIRMED_INPUT_COLUMNS:
        if col not in frame.columns:
            frame[col] = ""
    return frame[CONFIRMED_INPUT_COLUMNS + [col for col in frame.columns if col not in CONFIRMED_INPUT_COLUMNS]]


def _confirmed_inputs() -> pd.DataFrame:
    frame = st.session_state.get("confirmed_inputs")
    if isinstance(frame, pd.DataFrame):
        return frame.copy()
    frame = _load_confirmed_inputs_file()
    st.session_state["confirmed_inputs"] = frame.copy()
    return frame


def _confirmed_inputs_for_context() -> pd.DataFrame:
    frame = _confirmed_inputs()
    if frame.empty:
        return frame
    issuer = _current_issuer()
    fiscal_year = _current_fiscal_year()
    mask = pd.Series(True, index=frame.index)
    if issuer:
        mask &= frame["issuer"].fillna("").astype(str).eq(issuer)
    if fiscal_year:
        mask &= frame["fiscal_year"].fillna("").astype(str).eq(fiscal_year)
    out = frame[mask].copy()
    if "last_updated" in out.columns:
        out = out.sort_values("last_updated").drop_duplicates("field_name", keep="last")
    return out.reset_index(drop=True)


def _write_confirmed_inputs(frame: pd.DataFrame) -> None:
    path = _confirmed_inputs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    for col in CONFIRMED_INPUT_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    out = out[CONFIRMED_INPUT_COLUMNS + [col for col in out.columns if col not in CONFIRMED_INPUT_COLUMNS]]
    out.to_csv(path, index=False)
    st.session_state["confirmed_inputs"] = out.copy()


def _confirmed_value_lookup() -> dict[str, dict[str, Any]]:
    confirmed = _confirmed_inputs_for_context()
    if confirmed.empty or "field_name" not in confirmed.columns:
        return {}
    usable = confirmed[confirmed["status"].fillna("").astype(str).eq("Verified")].copy()
    return {
        str(row.get("field_name", "") or "").strip(): row.to_dict()
        for _, row in usable.iterrows()
        if str(row.get("field_name", "") or "").strip() and _has_value(row.get("confirmed_value"))
    }


def confirmed_inputs_to_issuer_data(methodology_id: str | None = None) -> dict[str, Any]:
    """Return confirmed inputs for the current context as formula-ready issuer_data overrides."""
    _ = methodology_id
    return {
        field: clean_numeric(row.get("confirmed_value"))
        for field, row in _confirmed_value_lookup().items()
        if _has_value(row.get("confirmed_value"))
    }


def apply_confirmed_inputs_to_issuer_data(
    issuer_data: dict[str, Any] | None,
    methodology_id: str | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Overlay confirmed_inputs.csv values on top of current issuer_data for formula/rating use."""
    out = dict(issuer_data or {})
    overrides = confirmed_inputs_to_issuer_data(methodology_id)
    for field, value in overrides.items():
        out[field] = value
    confirmed = _confirmed_inputs_for_context()
    if confirmed.empty:
        return out, confirmed
    confirmed = confirmed[confirmed["field_name"].astype(str).isin(overrides.keys())].copy()
    return out, confirmed.reset_index(drop=True)


def _load_data_dictionary() -> pd.DataFrame:
    path = _project_path("config", "data_dictionary.csv")
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _load_source_priority() -> pd.DataFrame:
    path = _project_path("config", "source_priority.csv")
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _dictionary_lookup() -> dict[str, dict[str, Any]]:
    dictionary = _load_data_dictionary()
    if dictionary.empty or "field_name" not in dictionary.columns:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for _, row in dictionary.iterrows():
        field = str(row.get("field_name", "") or "").strip()
        if field:
            out[field] = row.to_dict()
    return out


def _source_priority_lookup(methodology_id: str) -> dict[str, dict[str, Any]]:
    priority = _load_source_priority()
    if priority.empty or "field_name" not in priority.columns:
        return {}
    priority = priority.copy()
    if "methodology_id" not in priority.columns:
        priority["methodology_id"] = "default"
    priority["field_name"] = priority["field_name"].fillna("").astype(str).str.strip()
    priority["methodology_id"] = priority["methodology_id"].fillna("default").astype(str).str.strip()
    exact = priority[priority["methodology_id"].eq(str(methodology_id))]
    default = priority[priority["methodology_id"].isin(["default", "all", ""])]
    merged = pd.concat([default, exact], ignore_index=True).drop_duplicates("field_name", keep="last")
    return {str(row["field_name"]): row.to_dict() for _, row in merged.iterrows() if str(row["field_name"])}


def _candidate_frames() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for state_key in ["uploaded_source_candidates", "api_source_candidates"]:
        source_map = st.session_state.get(state_key, {}) or {}
        if not isinstance(source_map, dict):
            continue
        for source_slot, frame in source_map.items():
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                item = frame.copy()
                item["candidate_bucket"] = source_slot
                frames.append(item)
    for state_key in ["manual_source_candidates", "approved_source_candidates", "source_candidates"]:
        frame = st.session_state.get(state_key)
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            item = frame.copy()
            item["candidate_bucket"] = state_key
            frames.append(item)
    if not frames:
        return pd.DataFrame()
    return normalize_source_candidates(pd.concat(frames, ignore_index=True, sort=False))


def _source_detail(row: pd.Series | dict[str, Any]) -> str:
    source = str(row.get("canonical_source") or row.get("source_name") or "").strip()
    bits = [
        str(row.get("source_file", "") or "").strip(),
        str(row.get("source_table", "") or "").strip(),
        str(row.get("source_cell_or_api", "") or row.get("source_detail", "") or "").strip(),
    ]
    detail = " / ".join(bit for bit in bits if bit)
    return f"{source}: {detail}" if detail and source else source or detail


def _first_candidate(field: str, candidates: pd.DataFrame) -> pd.Series | None:
    if not isinstance(candidates, pd.DataFrame) or candidates.empty or "field_name" not in candidates.columns:
        return None
    rows = candidates[candidates["field_name"].astype(str).eq(field)].copy()
    if rows.empty:
        return None
    if "confidence" in rows.columns:
        rows["_confidence_sort"] = pd.to_numeric(rows["confidence"], errors="coerce").fillna(0)
        rows = rows.sort_values("_confidence_sort", ascending=False)
    return rows.iloc[0]


def _candidate_sources(field: str, candidates: pd.DataFrame) -> str:
    if not isinstance(candidates, pd.DataFrame) or candidates.empty or "field_name" not in candidates.columns:
        return ""
    rows = candidates[candidates["field_name"].astype(str).eq(field)]
    if rows.empty:
        return ""
    source_col = "canonical_source" if "canonical_source" in rows.columns else "source_name"
    sources = rows[source_col].dropna().astype(str).str.strip()
    return "; ".join(dict.fromkeys(source for source in sources if source))


def _candidate_field_names(candidates: pd.DataFrame) -> list[str]:
    fields: set[str] = set()
    if isinstance(candidates, pd.DataFrame) and not candidates.empty and "field_name" in candidates.columns:
        fields.update(str(field).strip() for field in candidates["field_name"].dropna().astype(str) if str(field).strip())
    source_report = st.session_state.get("source_report")
    if isinstance(source_report, pd.DataFrame) and not source_report.empty and "field_name" in source_report.columns:
        fields.update(str(field).strip() for field in source_report["field_name"].dropna().astype(str) if str(field).strip())
    issuer_data = st.session_state.get("issuer_data", {}) or {}
    if isinstance(issuer_data, dict):
        fields.update(str(field).strip() for field in issuer_data if str(field).strip())
    manual_values = st.session_state.get("manual_source_values", {}) or {}
    if isinstance(manual_values, dict):
        fields.update(str(field).strip() for field in manual_values if str(field).strip())
    fields.update(_confirmed_value_lookup().keys())
    return sorted(fields)


def _candidate_value_summary(field: str, candidates: pd.DataFrame) -> dict[str, Any]:
    if not isinstance(candidates, pd.DataFrame) or candidates.empty or "field_name" not in candidates.columns:
        return {"candidate_values": "", "candidate_count": 0, "material_difference": False}
    rows = candidates[candidates["field_name"].fillna("").astype(str).eq(field)].copy()
    if rows.empty or "value" not in rows.columns:
        return {"candidate_values": "", "candidate_count": 0, "material_difference": False}
    values = [value for value in rows["value"].tolist() if _has_value(value)]
    unique_display = list(dict.fromkeys(str(value) for value in values))
    numeric_values = [_parse_float(value) for value in values]
    numeric_values = [value for value in numeric_values if value is not None]
    material_difference = False
    if len(numeric_values) > 1:
        spread = max(numeric_values) - min(numeric_values)
        baseline = max(abs(numeric_values[0]), 1.0)
        tolerance = FIELD_TOLERANCES.get(field, max(baseline * 0.01, 0.01))
        material_difference = spread > tolerance
    return {
        "candidate_values": "; ".join(unique_display[:5]),
        "candidate_count": len(unique_display),
        "material_difference": material_difference,
    }


def _dependency_label(field: str) -> str:
    if field in DIRECT_METRIC_DEPENDENCIES:
        return "; ".join(DIRECT_METRIC_DEPENDENCIES[field])
    parents = [
        metric
        for metric, fields in DIRECT_METRIC_DEPENDENCIES.items()
        if field in fields
    ]
    return "; ".join(parents)


def _required_source_fields(methodology_id: str) -> list[str]:
    try:
        required = list(required_fields_for_methodology(methodology_id))
    except Exception:
        required = []
    if methodology_id == "sp_local_gov_k12":
        for field in SP_LOCAL_GOV_FIELD_CHECKLIST:
            name = field["field_or_metric"]
            if " / " not in name:
                required.append(name)
        for fields in DIRECT_METRIC_DEPENDENCIES.values():
            required.extend(fields)
    return sorted(dict.fromkeys(str(field) for field in required if str(field)))


def _field_stage(field: str) -> str:
    if field in DIRECT_METRIC_FIELDS:
        return "direct_metric_candidate"
    return "raw_source_field"


def _field_factor(field: str, dictionary: dict[str, dict[str, Any]]) -> str:
    if field in FIELD_FACTOR_OVERRIDES:
        return FIELD_FACTOR_OVERRIDES[field]
    row = dictionary.get(field, {})
    return str(row.get("field_category") or "Source").strip()


def _field_notes(field: str, dictionary: dict[str, dict[str, Any]]) -> str:
    if field in FIELD_EVIDENCE_HINTS:
        return FIELD_EVIDENCE_HINTS[field]
    row = dictionary.get(field, {})
    return str(row.get("notes") or "").strip()


def _selected_source_by_field() -> dict[str, pd.Series]:
    selected = selected_source_report(st.session_state.get("source_report", pd.DataFrame()))
    if selected.empty or "field_name" not in selected.columns:
        return {}
    return {
        str(row.get("field_name", "") or "").strip(): row
        for _, row in selected.iterrows()
        if str(row.get("field_name", "") or "").strip()
    }


def _source_value_row(
    field: str,
    selected_by_field: dict[str, pd.Series],
    candidates: pd.DataFrame,
    confirmed_by_field: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if field in confirmed_by_field:
        row = confirmed_by_field[field]
        return {
            "current_source_value": row.get("confirmed_value"),
            "current_source": row.get("confirmed_source") or "confirmed_inputs.csv",
            "source_status": "confirmed_input",
            "source_value_origin": "confirmed_inputs",
            "current_confidence": row.get("confidence_score", ""),
        }
    if field in selected_by_field:
        row = selected_by_field[field]
        return {
            "current_source_value": row.get("value"),
            "current_source": _source_detail(row),
            "source_status": str(row.get("readiness_status") or row.get("source_quality_status") or "").strip(),
            "source_value_origin": "selected_source_report",
            "current_confidence": row.get("confidence", ""),
        }
    candidate = _first_candidate(field, candidates)
    if candidate is not None:
        return {
            "current_source_value": candidate.get("value"),
            "current_source": _source_detail(candidate),
            "source_status": "candidate_available",
            "source_value_origin": "candidate_not_saved",
            "current_confidence": candidate.get("confidence", ""),
        }
    manual_values = st.session_state.get("manual_source_values", {}) or {}
    if field in manual_values and str(manual_values[field]).strip() != "":
        return {
            "current_source_value": manual_values[field],
            "current_source": "Manual: unsaved user input",
            "source_status": "manual_input",
            "source_value_origin": "manual_source_values",
            "current_confidence": "",
        }
    if field in DIRECT_METRIC_FIELDS:
        debug_row = _direct_metric_debug_lookup().get(field, {})
        workbook_value = debug_row.get("final_formula_input", debug_row.get("workbook_value"))
        if _has_value(workbook_value):
            return {
                "current_source_value": workbook_value,
                "current_source": debug_row.get("source_used") or "CreditScope workbook direct metric",
                "source_status": "workbook_direct_metric",
                "source_value_origin": "workbook_direct_metric_debug",
                "current_confidence": "",
            }
        issuer_data = st.session_state.get("issuer_data", {}) or {}
        if field in issuer_data and _has_value(issuer_data[field]):
            return {
                "current_source_value": issuer_data[field],
                "current_source": "issuer_data direct metric",
                "source_status": "issuer_data_direct_metric",
                "source_value_origin": "issuer_data",
                "current_confidence": "",
            }
    return {
        "current_source_value": "",
        "current_source": "",
        "source_status": "missing",
        "source_value_origin": "no_candidate",
        "current_confidence": "",
    }


def _formula_results_frame() -> pd.DataFrame:
    for state_key in ["methodology_formula_results", "formula_results"]:
        frame = st.session_state.get(state_key)
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            return frame.copy()
    return pd.DataFrame()


def _split_missing_fields(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = re.split(r"[;,|]", str(value))
    return [
        str(item).strip().strip("'\"[]()")
        for item in values
        if str(item).strip().strip("'\"[]()")
    ]


def _formula_status_lookup() -> dict[str, dict[str, Any]]:
    frame = _formula_results_frame()
    if frame.empty or "formula_id" not in frame.columns:
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        formula_id = str(row.get("formula_id", "") or "").strip()
        if formula_id:
            lookup[formula_id] = row.to_dict()
    return lookup


def _formula_blocking_missing_fields() -> set[str]:
    frame = _formula_results_frame()
    if frame.empty:
        return set()
    blocking: set[str] = set()
    for _, row in frame.iterrows():
        status = str(row.get("status", "") or "").strip().lower()
        if status not in {"missing", "error"}:
            continue
        formula_id = str(row.get("formula_id", "") or "").strip()
        if formula_id:
            blocking.add(formula_id)
        for field in _split_missing_fields(row.get("missing_fields")):
            blocking.add(field)
    return blocking


def _parent_direct_metrics(field: str) -> list[str]:
    return [
        metric
        for metric, dependencies in DIRECT_METRIC_DEPENDENCIES.items()
        if field in dependencies
    ]


def _has_current_source_value(
    field: str,
    selected_by_field: dict[str, pd.Series],
    candidates: pd.DataFrame,
    confirmed_by_field: dict[str, dict[str, Any]],
) -> bool:
    source_value = _source_value_row(field, selected_by_field, candidates, confirmed_by_field)
    return _has_value(source_value.get("current_source_value"))


def _requirement_classification(
    field: str,
    required_fields: set[str],
    selected_by_field: dict[str, pd.Series],
    candidates: pd.DataFrame,
    confirmed_by_field: dict[str, dict[str, Any]],
    formula_lookup: dict[str, dict[str, Any]],
    blocking_fields: set[str],
) -> tuple[str, str]:
    if field not in required_fields:
        return (
            "Optional / Contextual",
            "Not used by the current methodology path or only present as an extra source candidate.",
        )

    current_value_available = _has_current_source_value(field, selected_by_field, candidates, confirmed_by_field)
    formula_status = str(formula_lookup.get(field, {}).get("status", "") or "").strip().lower()
    if field in blocking_fields and not current_value_available:
        return (
            "Blocking Required",
            "Current formula results show this field is missing or errored.",
        )

    if field in DIRECT_METRIC_FIELDS:
        if current_value_available or formula_status in {"ready", "manual"}:
            return (
                "Validation Support",
                "A direct metric is already available for scoring; ACFR/source work is evidence validation.",
            )
        return (
            "Blocking Required",
            "This direct metric is required by the formula path and has no usable value yet.",
        )

    parents = _parent_direct_metrics(field)
    active_parent_ready = False
    for parent in parents:
        parent_status = str(formula_lookup.get(parent, {}).get("status", "") or "").strip().lower()
        parent_has_value = _has_current_source_value(parent, selected_by_field, candidates, confirmed_by_field)
        if parent_has_value or parent_status in {"ready", "manual"}:
            active_parent_ready = True
            break
    if active_parent_ready:
        return (
            "Validation Support",
            "The related direct metric already feeds scoring; this raw field supports ACFR/API double-check only.",
        )

    if _formula_results_frame().empty:
        if current_value_available:
            return (
                "Validation Support",
                "A source value is present before formula execution; validate evidence but it is not currently missing.",
            )
        return (
            "Blocking Required",
            "No formula run is available yet and this methodology input has no usable source value.",
        )

    if current_value_available:
        return (
            "Validation Support",
            "The formula layer is not missing this field; use it for source validation.",
        )

    return (
        "Validation Support",
        "Not currently blocking formula output; locate evidence only if this source support is needed.",
    )


def _base_check_rows(methodology_id: str) -> list[dict[str, Any]]:
    dictionary = _dictionary_lookup()
    priority = _source_priority_lookup(methodology_id)
    candidates = _candidate_frames()
    selected_by_field = _selected_source_by_field()
    confirmed_by_field = _confirmed_value_lookup()
    required_fields = _required_source_fields(methodology_id)
    required_set = set(required_fields)
    formula_lookup = _formula_status_lookup()
    blocking_fields = _formula_blocking_missing_fields()
    optional_fields = [field for field in _candidate_field_names(candidates) if field not in required_set]
    rows: list[dict[str, Any]] = []
    for field in required_fields + optional_fields:
        source_value = _source_value_row(field, selected_by_field, candidates, confirmed_by_field)
        priority_row = priority.get(field, {})
        candidate_summary = _candidate_value_summary(field, candidates)
        requirement_class, requirement_reason = _requirement_classification(
            field,
            required_set,
            selected_by_field,
            candidates,
            confirmed_by_field,
            formula_lookup,
            blocking_fields,
        )
        rows.append(
            {
                "factor": _field_factor(field, dictionary),
                "field_name": field,
                "requirement_class": requirement_class,
                "requirement_reason": requirement_reason,
                "required_status": "Required" if requirement_class == "Blocking Required" else "Optional",
                "data_stage": _field_stage(field),
                "current_source_value": source_value["current_source_value"],
                "current_source": source_value["current_source"],
                "source_status": source_value["source_status"],
                "source_value_origin": source_value["source_value_origin"],
                "current_confidence": source_value.get("current_confidence", ""),
                "candidate_sources": _candidate_sources(field, candidates),
                "candidate_values": candidate_summary["candidate_values"],
                "candidate_count": candidate_summary["candidate_count"],
                "material_difference": candidate_summary["material_difference"],
                "preferred_sources": str(priority_row.get("priority_sources") or "").strip(),
                "min_confidence": priority_row.get("min_confidence", ""),
                "formula_dependency": _dependency_label(field),
                "evidence_target": _field_notes(field, dictionary),
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


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and pd.isna(value):
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _direct_metric_debug_lookup() -> dict[str, dict[str, Any]]:
    debug = st.session_state.get("workbook_direct_metric_debug", pd.DataFrame())
    if not isinstance(debug, pd.DataFrame) or debug.empty or "field_name" not in debug.columns:
        return {}
    return {
        str(row.get("field_name", "") or "").strip(): row.to_dict()
        for _, row in debug.iterrows()
        if str(row.get("field_name", "") or "").strip()
    }


def _confirmation_checks(methodology_id: str) -> pd.DataFrame:
    saved = st.session_state.get("data_confirmation_checks")
    base = pd.DataFrame(_base_check_rows(methodology_id))
    if isinstance(saved, pd.DataFrame) and not saved.empty:
        saved = saved.copy()
        if "field_name" not in saved.columns and "field_or_metric" in saved.columns:
            saved["field_name"] = saved["field_or_metric"]
        rename_map = {
            "evidence_value": "independent_value",
            "evidence_line_item": "independent_source",
            "evidence_page": "citation",
            "review_notes": "review_note",
        }
        for new_col, old_col in rename_map.items():
            if new_col in saved.columns and old_col not in saved.columns:
                saved[old_col] = saved[new_col]
        editable_cols = [
            "field_name",
            "independent_value",
            "independent_source",
            "citation",
            "review_note",
            "evidence_source",
            "evidence_strength",
            "validation_status",
        ]
        saved_editable = saved[[col for col in editable_cols if col in saved.columns]].copy()
        base = base.drop(
            columns=[
                col
                for col in [
                    "independent_value",
                    "independent_source",
                    "citation",
                    "review_note",
                    "evidence_source",
                    "evidence_strength",
                    "validation_status",
                ]
                if col in base.columns
            ]
        )
        base = base.merge(saved_editable, on="field_name", how="left")
    for col in [
        "independent_value",
        "independent_source",
        "citation",
        "review_note",
        "evidence_source",
        "evidence_strength",
        "validation_status",
    ]:
        if col not in base.columns:
            base[col] = ""
        base[col] = base[col].fillna("")
    return base


def _expected_source(row: pd.Series) -> str:
    for col in ["preferred_sources", "candidate_sources", "current_source"]:
        value = str(row.get(col, "") or "").strip()
        if value:
            return value
    return "Not configured"


def _field_definition(field: str, dictionary: dict[str, dict[str, Any]] | None = None) -> str:
    lookup = dictionary if dictionary is not None else _dictionary_lookup()
    row = lookup.get(field, {})
    note = str(row.get("notes") or "").strip()
    return note or FIELD_EVIDENCE_HINTS.get(field, "") or "No field definition is configured yet."


def _why_field_matters(row: pd.Series) -> str:
    dependency = str(row.get("formula_dependency", "") or "").strip()
    factor = str(row.get("factor", "") or "rating factor").strip()
    if dependency:
        return f"Feeds {dependency} in the {factor} factor."
    return f"Supports the {factor} factor."


def _suggested_search_terms(field: str, row: pd.Series) -> str:
    terms = [field.replace("_", " ")]
    evidence = str(row.get("evidence_target", "") or "")
    expected = str(row.get("expected_source", "") or row.get("preferred_sources", "") or "")
    if evidence:
        terms.append(evidence.split(".")[0])
    if expected and expected != "Not configured":
        terms.append(expected.replace("|", " "))
    return " | ".join(dict.fromkeys(term for term in terms if term))


def _suggested_document_section(field: str, row: pd.Series) -> str:
    text = " ".join([field, str(row.get("factor", "")), str(row.get("evidence_target", ""))]).lower()
    if any(token in text for token in ["revenue", "expense", "expenditure", "transfer", "operating_margin"]):
        return "ACFR Statement of Revenues, Expenses, and Changes in Net Position"
    if any(token in text for token in ["fund_balance", "reserve", "liquidity"]):
        return "ACFR Balance Sheet / Statistical Section"
    if any(token in text for token in ["pension", "opeb", "liability"]):
        return "Notes to Financial Statements"
    if any(token in text for token in ["debt", "debt_service", "maturity"]):
        return "Outstanding Debt Schedule / Official Statement"
    if any(token in text for token in ["population", "income", "gdp", "economy"]):
        return "Census / BEA external source"
    return "Manual Entry"


def _confidence_value(row: pd.Series) -> float | None:
    return _parse_float(row.get("current_confidence"))


def _min_confidence(row: pd.Series) -> float | None:
    return _parse_float(row.get("min_confidence"))


def _value_format_reason(value: Any) -> str:
    if not _has_value(value):
        return ""
    text = str(value).strip()
    numeric = _parse_float(value)
    if numeric is None:
        return "Value format appears abnormal or non-numeric"
    if numeric < 0:
        return "Negative value requires confirmation"
    if numeric == 0:
        return "Value is zero and requires confirmation"
    if ";" in text:
        return "Multiple period values detected; confirm the intended year or average"
    return ""


def _status_reason(row: pd.Series) -> str:
    requirement_class = str(row.get("requirement_class", "") or "").strip()
    required_status = str(row.get("required_status", "Required") or "Required")
    current_value = row.get("current_source_value")
    source_status = str(row.get("source_status", "") or "")
    if requirement_class == "Optional / Contextual" or required_status == "Optional" and not requirement_class:
        return "Optional/contextual field is not required for the current methodology path."
    if requirement_class == "Validation Support" and not _has_value(current_value):
        return "Validation support field has no independent source value yet; this does not block scoring while the direct/system metric is available."
    if not _has_value(current_value):
        return "Blocking formula input has no current value."
    reasons: list[str] = []
    if bool(row.get("material_difference", False)):
        reasons.append(f"Multiple values detected: {row.get('candidate_values', '')}")
    confidence = _confidence_value(row)
    min_confidence = _min_confidence(row)
    if confidence is not None and min_confidence is not None and confidence < min_confidence:
        reasons.append(f"Source confidence {confidence:g} is below threshold {min_confidence:g}")
    if not str(row.get("preferred_sources", "") or "").strip():
        reasons.append("Expected source is not configured")
    if source_status == "manual_input" and not _has_value(row.get("independent_value")):
        reasons.append("Value is manually entered without evidence")
    if source_status == "candidate_available":
        reasons.append("Value is available as a candidate but has not been selected into issuer_data")
    if source_status in {"source_pending", "needs_review", "scorecard_implied", "missing"}:
        reasons.append(f"Current source status is {source_status}")
    format_reason = _value_format_reason(current_value)
    if format_reason:
        reasons.append(format_reason)
    if reasons:
        return "; ".join(dict.fromkeys(reason for reason in reasons if reason))
    if requirement_class == "Validation Support":
        return "Value is available for scoring; ACFR/API evidence can be used as a validation check."
    return "Value has an acceptable source and no rule-based exceptions were detected."


def _completeness_status(row: pd.Series) -> str:
    requirement_class = str(row.get("requirement_class", "") or "").strip()
    if requirement_class == "Optional / Contextual" or str(row.get("required_status", "Required") or "Required") == "Optional" and not requirement_class:
        return "Optional"
    if requirement_class == "Validation Support" and not _has_value(row.get("current_source_value")):
        return "Needs Review"
    if not _has_value(row.get("current_source_value")):
        return "Missing"
    reason = _status_reason(row)
    if reason not in {
        "Value has an acceptable source and no rule-based exceptions were detected.",
        "Value is available for scoring; ACFR/API evidence can be used as a validation check.",
    }:
        return "Needs Review"
    return "Verified"


def _completeness_frame(methodology_id: str) -> pd.DataFrame:
    checks = _confirmation_checks(methodology_id).copy()
    if checks.empty:
        return checks
    checks["expected_source"] = checks.apply(_expected_source, axis=1)
    checks["suggested_search_terms"] = checks.apply(lambda row: _suggested_search_terms(str(row.get("field_name", "")), row), axis=1)
    checks["suggested_document_section"] = checks.apply(lambda row: _suggested_document_section(str(row.get("field_name", "")), row), axis=1)
    checks["status_reason"] = checks.apply(_status_reason, axis=1)
    checks["current_status"] = checks.apply(_completeness_status, axis=1)
    checks["status_rank"] = checks["current_status"].map(COMPLETENESS_STATUS_ORDER).fillna(9)
    if "requirement_class" not in checks.columns:
        checks["requirement_class"] = checks["required_status"].map(
            {"Required": "Blocking Required", "Optional": "Optional / Contextual"}
        ).fillna("Validation Support")
    checks["requirement_rank"] = checks["requirement_class"].map(REQUIREMENT_CLASS_ORDER).fillna(9)
    checks["priority_rank"] = checks.apply(
        lambda row: 0
        if row.get("requirement_class") == "Blocking Required" and row.get("current_status") == "Missing"
        else 1
        if row.get("requirement_class") == "Blocking Required" and row.get("current_status") == "Needs Review"
        else 2
        if row.get("requirement_class") == "Validation Support" and row.get("current_status") in {"Missing", "Needs Review"}
        else 3,
        axis=1,
    )
    return checks.sort_values(["priority_rank", "requirement_rank", "status_rank", "factor", "field_name"]).reset_index(drop=True)


def _difference_pct(system_value: Any, evidence_value: Any) -> float | None:
    system = _parse_float(system_value)
    evidence = _parse_float(evidence_value)
    if system is None or evidence is None:
        return None
    if system == 0:
        return 0.0 if evidence == 0 else None
    return abs((evidence - system) / system) * 100


def _validation_status(field: str, system_value: Any, evidence_value: Any, saved_status: Any = "") -> str:
    saved = str(saved_status or "").strip()
    if saved in VALIDATION_STATUS_OPTIONS and saved not in {"Unverified", "Awaiting Evidence"}:
        return saved
    if not _has_value(evidence_value):
        return "Awaiting Evidence"
    system = _parse_float(system_value)
    evidence = _parse_float(evidence_value)
    if system is None or evidence is None:
        return "Needs Review"
    abs_diff = abs(evidence - system)
    tolerance = FIELD_TOLERANCES.get(field, max(abs(system) * 0.01, 0.01))
    if abs_diff <= tolerance:
        return "Verified"
    if abs_diff <= max(tolerance * 3, abs(system) * 0.02):
        return "Supported"
    return "Needs Review"


def _confidence_score(label: str) -> float:
    return {"High": 0.95, "Medium": 0.75, "Low": 0.45}.get(str(label), 0.75)


def _evidence_support(row: pd.Series, evidence_note: Any = "", confirmed_value: Any = "") -> tuple[str, str, float]:
    note = str(evidence_note or row.get("review_note", "") or "").strip()
    source = str(row.get("independent_source", "") or row.get("evidence_source", "") or "").strip()
    value = confirmed_value if _has_value(confirmed_value) else row.get("independent_value")
    if not source and not note:
        return "Awaiting Evidence", "No independent evidence has been entered yet.", 0.0
    source_text = " ".join([source, note]).lower()
    field_tokens = [token for token in str(row.get("field_name", "")).lower().split("_") if len(token) > 2]
    keyword_hit = any(token in source_text for token in field_tokens)
    if _has_value(value) and keyword_hit:
        return "Supported", "Evidence note/source contains relevant keywords and a proposed value is present.", 0.9
    if _has_value(value) or source:
        return "Weak Support", "Evidence is present but keyword/value support is incomplete.", 0.6
    return "Unsupported", "Evidence does not support a usable value yet.", 0.2


def _confirmed_row_from_action(
    row: pd.Series,
    action: str,
    confirmed_value: Any,
    source_note: str,
    evidence_note: str,
    confidence_label: str,
) -> dict[str, Any]:
    current_value = row.get("current_source_value")
    if action == "Accept current value":
        final_value = current_value
        source = str(row.get("current_source", "") or source_note or "").strip()
        status = "Verified" if _has_value(final_value) else "Missing"
        reason = "Analyst accepted the current system value." if _has_value(final_value) else "Current value is unavailable."
    elif action in {"Replace with evidence value", "Manually override"}:
        final_value = confirmed_value
        source = source_note or str(row.get("evidence_source", "") or row.get("independent_source", "") or "Manual Entry")
        status = "Verified" if _has_value(final_value) and confidence_label != "Low" else "Needs Review"
        reason = "Analyst confirmed a replacement value." if action == "Replace with evidence value" else "Analyst manually overrode the value."
    elif action == "Mark as unavailable":
        final_value = ""
        source = source_note or "Unavailable"
        status = "Missing"
        reason = "Analyst marked this required value as unavailable."
    else:
        final_value = confirmed_value if _has_value(confirmed_value) else current_value
        source = source_note or str(row.get("current_source", "") or "Review Queue")
        status = "Needs Review"
        reason = "Analyst sent this field to review later."

    evidence_status, evidence_reason, evidence_score = _evidence_support(row, evidence_note, final_value)
    confidence_score = min(_confidence_score(confidence_label), evidence_score if evidence_status != "Unsupported" else _confidence_score(confidence_label))
    if status == "Verified" and evidence_status == "Unsupported" and action != "Accept current value":
        status = "Needs Review"
        reason = f"{reason} Evidence support is currently unsupported."
    return {
        "issuer": _current_issuer(),
        "fiscal_year": _current_fiscal_year(),
        "field_name": str(row.get("field_name", "") or "").strip(),
        "factor": str(row.get("factor", "") or "").strip(),
        "confirmed_value": final_value,
        "original_value": current_value,
        "confirmed_source": source,
        "source_type": _source_name_from_text(source, fallback="Manual"),
        "evidence_note": evidence_note,
        "confidence_score": confidence_score,
        "status": status,
        "status_reason": f"{reason} Evidence Status: {evidence_status}. {evidence_reason}",
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def _save_confirmed_field(row: pd.Series, confirmed_row: dict[str, Any]) -> None:
    existing = _confirmed_inputs()
    field = str(confirmed_row.get("field_name", "") or "").strip()
    issuer = str(confirmed_row.get("issuer", "") or "").strip()
    fiscal_year = str(confirmed_row.get("fiscal_year", "") or "").strip()
    if not existing.empty:
        keep = ~(
            existing["issuer"].fillna("").astype(str).eq(issuer)
            & existing["fiscal_year"].fillna("").astype(str).eq(fiscal_year)
            & existing["field_name"].fillna("").astype(str).eq(field)
        )
        existing = existing[keep].copy()
    updated = pd.concat([existing, pd.DataFrame([confirmed_row])], ignore_index=True, sort=False)
    _write_confirmed_inputs(updated)

    checks = _confirmation_checks(st.session_state.get("methodology_id", "moodys_ccd_go")).copy()
    if not checks.empty and "field_name" in checks.columns:
        mask = checks["field_name"].astype(str).eq(field)
        if mask.any():
            checks.loc[mask, "independent_value"] = confirmed_row.get("confirmed_value", "")
            checks.loc[mask, "independent_source"] = confirmed_row.get("confirmed_source", "")
            checks.loc[mask, "review_note"] = confirmed_row.get("evidence_note", "")
            checks.loc[mask, "evidence_source"] = confirmed_row.get("confirmed_source", "")
        st.session_state["data_confirmation_checks"] = checks
    _apply_confirmed_row_to_workflow_state(confirmed_row)


def _apply_confirmed_row_to_workflow_state(confirmed_row: dict[str, Any]) -> bool:
    status = str(confirmed_row.get("status", "") or "").strip().lower()
    if status != "verified":
        return False
    field = str(confirmed_row.get("field_name", "") or "").strip()
    value = clean_numeric(confirmed_row.get("confirmed_value"))
    if not field or not _has_value(value):
        return False

    issuer_data = dict(st.session_state.get("issuer_data", {}) or {})
    issuer_data[field] = value
    st.session_state["issuer_data"] = issuer_data
    st.session_state["source_saved_needs_formula_run"] = True
    st.session_state["formula_results"] = pd.DataFrame()
    st.session_state["methodology_formula_results"] = pd.DataFrame()
    st.session_state["rating_output"] = None

    source_name = str(confirmed_row.get("source_type", "") or "").strip() or "Manual"
    source_label = str(confirmed_row.get("confirmed_source", "") or "").strip()
    candidate = normalize_source_candidates(
        [
            {
                "field_name": field,
                "value": value,
                "source_name": source_name,
                "source_type": "Manual" if source_name == "Manual" else "Document",
                "source_detail": "review_adjust_confirmed_input",
                "confidence": confirmed_row.get("confidence_score", 0.75),
                "source_file": "",
                "source_table": "",
                "source_cell_or_api": source_label,
                "source_label": source_label,
                "candidate_status": "ready",
                "notes": confirmed_row.get("evidence_note", ""),
            }
        ]
    )
    existing = st.session_state.get("approved_source_candidates")
    if isinstance(existing, pd.DataFrame) and not existing.empty:
        existing = existing[existing["field_name"].astype(str).ne(field)].copy()
        candidate = normalize_source_candidates(pd.concat([existing, candidate], ignore_index=True, sort=False))
    st.session_state["approved_source_candidates"] = candidate
    return True


def _evidence_source_label(row: pd.Series) -> str:
    explicit = str(row.get("evidence_source", "") or "").strip()
    if explicit:
        return explicit
    source_text = " ".join(
        str(row.get(col, "") or "")
        for col in ["independent_source", "citation", "evidence_target", "preferred_sources"]
    ).lower()
    if "acfr" in source_text or "audit" in source_text:
        return "ACFR"
    if "os" in source_text or "official statement" in source_text or "debt" in source_text:
        return "Official Statement"
    if "bea" in source_text:
        return "BEA"
    if "census" in source_text or "acs" in source_text:
        return "Census / ACS"
    return ""


def _evidence_strength(row: pd.Series) -> str:
    explicit = str(row.get("evidence_strength", "") or "").strip()
    if explicit in EVIDENCE_STRENGTH_OPTIONS:
        return explicit
    if not _has_evidence_entered(row):
        return "Not Entered"
    evidence_source = _evidence_source_label(row).lower()
    line_item = str(row.get("independent_source", "") or "").lower()
    if "acfr" in evidence_source or "note" in line_item or "statement" in line_item:
        return "Strong"
    if "official statement" in evidence_source or "appendix" in line_item:
        return "Medium"
    return "Weak"


def _evidence_relevant(row: pd.Series) -> bool:
    if not _has_value(row.get("current_source_value")):
        return False
    requirement_class = str(row.get("requirement_class", "") or "").strip()
    if requirement_class == "Optional / Contextual":
        return False
    field = str(row.get("field_name", "") or "")
    if _has_evidence_entered(row):
        return True
    if field in DIRECT_METRIC_FIELDS:
        return True
    if requirement_class == "Blocking Required":
        return True
    return not _parent_direct_metrics(field)


def _has_evidence_entered(row: pd.Series | dict[str, Any]) -> bool:
    evidence_cols = [
        "independent_value",
        "independent_source",
        "evidence_value",
        "evidence_source",
        "evidence_page",
        "evidence_line_item",
        "review_note",
        "review_notes",
        "citation",
    ]
    return any(_has_value(row.get(col)) for col in evidence_cols)


def _evidence_role(row: pd.Series) -> str:
    field = str(row.get("field_name", "") or "")
    if str(row.get("data_stage", "") or "") == "direct_metric_candidate":
        return "Scoring metric"
    if str(row.get("requirement_class", "") or "") == "Blocking Required":
        return "Blocking input"
    if not _parent_direct_metrics(field):
        return "Formula input"
    return "Raw support field"


def _evidence_validation_frame(methodology_id: str) -> pd.DataFrame:
    checks = _confirmation_checks(methodology_id).copy()
    if checks.empty:
        return checks
    checks = checks[checks.apply(_evidence_relevant, axis=1)].copy()
    if checks.empty:
        return checks
    checks["field_name"] = checks["field_name"].astype(str)
    checks["priority_class"] = checks.get("requirement_class", "")
    checks["evidence_role"] = checks.apply(_evidence_role, axis=1)
    checks["system_value"] = checks["current_source_value"]
    checks["system_source"] = checks["current_source"]
    checks["evidence_source"] = checks.apply(_evidence_source_label, axis=1)
    checks["evidence_page"] = checks["citation"]
    checks["evidence_line_item"] = checks["independent_source"]
    checks["evidence_value"] = checks["independent_value"]
    checks["difference_pct"] = checks.apply(
        lambda row: _difference_pct(row.get("system_value"), row.get("evidence_value")),
        axis=1,
    )
    checks["validation_status"] = checks.apply(
        lambda row: _validation_status(
            str(row.get("field_name", "")),
            row.get("system_value"),
            row.get("evidence_value"),
            row.get("validation_status", ""),
        ),
        axis=1,
    )
    checks["evidence_strength"] = checks.apply(_evidence_strength, axis=1)
    checks["review_notes"] = checks["review_note"]
    support = checks.apply(lambda row: _evidence_support(row, row.get("review_notes"), row.get("evidence_value")), axis=1)
    checks["evidence_status"] = [item[0] for item in support]
    checks["evidence_reason"] = [item[1] for item in support]
    checks["confidence_score"] = [item[2] for item in support]
    status_rank = {"Needs Review": 0, "Awaiting Evidence": 1, "Unverified": 1, "Supported": 2, "Verified": 3}
    checks["validation_rank"] = checks["validation_status"].map(status_rank).fillna(9)
    return checks.sort_values(["validation_rank", "factor", "field_name"]).reset_index(drop=True)


def evidence_confidence_metrics(methodology_id: str | None = None) -> dict[str, Any]:
    methodology = methodology_id or st.session_state.get("methodology_id", "moodys_ccd_go")
    completeness = _completeness_frame(methodology)
    evidence = _evidence_validation_frame(methodology)
    blocking = (
        completeness[completeness["requirement_class"].astype(str).eq("Blocking Required")].copy()
        if isinstance(completeness, pd.DataFrame) and not completeness.empty and "requirement_class" in completeness.columns
        else pd.DataFrame()
    )
    support = (
        completeness[completeness["requirement_class"].astype(str).eq("Validation Support")].copy()
        if isinstance(completeness, pd.DataFrame) and not completeness.empty and "requirement_class" in completeness.columns
        else pd.DataFrame()
    )
    required_count = int(len(blocking))
    missing_count = (
        int(blocking["current_status"].astype(str).eq("Missing").sum())
        if not blocking.empty and "current_status" in blocking.columns
        else 0
    )
    needs_review_count = (
        int(blocking["current_status"].astype(str).eq("Needs Review").sum())
        if not blocking.empty and "current_status" in blocking.columns
        else 0
    )
    verified_required = (
        int(blocking["current_status"].astype(str).eq("Verified").sum())
        if not blocking.empty and "current_status" in blocking.columns
        else 0
    )
    data_completeness = (verified_required / required_count * 100) if required_count else 100.0
    evidence_count = int(len(evidence)) if isinstance(evidence, pd.DataFrame) else 0
    supported_statuses = {"Verified", "Supported", "Needs Review"}
    evidence_supported = (
        int(evidence["validation_status"].astype(str).isin(supported_statuses).sum())
        if isinstance(evidence, pd.DataFrame) and not evidence.empty and "validation_status" in evidence.columns
        else 0
    )
    verified_count = (
        int(evidence["validation_status"].astype(str).isin({"Verified", "Supported"}).sum())
        if isinstance(evidence, pd.DataFrame) and not evidence.empty and "validation_status" in evidence.columns
        else 0
    )
    awaiting_count = (
        int(evidence["validation_status"].astype(str).isin({"Awaiting Evidence", "Unverified"}).sum())
        if isinstance(evidence, pd.DataFrame) and not evidence.empty and "validation_status" in evidence.columns
        else 0
    )
    variance_count = (
        int(evidence["validation_status"].astype(str).eq("Needs Review").sum())
        if isinstance(evidence, pd.DataFrame) and not evidence.empty and "validation_status" in evidence.columns
        else 0
    )
    evidence_coverage = (evidence_supported / evidence_count * 100) if evidence_count else 0.0
    return {
        "required_fields": required_count,
        "blocking_required_fields": required_count,
        "validation_support_fields": int(len(support)),
        "missing_fields": missing_count,
        "needs_review_fields": needs_review_count,
        "data_completeness_pct": data_completeness,
        "evidence_required_fields": evidence_count,
        "evidence_supported_fields": evidence_supported,
        "evidence_coverage_pct": evidence_coverage,
        "verified_fields": verified_required,
        "evidence_verified_fields": verified_count,
        "evidence_awaiting_fields": awaiting_count,
        "evidence_variance_fields": variance_count,
        "verified_denominator": required_count,
    }


def _manual_score_readiness(methodology_id: str) -> tuple[pd.DataFrame, int, int]:
    try:
        template = load_factor_template(methodology_id, templates_dir="templates")
        candidates = manual_score_candidates(methodology_id, template)
    except Exception:
        candidates = pd.DataFrame(columns=["section", "factor", "metric", "formula_id"])
    if candidates.empty:
        return candidates, 0, 0
    stored = st.session_state.get("manual_scores", {}) or {}
    candidates = candidates.copy()

    def has_score(formula_id: Any) -> bool:
        value = stored.get(str(formula_id), None) if isinstance(stored, dict) else None
        if isinstance(value, dict):
            numeric = value.get("numeric_score")
        else:
            numeric = value
        numeric = pd.to_numeric(numeric, errors="coerce")
        return pd.notna(numeric)

    candidates["manual_score_status"] = candidates["formula_id"].map(lambda fid: "Entered" if has_score(fid) else "Missing")
    missing = int(candidates["manual_score_status"].eq("Missing").sum())
    entered = int(candidates["manual_score_status"].eq("Entered").sum())
    return candidates, missing, entered


def rating_readiness_metrics(methodology_id: str | None = None) -> dict[str, Any]:
    methodology = methodology_id or st.session_state.get("methodology_id", "moodys_ccd_go")
    source_report = st.session_state.get("source_report")
    source_selected = selected_source_report(source_report)
    raw_missing = raw_pending = raw_ready = 0
    if isinstance(source_selected, pd.DataFrame) and not source_selected.empty and "readiness_status" in source_selected.columns:
        raw_counts = source_selected["readiness_status"].fillna("").astype(str).value_counts().to_dict()
        raw_ready = int(raw_counts.get("independent_ready", 0))
        raw_missing = int(raw_counts.get("missing", 0))
        raw_pending = int(raw_counts.get("source_pending", 0) + raw_counts.get("needs_review", 0))

    formula_results = _formula_results_frame()
    formula_ready = formula_missing = formula_manual = 0
    if not formula_results.empty and "status" in formula_results.columns:
        formula_counts = formula_results["status"].fillna("").astype(str).str.lower().value_counts().to_dict()
        formula_ready = int(formula_counts.get("ready", 0))
        formula_manual = int(formula_counts.get("manual", 0))
        formula_missing = int(formula_counts.get("missing", 0) + formula_counts.get("error", 0))

    completeness = _completeness_frame(methodology)
    blocking = (
        completeness[completeness["requirement_class"].astype(str).eq("Blocking Required")].copy()
        if isinstance(completeness, pd.DataFrame) and not completeness.empty and "requirement_class" in completeness.columns
        else pd.DataFrame()
    )
    blocking_missing = (
        int(blocking["current_status"].astype(str).eq("Missing").sum())
        if not blocking.empty and "current_status" in blocking.columns
        else 0
    )
    blocking_review = (
        int(blocking["current_status"].astype(str).eq("Needs Review").sum())
        if not blocking.empty and "current_status" in blocking.columns
        else 0
    )

    manual_rows, manual_missing, manual_entered = _manual_score_readiness(methodology)
    rating_output = st.session_state.get("rating_output")
    rating_result = rating_output.get("rating_result", {}) if isinstance(rating_output, dict) else {}
    rating_label = rating_result.get("indicative_rating") or ""

    has_issuer_data = bool(st.session_state.get("issuer_data") or {})
    formulas_ran = not formula_results.empty
    active_manual_missing = manual_missing if formulas_ran else 0
    active_formula_blockers = formula_missing + blocking_missing if has_issuer_data or formulas_ran else 0
    active_review_warnings = blocking_review if has_issuer_data or formulas_ran else 0
    rating_ready = bool(
        has_issuer_data
        and formulas_ran
        and formula_missing == 0
        and blocking_missing == 0
        and manual_missing == 0
    )
    rating_produced = bool(rating_label)

    if not has_issuer_data:
        stage = "Data Intake"
        next_action = "Upload/fetch source data, then save issuer_data."
    elif not formulas_ran:
        stage = "Formula Calculation"
        next_action = "Run formulas from current issuer_data."
    elif formula_missing or blocking_missing:
        stage = "Blocking Inputs"
        next_action = "Resolve formula missing/error rows and Blocking Required fields with no value."
    elif active_manual_missing:
        stage = "Manual Scores"
        next_action = "Enter manual qualitative scores, then run scoreboard."
    elif not rating_produced:
        stage = "Scoreboard"
        next_action = "Run scoreboard from current formula results."
    else:
        stage = "Evidence Confidence"
        next_action = "Rating is produced. Use Evidence Workbench to validate rating-driving values."

    checklist = pd.DataFrame(
        [
            {
                "track": "Data Intake",
                "check": "Raw source extraction",
                "status": "Ready" if has_issuer_data else "Not Started",
                "blocking": "No",
                "count": f"{raw_ready} ready / {raw_pending} pending / {raw_missing} raw missing",
                "next_action": "Raw missing fields are evidence/support gaps unless they also appear as Blocking Required.",
            },
            {
                "track": "Rating Readiness",
                "check": "Formula inputs",
                "status": "Ready" if formulas_ran and formula_missing == 0 and blocking_missing == 0 else "Needs Work",
                "blocking": "Yes",
                "count": f"{formula_ready} ready / {formula_missing if formulas_ran else 0} missing-error / {blocking_missing if has_issuer_data or formulas_ran else 0} blocking missing / {active_review_warnings} review warnings",
                "next_action": "Run formulas or resolve true missing/error fields. Review warnings move to Evidence Confidence.",
            },
            {
                "track": "Rating Readiness",
                "check": "Manual scores",
                "status": "Ready" if active_manual_missing == 0 else "Needs Entry",
                "blocking": "Yes",
                "count": f"{manual_entered} entered / {active_manual_missing} active missing",
                "next_action": "Fill qualitative scores only after formula results exist.",
            },
            {
                "track": "Rating Readiness",
                "check": "Scoreboard output",
                "status": "Produced" if rating_produced else "Not Run",
                "blocking": "Yes",
                "count": rating_label or "No rating yet",
                "next_action": "Run scoreboard when formulas and manual scores are ready.",
            },
        ]
    )

    evidence_metrics = evidence_confidence_metrics(methodology)
    return {
        "stage": stage,
        "next_action": next_action,
        "rating_ready": rating_ready,
        "rating_produced": rating_produced,
        "rating_label": rating_label,
        "raw_source_missing": raw_missing,
        "raw_source_pending": raw_pending,
        "formula_ready": formula_ready,
        "formula_blocking_missing": active_formula_blockers,
        "blocking_missing": blocking_missing,
        "blocking_review_warnings": active_review_warnings,
        "manual_score_missing": active_manual_missing,
        "manual_score_total_missing": manual_missing,
        "manual_score_entered": manual_entered,
        "evidence_awaiting": evidence_metrics.get("evidence_awaiting_fields", 0),
        "evidence_variance": evidence_metrics.get("evidence_variance_fields", 0),
        "evidence_verified": evidence_metrics.get("evidence_verified_fields", 0),
        "checklist": checklist,
        "manual_rows": manual_rows,
    }


def render_rating_readiness_overview(methodology_id: str | None = None, *, expanded: bool = True) -> dict[str, Any]:
    metrics = rating_readiness_metrics(methodology_id)
    with st.container(border=True):
        st.markdown("**Rating Readiness**")
        st.caption("This is the main operating view: it separates rating blockers from raw source/evidence work.")
        cols = st.columns(5)
        cols[0].metric("Current Stage", metrics["stage"])
        cols[1].metric("Formula Blocking Missing", metrics["formula_blocking_missing"])
        cols[2].metric("Manual Score Missing", metrics["manual_score_missing"])
        cols[3].metric("Evidence Awaiting", metrics["evidence_awaiting"])
        cols[4].metric("Rating", metrics["rating_label"] or ("Ready to Run" if metrics["rating_ready"] else "Not Ready"))
        if metrics["rating_ready"] or metrics["rating_produced"]:
            st.success(metrics["next_action"])
        else:
            st.warning(metrics["next_action"])
        with st.expander("Readiness checklist", expanded=expanded):
            st.dataframe(clean_for_display(metrics["checklist"]), width="stretch", hide_index=True)
        if metrics.get("manual_score_missing", 0) and isinstance(metrics.get("manual_rows"), pd.DataFrame) and not metrics["manual_rows"].empty:
            missing_manual = metrics["manual_rows"][metrics["manual_rows"]["manual_score_status"].astype(str).eq("Missing")]
            if not missing_manual.empty:
                with st.expander("Manual scores still needed", expanded=False):
                    st.dataframe(clean_for_display(missing_manual), width="stretch", hide_index=True)
    return metrics


def _save_confirmation_checks(edited: pd.DataFrame) -> None:
    st.session_state["data_confirmation_checks"] = edited.copy()


def _openai_api_key() -> str:
    for name in ("OPENAI_API_KEY", "openai_api_key"):
        value = os.environ.get(name)
        if value:
            return str(value)
    try:
        for name in ("OPENAI_API_KEY", "openai_api_key"):
            try:
                value = st.secrets.get(name)
            except Exception:
                value = None
            if value:
                return str(value)
    except Exception:
        return ""
    return ""


def _openai_model() -> str:
    for name in ("OPENAI_MODEL", "openai_model"):
        value = os.environ.get(name)
        if value:
            return str(value)
    try:
        for name in ("OPENAI_MODEL", "openai_model"):
            try:
                value = st.secrets.get(name)
            except Exception:
                value = None
            if value:
                return str(value)
    except Exception:
        pass
    return "gpt-4o-mini"


def _json_object_from_text(text: Any) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _ai_evidence_results_frame() -> pd.DataFrame:
    frame = st.session_state.get("ai_evidence_results")
    if isinstance(frame, pd.DataFrame):
        out = frame.copy()
    else:
        out = pd.DataFrame(columns=AI_EVIDENCE_RESULT_COLUMNS)
    for col in AI_EVIDENCE_RESULT_COLUMNS:
        if col not in out.columns:
            out[col] = False if col == "selected" else ""
    return out[AI_EVIDENCE_RESULT_COLUMNS + [col for col in out.columns if col not in AI_EVIDENCE_RESULT_COLUMNS]]


def _upsert_ai_evidence_result(result: dict[str, Any]) -> pd.DataFrame:
    existing = _ai_evidence_results_frame()
    field = str(result.get("field_name", "") or "").strip()
    if field and not existing.empty and "field_name" in existing.columns:
        existing = existing[~existing["field_name"].astype(str).eq(field)].copy()
    updated = pd.concat([existing, pd.DataFrame([result])], ignore_index=True, sort=False)
    st.session_state["ai_evidence_results"] = updated
    return updated


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "selected", "checked"}


def _ai_confidence(value: Any) -> float:
    parsed = _parse_float(value)
    if parsed is None:
        return 0.0
    if parsed > 1 and parsed <= 100:
        parsed = parsed / 100
    return max(0.0, min(1.0, parsed))


def _ai_evidence_strength(confidence: float, citation: Any, line_item: Any) -> str:
    if confidence >= 0.85 and (_has_value(citation) or _has_value(line_item)):
        return "Strong"
    if confidence >= 0.6:
        return "Medium"
    return "Weak"


def _ai_evidence_prompt(row: pd.Series, evidence_text: str) -> list[dict[str, str]]:
    field = str(row.get("field_name", "") or "").strip()
    context = {
        "field_name": field,
        "factor": row.get("factor", ""),
        "priority_class": row.get("priority_class", row.get("requirement_class", "")),
        "evidence_role": row.get("evidence_role", ""),
        "system_value": row.get("system_value", row.get("current_source_value", "")),
        "system_source": row.get("system_source", row.get("current_source", "")),
        "expected_source": row.get("expected_source", row.get("preferred_sources", "")),
        "suggested_document_section": row.get("suggested_document_section", ""),
        "definition_or_hint": row.get("evidence_target", "") or FIELD_EVIDENCE_HINTS.get(field, ""),
    }
    schema = {
        "field_name": field,
        "extracted_value": "number or null",
        "unit": "short unit label",
        "evidence_source": "ACFR, Official Statement, BEA, Census, workbook, or other source label",
        "evidence_page": "page number or blank",
        "evidence_line_item": "line item/table title used",
        "citation": "page/table/line citation from provided text",
        "confidence": "0 to 1",
        "suggested_action": "Accept System Value, Replace With Evidence Value, Send To Review, or Await Evidence",
        "reasoning": "one short explanation grounded only in the provided text",
        "extraction_status": "value_found, no_value_found, ambiguous, or not_applicable",
    }
    user_content = (
        "Current rating data context:\n"
        f"{json.dumps(context, ensure_ascii=False, default=str)}\n\n"
        "Evidence text/snippet to inspect:\n"
        f"{evidence_text[:14000]}\n\n"
        "Return one JSON object matching this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    return [
        {
            "role": "system",
            "content": (
                "You are a municipal credit data verification assistant. "
                "Extract candidate evidence values only from the provided text. "
                "Do not invent numbers. If the provided text does not support the field, return extracted_value null. "
                "Do not change methodology formulas, score thresholds, rating mappings, or final ratings."
            ),
        },
        {"role": "user", "content": user_content},
    ]


def _openai_chat_json(model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    api_key = _openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured in environment variables or Streamlit secrets.")
    try:
        from openai import OpenAI  # type: ignore

        client = OpenAI(api_key=api_key)
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            if "response_format" not in str(exc).lower():
                raise
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
            )
        content = completion.choices[0].message.content
        return _json_object_from_text(content)
    except ImportError:
        import openai  # type: ignore

        openai.api_key = api_key
        completion = openai.ChatCompletion.create(model=model, messages=messages, temperature=0)
        content = completion["choices"][0]["message"]["content"]
        return _json_object_from_text(content)


def _normalize_ai_evidence_result(row: pd.Series, payload: dict[str, Any], model: str) -> dict[str, Any]:
    field = str(row.get("field_name", "") or "").strip()
    extracted_value = payload.get("extracted_value")
    if isinstance(extracted_value, (list, dict)):
        extracted_value = json.dumps(extracted_value, ensure_ascii=False, default=str)
    confidence = _ai_confidence(payload.get("confidence"))
    suggested_action = str(payload.get("suggested_action", "") or "").strip()
    if suggested_action not in AI_EVIDENCE_ACTIONS:
        if not _has_value(extracted_value):
            suggested_action = "Await Evidence"
        else:
            status = _validation_status(field, row.get("system_value"), extracted_value)
            suggested_action = "Accept System Value" if status in {"Verified", "Supported"} else "Send To Review"
    evidence_source = str(payload.get("evidence_source", "") or _evidence_source_label(row) or "AI Evidence Assist").strip()
    citation = str(payload.get("citation", "") or "").strip()
    evidence_page = str(payload.get("evidence_page", "") or "").strip()
    evidence_line_item = str(payload.get("evidence_line_item", "") or "").strip()
    if not citation and evidence_page:
        citation = f"Page {evidence_page}"
    extraction_status = str(payload.get("extraction_status", "") or "").strip()
    if not extraction_status:
        extraction_status = "value_found" if _has_value(extracted_value) else "no_value_found"
    return {
        "selected": bool(_has_value(extracted_value)),
        "field_name": field,
        "factor": row.get("factor", ""),
        "system_value": row.get("system_value", row.get("current_source_value", "")),
        "extracted_value": "" if extracted_value is None else extracted_value,
        "unit": str(payload.get("unit", "") or "").strip(),
        "evidence_source": evidence_source,
        "evidence_page": evidence_page,
        "evidence_line_item": evidence_line_item,
        "citation": citation,
        "confidence": confidence,
        "suggested_action": suggested_action,
        "reasoning": str(payload.get("reasoning", "") or "").strip(),
        "extraction_status": extraction_status,
        "model": model,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def _run_ai_evidence_extraction(row: pd.Series, evidence_text: str, model: str) -> dict[str, Any]:
    if not evidence_text.strip():
        raise RuntimeError("Evidence text is empty. Paste the located ACFR/OS/API snippet before running AI extraction.")
    payload = _openai_chat_json(model, _ai_evidence_prompt(row, evidence_text))
    if not payload:
        raise RuntimeError("The AI response did not contain a usable JSON object.")
    return _normalize_ai_evidence_result(row, payload, model)


def _apply_ai_evidence_results(methodology_id: str, results: pd.DataFrame) -> int:
    if not isinstance(results, pd.DataFrame) or results.empty:
        return 0
    checks = _confirmation_checks(methodology_id).copy()
    if checks.empty or "field_name" not in checks.columns:
        return 0
    applied = 0
    for _, result in results.iterrows():
        if not _truthy(result.get("selected", True)):
            continue
        field = str(result.get("field_name", "") or "").strip()
        extracted_value = result.get("extracted_value")
        if not field or not _has_value(extracted_value):
            continue
        mask = checks["field_name"].astype(str).eq(field)
        if not mask.any():
            continue
        source = str(result.get("evidence_source", "") or "AI Evidence Assist").strip()
        line_item = str(result.get("evidence_line_item", "") or "").strip()
        citation = str(result.get("citation", "") or result.get("evidence_page", "") or "").strip()
        confidence = _ai_confidence(result.get("confidence"))
        source_note = "; ".join(item for item in [source, line_item] if item)
        review_note = str(result.get("reasoning", "") or "").strip()
        if review_note:
            review_note = f"AI Evidence Assist: {review_note}"
        else:
            review_note = "AI Evidence Assist supplied this candidate evidence value."
        current_value = checks.loc[mask, "current_source_value"].iloc[0] if "current_source_value" in checks.columns else ""
        checks.loc[mask, "independent_value"] = extracted_value
        checks.loc[mask, "independent_source"] = source_note or source
        checks.loc[mask, "citation"] = citation
        checks.loc[mask, "review_note"] = review_note
        checks.loc[mask, "evidence_source"] = source
        checks.loc[mask, "evidence_strength"] = _ai_evidence_strength(confidence, citation, line_item)
        checks.loc[mask, "validation_status"] = _validation_status(field, current_value, extracted_value, "")
        applied += 1
    if applied:
        st.session_state["data_confirmation_checks"] = checks
    return applied


def _render_step_1_context() -> None:
    cols = st.columns(3)
    cols[0].metric("Issuer", st.session_state.get("issuer_name") or "Not set")
    cols[1].metric("Methodology", st.session_state.get("methodology_id") or "Not set")
    cols[2].metric("Fiscal Year", st.session_state.get("analysis_year") or "Not set")


def _render_workflow_issuer_data_snapshot() -> None:
    issuer_data = dict(st.session_state.get("issuer_data", {}) or {})
    st.markdown("**Current Workflow issuer_data**")
    if not issuer_data:
        st.info("No issuer_data is saved yet. Start in Workflow > Source Data, then return here to review or replace values.")
        return

    source_report = st.session_state.get("source_report")
    source_lookup: dict[str, str] = {}
    if isinstance(source_report, pd.DataFrame) and not source_report.empty and "field_name" in source_report.columns:
        selected = selected_source_report(source_report)
        if isinstance(selected, pd.DataFrame) and not selected.empty:
            for _, row in selected.iterrows():
                field = str(row.get("field_name", "") or "").strip()
                source_parts = [
                    str(row.get("canonical_source") or row.get("source_name") or "").strip(),
                    str(row.get("source_file") or "").strip(),
                    str(row.get("source_cell_or_api") or row.get("source_table") or "").strip(),
                ]
                if field:
                    source_lookup[field] = ": ".join(part for part in source_parts if part)

    rows = [
        {
            "field_name": field,
            "current_value": value,
            "current_source": source_lookup.get(field, "issuer_data"),
        }
        for field, value in sorted(issuer_data.items())
    ]
    cols = st.columns(3)
    cols[0].metric("Current Inputs", len(rows))
    cols[1].metric("Confirmed Overrides", len(_confirmed_value_lookup()))
    approved = st.session_state.get("approved_source_candidates")
    cols[2].metric("Approved Candidates", len(approved) if isinstance(approved, pd.DataFrame) else 0)
    st.caption(
        "This is the same table used by Workflow formulas. Verified replacements saved below are written back here and require a formula rerun."
    )
    with st.expander("View current issuer_data table", expanded=False):
        st.dataframe(clean_for_display(pd.DataFrame(rows)), width="stretch", hide_index=True)


def _render_step_2_file_registry() -> pd.DataFrame:
    registry = _current_source_registry()
    if registry.empty:
        st.info("No source files uploaded yet. Use Workflow > Source Data to upload CreditScope, ACFR, and debt support files.")
    else:
        st.dataframe(clean_for_display(registry), width="stretch", hide_index=True)
    return registry


def _render_step_3_source_map(registry: pd.DataFrame) -> None:
    source_map = _source_map_frame(registry)
    st.dataframe(clean_for_display(source_map), width="stretch", hide_index=True)


def _queue_display(frame: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "field_name",
        "factor",
        "requirement_class",
        "data_stage",
        "requirement_reason",
        "expected_source",
        "current_status",
        "status_reason",
        "suggested_search_terms",
        "suggested_document_section",
        "current_source_value",
        "current_source",
    ]
    return clean_for_display(frame[[col for col in cols if col in frame.columns]]).rename(
        columns={
            "field_name": "Field",
            "factor": "Factor",
            "requirement_class": "Priority Class",
            "data_stage": "Data Stage",
            "requirement_reason": "Why This Class",
            "expected_source": "Expected Source",
            "current_status": "Current Status",
            "status_reason": "Status Reason",
            "suggested_search_terms": "Suggested Search Terms",
            "suggested_document_section": "Suggested Document Section",
            "current_source_value": "Current Value",
            "current_source": "Current Source",
        }
    )


def _field_level_ai_model() -> str:
    model = str(st.session_state.get("ai_evidence_inline_model") or _openai_model()).strip()
    return model or "gpt-4o-mini"


def _source_note_from_ai_result(result: dict[str, Any]) -> str:
    parts = [
        str(result.get("evidence_source", "") or "").strip(),
        str(result.get("evidence_line_item", "") or "").strip(),
        str(result.get("citation", "") or result.get("evidence_page", "") or "").strip(),
    ]
    return "; ".join(dict.fromkeys(part for part in parts if part))


def _confidence_label_from_ai(result: dict[str, Any]) -> str:
    confidence = _ai_confidence(result.get("confidence"))
    if confidence >= 0.85:
        return "High"
    if confidence >= 0.55:
        return "Medium"
    return "Low"


def _render_inline_ai_prefill(row: pd.Series, clean_field: str, field: str) -> None:
    api_key_available = bool(_openai_api_key())
    model = _field_level_ai_model()
    with st.expander("AI evidence extraction for this field", expanded=False):
        if not api_key_available:
            st.warning("OPENAI_API_KEY is not configured. Paste-based AI extraction is disabled for this field.")
        st.caption(
            "Paste only the relevant ACFR, official statement, API, or workbook excerpt. "
            "AI will prefill the confirmed value/source fields below; it will not approve or apply the value by itself."
        )
        evidence_text = st.text_area(
            "Evidence excerpt for AI",
            value=st.session_state.get(f"field_review_ai_text_{clean_field}", ""),
            height=150,
            key=f"field_review_ai_text_{clean_field}",
            placeholder="Paste the page/table/API record that supports this specific field.",
        )
        disabled = (not api_key_available) or (not model) or (not str(evidence_text or "").strip())
        if st.button("AI extract candidate for this field", key=f"field_review_ai_run_{clean_field}", disabled=disabled):
            try:
                with st.spinner(f"Extracting candidate value for {field}..."):
                    result = _run_ai_evidence_extraction(row, evidence_text, model)
                _upsert_ai_evidence_result(result)
                extracted_value = result.get("extracted_value")
                if _has_value(extracted_value):
                    st.session_state[f"field_review_value_{clean_field}"] = str(extracted_value)
                    st.session_state[f"field_review_source_{clean_field}"] = _source_note_from_ai_result(result)
                    note = str(result.get("reasoning", "") or "").strip()
                    if note:
                        note = f"AI Evidence Assist: {note}"
                    else:
                        note = "AI Evidence Assist extracted this candidate from the pasted evidence."
                    st.session_state[f"field_review_evidence_{clean_field}"] = note
                    st.session_state[f"field_review_confidence_{clean_field}"] = _confidence_label_from_ai(result)
                    st.session_state[f"field_review_action_{clean_field}"] = "Replace with evidence value"
                    st.session_state["data_confirmation_save_notice"] = f"AI prefilled candidate evidence for {field}."
                    st.rerun()
                st.warning("AI did not find a usable value in the pasted excerpt. The result was saved for review.")
            except Exception as exc:
                st.error("AI evidence extraction failed.")
                st.exception(exc)


def _render_field_review_panels(frame: pd.DataFrame, title: str) -> None:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        st.info(f"No {title.lower()} fields need action.")
        return
    dictionary = _dictionary_lookup()
    for _, row in frame.iterrows():
        field = str(row.get("field_name", "") or "").strip()
        if not field:
            continue
        status = str(row.get("current_status", "") or "")
        expander_label = f"{field} - {status}"
        with st.expander(expander_label, expanded=False):
            cols = st.columns(2)
            cols[0].markdown(f"**Field Name**  \n{field}")
            cols[1].markdown(f"**Factor / Category**  \n{row.get('factor', '')}")
            st.markdown(f"**Priority Class**  \n{row.get('requirement_class', '')}")
            st.markdown(f"**Why this priority**  \n{row.get('requirement_reason', '')}")
            st.markdown(f"**Definition**  \n{_field_definition(field, dictionary)}")
            st.markdown(f"**Why this field matters**  \n{_why_field_matters(row)}")
            detail_cols = st.columns(2)
            detail_cols[0].markdown(f"**Expected Source**  \n{row.get('expected_source', '')}")
            detail_cols[1].markdown(f"**Suggested Section**  \n{row.get('suggested_document_section', '')}")
            st.markdown(f"**Current Value**  \n{row.get('current_source_value', '') or 'Missing'}")
            st.markdown(f"**Current Source**  \n{row.get('current_source', '') or 'Not available'}")
            st.markdown(f"**Status Reason**  \n{row.get('status_reason', '')}")
            st.markdown(f"**Suggested Search Terms**  \n{row.get('suggested_search_terms', '')}")

            clean_field = re.sub(r"[^A-Za-z0-9_]+", "_", field)
            default_value = row.get("independent_value") if _has_value(row.get("independent_value")) else row.get("current_source_value")
            action_default = 1 if status == "Missing" else 0
            action_key = f"field_review_action_{clean_field}"
            evidence_key = f"field_review_evidence_{clean_field}"
            value_key = f"field_review_value_{clean_field}"
            source_key = f"field_review_source_{clean_field}"
            confidence_key = f"field_review_confidence_{clean_field}"
            if action_key not in st.session_state:
                st.session_state[action_key] = FIELD_REVIEW_ACTIONS[action_default]
            if evidence_key not in st.session_state:
                st.session_state[evidence_key] = str(row.get("review_note", "") or "")
            if value_key not in st.session_state:
                st.session_state[value_key] = "" if default_value is None else str(default_value)
            if source_key not in st.session_state:
                st.session_state[source_key] = str(row.get("independent_source", "") or row.get("current_source", "") or "")
            if confidence_key not in st.session_state:
                st.session_state[confidence_key] = "Medium"
            _render_inline_ai_prefill(row, clean_field, field)
            with st.form(f"field_review_form_{clean_field}"):
                action = st.selectbox(
                    "Action",
                    FIELD_REVIEW_ACTIONS,
                    key=action_key,
                )
                evidence_note = st.text_area(
                    "Evidence Input Box",
                    key=evidence_key,
                )
                confirmed_value = st.text_input(
                    "Confirmed Value",
                    key=value_key,
                )
                source_note = st.text_input(
                    "Source Note",
                    key=source_key,
                )
                confidence = st.selectbox(
                    "Confidence",
                    CONFIDENCE_OPTIONS,
                    key=confidence_key,
                )
                if st.form_submit_button("Save confirmed input", type="primary"):
                    confirmed_row = _confirmed_row_from_action(
                        row,
                        action,
                        confirmed_value,
                        source_note,
                        evidence_note,
                        confidence,
                    )
                    _save_confirmed_field(row, confirmed_row)
                    st.session_state["data_confirmation_save_notice"] = f"Saved confirmed input for {field}."
                    st.rerun()


def _render_data_completeness_review(methodology_id: str) -> pd.DataFrame:
    completeness = _completeness_frame(methodology_id)
    if completeness.empty:
        st.info("No required-field list is available for this methodology yet.")
        return completeness

    blocking = completeness[completeness["requirement_class"].astype(str).eq("Blocking Required")].copy()
    support = completeness[completeness["requirement_class"].astype(str).eq("Validation Support")].copy()
    optional = completeness[completeness["requirement_class"].astype(str).eq("Optional / Contextual")].copy()
    counts = blocking["current_status"].value_counts().to_dict() if not blocking.empty else {}
    verified = int(counts.get("Verified", 0))
    needs_review = int(counts.get("Needs Review", 0))
    missing = int(counts.get("Missing", 0))
    active_context = bool(st.session_state.get("issuer_data") or {}) or not _formula_results_frame().empty or not _confirmed_inputs_for_context().empty
    active_missing = missing if active_context else 0
    active_review = needs_review if active_context else 0
    completion_rate = (verified / len(blocking) * 100) if active_context and len(blocking) else 100.0
    completion_label = f"{completion_rate:.0f}%" if active_context else "Not started"
    support_to_check = 0
    if not support.empty and "current_status" in support.columns:
        support_to_check = int(support["current_status"].astype(str).isin({"Missing", "Needs Review"}).sum())

    verified_df = completeness[completeness["current_status"].astype(str).eq("Verified")].copy()
    cols = st.columns(5)
    cols[0].metric("Rating Blockers", active_missing)
    cols[1].metric("Blocking Review", active_review)
    cols[2].metric("Blocking Verified", verified)
    cols[3].metric("Rating Inputs to Validate", len(support))
    cols[4].metric("Blocking Completion", completion_label)
    if not active_context:
        st.info("No issuer_data or formula results are saved yet. This section is showing the potential field map, not an active missing queue.")
    st.caption(
        "Rating Blockers are the only fields in this section that stop formulas. "
        "Source readiness missing rows are evidence/support gaps unless they also appear here as Rating Blockers."
    )
    if _openai_api_key():
        if "ai_evidence_inline_model" not in st.session_state:
            st.session_state["ai_evidence_inline_model"] = _openai_model()
        st.text_input(
            "AI evidence model",
            key="ai_evidence_inline_model",
            help="Used by the field-level AI extraction buttons inside the review panels.",
        )
    else:
        st.caption("Field-level AI extraction is available after OPENAI_API_KEY is configured.")

    st.markdown("**Priority Review Queue**")
    st.caption(
        f"Rating-input support rows awaiting validation: {support_to_check}. "
        "Optional / Contextual rows are visible for transparency but are not part of the current rating path."
    )
    tabs = st.tabs(["1. Blocking Required", "2. Rating Inputs", "3. Optional / Contextual", "4. Verified"])
    for tab, label, frame in [
        (tabs[0], "Blocking Required", blocking),
        (tabs[1], "Validation Support", support),
        (tabs[2], "Optional / Contextual", optional),
        (tabs[3], "Verified", verified_df),
    ]:
        with tab:
            if frame.empty:
                st.info(f"No {label.lower()} fields.")
            else:
                st.dataframe(_queue_display(frame), width="stretch", hide_index=True)
            if label in {"Blocking Required", "Validation Support"}:
                action_frame = (
                    frame[frame["current_status"].astype(str).isin({"Missing", "Needs Review"})].copy()
                    if active_context
                    else pd.DataFrame()
                )
                if label == "Validation Support":
                    st.caption(
                        "These fields already have a system value or support a direct metric. "
                        "Use the panels only when you want to verify, replace, or document that value."
                    )
                _render_field_review_panels(action_frame, label)
    return completeness


def _render_metric_calculation_checkpoint() -> None:
    formula_results = st.session_state.get("methodology_formula_results")
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        formula_results = st.session_state.get("formula_results", pd.DataFrame())
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        st.info("No formula results yet. Resolve required fields, then run formulas in the main Workflow page.")
        return
    counts = formula_results["status"].fillna("unknown").astype(str).value_counts().to_dict() if "status" in formula_results.columns else {}
    cols = st.columns(4)
    cols[0].metric("Formula Rows", len(formula_results))
    cols[1].metric("Ready", int(counts.get("ready", 0)))
    cols[2].metric("Manual", int(counts.get("manual", 0)))
    cols[3].metric("Missing/Error", int(counts.get("missing", 0)) + int(counts.get("error", 0)))
    show_cols = ["formula_id", "formula_name", "category", "status", "value", "missing_fields"]
    st.dataframe(
        clean_for_display(formula_results[[col for col in show_cols if col in formula_results.columns]]),
        width="stretch",
        hide_index=True,
    )


def _render_field_level_ai_cards(methodology_id: str, evidence: pd.DataFrame) -> None:
    if not isinstance(evidence, pd.DataFrame) or evidence.empty:
        return
    st.markdown("**Field-level AI evidence search**")
    st.caption(
        "Each rating input below has its own AI extraction card. Paste the located ACFR/API/OS/workbook excerpt; "
        "AI extracts a candidate value and pre-fills the Evidence Workbench for human approval."
    )
    api_key_available = bool(_openai_api_key())
    if not api_key_available:
        st.warning("OPENAI_API_KEY is not configured. Field-level AI cards are visible, but extraction is disabled.")
    model = _field_level_ai_model()
    st.caption(f"AI model: {model}")

    display = evidence.copy()
    if "validation_status" in display.columns:
        status_rank = {"Needs Review": 0, "Awaiting Evidence": 1, "Unverified": 1, "Supported": 2, "Verified": 3}
        display["inline_rank"] = display["validation_status"].map(status_rank).fillna(9)
        display = display.sort_values(["inline_rank", "factor", "field_name"])

    for _, row in display.iterrows():
        field = str(row.get("field_name", "") or "").strip()
        if not field:
            continue
        clean_field = re.sub(r"[^A-Za-z0-9_]+", "_", field)
        status = str(row.get("validation_status", "") or "Awaiting Evidence")
        role = str(row.get("evidence_role", "") or "")
        label = f"{field} | {role} | {status}"
        with st.expander(label, expanded=False):
            cols = st.columns(3)
            cols[0].metric("System Value", row.get("system_value", ""))
            cols[1].metric("Evidence Status", status)
            cols[2].metric("Evidence Role", role)
            st.caption(f"Suggested section: {row.get('suggested_document_section', '') or row.get('evidence_target', '') or 'Not configured'}")
            st.caption(f"System source: {row.get('system_source', '') or 'Not available'}")
            evidence_text = st.text_area(
                "Paste evidence excerpt for this field",
                value=st.session_state.get(f"inline_ai_text_{clean_field}", ""),
                height=160,
                key=f"inline_ai_text_{clean_field}",
                placeholder="Paste only the relevant page/table/API record for this field.",
            )
            run_disabled = (not api_key_available) or (not str(model or "").strip()) or (not str(evidence_text or "").strip())
            if st.button("Run AI extraction and prefill evidence", key=f"inline_ai_run_{clean_field}", disabled=run_disabled):
                try:
                    with st.spinner(f"Running AI extraction for {field}..."):
                        result = _run_ai_evidence_extraction(row, evidence_text, str(model).strip())
                    _upsert_ai_evidence_result(result)
                    applied = _apply_ai_evidence_results(methodology_id, pd.DataFrame([result]))
                    if applied:
                        st.session_state["data_confirmation_save_notice"] = f"AI evidence candidate applied for {field}."
                        st.rerun()
                    st.warning("AI did not find a usable value in the provided excerpt. Candidate was saved for review.")
                except Exception as exc:
                    st.error("AI evidence extraction failed.")
                    st.exception(exc)


def _uploaded_pdf_documents() -> list[Any]:
    return normalize_pdf_documents(st.session_state.get("uploaded_pdf_documents", {}))


def _pdf_documents_summary() -> pd.DataFrame:
    rows = [
        {
            "source_slot": doc.source_slot,
            "source_name": doc.source_name,
            "file_name": doc.file_name,
            "file_size_mb": len(doc.payload) / 1_000_000,
        }
        for doc in _uploaded_pdf_documents()
    ]
    return pd.DataFrame(rows)


def _pdf_docs_signature(docs: list[Any]) -> tuple[tuple[str, str, int], ...]:
    return tuple((doc.source_slot, doc.file_name, len(doc.payload)) for doc in docs)


def _acfr_pdf_pages_frame() -> pd.DataFrame:
    docs = _uploaded_pdf_documents()
    signature = _pdf_docs_signature(docs)
    cache = st.session_state.get("acfr_pdf_pages_cache")
    if isinstance(cache, dict) and cache.get("signature") == signature and isinstance(cache.get("pages"), pd.DataFrame):
        return cache["pages"].copy()
    pages = extract_all_pdf_pages(docs)
    st.session_state["acfr_pdf_pages_cache"] = {"signature": signature, "pages": pages.copy()}
    return pages


def _field_option_label(evidence: pd.DataFrame, idx: int) -> str:
    row = evidence.loc[idx]
    field = str(row.get("field_name", "") or "")
    factor = str(row.get("factor", "") or "")
    value = row.get("system_value", "")
    status = str(row.get("validation_status", "") or "")
    return f"{field} | {factor} | system={value} | {status}"


def _run_acfr_ai_for_row(row: pd.Series, snippets: pd.DataFrame, model: str) -> dict[str, Any]:
    evidence_text = snippets_to_evidence_text(snippets)
    if not evidence_text.strip():
        raise RuntimeError("No ACFR text snippets were located for this field.")
    result = _run_ai_evidence_extraction(row, evidence_text, model)
    if not str(result.get("evidence_source", "") or "").strip():
        result["evidence_source"] = "ACFR Auto Extraction"
    reasoning = str(result.get("reasoning", "") or "").strip()
    result["reasoning"] = f"ACFR Auto Extraction: {reasoning}" if reasoning else "ACFR Auto Extraction located this candidate from uploaded PDF evidence."
    return result


def _render_acfr_auto_extraction(methodology_id: str) -> None:
    evidence = _evidence_validation_frame(methodology_id)
    docs_summary = _pdf_documents_summary()
    st.caption(
        "This tool searches uploaded ACFR/OS PDFs, sends the most relevant page snippets to AI, and writes candidate evidence values. "
        "It does not approve values or change issuer_data."
    )
    if docs_summary.empty:
        st.info("No uploaded PDF evidence is available. Upload ACFR/OS PDFs in Workflow > Source Data first.")
        return
    with st.expander("Uploaded PDF evidence files", expanded=False):
        st.dataframe(clean_for_display(docs_summary), width="stretch", hide_index=True)

    pages = _acfr_pdf_pages_frame()
    if not isinstance(pages, pd.DataFrame) or pages.empty:
        st.warning("No PDF pages could be read from the uploaded evidence files.")
        return
    file_options = sorted(pages["file_name"].dropna().astype(str).unique()) if "file_name" in pages.columns else []
    selected_files = st.multiselect(
        "PDF files to search",
        file_options,
        default=file_options,
        help="Use all three ACFRs for trend checks; narrow to the current-year ACFR for current-year debt or pension checks.",
        key="acfr_auto_selected_pdf_files",
    )
    if selected_files:
        pages = pages[pages["file_name"].astype(str).isin(selected_files)].copy()
    page_counts = pages["extraction_status"].fillna("unknown").astype(str).value_counts().to_dict()
    cols = st.columns(4)
    cols[0].metric("PDF files", len(docs_summary))
    cols[1].metric("PDF pages", len(pages))
    cols[2].metric("Text pages", int(page_counts.get("text_ready", 0)))
    cols[3].metric("Blank/scanned", int(page_counts.get("blank_or_scanned", 0)))
    if int(page_counts.get("parser_missing", 0)):
        st.warning("Local PDF extraction requires pypdf. Add it to requirements and redeploy before using ACFR auto extraction.")
    if "error" in pages.columns:
        errors = pages[pages["error"].fillna("").astype(str).str.strip().ne("")]
        if not errors.empty:
            with st.expander("PDF extraction warnings", expanded=False):
                st.dataframe(clean_for_display(errors[["file_name", "page_number", "extraction_status", "error"]]), width="stretch", hide_index=True)

    if not isinstance(evidence, pd.DataFrame) or evidence.empty:
        st.info("No rating input evidence rows are available yet. Save issuer_data and run formulas first.")
        return

    model = _field_level_ai_model()
    api_key_available = bool(_openai_api_key())
    if not api_key_available:
        st.warning("OPENAI_API_KEY is not configured. Page location works, but AI extraction is disabled.")
    st.caption(f"AI model: {model}")

    options = list(evidence.index)
    selected_idx = st.selectbox(
        "Field to auto-extract from PDFs",
        options,
        format_func=lambda idx: _field_option_label(evidence, idx),
        key="acfr_auto_extract_field_index",
    )
    selected_row = evidence.loc[selected_idx]
    snippets = rank_pdf_snippets_for_field(pages, selected_row, top_n=5)
    if snippets.empty:
        st.warning("No candidate PDF pages matched this field. Try a different field or use manual evidence paste.")
    else:
        st.markdown("**Located candidate pages**")
        display_cols = ["file_name", "page_number", "score", "matched_terms", "snippet"]
        st.dataframe(clean_for_display(snippets[[col for col in display_cols if col in snippets.columns]]), width="stretch", hide_index=True)

    action_cols = st.columns(2)
    if action_cols[0].button("Save located snippets for selected field", disabled=snippets.empty):
        st.session_state["last_acfr_auto_snippets"] = snippets.copy()
        st.success("Located snippets saved in session for review.")
    if action_cols[1].button(
        "Run ACFR AI extraction for selected field",
        type="primary",
        disabled=snippets.empty or not api_key_available or not model,
    ):
        try:
            with st.spinner(f"Running ACFR AI extraction for {selected_row.get('field_name', '')}..."):
                result = _run_acfr_ai_for_row(selected_row, snippets, model)
            _upsert_ai_evidence_result(result)
            applied = _apply_ai_evidence_results(methodology_id, pd.DataFrame([result]))
            if applied:
                st.session_state["data_confirmation_save_notice"] = (
                    f"ACFR AI candidate applied to Evidence Workbench for {result.get('field_name')}."
                )
                st.rerun()
            st.warning("AI did not find a usable value in the located ACFR snippets. Candidate was saved for review.")
        except Exception as exc:
            st.error("ACFR AI extraction failed.")
            st.exception(exc)

    with st.expander("Batch ACFR extraction", expanded=False):
        st.caption("Runs the same locate-and-extract process across awaiting evidence rows. Results still require Approval Decisions.")
        max_fields = st.number_input("Maximum fields this run", min_value=1, max_value=30, value=8, step=1)
        awaiting = evidence[evidence["validation_status"].astype(str).isin({"Awaiting Evidence", "Unverified", "Needs Review"})].copy()
        st.write(f"{len(awaiting)} field(s) are eligible for batch extraction.")
        if st.button("Run ACFR AI extraction for batch", disabled=awaiting.empty or not api_key_available or not model):
            results: list[dict[str, Any]] = []
            no_snippet: list[str] = []
            with st.spinner("Running batch ACFR AI extraction..."):
                for _, row in awaiting.head(int(max_fields)).iterrows():
                    field_snippets = rank_pdf_snippets_for_field(pages, row, top_n=5)
                    if field_snippets.empty:
                        no_snippet.append(str(row.get("field_name", "")))
                        continue
                    try:
                        result = _run_acfr_ai_for_row(row, field_snippets, model)
                        results.append(result)
                    except Exception as exc:
                        results.append(
                            {
                                "selected": False,
                                "field_name": row.get("field_name", ""),
                                "factor": row.get("factor", ""),
                                "system_value": row.get("system_value", ""),
                                "extracted_value": "",
                                "unit": "",
                                "evidence_source": "ACFR Auto Extraction",
                                "evidence_page": "",
                                "evidence_line_item": "",
                                "citation": "",
                                "confidence": 0.0,
                                "suggested_action": "Await Evidence",
                                "reasoning": f"ACFR Auto Extraction failed: {exc}",
                                "extraction_status": "error",
                                "model": model,
                                "last_updated": datetime.now(timezone.utc).isoformat(),
                            }
                        )
            if results:
                result_frame = pd.DataFrame(results)
                for _, result in result_frame.iterrows():
                    _upsert_ai_evidence_result(result.to_dict())
                applied = _apply_ai_evidence_results(methodology_id, result_frame)
                st.session_state["data_confirmation_save_notice"] = (
                    f"ACFR batch extraction created {len(results)} candidate(s); {applied} populated Evidence Workbench."
                )
                st.rerun()
            if no_snippet:
                st.warning(f"No matching PDF snippets found for: {', '.join(no_snippet[:10])}")


def _render_step_4_candidates(methodology_id: str) -> None:
    evidence = _evidence_validation_frame(methodology_id)
    if evidence.empty:
        st.info("No fields are ready for evidence work yet. Resolve blocking fields or save source values first.")
        return
    completeness = _completeness_frame(methodology_id)
    missing_count = (
        int(
            completeness[
                completeness["requirement_class"].astype(str).eq("Blocking Required")
            ]["current_status"].astype(str).eq("Missing").sum()
        )
        if isinstance(completeness, pd.DataFrame)
        and not completeness.empty
        and {"current_status", "requirement_class"}.issubset(completeness.columns)
        else 0
    )
    if missing_count:
        st.warning(f"{missing_count} blocking required fields are still missing. Evidence validation below only covers fields that already have a system value.")
    validation_counts = evidence["validation_status"].value_counts().to_dict()
    ready_for_decision = evidence[evidence["validation_status"].astype(str).isin({"Verified", "Supported", "Needs Review"})].copy()
    st.markdown("**Evidence Workbench**")
    st.caption(
        "This is not the source extraction table. It starts from the value currently feeding the rating path, "
        "then lets you add independent ACFR/API/OS evidence next to it. Empty evidence means no QA has been run yet, not a rating failure."
    )
    cols = st.columns(4)
    cols[0].metric("Evidence Queue", len(evidence))
    cols[1].metric("Awaiting Evidence", int(validation_counts.get("Awaiting Evidence", 0)) + int(validation_counts.get("Unverified", 0)))
    cols[2].metric("Ready For Approval", len(ready_for_decision))
    cols[3].metric("Needs Review", int(validation_counts.get("Needs Review", 0)))
    st.caption(
        "Blank evidence means Awaiting Evidence. It stays outside Approval Decisions until you enter evidence, run AI extraction, "
        "or a variance/review issue is detected."
    )
    editable_cols = [
        "priority_class",
        "evidence_role",
        "factor",
        "field_name",
        "system_value",
        "system_source",
        "evidence_source",
        "evidence_page",
        "evidence_line_item",
        "evidence_value",
        "difference_pct",
        "validation_status",
        "evidence_strength",
        "evidence_status",
        "evidence_reason",
        "confidence_score",
        "review_notes",
    ]
    with st.form("data_confirmation_candidate_form"):
        edited = st.data_editor(
            clean_for_display(evidence[[col for col in editable_cols if col in evidence.columns]]),
            width="stretch",
            hide_index=True,
            num_rows="fixed",
            key="data_confirmation_candidate_editor",
            column_config={
                "factor": st.column_config.TextColumn("Factor", disabled=True),
                "priority_class": st.column_config.TextColumn("Priority Class", disabled=True),
                "evidence_role": st.column_config.TextColumn("Evidence Role", disabled=True),
                "field_name": st.column_config.TextColumn("Field", disabled=True),
                "system_value": st.column_config.TextColumn("System Value", disabled=True),
                "system_source": st.column_config.TextColumn("System Source", disabled=True),
                "evidence_source": st.column_config.TextColumn("Evidence Source"),
                "evidence_page": st.column_config.TextColumn("Evidence Page"),
                "evidence_line_item": st.column_config.TextColumn("Evidence Line Item"),
                "evidence_value": st.column_config.TextColumn("Evidence Value"),
                "difference_pct": st.column_config.NumberColumn("Difference %", disabled=True, format="%.2f"),
                "validation_status": st.column_config.SelectboxColumn(
                    "Evidence Result",
                    options=VALIDATION_STATUS_OPTIONS,
                ),
                "evidence_strength": st.column_config.SelectboxColumn(
                    "Evidence Strength",
                    options=EVIDENCE_STRENGTH_OPTIONS,
                ),
                "evidence_status": st.column_config.TextColumn("Evidence Status", disabled=True),
                "evidence_reason": st.column_config.TextColumn("Evidence Reason", disabled=True),
                "confidence_score": st.column_config.NumberColumn("Confidence Score", disabled=True, format="%.2f"),
                "review_notes": st.column_config.TextColumn("Review Notes"),
            },
        )
        if st.form_submit_button("Save evidence validation", type="primary"):
            _save_confirmation_checks(edited)
            st.success("Evidence validation saved.")
    with st.expander("AI extraction cards for each rating input", expanded=False):
        _render_field_level_ai_cards(methodology_id, evidence)


def _render_ai_evidence_assist(methodology_id: str) -> None:
    evidence = _evidence_validation_frame(methodology_id)
    if not isinstance(evidence, pd.DataFrame) or evidence.empty:
        st.info("No evidence rows are ready for AI assistance yet. Save issuer_data or resolve active blockers first.")
        return

    st.caption(
        "AI reads only the evidence text you provide here and writes candidate evidence fields. "
        "It does not approve values or change the rating by itself."
    )
    api_key_available = bool(_openai_api_key())
    if not api_key_available:
        st.warning("OPENAI_API_KEY is not configured. Add it in Streamlit secrets or environment variables to enable AI extraction.")

    model = st.text_input("OpenAI model", value=_openai_model(), key="ai_evidence_model")
    options = list(evidence.index)

    def option_label(idx: int) -> str:
        row = evidence.loc[idx]
        field = str(row.get("field_name", "") or "")
        factor = str(row.get("factor", "") or "")
        value = row.get("system_value", "")
        status = str(row.get("validation_status", "") or "")
        return f"{field} | {factor} | system={value} | {status}"

    selected_idx = st.selectbox(
        "Field to run",
        options,
        format_func=option_label,
        key="ai_evidence_field_index",
    )
    selected_row = evidence.loc[selected_idx]
    context_cols = st.columns(3)
    context_cols[0].metric("System Value", selected_row.get("system_value", ""))
    context_cols[1].metric("Evidence Status", selected_row.get("validation_status", ""))
    context_cols[2].metric("Evidence Role", selected_row.get("evidence_role", ""))
    target = selected_row.get("suggested_document_section", "") or selected_row.get("evidence_target", "")
    with st.container(border=True):
        st.markdown("**AI extraction packet**")
        packet_cols = st.columns(2)
        packet_cols[0].markdown(f"**Field**  \n{selected_row.get('field_name', '')}")
        packet_cols[1].markdown(f"**Factor**  \n{selected_row.get('factor', '')}")
        packet_cols[0].markdown(f"**System source**  \n{selected_row.get('system_source', '') or 'Not available'}")
        packet_cols[1].markdown(f"**Suggested evidence section**  \n{target or 'Not configured'}")
        st.caption(
            "Paste only the located ACFR/API/OS/workbook excerpt for this field. "
            "The model is instructed to return JSON and to avoid inventing values."
        )
    evidence_text = st.text_area(
        "Paste located ACFR / OS / API / workbook text",
        value=st.session_state.get("ai_evidence_text", ""),
        height=220,
        key="ai_evidence_text",
        placeholder="Paste the table rows, page text, OCR output, or API record that should support this field.",
    )
    run_disabled = (not api_key_available) or (not str(evidence_text or "").strip()) or (not str(model or "").strip())
    if st.button("Run AI extraction for selected field", type="primary", disabled=run_disabled):
        try:
            with st.spinner("Running AI evidence extraction..."):
                result = _run_ai_evidence_extraction(selected_row, evidence_text, model.strip())
            _upsert_ai_evidence_result(result)
            st.success(f"AI evidence candidate saved for {result.get('field_name')}.")
        except Exception as exc:
            st.error("AI evidence extraction failed.")
            st.exception(exc)

    results = _ai_evidence_results_frame()
    if results.empty:
        st.info("No AI evidence candidates have been created yet.")
        return

    st.markdown("**AI evidence candidates**")
    st.caption("Review these rows first. Applying them only pre-fills Evidence Workbench; Approval Decisions still controls final adoption.")
    result_cols = [
        "selected",
        "field_name",
        "system_value",
        "extracted_value",
        "unit",
        "evidence_source",
        "evidence_page",
        "evidence_line_item",
        "citation",
        "confidence",
        "suggested_action",
        "reasoning",
        "extraction_status",
    ]
    edited_results = st.data_editor(
        clean_for_display(results[[col for col in result_cols if col in results.columns]]),
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        key="ai_evidence_results_editor",
        column_config={
            "selected": st.column_config.CheckboxColumn("Apply", default=True),
            "field_name": st.column_config.TextColumn("Field", disabled=True),
            "system_value": st.column_config.TextColumn("System Value", disabled=True),
            "extracted_value": st.column_config.TextColumn("AI Evidence Value"),
            "unit": st.column_config.TextColumn("Unit"),
            "evidence_source": st.column_config.TextColumn("Evidence Source"),
            "evidence_page": st.column_config.TextColumn("Evidence Page"),
            "evidence_line_item": st.column_config.TextColumn("Evidence Line Item"),
            "citation": st.column_config.TextColumn("Citation"),
            "confidence": st.column_config.NumberColumn("AI Confidence", min_value=0.0, max_value=1.0, format="%.2f"),
            "suggested_action": st.column_config.SelectboxColumn("Suggested Action", options=AI_EVIDENCE_ACTIONS),
            "reasoning": st.column_config.TextColumn("AI Reasoning"),
            "extraction_status": st.column_config.TextColumn("Extraction Status", disabled=True),
        },
    )
    if st.button("Apply selected AI suggestions to Evidence Workbench"):
        applied = _apply_ai_evidence_results(methodology_id, edited_results)
        st.session_state["ai_evidence_results"] = edited_results.copy()
        if applied:
            st.success(f"Applied {applied} AI evidence candidate(s). Review them in Evidence Workbench, then approve in Approval Decisions.")
            st.rerun()
        else:
            st.warning("No AI evidence candidates were applied. Select rows with an AI Evidence Value first.")


def _source_name_from_text(value: Any, fallback: str = "ACFR") -> str:
    text = str(value or "").lower()
    if "bea" in text:
        return "BEA"
    if "census" in text or "acs" in text:
        return "CensusACS"
    if "official" in text or "statement" in text or "debt" in text or "os" == text.strip():
        return "OS"
    if "rating" in text or "committee" in text:
        return "RatingReport"
    if "creditscope" in text or "workbook" in text:
        return "CreditScope"
    if "manual" in text or "override" in text:
        return "Manual"
    if "acfr" in text or "audit" in text or not text.strip():
        return fallback
    return fallback


def _approval_candidates(approvals: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(approvals, pd.DataFrame) or approvals.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, row in approvals.iterrows():
        decision = str(row.get("approval_decision", "") or "").strip()
        field = str(row.get("field_name", "") or "").strip()
        if not field or decision in {"Await Evidence", "Send To Review"}:
            continue
        approved_value = row.get("approved_value")
        if approved_value is None or str(approved_value).strip() == "":
            if decision == "Replace With Evidence Value":
                approved_value = row.get("evidence_value", row.get("independent_value"))
            elif decision == "Accept System Value":
                approved_value = row.get("system_value", row.get("current_source_value"))
        if approved_value is None or str(approved_value).strip() == "":
            continue
        system_source = str(row.get("system_source", row.get("current_source", "")) or "").strip()
        evidence_source = str(row.get("evidence_source", "") or "").strip()
        evidence_line_item = str(row.get("evidence_line_item", row.get("independent_source", "")) or "").strip()
        evidence_page = str(row.get("evidence_page", row.get("citation", "")) or "").strip()
        if decision == "Accept System Value":
            source_name = _source_name_from_text(system_source, fallback="CreditScope")
            source_file = system_source
            source_detail = "system_value_approval"
            source_cell_or_api = ""
            confidence = 0.90 if str(row.get("validation_status", "")) in {"Verified", "Supported"} else 0.75
        else:
            source_name = _source_name_from_text(f"{evidence_source} {evidence_line_item}", fallback="ACFR")
            source_file = evidence_source or evidence_line_item
            source_detail = "evidence_value_approval"
            source_cell_or_api = evidence_page
            strength = str(row.get("evidence_strength", "") or "")
            confidence = 0.92 if strength == "Strong" else 0.82 if strength == "Medium" else 0.68
        rows.append(
            {
                "field_name": field,
                "value": approved_value,
                "source_name": source_name,
                "source_type": "Document" if source_name not in {"Manual", "BEA", "CensusACS", "CreditScope"} else "",
                "source_detail": source_detail,
                "confidence": confidence,
                "source_file": source_file,
                "source_cell_or_api": source_cell_or_api,
                "source_label": decision,
                "candidate_status": "ready",
                "notes": str(row.get("approval_note") or row.get("review_note") or "").strip(),
            }
        )
    return normalize_source_candidates(pd.DataFrame(rows)) if rows else pd.DataFrame()


def _methodology_formula_results(methodology_id: str, formula_results: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        return pd.DataFrame()
    try:
        template = load_factor_template(methodology_id, templates_dir="templates")
    except Exception:
        return formula_results.copy()
    if template.empty or "formula_id" not in template.columns or "formula_id" not in formula_results.columns:
        return formula_results.copy()
    ids = set(template["formula_id"].dropna().astype(str))
    return formula_results[formula_results["formula_id"].astype(str).isin(ids)].copy()


def _apply_approved_values_to_issuer_data(methodology_id: str, approvals: pd.DataFrame) -> pd.DataFrame:
    approved_candidates = _approval_candidates(approvals)
    if not isinstance(approved_candidates, pd.DataFrame) or approved_candidates.empty:
        return pd.DataFrame()

    issuer_data = dict(st.session_state.get("issuer_data", {}) or {})
    rows: list[dict[str, Any]] = []
    for _, candidate in approved_candidates.iterrows():
        field = str(candidate.get("field_name", "") or "").strip()
        value = candidate.get("value")
        if not field or not _has_value(value):
            continue
        previous = issuer_data.get(field, "")
        issuer_data[field] = value
        rows.append(
            {
                "field_name": field,
                "previous_value": previous,
                "applied_value": value,
                "applied_source": candidate.get("source_name", "") or candidate.get("source_label", ""),
                "source_detail": candidate.get("source_detail", ""),
            }
        )

    if not rows:
        return pd.DataFrame()

    formula_results = calculate_all_formulas(issuer_data)
    st.session_state["issuer_data"] = issuer_data
    st.session_state["formula_results"] = formula_results
    st.session_state["methodology_formula_results"] = _methodology_formula_results(methodology_id, formula_results)
    st.session_state["rating_output"] = None
    st.session_state["approved_source_candidates"] = approved_candidates
    return pd.DataFrame(rows)


def _render_step_5_reconciliation(methodology_id: str) -> pd.DataFrame:
    evidence = _evidence_validation_frame(methodology_id)
    if not isinstance(evidence, pd.DataFrame) or evidence.empty:
        st.info("No evidence validation rows to approve yet.")
        return pd.DataFrame()
    saved = st.session_state.get("data_confirmation_approvals")
    approvals = evidence.copy()
    if isinstance(saved, pd.DataFrame) and not saved.empty:
        saved = saved.copy()
        if "field_name" not in saved.columns and "field_or_metric" in saved.columns:
            saved["field_name"] = saved["field_or_metric"]
        saved_cols = ["field_name", "approval_decision", "approved_value", "approval_note"]
        approvals = approvals.merge(saved[[col for col in saved_cols if col in saved.columns]], on="field_name", how="left")
    for col in ["approval_decision", "approved_value", "approval_note"]:
        if col not in approvals.columns:
            approvals[col] = ""
        approvals[col] = approvals[col].fillna("")
    approvals["has_evidence"] = approvals.apply(_has_evidence_entered, axis=1)
    approvals["approval_decision"] = approvals.apply(
        lambda row: str(row.get("approval_decision") or "").strip()
        or (
            "Accept System Value"
            if str(row.get("validation_status", "")) in {"Verified", "Supported"}
            else "Send To Review"
            if str(row.get("validation_status", "")) == "Needs Review"
            else "Await Evidence"
        ),
        axis=1,
    )
    approvals["approved_value"] = approvals.apply(
        lambda row: row.get("approved_value")
        if str(row.get("approved_value", "") or "").strip()
        else row.get("system_value")
        if row.get("approval_decision") == "Accept System Value"
        else row.get("evidence_value")
        if row.get("approval_decision") == "Replace With Evidence Value"
        else "",
        axis=1,
    )

    active_approvals = approvals[
        approvals["approval_decision"].astype(str).ne("Await Evidence")
        | approvals["has_evidence"].astype(bool)
        | approvals["validation_status"].astype(str).isin({"Verified", "Supported", "Needs Review"})
    ].copy()
    decisions = active_approvals["approval_decision"].value_counts().to_dict() if not active_approvals.empty else {}
    awaiting_count = int(len(approvals) - len(active_approvals))
    cols = st.columns(4)
    cols[0].metric("Awaiting Evidence", awaiting_count)
    cols[1].metric("Accept System", int(decisions.get("Accept System Value", 0)))
    cols[2].metric("Replace With Evidence", int(decisions.get("Replace With Evidence Value", 0)))
    cols[3].metric("Send To Review", int(decisions.get("Send To Review", 0)))
    st.caption(
        "Approval Decisions only shows rows with evidence entered or a real validation result. "
        "Awaiting Evidence rows are QA backlog, not rating blockers."
    )

    if active_approvals.empty:
        st.info("No approval decisions are needed yet. Add evidence in Evidence Workbench only when you want to validate or replace a system value.")
        return approvals

    with st.form("data_confirmation_approval_form"):
        edited = st.data_editor(
            clean_for_display(
                active_approvals[
                    [
                        "priority_class",
                        "evidence_role",
                        "factor",
                        "field_name",
                        "system_value",
                        "evidence_value",
                        "difference_pct",
                        "validation_status",
                        "evidence_strength",
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
                "priority_class": st.column_config.TextColumn("Priority Class", disabled=True),
                "evidence_role": st.column_config.TextColumn("Evidence Role", disabled=True),
                "factor": st.column_config.TextColumn("Factor", disabled=True),
                "field_name": st.column_config.TextColumn("Field", disabled=True),
                "system_value": st.column_config.TextColumn("System Value", disabled=True),
                "evidence_value": st.column_config.TextColumn("Evidence Value", disabled=True),
                "difference_pct": st.column_config.NumberColumn("Difference %", disabled=True, format="%.2f"),
                "validation_status": st.column_config.TextColumn("Status", disabled=True),
                "evidence_strength": st.column_config.TextColumn("Evidence Strength", disabled=True),
                "approval_decision": st.column_config.SelectboxColumn("Action", options=RECONCILIATION_ACTIONS),
                "approved_value": st.column_config.TextColumn("Approved Value"),
                "approval_note": st.column_config.TextColumn("Approval Note"),
            },
        )
        if st.form_submit_button("Save reconciliation decisions", type="primary"):
            st.session_state["data_confirmation_approvals"] = edited.copy()
            approved_candidates = _approval_candidates(edited)
            st.session_state["approved_source_candidates"] = approved_candidates
            st.success("Reconciliation decisions saved.")
            if not approved_candidates.empty:
                st.caption("Approved values can now be applied directly in Publish Path, or included as source candidates on the next Save issuer_data run.")
            return edited.copy()
    return approvals


def _render_step_6_publish(methodology_id: str, approvals: pd.DataFrame) -> None:
    metrics = evidence_confidence_metrics(methodology_id)
    cols = st.columns(3)
    cols[0].metric("Blocking Completion", f"{metrics['data_completeness_pct']:.0f}%")
    cols[1].metric("Evidence Coverage", f"{metrics['evidence_coverage_pct']:.0f}%")
    cols[2].metric("Blocking Verified", f"{metrics['verified_fields']} / {metrics['verified_denominator']}")

    confirmed = _confirmed_inputs_for_context()
    st.markdown("**Confirmed Output Preview**")
    if confirmed.empty:
        st.info("No confirmed inputs have been saved for this issuer/year yet.")
    else:
        st.caption(f"Rating Engine input source: `{_confirmed_inputs_path()}`")
        st.dataframe(clean_for_display(confirmed), width="stretch", hide_index=True)
        st.download_button(
            "Export Review Report",
            data=confirmed.to_csv(index=False).encode("utf-8"),
            file_name="confirmed_inputs_review_report.csv",
            mime="text/csv",
        )

    action_cols = st.columns(2)
    if action_cols[0].button("Save Confirmed Inputs", type="primary"):
        _write_confirmed_inputs(_confirmed_inputs())
        st.success("confirmed_inputs.csv saved.")
    if action_cols[1].button("Recalculate Status"):
        st.rerun()

    approved = approvals if isinstance(approvals, pd.DataFrame) and not approvals.empty else st.session_state.get("data_confirmation_approvals")
    if not isinstance(approved, pd.DataFrame) or approved.empty:
        st.info("No reconciliation decisions saved yet.")
    else:
        decisions = approved["approval_decision"].value_counts().to_dict() if "approval_decision" in approved.columns else {}
        cols = st.columns(3)
        cols[0].metric("Accepted System", int(decisions.get("Accept System Value", 0)))
        cols[1].metric("Evidence Replacements", int(decisions.get("Replace With Evidence Value", 0)))
        cols[2].metric("Review Queue", int(decisions.get("Send To Review", 0)))
        st.caption(
            "Use this when you want approved values to become the current formula input immediately. "
            "After applying, rerun the scoreboard because rating_output is cleared."
        )
        if st.button("Apply approved values to issuer_data and rerun formulas", type="primary"):
            applied = _apply_approved_values_to_issuer_data(methodology_id, approved)
            if applied.empty:
                st.warning("No approved values were available to apply. Save reconciliation decisions first.")
            else:
                st.session_state["data_confirmation_save_notice"] = f"Applied {len(applied)} approved value(s) and recalculated formulas."
                st.session_state["last_applied_approved_values"] = applied
                st.rerun()

    last_applied = st.session_state.get("last_applied_approved_values")
    if isinstance(last_applied, pd.DataFrame) and not last_applied.empty:
        with st.expander("Last applied values", expanded=False):
            st.dataframe(clean_for_display(last_applied), width="stretch", hide_index=True)

    export_frames = []
    completeness = _completeness_frame(methodology_id)
    evidence = _evidence_validation_frame(methodology_id)
    if isinstance(completeness, pd.DataFrame) and not completeness.empty:
        export_frames.append(completeness.assign(export_section="data_completeness"))
    if isinstance(evidence, pd.DataFrame) and not evidence.empty:
        export_frames.append(evidence.assign(export_section="evidence_validation"))
    if isinstance(approved, pd.DataFrame) and not approved.empty:
        export_frames.append(approved.assign(export_section="reconciliation_approval"))
    export_df = pd.concat(export_frames, ignore_index=True, sort=False) if export_frames else pd.DataFrame()
    if not export_df.empty:
        st.download_button(
            "Download evidence_reconciliation_workpaper.csv",
            data=export_df.to_csv(index=False).encode("utf-8"),
            file_name="evidence_reconciliation_workpaper.csv",
            mime="text/csv",
        )


def data_confirmation_export() -> pd.DataFrame:
    """Flat process export for docs, reports, or future page downloads."""
    sections = [
        ("operating_lanes", DATA_CONFIRMATION_LANES),
        ("human_workflow", HUMAN_WORKFLOW_STEPS),
        ("requirement_class_rules", REQUIREMENT_CLASS_RULES),
        ("file_registry_template", SP_LOCAL_GOV_FILE_REGISTRY),
        ("field_checklist", SP_LOCAL_GOV_FIELD_CHECKLIST),
        ("approval_rules", APPROVAL_STATUS_RULES),
        ("acfr_automation_plan", ACFR_AUTOMATION_PLAN),
    ]
    rows: list[dict[str, Any]] = []
    for section, items in sections:
        for item in items:
            rows.append({"section": section, **item})
    return pd.DataFrame(rows)


def _render_acfr_automation_plan() -> None:
    st.markdown("**ACFR auto-extraction target design**")
    st.caption(
        "The current production-safe pattern is candidate extraction first, human approval second, and issuer_data replacement only after approval."
    )
    st.dataframe(clean_for_display(_frame(ACFR_AUTOMATION_PLAN)), width="stretch", hide_index=True)
    st.info(
        "Recommended API path: use an AI model with PDF/file input or File Search to locate ACFR evidence, "
        "return structured JSON candidates, then route every candidate through Evidence Workbench and Approval Decisions."
    )


def _render_operating_lanes(methodology_id: str) -> None:
    metrics = rating_readiness_metrics(methodology_id)
    completeness = _completeness_frame(methodology_id)
    evidence = _evidence_validation_frame(methodology_id)

    blocking_missing = int(metrics.get("blocking_missing", 0) or 0)
    manual_missing = int(metrics.get("manual_score_missing", 0) or 0)
    evidence_awaiting = int(metrics.get("evidence_awaiting", 0) or 0)
    evidence_variance = int(metrics.get("evidence_variance", 0) or 0)
    evidence_verified = int(metrics.get("evidence_verified", 0) or 0)

    if not isinstance(completeness, pd.DataFrame) or completeness.empty:
        rating_inputs = 0
    else:
        rating_inputs = int(
            completeness["requirement_class"].astype(str).isin(["Blocking Required", "Validation Support"]).sum()
        )
    approval_rows = st.session_state.get("data_confirmation_approvals")
    approval_count = len(approval_rows) if isinstance(approval_rows, pd.DataFrame) else 0

    st.markdown("**How this page is organized**")
    st.caption(
        "A is the scoring path. B and C are quality-control paths. Source inventory can show missing support rows; "
        "only Rating Path blockers stop the score."
    )
    cols = st.columns(3)
    with cols[0]:
        with st.container(border=True):
            st.markdown("**A. Rating Path**")
            st.write(f"Blocking missing: **{blocking_missing}**")
            st.write(f"Manual missing: **{manual_missing}**")
            st.caption("Use this to produce or refresh the rating. " + str(metrics.get("next_action", "")))
    with cols[1]:
        with st.container(border=True):
            st.markdown("**B. Evidence Path**")
            st.write(f"Verified/support: **{evidence_verified}**")
            st.write(f"Awaiting evidence: **{evidence_awaiting}**")
            st.write(f"Variance/review: **{evidence_variance}**")
            st.caption(f"{rating_inputs} rating inputs are in scope for optional ACFR/API/OS validation.")
    with cols[2]:
        with st.container(border=True):
            st.markdown("**C. Publish Path**")
            st.write(f"Approval rows: **{approval_count}**")
            st.caption("Only approved values can replace issuer_data or flow into exports.")

    if isinstance(evidence, pd.DataFrame) and not evidence.empty:
        source_counts = evidence["evidence_role"].fillna("").astype(str).value_counts().to_dict()
        st.caption(
            "Evidence queue mix: "
            + ", ".join(f"{role or 'Unlabeled'}={count}" for role, count in source_counts.items())
        )


def _render_human_workflow_cards() -> None:
    methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")
    registry = pd.DataFrame()
    approvals = pd.DataFrame()

    render_rating_readiness_overview(methodology_id, expanded=False)
    _render_operating_lanes(methodology_id)
    _render_workflow_issuer_data_snapshot()

    with st.expander("A. Rating Path - required to produce or refresh the rating", expanded=True):
        st.caption(
            "This starts from the issuer_data saved in Workflow. Confirmed replacements from this page are written back "
            "to that same table and formulas must be rerun afterward."
        )
        st.markdown("**A1. Deal and source context**")
        _render_step_1_context()
        registry = _render_step_2_file_registry()
        with st.expander("Evidence locator map", expanded=False):
            _render_step_3_source_map(registry)

        st.markdown("**A2. Source confirmation queue**")
        render_source_confirmation_queue(
            methodology_id=methodology_id,
            source_report_key="source_report",
            source_candidates_key="source_candidates",
            pdf_evidence_key="source_intake_pdf_evidence",
            approved_candidates_key="approved_source_candidates",
            issuer_data_key="issuer_data",
            source_readiness_key="source_readiness_summary",
            source_candidates_output_key="source_candidates",
            decision_state_key="workflow_source_confirmation_decisions",
            recalculate_formulas=True,
            show_header=False,
        )

        st.markdown("**A3. Data completeness and blocking fields**")
        _render_data_completeness_review(methodology_id)

        st.markdown("**A4. Formula checkpoint**")
        _render_metric_calculation_checkpoint()

    with st.expander("B. Evidence Path - optional ACFR/API/OS validation", expanded=False):
        st.caption(
            "Use this only when you want to verify or replace values that already feed the rating path. "
            "Blank evidence is not an error and does not block scoring."
        )
        st.markdown("**B1. Evidence Workbench**")
        _render_step_4_candidates(methodology_id)

        st.markdown("**B2. ACFR auto extraction**")
        _render_acfr_auto_extraction(methodology_id)

        st.markdown("**B3. Selected-field AI assist**")
        _render_ai_evidence_assist(methodology_id)

    with st.expander("C. Publish Path - approve, apply, and export", expanded=False):
        st.caption(
            "Use this after evidence has been entered or AI has prefilled candidates. "
            "Applying approved values recalculates formulas and clears stale scoreboard output."
        )
        st.markdown("**C1. Approval Decisions**")
        approvals = _render_step_5_reconciliation(methodology_id)

        st.markdown("**C2. Apply approved values and export workpapers**")
        _render_step_6_publish(methodology_id, approvals)


def render_data_confirmation_workflow(methodology_id: str) -> None:
    notice = st.session_state.pop("data_confirmation_save_notice", None)
    if notice:
        st.success(notice)
    st.caption(
        "Review & Adjust starts from the Workflow issuer_data table. Investigate evidence here, then apply only "
        "verified replacements back to that same table."
    )

    tabs = st.tabs(["Review & Adjust", "Reference"])
    with tabs[0]:
        _render_human_workflow_cards()
    with tabs[1]:
        with st.expander("File registry", expanded=True):
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
        with st.expander("Field checklist", expanded=False):
            if methodology_id == "sp_local_gov_k12":
                checklist = _frame(SP_LOCAL_GOV_FIELD_CHECKLIST)
            else:
                checklist = pd.DataFrame(
                    [
                        {
                            "factor": "All",
                            "field_or_metric": "Methodology fields",
                            "primary_check": "Use Data Completeness Review to resolve missing values before documentary validation.",
                            "preferred_evidence": "Issuer-specific source document, API record, or approved manual input.",
                            "approval_note": "Evidence Workbench should only test fields that already have system values.",
                        }
                    ]
                )
            st.dataframe(clean_for_display(checklist), width="stretch", hide_index=True)
        with st.expander("ACFR automation plan", expanded=False):
            _render_acfr_automation_plan()
        with st.expander("Status definitions", expanded=False):
            st.write("Priority class definitions")
            st.dataframe(
                clean_for_display(_frame(REQUIREMENT_CLASS_RULES)),
                width="stretch",
                hide_index=True,
            )
            st.write("Approval and evidence status definitions")
            st.dataframe(
                clean_for_display(_frame(APPROVAL_STATUS_RULES)),
                width="stretch",
                hide_index=True,
            )
        with st.expander("Workflow model", expanded=False):
            st.dataframe(
                clean_for_display(_frame(HUMAN_WORKFLOW_STEPS)),
                width="stretch",
                hide_index=True,
            )
        st.download_button(
            "Download data_confirmation_plan.csv",
            data=data_confirmation_export().to_csv(index=False).encode("utf-8"),
            file_name="data_confirmation_plan.csv",
            mime="text/csv",
        )
