from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd
import streamlit as st

from engine.data_sourcing_engine import normalize_source_candidates, required_fields_for_methodology
from utils.ui_helpers import clean_for_display, selected_source_report


HUMAN_WORKFLOW_STEPS: List[Dict[str, str]] = [
    {
        "step": "1. Data Collection",
        "human_action": "Upload workbook, ACFRs, debt support, and fetch API candidates.",
        "system_action": "Register files and source candidates without making rating judgments.",
        "decision_output": "Source inventory",
    },
    {
        "step": "2. Data Completeness Review",
        "human_action": "Resolve required fields marked Missing before evidence validation.",
        "system_action": "Classify required fields as Complete, Needs Review, Missing, or Manual Override.",
        "decision_output": "Completeness status",
    },
    {
        "step": "3. Metric Calculation",
        "human_action": "Run formulas only after required fields have acceptable coverage.",
        "system_action": "Calculate metrics from approved system values.",
        "decision_output": "System values",
    },
    {
        "step": "4. Evidence Validation",
        "human_action": "Validate existing system values against ACFR, OS, API, or other support.",
        "system_action": "Evaluate only fields that already have a system value.",
        "decision_output": "Evidence status",
    },
    {
        "step": "5. Reconciliation & Approval",
        "human_action": "Accept system value, replace with evidence value, or send to review.",
        "system_action": "Store analyst decision and approved value for downstream use.",
        "decision_output": "Approved value",
    },
    {
        "step": "6. Publish Outputs",
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
        "status": "Unverified",
        "meaning": "No supporting evidence has been located for the system value.",
        "next_action": "Locate support or keep the value explicitly unverified.",
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
    "Manual Override": 2,
    "Complete": 3,
}

VALIDATION_STATUS_OPTIONS = ["Verified", "Supported", "Needs Review", "Unverified"]
EVIDENCE_STRENGTH_OPTIONS = ["Strong", "Medium", "Weak"]
RECONCILIATION_ACTIONS = ["Accept System Value", "Replace With Evidence Value", "Send To Review"]


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


def _project_path(*parts: str) -> Path:
    return Path(__file__).resolve().parents[1].joinpath(*parts)


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


def _source_value_row(field: str, selected_by_field: dict[str, pd.Series], candidates: pd.DataFrame) -> dict[str, Any]:
    if field in selected_by_field:
        row = selected_by_field[field]
        return {
            "current_source_value": row.get("value"),
            "current_source": _source_detail(row),
            "source_status": str(row.get("readiness_status") or row.get("source_quality_status") or "").strip(),
            "source_value_origin": "selected_source_report",
        }
    candidate = _first_candidate(field, candidates)
    if candidate is not None:
        return {
            "current_source_value": candidate.get("value"),
            "current_source": _source_detail(candidate),
            "source_status": "candidate_available",
            "source_value_origin": "candidate_not_saved",
        }
    manual_values = st.session_state.get("manual_source_values", {}) or {}
    if field in manual_values and str(manual_values[field]).strip() != "":
        return {
            "current_source_value": manual_values[field],
            "current_source": "Manual: unsaved user input",
            "source_status": "manual_input",
            "source_value_origin": "manual_source_values",
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
            }
        issuer_data = st.session_state.get("issuer_data", {}) or {}
        if field in issuer_data and _has_value(issuer_data[field]):
            return {
                "current_source_value": issuer_data[field],
                "current_source": "issuer_data direct metric",
                "source_status": "issuer_data_direct_metric",
                "source_value_origin": "issuer_data",
            }
    return {
        "current_source_value": "",
        "current_source": "",
        "source_status": "missing",
        "source_value_origin": "no_candidate",
    }


def _base_check_rows(methodology_id: str) -> list[dict[str, Any]]:
    dictionary = _dictionary_lookup()
    priority = _source_priority_lookup(methodology_id)
    candidates = _candidate_frames()
    selected_by_field = _selected_source_by_field()
    rows: list[dict[str, Any]] = []
    for field in _required_source_fields(methodology_id):
        source_value = _source_value_row(field, selected_by_field, candidates)
        priority_row = priority.get(field, {})
        rows.append(
            {
                "factor": _field_factor(field, dictionary),
                "field_name": field,
                "data_stage": _field_stage(field),
                "current_source_value": source_value["current_source_value"],
                "current_source": source_value["current_source"],
                "source_status": source_value["source_status"],
                "source_value_origin": source_value["source_value_origin"],
                "candidate_sources": _candidate_sources(field, candidates),
                "preferred_sources": str(priority_row.get("priority_sources") or "").strip(),
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


def _completeness_status(row: pd.Series) -> str:
    source_status = str(row.get("source_status", "") or "")
    if not _has_value(row.get("current_source_value")):
        return "Missing"
    if source_status == "manual_input":
        return "Manual Override"
    if source_status in {"candidate_available", "source_pending", "needs_review", "scorecard_implied", "missing"}:
        return "Needs Review"
    return "Complete"


def _expected_source(row: pd.Series) -> str:
    for col in ["preferred_sources", "candidate_sources", "current_source"]:
        value = str(row.get(col, "") or "").strip()
        if value:
            return value
    return "Not configured"


def _completeness_frame(methodology_id: str) -> pd.DataFrame:
    checks = _confirmation_checks(methodology_id).copy()
    if checks.empty:
        return checks
    checks["expected_source"] = checks.apply(_expected_source, axis=1)
    checks["current_status"] = checks.apply(_completeness_status, axis=1)
    checks["status_rank"] = checks["current_status"].map(COMPLETENESS_STATUS_ORDER).fillna(9)
    return checks.sort_values(["status_rank", "factor", "field_name"]).reset_index(drop=True)


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
    if saved in VALIDATION_STATUS_OPTIONS and saved != "Unverified":
        return saved
    if not _has_value(evidence_value):
        return "Unverified"
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
    field = str(row.get("field_name", "") or "")
    factor = str(row.get("factor", "") or "")
    preferred = str(row.get("preferred_sources", "") or "")
    evidence = str(row.get("evidence_target", "") or "")
    source = str(row.get("current_source", "") or "")
    if field in DIRECT_METRIC_FIELDS:
        return True
    if "ACFR" in preferred or "OS" in preferred:
        return True
    if "ACFR" in evidence or "debt support" in evidence.lower():
        return True
    if "CreditScope" in source or "Manual" in source:
        return True
    return factor in {"Financial Performance", "Reserves and Liquidity", "Debt & Liabilities", "Pension", "OPEB"}


def _evidence_validation_frame(methodology_id: str) -> pd.DataFrame:
    checks = _confirmation_checks(methodology_id).copy()
    if checks.empty:
        return checks
    checks = checks[checks.apply(_evidence_relevant, axis=1)].copy()
    if checks.empty:
        return checks
    checks["field_name"] = checks["field_name"].astype(str)
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
    status_rank = {"Needs Review": 0, "Unverified": 1, "Supported": 2, "Verified": 3}
    checks["validation_rank"] = checks["validation_status"].map(status_rank).fillna(9)
    return checks.sort_values(["validation_rank", "factor", "field_name"]).reset_index(drop=True)


def evidence_confidence_metrics(methodology_id: str | None = None) -> dict[str, Any]:
    methodology = methodology_id or st.session_state.get("methodology_id", "moodys_ccd_go")
    completeness = _completeness_frame(methodology)
    evidence = _evidence_validation_frame(methodology)
    required_count = int(len(completeness)) if isinstance(completeness, pd.DataFrame) else 0
    missing_count = (
        int(completeness["current_status"].astype(str).eq("Missing").sum())
        if isinstance(completeness, pd.DataFrame) and not completeness.empty and "current_status" in completeness.columns
        else 0
    )
    data_completeness = ((required_count - missing_count) / required_count * 100) if required_count else 0.0
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
    evidence_coverage = (evidence_supported / required_count * 100) if required_count else 0.0
    return {
        "required_fields": required_count,
        "missing_fields": missing_count,
        "data_completeness_pct": data_completeness,
        "evidence_required_fields": evidence_count,
        "evidence_supported_fields": evidence_supported,
        "evidence_coverage_pct": evidence_coverage,
        "verified_fields": verified_count,
        "verified_denominator": required_count,
    }


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
        st.info("No source files uploaded yet. Use Workflow > Source Data to upload CreditScope, ACFR, and debt support files.")
    else:
        st.dataframe(clean_for_display(registry), width="stretch", hide_index=True)
    return registry


def _render_step_3_source_map(registry: pd.DataFrame) -> None:
    source_map = _source_map_frame(registry)
    st.dataframe(clean_for_display(source_map), width="stretch", hide_index=True)


def _render_data_completeness_review(methodology_id: str) -> pd.DataFrame:
    completeness = _completeness_frame(methodology_id)
    if completeness.empty:
        st.info("No required-field list is available for this methodology yet.")
        return completeness

    counts = completeness["current_status"].value_counts().to_dict()
    cols = st.columns(4)
    cols[0].metric("Required Fields", len(completeness))
    cols[1].metric("Auto Filled", int(counts.get("Complete", 0)))
    cols[2].metric("Needs Review", int(counts.get("Needs Review", 0)))
    cols[3].metric("Missing", int(counts.get("Missing", 0)))

    display_cols = [
        "field_name",
        "factor",
        "expected_source",
        "current_status",
        "current_source_value",
        "current_source",
        "formula_dependency",
    ]
    st.dataframe(
        clean_for_display(completeness[[col for col in display_cols if col in completeness.columns]]).rename(
            columns={
                "field_name": "Field",
                "factor": "Factor",
                "expected_source": "Expected Source",
                "current_status": "Current Status",
                "current_source_value": "Current Value",
                "current_source": "Current Source",
                "formula_dependency": "Used By",
            }
        ),
        width="stretch",
        hide_index=True,
    )
    return completeness


def _render_metric_calculation_checkpoint() -> None:
    formula_results = st.session_state.get("methodology_formula_results")
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        formula_results = st.session_state.get("formula_results", pd.DataFrame())
    if not isinstance(formula_results, pd.DataFrame) or formula_results.empty:
        st.info("No formula results yet. Complete source fields, then run formulas in the main Workflow page.")
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


def _render_step_4_candidates(methodology_id: str) -> None:
    evidence = _evidence_validation_frame(methodology_id)
    if evidence.empty:
        st.info("No system values are ready for evidence validation yet. Resolve missing fields first.")
        return
    completeness = _completeness_frame(methodology_id)
    missing_count = (
        int(completeness["current_status"].astype(str).eq("Missing").sum())
        if isinstance(completeness, pd.DataFrame) and not completeness.empty and "current_status" in completeness.columns
        else 0
    )
    if missing_count:
        st.warning(f"{missing_count} required fields are still missing. Evidence validation below only covers fields that already have a system value.")
    counts = evidence["validation_status"].value_counts().to_dict()
    cols = st.columns(4)
    cols[0].metric("Evidence Fields", len(evidence))
    cols[1].metric("Verified", int(counts.get("Verified", 0)))
    cols[2].metric("Supported", int(counts.get("Supported", 0)))
    cols[3].metric("Unverified", int(counts.get("Unverified", 0)))
    st.caption("Evidence Validation only evaluates fields that already have a system value. Missing values belong in Data Completeness Review.")
    editable_cols = [
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
                "field_name": st.column_config.TextColumn("Field", disabled=True),
                "system_value": st.column_config.TextColumn("System Value", disabled=True),
                "system_source": st.column_config.TextColumn("System Source", disabled=True),
                "evidence_source": st.column_config.TextColumn("Evidence Source"),
                "evidence_page": st.column_config.TextColumn("Evidence Page"),
                "evidence_line_item": st.column_config.TextColumn("Evidence Line Item"),
                "evidence_value": st.column_config.TextColumn("Evidence Value"),
                "difference_pct": st.column_config.NumberColumn("Difference %", disabled=True, format="%.2f"),
                "validation_status": st.column_config.SelectboxColumn(
                    "Validation Status",
                    options=VALIDATION_STATUS_OPTIONS,
                ),
                "evidence_strength": st.column_config.SelectboxColumn(
                    "Evidence Strength",
                    options=EVIDENCE_STRENGTH_OPTIONS,
                ),
                "review_notes": st.column_config.TextColumn("Review Notes"),
            },
        )
        if st.form_submit_button("Save evidence validation", type="primary"):
            _save_confirmation_checks(edited)
            st.success("Evidence validation saved.")


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
        if not field or decision == "Send To Review":
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
    approvals["approval_decision"] = approvals.apply(
        lambda row: str(row.get("approval_decision") or "").strip()
        or ("Accept System Value" if str(row.get("validation_status", "")) in {"Verified", "Supported"} else "Send To Review"),
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

    decisions = approvals["approval_decision"].value_counts().to_dict()
    cols = st.columns(3)
    cols[0].metric("Accept System", int(decisions.get("Accept System Value", 0)))
    cols[1].metric("Replace With Evidence", int(decisions.get("Replace With Evidence Value", 0)))
    cols[2].metric("Send To Review", int(decisions.get("Send To Review", 0)))

    with st.form("data_confirmation_approval_form"):
        edited = st.data_editor(
            clean_for_display(
                approvals[
                    [
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
                st.caption("Approved values will be included as source candidates on the next Save issuer_data run.")
            return edited.copy()
    return approvals


def _render_step_6_publish(methodology_id: str, approvals: pd.DataFrame) -> None:
    metrics = evidence_confidence_metrics(methodology_id)
    cols = st.columns(3)
    cols[0].metric("Data Completeness", f"{metrics['data_completeness_pct']:.0f}%")
    cols[1].metric("Evidence Coverage", f"{metrics['evidence_coverage_pct']:.0f}%")
    cols[2].metric("Verified Fields", f"{metrics['verified_fields']} / {metrics['verified_denominator']}")

    approved = approvals if isinstance(approvals, pd.DataFrame) and not approvals.empty else st.session_state.get("data_confirmation_approvals")
    if not isinstance(approved, pd.DataFrame) or approved.empty:
        st.info("No reconciliation decisions saved yet.")
    else:
        decisions = approved["approval_decision"].value_counts().to_dict() if "approval_decision" in approved.columns else {}
        cols = st.columns(3)
        cols[0].metric("Accepted System", int(decisions.get("Accept System Value", 0)))
        cols[1].metric("Evidence Replacements", int(decisions.get("Replace With Evidence Value", 0)))
        cols[2].metric("Review Queue", int(decisions.get("Send To Review", 0)))

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
    approvals = pd.DataFrame()

    with st.expander("1. Data Collection", expanded=True):
        _render_step_1_context()
        registry = _render_step_2_file_registry()
        with st.expander("Evidence locator map", expanded=False):
            _render_step_3_source_map(registry)

    with st.expander("2. Data Completeness Review", expanded=True):
        _render_data_completeness_review(methodology_id)

    with st.expander("3. Metric Calculation", expanded=True):
        _render_metric_calculation_checkpoint()

    with st.expander("4. Evidence Validation", expanded=True):
        _render_step_4_candidates(methodology_id)

    with st.expander("5. Reconciliation & Approval", expanded=True):
        approvals = _render_step_5_reconciliation(methodology_id)

    with st.expander("6. Publish Outputs", expanded=False):
        _render_step_6_publish(methodology_id, approvals)


def render_data_confirmation_workflow(methodology_id: str) -> None:
    st.caption("Evidence & Reconciliation separates missing-data cleanup from documentary validation, then carries approved trust labels into rating outputs.")

    tabs = st.tabs(["Verification workflow", "File registry", "Field checklist", "Status definitions"])
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
                        "primary_check": "Use Data Completeness Review to resolve missing values before documentary validation.",
                        "preferred_evidence": "Issuer-specific source document, API record, or approved manual input.",
                        "approval_note": "Evidence Validation should only test fields that already have system values.",
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
