"""
Rating Engine for Scoreboard-Combo / CreditScope MVP
====================================================

Purpose
-------
Convert scored metrics/factors into an indicative rating outcome.

Pipeline position:

    Mapping Engine
        -> Calculator Engine
        -> Factor Engine
        -> Rating Engine

The Rating Engine is intentionally separated from the Factor Engine:
- Factor Engine: metric -> score -> factor/profile weighted averages
- Rating Engine: weighted averages/profile assessments -> anchor/rating

Supported schemes in this MVP
-----------------------------
1. Moody's scorecard style
   - moodys_ccd_go
   - moodys_k12
   Uses overall weighted score buckets from config/scoring_thresholds.csv.

2. S&P U.S. Government / Local Government style
   - sp_local_gov_k12
   - sp_local_gov
   - sp_us_government_2024
   Uses Institutional Framework (IF) x Individual Credit Profile (ICP)
   anchor matrix.

3. S&P Enterprise + Financial profile style
   - sp_water_sewer
   Uses Enterprise Risk/Profile x Financial Risk/Profile anchor matrix.

4. S&P education/community college weighted-score style
   - sp_community_college_go
   Uses the weighted-average score bucket observed in the community college
   scorecard.

This engine can run either from:
- an existing factor_engine output, or
- formula_results + scoring_thresholds + templates, in which case it will call
  factor_engine internally.

Important caveat
----------------
The output is an indicative model result, not a rating action. It is designed for
internal scorecard replication / workflow automation.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import math
import re

import pandas as pd

try:
    from engine.factor_engine import run_factor_engine, resolve_methodology_id
except Exception:  # pragma: no cover - fallback for direct script use
    from factor_engine import run_factor_engine, resolve_methodology_id  # type: ignore


# -----------------------------------------------------------------------------
# Rating scales and matrices
# -----------------------------------------------------------------------------

# Stronger ratings appear earlier. Used for notch adjustments and caps.
RATING_SCALE: List[str] = [
    "aaa", "aa+", "aa", "aa-", "a+", "a", "a-",
    "bbb+", "bbb", "bbb-", "bb+", "bb", "bb-",
    "b+", "b", "b-", "ccc+", "ccc", "ccc-", "cc", "c", "d",
]

# Moody's weighted-score buckets may come from config/scoring_thresholds.csv.
# Fallbacks are here for safety.
MOODYS_CCD_GO_BUCKETS: List[Tuple[Optional[float], Optional[float], str, bool, bool]] = [
    (None, 1.50, "Aaa", False, True),
    (1.50, 1.83, "Aa1", False, True),
    (1.83, 2.17, "Aa2", False, True),
    (2.17, 2.50, "Aa3", False, True),
    (2.50, 2.83, "A1", False, True),
    (2.83, 3.17, "A2", False, True),
    (3.17, 3.50, "A3", False, True),
    (3.50, 3.83, "Baa1", False, True),
    (3.83, 4.17, "Baa2", False, True),
    (4.17, 4.50, "Baa3", False, True),
    (4.50, 4.83, "Ba1", False, True),
    (4.83, 5.17, "Ba2", False, True),
    (5.17, 5.50, "Ba3", False, True),
    (5.50, 5.83, "B1", False, True),
    (5.83, 6.17, "B2", False, True),
    (6.17, 6.50, "B3 and below", False, True),
]

MOODYS_K12_BUCKETS: List[Tuple[Optional[float], Optional[float], str, bool, bool]] = [
    (None, 1.50, "Aaa", False, True),
    (1.50, 2.50, "Aa1", True, True),
    (2.50, 3.50, "Aa2", True, True),
    (3.50, 4.50, "Aa3", True, True),
    (4.50, 5.50, "A1", True, True),
    (5.50, 6.50, "A2", True, True),
    (6.50, 7.50, "A3", True, True),
    (7.50, 8.50, "Baa1", True, True),
    (8.50, 9.50, "Baa2", True, True),
    (9.50, 10.50, "Baa3", True, True),
    (10.50, 11.50, "Ba1", True, True),
    (11.50, 12.50, "Ba2", True, True),
    (12.50, 13.50, "Ba3", True, True),
    (13.50, 14.50, "B1", True, True),
    (14.50, 15.50, "B2", True, True),
    (15.50, 16.50, "B3", True, True),
    (16.50, 17.50, "Caa1", True, True),
    (17.50, 18.50, "Caa2", True, True),
    (18.50, 19.50, "Caa3", True, True),
    (19.50, 20.50, "Ca", True, True),
]

# S&P U.S. Governments 2024: Institutional Framework x ICP table.
# ICP columns are half-step points used by the official table.
SP_US_GOV_ICP_COLUMNS: List[float] = [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6]
SP_US_GOV_ANCHOR_MATRIX: Dict[int, List[str]] = {
    1: ["aaa", "aaa", "aa+", "aa", "aa-", "a+", "a", "a-", "bbb", "bb+", "bb-"],
    2: ["aaa", "aa+", "aa", "aa-", "a+", "a", "a-", "bbb+", "bbb-", "bb", "b+"],
    3: ["aa+", "aa", "aa-", "a+", "a", "a-", "bbb", "bbb-", "bb+", "bb-", "b"],
    4: ["aa-", "a+", "a", "a-", "bbb+", "bbb", "bb+", "bb", "bb-", "b", "b-"],
    5: ["a", "a-", "bbb+", "bbb", "bbb-", "bb+", "bb-", "b+", "b", "b-", "b-"],
    6: ["bbb+", "bbb", "bbb-", "bb+", "bb", "bb-", "b+", "b", "b-", "b-", "b-"],
}

# S&P enterprise/financial matrix used by Water/Sewer and Education frameworks.
SP_PROFILE_ANCHOR_MATRIX: Dict[int, Dict[int, str]] = {
    1: {1: "aaa", 2: "aa+", 3: "aa-", 4: "a", 5: "bbb+/bbb", 6: "bb+/bb"},
    2: {1: "aa+", 2: "aa/aa-", 3: "a+", 4: "a-", 5: "bbb/bbb-", 6: "bb/bb-"},
    3: {1: "aa-", 2: "a+", 3: "a", 4: "bbb+/bbb", 5: "bbb-/bb+", 6: "bb-"},
    4: {1: "a", 2: "a/a-", 3: "a-/bbb+", 4: "bbb/bbb-", 5: "bb", 6: "b+"},
    5: {1: "bbb+", 2: "bbb/bbb-", 3: "bbb-/bb+", 4: "bb", 5: "bb-", 6: "b"},
    6: {1: "bbb-", 2: "bb", 3: "bb-", 4: "b+", 5: "b", 6: "b-"},
}

# S&P education provider / community college weighted score buckets observed in
# the uploaded Contra Costa CCD S&P scorecard.
SP_COMMUNITY_COLLEGE_BUCKETS: List[Tuple[Optional[float], Optional[float], str, bool, bool]] = [
    (1.00, 1.64, "AAA", True, True),
    (1.65, 1.94, "AA+", True, True),
    (1.95, 2.34, "AA", True, True),
    (2.35, 2.84, "AA-", True, True),
    (2.85, 3.24, "A+", True, True),
    (3.25, 3.64, "A", True, True),
    (3.65, 3.94, "A-", True, True),
    (3.95, 4.24, "BBB+", True, True),
    (4.25, 4.54, "BBB", True, True),
    (4.55, 4.74, "BBB-", True, True),
    (4.75, 4.94, "BB", True, True),
    (4.95, 5.00, "B", True, True),
]


# -----------------------------------------------------------------------------
# Data model
# -----------------------------------------------------------------------------

@dataclass
class RatingResult:
    methodology_id: str
    agency: str
    rating_style: str
    overall_score: Optional[float]
    indicative_rating: str
    anchor: str = ""
    sacp: str = ""
    icr: str = ""
    enterprise_score: Optional[float] = None
    financial_score: Optional[float] = None
    icp_score: Optional[float] = None
    institutional_framework_score: Optional[float] = None
    coverage_status: str = ""
    warnings: List[str] = None  # type: ignore[assignment]
    applied_adjustments: List[Dict[str, Any]] = None  # type: ignore[assignment]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d.get("warnings") is None:
            d["warnings"] = []
        if d.get("applied_adjustments") is None:
            d["applied_adjustments"] = []
        return d


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------

def _clean_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (int, float)):
        if math.isinf(float(value)):
            return None
        return float(value)
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "null", "na", "n/a", "-", "--"}:
        return None
    text = text.replace("$", "").replace(",", "").replace("x", "").replace("X", "")
    is_pct = text.endswith("%")
    text = text.replace("%", "").strip()
    try:
        num = float(text)
    except ValueError:
        return None
    return num / 100.0 if is_pct else num


def _clean_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


def _in_range(
    value: float,
    min_value: Optional[float],
    max_value: Optional[float],
    min_inclusive: bool = True,
    max_inclusive: bool = True,
) -> bool:
    above = True if min_value is None else (value >= min_value if min_inclusive else value > min_value)
    below = True if max_value is None else (value <= max_value if max_inclusive else value < max_value)
    return bool(above and below)


def _normalize_rating(rating: str) -> str:
    return str(rating).strip().lower().replace(" ", "")


def _format_sp_rating(rating: str) -> str:
    text = str(rating or "").strip()
    if not text:
        return ""
    return "/".join(part.strip().upper() for part in text.split("/"))


def _rating_index(rating: str) -> Optional[int]:
    rating = _normalize_rating(rating)
    if "/" in rating:
        # For a range such as aa/aa-, use the stronger side as the anchor for notch math.
        rating = rating.split("/")[0]
    try:
        return RATING_SCALE.index(rating)
    except ValueError:
        return None


def _apply_notches(rating: str, notches: int) -> str:
    """
    Apply notch movement to an S&P-style rating.

    notches > 0 worsens the rating by that many notches.
    notches < 0 improves the rating by that many notches.
    """
    idx = _rating_index(rating)
    if idx is None:
        return rating
    new_idx = min(max(idx + int(notches), 0), len(RATING_SCALE) - 1)
    return RATING_SCALE[new_idx]


def _apply_cap(rating: str, cap: str) -> str:
    """Return the weaker of rating and cap."""
    ridx = _rating_index(rating)
    cidx = _rating_index(cap)
    if ridx is None or cidx is None:
        return rating
    return RATING_SCALE[max(ridx, cidx)]


def _round_to_nearest_half(value: float) -> float:
    return round(value * 2) / 2.0


def _round_to_profile_assessment(value: float) -> int:
    """S&P profile/factor assessments are generally rounded to whole-number 1-6 scale."""
    return int(min(max(round(float(value)), 1), 6))


def _lookup_half_step_anchor(if_score: float, icp_score: float) -> str:
    if_assessment = _round_to_profile_assessment(if_score)
    icp_half = min(max(_round_to_nearest_half(icp_score), 1.0), 6.0)
    col_idx = SP_US_GOV_ICP_COLUMNS.index(icp_half)
    return SP_US_GOV_ANCHOR_MATRIX[if_assessment][col_idx]


def _lookup_profile_anchor(enterprise_score: float, financial_score: float) -> str:
    erp = _round_to_profile_assessment(enterprise_score)
    frp = _round_to_profile_assessment(financial_score)
    return SP_PROFILE_ANCHOR_MATRIX[erp][frp]


def _profile_assessment_candidates(value: float) -> List[int]:
    value = min(max(float(value), 1.0), 6.0)
    if abs(value - round(value)) < 1e-9:
        return [int(round(value))]
    lo = int(math.floor(value))
    hi = int(math.ceil(value))
    return sorted({min(max(lo, 1), 6), min(max(hi, 1), 6)})


def _rating_range_strong_side_index(rating: str) -> Optional[int]:
    rating = str(rating or "").strip().lower()
    if not rating:
        return None
    first = rating.split("/")[0]
    return _rating_index(first)


def _rating_from_scale_index(idx: int) -> str:
    return RATING_SCALE[min(max(int(idx), 0), len(RATING_SCALE) - 1)]


def _lookup_profile_anchor_range(enterprise_score: float, financial_score: float) -> str:
    """
    Interpolate the S&P profile matrix for fractional profile scores.

    The official Water/Sewer workbook can show a range such as AA+/AA when a
    profile score falls between two whole-number profile assessments. Exact
    whole-number coordinates still return the matrix cell verbatim.
    """
    erp_candidates = _profile_assessment_candidates(enterprise_score)
    frp_candidates = _profile_assessment_candidates(financial_score)
    cells = [SP_PROFILE_ANCHOR_MATRIX[erp][frp] for erp in erp_candidates for frp in frp_candidates]
    unique_cells = list(dict.fromkeys(cells))
    if len(unique_cells) == 1:
        return unique_cells[0]

    indices = [_rating_range_strong_side_index(cell) for cell in unique_cells]
    indices = [idx for idx in indices if idx is not None]
    if not indices:
        return unique_cells[0]
    strong = min(indices)
    weak = max(indices)
    if strong == weak:
        return _rating_from_scale_index(strong)
    return f"{_rating_from_scale_index(strong)}/{_rating_from_scale_index(weak)}"


def _first_nonempty(*values: Any) -> Any:
    for v in values:
        if v is None:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        if isinstance(v, str) and v.strip() == "":
            continue
        return v
    return None


# -----------------------------------------------------------------------------
# Loading thresholds and scoring raw formula outputs
# -----------------------------------------------------------------------------

def load_scoring_thresholds(path: str | Path = "config/scoring_thresholds.csv") -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Scoring thresholds not found: {path}")
    df = pd.read_csv(path)
    required = {"methodology_id", "formula_id", "rule_type", "score"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing required columns: {sorted(missing)}")
    return df


def _formula_results_to_dict(formula_results: Any) -> Dict[str, Dict[str, Any]]:
    if formula_results is None:
        return {}
    if isinstance(formula_results, pd.DataFrame):
        records = formula_results.to_dict(orient="records")
    elif isinstance(formula_results, list):
        records = formula_results
    elif isinstance(formula_results, Mapping):
        out: Dict[str, Dict[str, Any]] = {}
        for k, v in formula_results.items():
            if isinstance(v, Mapping):
                rec = dict(v)
                rec.setdefault("formula_id", k)
                out[str(k)] = rec
            else:
                out[str(k)] = {"formula_id": k, "status": "ready", "value": v}
        return out
    else:
        raise TypeError("formula_results must be DataFrame, list[dict], dict, or None")

    out: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        if not isinstance(rec, Mapping):
            continue
        fid = str(rec.get("formula_id", "")).strip()
        if fid:
            out[fid] = dict(rec)
    return out


def _get_formula_value(formula_dict: Mapping[str, Mapping[str, Any]], formula_id: str) -> Optional[float]:
    rec = formula_dict.get(str(formula_id), {})
    return _clean_float(rec.get("value"))


def _rule_matches_value(value: float, rule: Mapping[str, Any], prefix: str = "") -> bool:
    min_v = _clean_float(rule.get(f"{prefix}min_value"))
    max_v = _clean_float(rule.get(f"{prefix}max_value"))
    min_inc = _clean_bool(rule.get(f"{prefix}min_inclusive"), True)
    max_inc = _clean_bool(rule.get(f"{prefix}max_inclusive"), False if min_v is not None else True)
    return _in_range(value, min_v, max_v, min_inc, max_inc)


def score_single_formula(
    methodology_id: str,
    formula_id: str,
    formula_results: Any,
    thresholds: pd.DataFrame,
    manual_scores: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Score one formula_id using direct, matrix, or manual threshold rules."""
    methodology_id = resolve_methodology_id(methodology_id)
    formula_dict = _formula_results_to_dict(formula_results)
    rules = thresholds[
        (thresholds["methodology_id"].astype(str) == methodology_id)
        & (thresholds["formula_id"].astype(str) == str(formula_id))
        & (thresholds["rule_type"].astype(str) != "overall_rating_bucket")
    ].copy()

    if rules.empty:
        return {
            "formula_id": formula_id,
            "status": "needs_score",
            "numeric_score": None,
            "score_label": "",
            "reason": "No scoring threshold found.",
        }

    manual_scores = manual_scores or {}
    raw_value = _get_formula_value(formula_dict, formula_id)

    # Manual score wins when supplied.
    if formula_id in manual_scores:
        item = manual_scores[formula_id]
        if isinstance(item, Mapping):
            val = _first_nonempty(item.get("numeric_score"), item.get("score"), item.get("assessment"))
            label = str(_first_nonempty(item.get("score_label"), item.get("rating"), item.get("label"), ""))
        else:
            val = item
            label = ""
        num = _clean_float(val)
        if num is not None:
            # Fill label from rules when possible.
            label_from_rule = ""
            for _, rule in rules.iterrows():
                if _clean_float(rule.get("score")) == num:
                    label_from_rule = str(rule.get("score_label", "") or "")
                    break
            return {
                "formula_id": formula_id,
                "status": "ready",
                "numeric_score": num,
                "score_label": label or label_from_rule,
                "reason": "Manual score supplied.",
            }
        if label:
            label_norm = label.strip().lower()
            for _, rule in rules.iterrows():
                score_label = str(rule.get("score_label", "") or "").strip().lower()
                notes = str(rule.get("notes", "") or "").strip().lower()
                if label_norm == score_label or label_norm in notes:
                    return {
                        "formula_id": formula_id,
                        "status": "ready",
                        "numeric_score": _clean_float(rule.get("score")),
                        "score_label": str(rule.get("score_label", "") or label),
                        "reason": "Manual categorical score supplied.",
                    }

    # Matrix rules require primary and secondary formula values.
    matrix_rules = rules[rules["rule_type"].astype(str) == "matrix_2d"]
    if not matrix_rules.empty:
        if raw_value is None:
            return {"formula_id": formula_id, "status": "missing", "numeric_score": None, "score_label": "", "reason": "Missing primary metric value."}
        for _, rule in matrix_rules.iterrows():
            secondary_id = str(rule.get("secondary_formula_id", "") or "").strip()
            secondary_value = _get_formula_value(formula_dict, secondary_id) if secondary_id else None
            if secondary_id and secondary_value is None:
                continue
            primary_ok = _rule_matches_value(raw_value, rule, "")
            if not primary_ok:
                continue
            # Secondary columns use secondary_min_value etc.; implement directly.
            sec_min = _clean_float(rule.get("secondary_min_value"))
            sec_max = _clean_float(rule.get("secondary_max_value"))
            sec_min_inc = _clean_bool(rule.get("secondary_min_inclusive"), True)
            sec_max_inc = _clean_bool(rule.get("secondary_max_inclusive"), False if sec_min is not None else True)
            secondary_ok = True if not secondary_id else _in_range(float(secondary_value), sec_min, sec_max, sec_min_inc, sec_max_inc)
            if secondary_ok:
                return {
                    "formula_id": formula_id,
                    "status": "ready",
                    "numeric_score": _clean_float(rule.get("score")),
                    "score_label": str(rule.get("score_label", "") or ""),
                    "reason": "Matched 2D matrix rule.",
                }
        return {
            "formula_id": formula_id,
            "status": "needs_score",
            "numeric_score": None,
            "score_label": "",
            "reason": "No matrix rule matched, or secondary metric is missing.",
        }

    # Direct numeric rules.
    direct_rules = rules[rules["rule_type"].astype(str).isin(["direct_numeric"])]
    if not direct_rules.empty:
        if raw_value is None:
            return {"formula_id": formula_id, "status": "missing", "numeric_score": None, "score_label": "", "reason": "Missing metric value."}
        for _, rule in direct_rules.iterrows():
            if _rule_matches_value(float(raw_value), rule, ""):
                return {
                    "formula_id": formula_id,
                    "status": "ready",
                    "numeric_score": _clean_float(rule.get("score")),
                    "score_label": str(rule.get("score_label", "") or ""),
                    "reason": "Matched direct numeric threshold.",
                }
        return {"formula_id": formula_id, "status": "needs_score", "numeric_score": None, "score_label": "", "reason": "Metric value did not match any threshold."}

    # Manual-only rules.
    if any(rules["rule_type"].astype(str).isin(["manual_score", "manual_categorical", "manual_or_external_scale"])):
        return {
            "formula_id": formula_id,
            "status": "manual",
            "numeric_score": None,
            "score_label": "",
            "reason": "Manual or external assessment required.",
        }

    return {"formula_id": formula_id, "status": "needs_score", "numeric_score": None, "score_label": "", "reason": "Unsupported rule type."}


def build_metric_scores_from_thresholds(
    methodology_id: str,
    formula_results: Any,
    thresholds: pd.DataFrame,
    manual_scores: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Build formula_id -> score dict for factor_engine.metric_scores.
    Only returns ready scores. Missing/manual items are left for coverage reporting.
    """
    methodology_id = resolve_methodology_id(methodology_id)
    formula_ids = sorted(
        set(
            thresholds[
                (thresholds["methodology_id"].astype(str) == methodology_id)
                & (thresholds["rule_type"].astype(str) != "overall_rating_bucket")
            ]["formula_id"].dropna().astype(str)
        )
    )
    out: Dict[str, Dict[str, Any]] = {}
    for fid in formula_ids:
        scored = score_single_formula(methodology_id, fid, formula_results, thresholds, manual_scores=manual_scores)
        if scored.get("status") == "ready" and scored.get("numeric_score") is not None:
            out[fid] = {
                "numeric_score": scored.get("numeric_score"),
                "score_label": scored.get("score_label", ""),
            }
    return out


# -----------------------------------------------------------------------------
# Rating mapping
# -----------------------------------------------------------------------------

def load_overall_rating_buckets(
    methodology_id: str,
    thresholds: Optional[pd.DataFrame] = None,
) -> List[Tuple[Optional[float], Optional[float], str, bool, bool]]:
    methodology_id = resolve_methodology_id(methodology_id)
    if thresholds is not None and not thresholds.empty:
        rows = thresholds[
            (thresholds["methodology_id"].astype(str) == methodology_id)
            & (thresholds["rule_type"].astype(str) == "overall_rating_bucket")
        ].copy()
        if not rows.empty:
            buckets = []
            for _, r in rows.iterrows():
                buckets.append(
                    (
                        _clean_float(r.get("min_value")),
                        _clean_float(r.get("max_value")),
                        str(r.get("score_label", "") or ""),
                        _clean_bool(r.get("min_inclusive"), True),
                        _clean_bool(r.get("max_inclusive"), True),
                    )
                )
            return buckets

    if methodology_id == "moodys_ccd_go":
        return MOODYS_CCD_GO_BUCKETS
    if methodology_id == "moodys_k12":
        return MOODYS_K12_BUCKETS
    return []


def map_weighted_score_to_rating(
    methodology_id: str,
    weighted_score: Optional[float],
    thresholds: Optional[pd.DataFrame] = None,
) -> str:
    if weighted_score is None:
        return ""
    buckets = load_overall_rating_buckets(methodology_id, thresholds)
    for lo, hi, label, lo_inc, hi_inc in buckets:
        if _in_range(float(weighted_score), lo, hi, lo_inc, hi_inc):
            return label
    return ""


def map_sp_community_college_score_to_rating(weighted_score: Optional[float]) -> str:
    if weighted_score is None:
        return ""
    for lo, hi, label, lo_inc, hi_inc in SP_COMMUNITY_COLLEGE_BUCKETS:
        if _in_range(float(weighted_score), lo, hi, lo_inc, hi_inc):
            return label
    return ""


def _get_section_score(section_scores: pd.DataFrame, names: Sequence[str]) -> Optional[float]:
    if section_scores is None or section_scores.empty:
        return None
    lower_names = [n.lower() for n in names]
    df = section_scores.copy()
    df["_section_lower"] = df["section"].astype(str).str.lower()
    row = df[df["_section_lower"].isin(lower_names)]
    if row.empty:
        # fuzzy contains
        mask = False
        for name in lower_names:
            mask = mask | df["_section_lower"].str.contains(re.escape(name), na=False)
        row = df[mask]
    if row.empty:
        return None
    return _clean_float(row.iloc[0].get("section_score"))


def _get_factor_score(factor_scores: pd.DataFrame, names: Sequence[str]) -> Optional[float]:
    if factor_scores is None or factor_scores.empty:
        return None
    lower_names = [n.lower() for n in names]
    df = factor_scores.copy()
    df["_factor_lower"] = df["factor"].astype(str).str.lower()
    row = df[df["_factor_lower"].isin(lower_names)]
    if row.empty:
        mask = False
        for name in lower_names:
            mask = mask | df["_factor_lower"].str.contains(re.escape(name), na=False)
        row = df[mask]
    if row.empty:
        return None
    return _clean_float(row.iloc[0].get("factor_score"))


def apply_rating_adjustments(
    base_rating: str,
    modifiers: Optional[Iterable[Mapping[str, Any]]] = None,
    caps: Optional[Iterable[Mapping[str, Any] | str]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Apply optional notch modifiers and caps.

    modifiers examples:
        {"notches": -1, "reason": "EBI > 150% of U.S."}  # improve one notch
        {"notches": 1, "reason": "Small population"}     # worsen one notch

    caps examples:
        "bbb"
        {"cap": "bbb", "reason": "Management assessment of 6"}
    """
    rating = base_rating
    applied: List[Dict[str, Any]] = []

    for mod in modifiers or []:
        notches = int(_clean_float(mod.get("notches", 0)) or 0)
        reason = str(mod.get("reason", ""))
        before = rating
        rating = _apply_notches(rating, notches)
        applied.append({"type": "modifier", "before": before, "after": rating, "notches": notches, "reason": reason})

    for cap_item in caps or []:
        if isinstance(cap_item, Mapping):
            cap = str(cap_item.get("cap", ""))
            reason = str(cap_item.get("reason", ""))
        else:
            cap = str(cap_item)
            reason = ""
        if not cap:
            continue
        before = rating
        rating = _apply_cap(rating, cap)
        if rating != before:
            applied.append({"type": "cap", "before": before, "after": rating, "cap": cap, "reason": reason})
    return rating, applied


# -----------------------------------------------------------------------------
# Main engine
# -----------------------------------------------------------------------------

def run_rating_engine(
    methodology_id: str,
    factor_engine_output: Optional[Mapping[str, Any]] = None,
    formula_results: Any = None,
    manual_scores: Optional[Mapping[str, Any]] = None,
    metric_scores: Optional[Mapping[str, Any]] = None,
    thresholds_path: str | Path = "config/scoring_thresholds.csv",
    templates_dir: str | Path = "templates",
    modifiers: Optional[Iterable[Mapping[str, Any]]] = None,
    caps: Optional[Iterable[Mapping[str, Any] | str]] = None,
) -> Dict[str, Any]:
    """
    Full rating engine.

    Parameters
    ----------
    methodology_id:
        Canonical methodology id, e.g. "moodys_ccd_go", "sp_water_sewer".
    factor_engine_output:
        Existing output from engine.factor_engine.run_factor_engine. If supplied,
        formula scoring is skipped and this output is used directly.
    formula_results:
        Formula output from calculator/formula engine. Used when factor_engine_output
        is not supplied.
    manual_scores:
        Manual qualitative scores keyed by formula_id.
    metric_scores:
        Optional pre-computed metric scores keyed by formula_id. These override
        automatically scored thresholds when passed to factor_engine.
    thresholds_path:
        Path to config/scoring_thresholds.csv.
    modifiers/caps:
        Optional S&P-style notch adjustments and caps.

    Returns
    -------
    dict with:
        rating_result, factor_engine_output, scored_metric_overrides
    """
    methodology_id = resolve_methodology_id(methodology_id)
    thresholds = load_scoring_thresholds(thresholds_path) if Path(thresholds_path).exists() else pd.DataFrame()

    warnings: List[str] = []
    scored_metric_overrides: Dict[str, Any] = {}

    if factor_engine_output is None:
        # Score raw formula outputs using thresholds, then aggregate via Factor Engine.
        auto_metric_scores = build_metric_scores_from_thresholds(
            methodology_id=methodology_id,
            formula_results=formula_results,
            thresholds=thresholds,
            manual_scores=manual_scores,
        ) if not thresholds.empty else {}
        scored_metric_overrides.update(auto_metric_scores)
        if metric_scores:
            scored_metric_overrides.update(metric_scores)

        factor_engine_output = run_factor_engine(
            methodology_id=methodology_id,
            formula_results=formula_results,
            metric_scores=scored_metric_overrides,
            manual_scores=manual_scores,
            threshold_rules=None,
            templates_dir=templates_dir,
        )

    factor_df = factor_engine_output.get("factor_scores", pd.DataFrame())  # type: ignore[union-attr]
    section_df = factor_engine_output.get("section_scores", pd.DataFrame())  # type: ignore[union-attr]
    overall_score = _clean_float(factor_engine_output.get("overall_score"))  # type: ignore[union-attr]
    coverage_summary = factor_engine_output.get("coverage_summary", {})  # type: ignore[union-attr]

    agency = "Moody's" if methodology_id.startswith("moodys") else "S&P"
    rating_style = ""
    anchor = ""
    sacp = ""
    icr = ""
    rating = ""
    enterprise_score = None
    financial_score = None
    icp_score = None
    if_score = None

    # Moody's: direct weighted-average score -> rating bucket.
    if methodology_id in {"moodys_ccd_go", "moodys_k12"}:
        rating_style = "moody_weighted_score_bucket"
        rating = map_weighted_score_to_rating(methodology_id, overall_score, thresholds)
        if not rating:
            warnings.append("No Moody's rating bucket matched the weighted score.")
        anchor = rating
        sacp = rating
        icr = rating

    # S&P Local Government / U.S. Government: IF x ICP anchor.
    elif methodology_id in {"sp_local_gov_k12", "sp_local_gov", "sp_us_government_2024"}:
        rating_style = "sp_if_x_icp_anchor_matrix"
        icp_score = _get_section_score(section_df, ["Individual Credit Profile Assessment", "ICP"])
        if_score = _get_factor_score(factor_df, ["Institutional Framework"])
        if icp_score is None:
            icp_score = overall_score
            warnings.append("Could not find explicit ICP section score; using overall_score as ICP proxy.")
        if if_score is None:
            if manual_scores and "institutional_framework_rating" in manual_scores:
                val = manual_scores["institutional_framework_rating"]
                if isinstance(val, Mapping):
                    if_score = _clean_float(_first_nonempty(val.get("numeric_score"), val.get("score"), val.get("assessment")))
                else:
                    if_score = _clean_float(val)
        if icp_score is None or if_score is None:
            warnings.append("S&P local government anchor requires both ICP score and Institutional Framework score.")
            rating = ""
        else:
            anchor = _lookup_half_step_anchor(if_score, icp_score)
            sacp, applied = apply_rating_adjustments(anchor, modifiers=modifiers, caps=caps)
            icr = sacp
            rating = icr

    # S&P Community College / Education: direct weighted-score bucket.
    elif methodology_id == "sp_community_college_go":
        rating_style = "sp_education_weighted_score_bucket"
        enterprise_score = _get_section_score(section_df, ["Enterprise Profile", "Enterprise Risk Profile"])
        financial_score = _get_section_score(section_df, ["Financial Profile", "Financial Risk Profile"])
        rating = map_sp_community_college_score_to_rating(overall_score)
        if not rating:
            warnings.append("No S&P community college rating bucket matched the weighted score.")
        anchor = rating
        sacp = rating
        icr = rating

    # S&P Water/Sewer: Enterprise x Financial anchor.
    elif methodology_id == "sp_water_sewer":
        rating_style = "sp_enterprise_x_financial_anchor_matrix"
        enterprise_score = _get_section_score(section_df, ["Enterprise Profile", "Enterprise Risk Profile"])
        financial_score = _get_section_score(section_df, ["Financial Profile", "Financial Risk Profile"])
        if enterprise_score is None:
            warnings.append("Missing Enterprise Profile score.")
        if financial_score is None:
            warnings.append("Missing Financial Profile score.")
        if enterprise_score is not None and financial_score is not None:
            anchor = _lookup_profile_anchor_range(enterprise_score, financial_score)
            sacp, applied = apply_rating_adjustments(anchor, modifiers=modifiers, caps=caps)
            icr = sacp
            rating = icr
        else:
            rating = ""

    else:
        rating_style = "unsupported"
        warnings.append(f"No rating style implemented for methodology_id={methodology_id}.")

    if methodology_id not in {"sp_local_gov_k12", "sp_local_gov", "sp_us_government_2024", "sp_water_sewer", "sp_community_college_go"}:
        applied_adjustments: List[Dict[str, Any]] = []
    elif anchor and not sacp:
        sacp, applied_adjustments = apply_rating_adjustments(anchor, modifiers=modifiers, caps=caps)
        icr = sacp
        rating = icr
    else:
        # If adjustments were already applied inside branch, recompute their record for transparency.
        _, applied_adjustments = apply_rating_adjustments(anchor, modifiers=modifiers, caps=caps) if anchor else ("", [])

    coverage_status = _coverage_status_from_summary(coverage_summary)
    if coverage_status != "ready":
        if rating:
            warnings.append(
                "Indicative rating withheld because formula coverage is not ready. "
                "Use the score/profile outputs as a partial preview only."
            )
        rating = ""
        anchor = ""
        sacp = ""
        icr = ""

    if not rating:
        warnings.append("Indicative rating could not be produced because required scores are missing.")

    if agency == "S&P":
        anchor = _format_sp_rating(anchor)
        sacp = _format_sp_rating(sacp)
        icr = _format_sp_rating(icr)
        rating = _format_sp_rating(rating)

    result = RatingResult(
        methodology_id=methodology_id,
        agency=agency,
        rating_style=rating_style,
        overall_score=overall_score,
        indicative_rating=rating,
        anchor=anchor,
        sacp=sacp,
        icr=icr,
        enterprise_score=enterprise_score,
        financial_score=financial_score,
        icp_score=icp_score,
        institutional_framework_score=if_score,
        coverage_status=coverage_status,
        warnings=warnings,
        applied_adjustments=applied_adjustments,
    )

    return {
        "rating_result": result.to_dict(),
        "factor_engine_output": factor_engine_output,
        "scored_metric_overrides": scored_metric_overrides,
    }


def _coverage_status_from_summary(summary: Mapping[str, Any]) -> str:
    if not summary:
        return "unknown"
    if summary.get("metrics_error", 0):
        return "error"
    if summary.get("metrics_missing", 0) or summary.get("metrics_manual", 0) or summary.get("metrics_need_score", 0):
        return "partial"
    return "ready"


# -----------------------------------------------------------------------------
# Validation / convenience helpers
# -----------------------------------------------------------------------------

def summarize_rating_output(output: Mapping[str, Any]) -> pd.DataFrame:
    """Return a one-row DataFrame suitable for Streamlit display."""
    rr = output.get("rating_result", {})
    if not isinstance(rr, Mapping):
        return pd.DataFrame()
    columns = [
        "methodology_id", "agency", "rating_style", "overall_score",
        "enterprise_score", "financial_score", "icp_score",
        "institutional_framework_score", "anchor", "sacp", "icr",
        "indicative_rating", "coverage_status",
    ]
    return pd.DataFrame([{c: rr.get(c) for c in columns}])


def compare_to_benchmark(
    output: Mapping[str, Any],
    benchmark_rating: str,
    benchmark_score: Optional[float] = None,
    tolerance: float = 0.02,
) -> Dict[str, Any]:
    """Small validation helper for comparing against uploaded scorecard samples."""
    rr = output.get("rating_result", {})
    model_rating = str(rr.get("indicative_rating", "")) if isinstance(rr, Mapping) else ""
    model_score = _clean_float(rr.get("overall_score")) if isinstance(rr, Mapping) else None
    rating_match = model_rating.strip().lower() == str(benchmark_rating).strip().lower()
    score_match = None
    if benchmark_score is not None and model_score is not None:
        score_match = abs(model_score - float(benchmark_score)) <= tolerance
    return {
        "model_rating": model_rating,
        "benchmark_rating": benchmark_rating,
        "rating_match": rating_match,
        "model_score": model_score,
        "benchmark_score": benchmark_score,
        "score_match": score_match,
        "tolerance": tolerance,
    }


if __name__ == "__main__":
    # Tiny smoke test for the Moody CCD GO scorecard mapping seen in the uploaded sample.
    fake_factor_output = {
        "overall_score": 1.52,
        "factor_scores": pd.DataFrame(),
        "section_scores": pd.DataFrame(),
        "coverage_summary": {"metrics_missing": 0, "metrics_manual": 0, "metrics_need_score": 0, "metrics_error": 0},
    }
    out = run_rating_engine("moodys_ccd_go", factor_engine_output=fake_factor_output, thresholds_path="config/scoring_thresholds.csv")
    print(out["rating_result"])
