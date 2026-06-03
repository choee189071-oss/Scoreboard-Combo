"""
Mapping Engine for CreditScope / IPEDS / OS / ACFR uploads.

Purpose
-------
Convert uploaded source files with human-facing column names into canonical
issuer_data fields used by the formula engine.

Example
-------
CreditScope CSV columns:
    Population, Full Value, Net Direct Debt

field_mapping.csv:
    field_name,source_name,possible_column_names,match_type,notes
    population,CreditScope,"Population|Resident Population",fuzzy,...

Output:
    issuer_data = {
        "population": 530000,
        "full_value": 40000000000,
        "net_direct_debt": 700000000,
    }
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
import re

import pandas as pd
from openpyxl import load_workbook


DEFAULT_MAPPING_PATH = Path("config/field_mapping.csv")

CREDITSCOPE_THOUSAND_DOLLAR_FIELDS = {
    "full_value",
    "assessed_value",
    "assessed_value_prior",
    "operating_revenue",
    "operating_expense",
    "governmental_revenue",
    "governmental_expense",
    "transfers",
    "committed_fund_balance",
    "assigned_fund_balance",
    "unassigned_fund_balance",
    "fund_balance",
    "cash",
    "cash_and_investments",
    "net_assets",
    "revenue",
    "debt",
    "long_term_debt",
    "net_direct_debt",
    "debt_service",
    "pension_cost",
    "opeb_cost",
    "net_pension_liability",
    "mads",
    "adjusted_npl",
    "adjusted_opeb",
}


@dataclass
class FieldMatch:
    """One canonical field mapped from one uploaded column."""

    field_name: str
    source_name: str
    matched_column: Optional[str]
    matched_label: Optional[str]
    match_method: str  # exact_alias, normalized_alias, fuzzy_alias, not_found
    confidence: float
    value: Any = None
    notes: str = ""

    @property
    def status(self) -> str:
        if self.matched_column is None:
            return "missing"
        if self.value is None or (isinstance(self.value, float) and pd.isna(self.value)):
            return "missing_value"
        if self.confidence >= 0.97:
            return "ready"
        if self.confidence >= 0.78:
            return "review"
        return "low_confidence"


def normalize_label(value: Any) -> str:
    """Normalize labels for resilient matching."""
    if value is None:
        return ""
    text = str(value).strip().lower()
