"""
Factor Engine for Scoreboard-Combo / CreditScope MVP
====================================================

Purpose
-------
Turn calculated metrics into methodology-level factor scores.

This engine sits AFTER the Mapping Engine and Calculator Engine:

    uploaded source files
        -> issuer_data
        -> formula_results
        -> factor_scores
        -> rating / anchor engine

It intentionally does NOT hard-code all rating thresholds yet. Instead, it:
1. loads a methodology template such as templates/sp_water_sewer.csv;
2. joins formula outputs to the template;
3. accepts metric scores from either:
   - formula_results columns: numeric_score / score / assessment;
   - metric_scores/manual_scores dicts supplied by the UI; or
   - optional threshold rules supplied later; and
4. aggregates metric scores into factor, section/profile, and overall scores.

Why this design?
----------------
Credit scorecards usually have two layers:
- Metric calculation: e.g., cash / operating revenue = 0.39
- Score assignment: e.g., 0.39 -> Aaa / 1.0 or assessment score 1

Your Calculator Engine does the first layer. This Factor Engine does the
aggregation layer and leaves room for a later Scoring/Threshold Engine.

Supported methodology templates already present in your project
---------------------------------------------------------------
- moodys_ccd_go.csv
- moodys_k12.csv
- sp_community_college_go.csv
- sp_local_gov_k12.csv
- sp_water_sewer.csv

Recommended canonical methodology IDs
-------------------------------------
- moodys_ccd_go
- moodys_k12
- sp_community_college_go
- sp_local_gov_k12
- sp_local_gov              alias of sp_local_gov_k12 for now
- sp_us_government_2024     alias of sp_local_gov_k12 for now
- sp_water_sewer

Expected formula_results input
------------------------------
A pandas DataFrame from engine.formula_engine.calculate_all_formulas, with:
- formula_id
- formula_name
- status
- value

Optional scoring columns if available:
- numeric_score
- score
- assessment
- rating

Example
-------
from engine.formula_engine import calculate_all_formulas
from engine.factor_engine import run_factor_engine

formula_results = calculate_all_formulas(issuer_data)
manual_scores = {
    "management_assessment": 2,
    "institutional_framework_rating": 6,
}

out = run_factor_engine(
    methodology_id="sp_local_gov_k12",
    formula_results=formula_results,
    manual_scores=manual_scores,
)

print(out["overall_score"])
print(out["factor_scores"])
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import math
import re

import pandas as pd


STATUS_READY = "ready"
STATUS_PARTIAL = "partial"
STATUS_MISSING = "missing"
STATUS_NEEDS_SCORE = "needs_score"
STATUS_ERROR = "error"
STATUS_MANUAL = "manual"


# -----------------------------------------------------------------------------
# Scheme registry
# -----------------------------------------------------------------------------

SCHEME_REGISTRY: Dict[str, Dict[str, Any]] = {
    "moodys_ccd_go": {
        "display_name": "Moody's CCD GO",
        "template": "moodys_ccd_go.csv",
        "agency": "Moody's",
        "score_direction": "lower_is_stronger",
        "aggregation_style": "direct_weighted_average",
        "profile_weights": {},
        "notes": "Community college district GO scorecard structure observed from Contra Costa CCD Moody's Higher Ed scorecard.",
    },
    "moodys_k12": {
        "display_name": "Moody's K-12 Public School Districts",
        "template": "moodys_k12.csv",
        "agency": "Moody's",
        "score_direction": "lower_is_stronger",
        "aggregation_style": "direct_weighted_average",
        "profile_weights": {},
        "notes": "K-12 scorecard structure observed from Alum Rock Union ESD scorecard.",
    },
    "sp_local_gov_k12": {
        "display_name": "S&P U.S. Government / Local Government / K-12 GO",
        "template": "sp_local_gov_k12.csv",
        "agency": "S&P",
        "score_direction": "lower_is_stronger",
        "aggregation_style": "direct_weighted_average",
        "profile_weights": {},
        "notes": "U.S. Government methodology ICP: Economy, Financial Performance, Reserves & Liquidity, Management, Debt & Liabilities at 20% each.",
    },
    "sp_local_gov": {
        "display_name": "S&P Local Government GO",
        "template": "sp_local_gov_k12.csv",
        "agency": "S&P",
        "score_direction": "lower_is_stronger",
        "aggregation_style": "direct_weighted_average",
        "profile_weights": {},
        "notes": "Alias to sp_local_gov_k12 until a separate local-government template is created.",
    },
    "sp_us_government_2024": {
        "display_name": "S&P Methodology for Rating U.S. Governments 2024",
        "template": "sp_local_gov_k12.csv",
        "agency": "S&P",
        "score_direction": "lower_is_stronger",
        "aggregation_style": "direct_weighted_average",
        "profile_weights": {},
        "notes": "Alias to sp_local_gov_k12. Rating anchor requires separate IF x ICP rating engine.",
    },
    "sp_water_sewer": {
        "display_name": "S&P Municipal Water / Sewer / Solid Waste Utility",
        "template": "sp_water_sewer.csv",
        "agency": "S&P",
        "score_direction": "lower_is_stronger",
        "aggregation_style": "profile_then_blended_average",
        "profile_weights": {"Enterprise Profile": 0.50, "Financial Profile": 0.50},
        "notes": "Enterprise and Financial profiles are calculated separately, then blended 50/50 as a practical MVP proxy before anchor lookup.",
    },
    "sp_community_college_go": {
        "display_name": "S&P Not-For-Profit Education / Community College GO",
        "template": "sp_community_college_go.csv",
        "agency": "S&P",
        "score_direction": "lower_is_stronger",
        "aggregation_style": "profile_then_blended_average",
        "profile_weights": {"Enterprise Profile": 0.50, "Financial Profile": 0.50},
        "notes": "Education provider methodology: Enterprise profile and Financial profile are separately assessed before anchor lookup.",
    },
}


# Moody score-to-rating scale seen in uploaded scorecards. This is useful for
# displaying indicative category from weighted score, not for assigning metric
# thresholds.
MOODYS_WEIGHTED_SCORE_BUCKETS: List[Tuple[float, float, str]] = [
    (float("-inf"), 1.5, "Aaa"),
    (1.5, 4.5, "Aa1-Aa3"),
    (4.5, 7.5, "A1-A3"),
    (7.5, 10.5, "Baa1-Baa3"),
    (10.5, 13.5, "Ba1-Ba3"),
    (13.5, 16.5, "B1-B3"),
    (16.5, 19.5, "Caa1-Caa3"),
]

# S&P direct weighted score buckets observed in community college scorecard.
SP_WEIGHTED_SCORE_BUCKETS: List[Tuple[float, float, str]] = [
    (1.00, 1.64, "AAA"),
    (1.65, 1.94, "AA+"),
    (1.95, 2.34, "AA"),
    (2.35, 2.84, "AA-"),
    (2.85, 3.24, "A+"),
    (3.25, 3.64, "A"),
    (3.65, 3.94, "A-"),
    (3.95, 4.24, "BBB+"),
    (4.25, 4.54, "BBB"),
    (4.55, 4.74, "BBB-"),
    (4.75, 4.94, "BB"),
    (4.95, 5.00, "B"),
]


# -----------------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------------

@dataclass
class MetricScore:
    methodology_id: str
    section: str
    factor: str
    factor_weight: float
    metric: str
    metric_weight: float
    formula_id: str
    source_priority: str
    formula_status: str = STATUS_MISSING
    raw_value: Any = None
    numeric_score: Optional[float] = None
    score_label: str = ""
    status: str = STATUS_MISSING
    missing_reason: str = ""

    @property
    def metric_weighted_score(self) -> Optional[float]:
        if self.numeric_score is None:
            return None
        return self.numeric_score * self.metric_weight

    def to_dict(self) -> Dict[str, Any]:
        return {
            "methodology_id": self.methodology_id,
            "section": self.section,
            "factor": self.factor,
            "factor_weight": self.factor_weight,
            "metric": self.metric,
            "metric_weight": self.metric_weight,
            "formula_id": self.formula_id,
            "source_priority": self.source_priority,
            "formula_status": self.formula_status,
            "raw_value": self.raw_value,
            "numeric_score": self.numeric_score,
            "score_label": self.score_label,
            "metric_weighted_score": self.metric_weighted_score,
            "status": self.status,
            "missing_reason": self.missing_reason,
        }


# -----------------------------------------------------------------------------
# Loading and normalization helpers
# -----------------------------------------------------------------------------

def list_supported_schemes() -> pd.DataFrame:
    """Return supported methodology IDs and descriptions."""
    rows = []
    for methodology_id, meta in SCHEME_REGISTRY.items():
        rows.append({"methodology_id": methodology_id, **meta})
    return pd.DataFrame(rows)


def resolve_methodology_id(methodology_id: str) -> str:
    key = str(methodology_id).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "s&p_local_gov": "sp_local_gov",
        "sp_local_government": "sp_local_gov",
        "s&p_local_government": "sp_local_gov",
        "s&p_water_sewer": "sp_water_sewer",
        "sp_water_&_sewer": "sp_water_sewer",
        "sp_water_and_sewer": "sp_water_sewer",
        "s&p_community_college": "sp_community_college_go",
        "sp_ccd_go": "sp_community_college_go",
        "moodys_ccd": "moodys_ccd_go",
        "moody_ccd_go": "moodys_ccd_go",
        "moody's_ccd_go": "moodys_ccd_go",
        "moody_k12": "moodys_k12",
        "moody's_k12": "moodys_k12",
    }
    key = aliases.get(key, key)
    if key not in SCHEME_REGISTRY:
        raise ValueError(f"Unsupported methodology_id={methodology_id!r}. Supported: {sorted(SCHEME_REGISTRY)}")
    return key


def load_factor_template(
    methodology_id: str,
    templates_dir: str | Path = "templates",
) -> pd.DataFrame:
    """Load the factor template for a methodology."""
    methodology_id = resolve_methodology_id(methodology_id)
    template_name = SCHEME_REGISTRY[methodology_id]["template"]
    path = Path(templates_dir) / template_name
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")

    df = pd.read_csv(path)
    required_cols = {"section", "factor", "factor_weight", "metric", "metric_weight", "formula_id", "source_priority"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Template {path} is missing columns: {sorted(missing)}")

    df = df.dropna(how="all").copy()
    for col in ["section", "factor", "metric", "formula_id", "source_priority"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["factor_weight"] = pd.to_numeric(df["factor_weight"], errors="coerce").fillna(0.0)
    df["metric_weight"] = pd.to_numeric(df["metric_weight"], errors="coerce").fillna(0.0)
    df = df[df["formula_id"] != ""].reset_index(drop=True)
    return df


def _formula_results_to_dict(formula_results: Any) -> Dict[str, Dict[str, Any]]:
    """Normalize formula_results DataFrame/list/dict into formula_id -> record."""
    if formula_results is None:
        return {}

    if isinstance(formula_results, pd.DataFrame):
        records = formula_results.to_dict(orient="records")
    elif isinstance(formula_results, list):
        records = formula_results
    elif isinstance(formula_results, Mapping):
        # Either formula_id -> {..} or formula_id -> scalar value.
        out: Dict[str, Dict[str, Any]] = {}
        for fid, rec in formula_results.items():
            if isinstance(rec, Mapping):
                row = dict(rec)
                row.setdefault("formula_id", fid)
                out[str(fid)] = row
            else:
                out[str(fid)] = {"formula_id": fid, "status": STATUS_READY, "value": rec}
        return out
    else:
        raise TypeError("formula_results must be a pandas DataFrame, list of dicts, dict, or None")

    out = {}
    for rec in records:
        if not isinstance(rec, Mapping):
            continue
        fid = str(rec.get("formula_id", "")).strip()
        if fid:
            out[fid] = dict(rec)
    return out


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _clean_float(value: Any) -> Optional[float]:
    if _is_missing(value):
        return None
    if isinstance(value, (int, float)):
        if math.isnan(float(value)) or math.isinf(float(value)):
            return None
        return float(value)
    text = str(value).strip()
    if text.lower() in {"na", "n/a", "none", "null", "missing", "manual", "-", "--"}:
        return None
    # Accept Moody-style strings like "1.7x" and percentages.
    text = text.replace("$", "").replace(",", "").replace("x", "").replace("X", "").strip()
    is_percent = text.endswith("%")
    text = text.replace("%", "").strip()
    try:
        num = float(text)
    except ValueError:
        return None
    return num / 100.0 if is_percent else num


def _numeric_score_from_record(record: Mapping[str, Any]) -> Tuple[Optional[float], str]:
    """Pull a numeric score from formula result if present."""
    for col in ["numeric_score", "score_value", "assessment_score", "assessment", "score"]:
        if col in record:
            val = _clean_float(record.get(col))
            if val is not None:
                return val, str(record.get(col, ""))
    for col in ["score_label", "rating", "indicative_score"]:
        if col in record and not _is_missing(record.get(col)):
            return None, str(record.get(col))
    return None, ""


def _score_from_dict(formula_id: str, scores: Optional[Mapping[str, Any]]) -> Tuple[Optional[float], str]:
    if not scores or formula_id not in scores:
        return None, ""
    item = scores[formula_id]
    if isinstance(item, Mapping):
        numeric = _clean_float(item.get("numeric_score", item.get("score", item.get("assessment"))))
        label = str(item.get("score_label", item.get("rating", item.get("label", ""))))
        return numeric, label
    numeric = _clean_float(item)
    return numeric, str(item) if numeric is None else ""


# -----------------------------------------------------------------------------
# Optional threshold scoring hooks
# -----------------------------------------------------------------------------

def score_value_with_thresholds(
    formula_id: str,
    raw_value: Any,
    threshold_rules: Optional[pd.DataFrame] = None,
) -> Tuple[Optional[float], str]:
    """
    Optional scoring hook for later Step 5.5.

    threshold_rules expected columns:
    - formula_id
    - min_value
    - max_value
    - numeric_score
    - score_label optional
    - inclusive_min optional bool
    - inclusive_max optional bool

    If no matching threshold is found, returns (None, "").
    """
    if threshold_rules is None or threshold_rules.empty:
        return None, ""
    value = _clean_float(raw_value)
    if value is None:
        return None, ""

    rules = threshold_rules[threshold_rules["formula_id"].astype(str) == str(formula_id)].copy()
    if rules.empty:
        return None, ""

    for _, rule in rules.iterrows():
        min_v = _clean_float(rule.get("min_value"))
        max_v = _clean_float(rule.get("max_value"))
        inc_min = bool(rule.get("inclusive_min", True))
        inc_max = bool(rule.get("inclusive_max", True))

        above_min = True if min_v is None else (value >= min_v if inc_min else value > min_v)
        below_max = True if max_v is None else (value <= max_v if inc_max else value < max_v)
        if above_min and below_max:
            return _clean_float(rule.get("numeric_score")), str(rule.get("score_label", ""))
    return None, ""


# -----------------------------------------------------------------------------
# Core factor engine
# -----------------------------------------------------------------------------

def build_metric_scores(
    methodology_id: str,
    formula_results: Any = None,
    metric_scores: Optional[Mapping[str, Any]] = None,
    manual_scores: Optional[Mapping[str, Any]] = None,
    threshold_rules: Optional[pd.DataFrame] = None,
    templates_dir: str | Path = "templates",
) -> pd.DataFrame:
    """
    Join a methodology template to formula results and produce metric-level scores.

    metric_scores and manual_scores both accept formula_id -> score.
    manual_scores is just a semantic alias used by the UI for qualitative fields.
    """
    methodology_id = resolve_methodology_id(methodology_id)
    template = load_factor_template(methodology_id, templates_dir=templates_dir)
    formula_dict = _formula_results_to_dict(formula_results)

    rows: List[MetricScore] = []
    for _, tpl in template.iterrows():
        fid = str(tpl["formula_id"]).strip()
        rec = formula_dict.get(fid, {})
        formula_status = str(rec.get("status", STATUS_MISSING)).lower().strip() if rec else STATUS_MISSING
        raw_value = rec.get("value", None) if rec else None

        numeric_score, score_label = _numeric_score_from_record(rec) if rec else (None, "")

        # Explicit UI scores override formula_result scores.
        override_score, override_label = _score_from_dict(fid, metric_scores)
        if override_score is not None or override_label:
            numeric_score, score_label = override_score, override_label

        manual_score, manual_label = _score_from_dict(fid, manual_scores)
        if manual_score is not None or manual_label:
            numeric_score, score_label = manual_score, manual_label

        # Optional threshold scoring if raw metric is available.
        if numeric_score is None and not _is_missing(raw_value):
            thr_score, thr_label = score_value_with_thresholds(fid, raw_value, threshold_rules)
            if thr_score is not None or thr_label:
                numeric_score, score_label = thr_score, thr_label

        if numeric_score is not None:
            status = STATUS_READY
            missing_reason = ""
        elif formula_status in {STATUS_MANUAL, "manual"}:
            status = STATUS_MANUAL
            missing_reason = "Manual score required."
        elif formula_status in {STATUS_READY, "ready"} and numeric_score is None:
            status = STATUS_NEEDS_SCORE
            missing_reason = "Metric calculated, but no score/threshold assigned yet."
        elif formula_status in {STATUS_ERROR, "error"}:
            status = STATUS_ERROR
            missing_reason = str(rec.get("error", "Formula error."))
        else:
            status = STATUS_MISSING
            mf = str(rec.get("missing_fields", "")).strip() if rec else ""
            missing_reason = f"Missing formula output for {fid}." if not mf else f"Missing required field(s): {mf}"

        rows.append(
            MetricScore(
                methodology_id=methodology_id,
                section=str(tpl["section"]),
                factor=str(tpl["factor"]),
                factor_weight=float(tpl["factor_weight"]),
                metric=str(tpl["metric"]),
                metric_weight=float(tpl["metric_weight"]),
                formula_id=fid,
                source_priority=str(tpl.get("source_priority", "")),
                formula_status=formula_status,
                raw_value=raw_value,
                numeric_score=numeric_score,
                score_label=score_label,
                status=status,
                missing_reason=missing_reason,
            ).to_dict()
        )

    return pd.DataFrame(rows)


def aggregate_factor_scores(metric_score_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate metric scores into factor scores."""
    if metric_score_df.empty:
        return pd.DataFrame()

    rows: List[Dict[str, Any]] = []
    group_cols = ["methodology_id", "section", "factor", "factor_weight"]
    for keys, group in metric_score_df.groupby(group_cols, dropna=False):
        methodology_id, section, factor, factor_weight = keys
        scored = group[group["numeric_score"].notna()].copy()
        available_weight = float(scored["metric_weight"].sum()) if not scored.empty else 0.0
        total_weight = float(group["metric_weight"].sum()) if not group.empty else 0.0

        if available_weight > 0:
            factor_score = float((scored["numeric_score"] * scored["metric_weight"]).sum() / available_weight)
        else:
            factor_score = None

        if available_weight == 0:
            status = STATUS_MISSING
        elif available_weight < total_weight:
            status = STATUS_PARTIAL
        else:
            status = STATUS_READY

        rows.append({
            "methodology_id": methodology_id,
            "section": section,
            "factor": factor,
            "factor_weight": float(factor_weight),
            "factor_score": factor_score,
            "available_metric_weight": available_weight,
            "total_metric_weight": total_weight,
            "coverage_pct": available_weight / total_weight if total_weight else 0.0,
            "weighted_factor_score": factor_score * float(factor_weight) if factor_score is not None else None,
            "status": status,
        })
    return pd.DataFrame(rows)


def aggregate_section_scores(methodology_id: str, factor_score_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate factors into profile/section scores."""
    if factor_score_df.empty:
        return pd.DataFrame()

    methodology_id = resolve_methodology_id(methodology_id)
    meta = SCHEME_REGISTRY[methodology_id]
    profile_weights = meta.get("profile_weights", {}) or {}

    rows: List[Dict[str, Any]] = []
    for section, group in factor_score_df.groupby("section", dropna=False):
        scored = group[group["factor_score"].notna()].copy()
        available_weight = float(scored["factor_weight"].sum()) if not scored.empty else 0.0
        total_weight = float(group["factor_weight"].sum()) if not group.empty else 0.0

        if available_weight > 0:
            section_score = float((scored["factor_score"] * scored["factor_weight"]).sum() / available_weight)
        else:
            section_score = None

        if available_weight == 0:
            status = STATUS_MISSING
        elif available_weight < total_weight:
            status = STATUS_PARTIAL
        else:
            status = STATUS_READY

        rows.append({
            "methodology_id": methodology_id,
            "section": section,
            "section_weight": float(profile_weights.get(section, 1.0)),
            "section_score": section_score,
            "available_factor_weight": available_weight,
            "total_factor_weight": total_weight,
            "coverage_pct": available_weight / total_weight if total_weight else 0.0,
            "weighted_section_score": section_score * float(profile_weights.get(section, 1.0)) if section_score is not None else None,
            "status": status,
        })
    return pd.DataFrame(rows)


def aggregate_overall_score(methodology_id: str, section_score_df: pd.DataFrame) -> Optional[float]:
    """Aggregate section/profile scores into an overall score."""
    if section_score_df.empty:
        return None
    methodology_id = resolve_methodology_id(methodology_id)
    meta = SCHEME_REGISTRY[methodology_id]
    profile_weights = meta.get("profile_weights", {}) or {}

    scored = section_score_df[section_score_df["section_score"].notna()].copy()
    if scored.empty:
        return None

    if profile_weights:
        available_weight = float(scored["section_weight"].sum())
        if available_weight == 0:
            return None
        return float((scored["section_score"] * scored["section_weight"]).sum() / available_weight)

    # Direct scorecards often have one section or factor weights already sum to 1.
    # Use factor-level aggregation if available via section scores. If multiple
    # sections exist without explicit profile weights, average by section_weight=1.
    available_weight = float(scored["section_weight"].sum())
    return float((scored["section_score"] * scored["section_weight"]).sum() / available_weight) if available_weight else None


def score_to_indicative_rating(methodology_id: str, score: Optional[float]) -> str:
    """Map weighted score to an indicative rating range when a simple scale is available."""
    if score is None:
        return ""
    methodology_id = resolve_methodology_id(methodology_id)
    agency = SCHEME_REGISTRY[methodology_id]["agency"]
    buckets = MOODYS_WEIGHTED_SCORE_BUCKETS if agency == "Moody's" else SP_WEIGHTED_SCORE_BUCKETS
    for lo, hi, rating in buckets:
        if score >= lo and score <= hi:
            return rating
    return ""


def run_factor_engine(
    methodology_id: str,
    formula_results: Any = None,
    metric_scores: Optional[Mapping[str, Any]] = None,
    manual_scores: Optional[Mapping[str, Any]] = None,
    threshold_rules: Optional[pd.DataFrame] = None,
    templates_dir: str | Path = "templates",
) -> Dict[str, Any]:
    """
    Full factor-engine pipeline.

    Returns a dictionary with:
    - metric_scores: metric-level DataFrame
    - factor_scores: factor-level DataFrame
    - section_scores: section/profile-level DataFrame
    - overall_score: numeric weighted score if available
    - indicative_rating: simple scorecard indicated rating/range if available
    - coverage_summary: status counts and coverage percentages
    - scheme: metadata for selected methodology
    """
    methodology_id = resolve_methodology_id(methodology_id)
    metric_df = build_metric_scores(
        methodology_id=methodology_id,
        formula_results=formula_results,
        metric_scores=metric_scores,
        manual_scores=manual_scores,
        threshold_rules=threshold_rules,
        templates_dir=templates_dir,
    )
    factor_df = aggregate_factor_scores(metric_df)
    section_df = aggregate_section_scores(methodology_id, factor_df)
    overall = aggregate_overall_score(methodology_id, section_df)
    indicative = score_to_indicative_rating(methodology_id, overall)

    metric_counts = metric_df["status"].value_counts().to_dict() if not metric_df.empty else {}
    factor_counts = factor_df["status"].value_counts().to_dict() if not factor_df.empty else {}

    coverage_summary = {
        "metric_count": int(len(metric_df)),
        "metrics_ready": int(metric_counts.get(STATUS_READY, 0)),
        "metrics_partial": int(metric_counts.get(STATUS_PARTIAL, 0)),
        "metrics_missing": int(metric_counts.get(STATUS_MISSING, 0)),
        "metrics_manual": int(metric_counts.get(STATUS_MANUAL, 0)),
        "metrics_need_score": int(metric_counts.get(STATUS_NEEDS_SCORE, 0)),
        "metrics_error": int(metric_counts.get(STATUS_ERROR, 0)),
        "factor_count": int(len(factor_df)),
        "factors_ready": int(factor_counts.get(STATUS_READY, 0)),
        "factors_partial": int(factor_counts.get(STATUS_PARTIAL, 0)),
        "factors_missing": int(factor_counts.get(STATUS_MISSING, 0)),
        "overall_score_available": overall is not None,
    }

    return {
        "methodology_id": methodology_id,
        "scheme": {"methodology_id": methodology_id, **SCHEME_REGISTRY[methodology_id]},
        "metric_scores": metric_df,
        "factor_scores": factor_df,
        "section_scores": section_df,
        "overall_score": overall,
        "indicative_rating": indicative,
        "coverage_summary": coverage_summary,
    }


# -----------------------------------------------------------------------------
# Streamlit-friendly helpers
# -----------------------------------------------------------------------------

def get_missing_or_unscored_metrics(engine_output: Dict[str, Any]) -> pd.DataFrame:
    """Return metrics that are not fully score-ready."""
    df = engine_output.get("metric_scores", pd.DataFrame())
    if df is None or df.empty:
        return pd.DataFrame()
    return df[df["status"] != STATUS_READY].copy()


def get_ready_factor_scores(engine_output: Dict[str, Any]) -> pd.DataFrame:
    """Return factor scores with a usable numeric score."""
    df = engine_output.get("factor_scores", pd.DataFrame())
    if df is None or df.empty:
        return pd.DataFrame()
    return df[df["factor_score"].notna()].copy()


def make_scoreboard_preview(engine_output: Dict[str, Any]) -> pd.DataFrame:
    """Compact table for pages/4_Scoreboard.py."""
    factor_df = engine_output.get("factor_scores", pd.DataFrame())
    if factor_df is None or factor_df.empty:
        return pd.DataFrame(columns=["section", "factor", "factor_score", "factor_weight", "status"])
    cols = ["section", "factor", "factor_score", "factor_weight", "weighted_factor_score", "coverage_pct", "status"]
    return factor_df[cols].copy()
