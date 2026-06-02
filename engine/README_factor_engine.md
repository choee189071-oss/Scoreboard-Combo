# Factor Engine Patch

Add this file to your project:

```text
engine/factor_engine.py
```

Optional reference file:

```text
config/factor_scheme_registry.csv
```

## What it does

The factor engine reads your existing methodology templates:

```text
templates/moodys_ccd_go.csv
templates/moodys_k12.csv
templates/sp_community_college_go.csv
templates/sp_local_gov_k12.csv
templates/sp_water_sewer.csv
```

Then it joins those templates with formula results from `engine.formula_engine.calculate_all_formulas()`.

It produces:

- metric-level status: ready / missing / manual / needs_score / error
- factor-level scores
- section/profile-level scores
- overall weighted score when scores are available
- indicative rating/range when a simple score scale is available

## Important design note

This engine does **not** pretend that every raw metric can already be turned into a score.

Example:

```text
full_value_per_capita = 121,794
```

That is a calculated metric, but the system still needs a scoring rule to know whether it is Aaa / Aa / A or 1 / 2 / 3.

So the engine will mark the metric as:

```text
needs_score
```

until you provide either:

1. a manual score from the UI, or
2. a score threshold table later.

## Minimal usage

```python
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

print(out["factor_scores"])
print(out["overall_score"])
print(out["indicative_rating"])
```

## Supported methodology IDs

```text
moodys_ccd_go
moodys_k12
sp_local_gov_k12
sp_local_gov
sp_us_government_2024
sp_water_sewer
sp_community_college_go
```

## Next step after this patch

Build a small scoring-threshold table for one methodology first, probably:

```text
sp_local_gov_k12
```

or

```text
moodys_ccd_go
```

Then the pipeline becomes:

```text
Mapping Engine
↓
Calculator Engine
↓
Scoring Threshold Engine
↓
Factor Engine
↓
Rating Engine
```
