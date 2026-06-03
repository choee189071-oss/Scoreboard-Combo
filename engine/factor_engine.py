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
