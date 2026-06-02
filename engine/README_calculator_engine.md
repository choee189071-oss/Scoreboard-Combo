# Calculator Engine Usage

This patch adds:

```text
engine/calculator_engine.py
engine/formula_engine.py
```

`formula_engine.py` is a compatibility wrapper, so Streamlit pages can import either:

```python
from engine.calculator_engine import calculate_all_formulas
```

or:

```python
from engine.formula_engine import calculate_all_formulas
```

## Minimal example

```python
from engine.formula_engine import calculate_all_formulas, summarize_calculation_results

issuer_data = {
    "population": 530000,
    "full_value": 40000000000,
    "net_direct_debt": 700000000,
    "operating_revenue": 100000000,
    "operating_expense": 85000000,
    "mads": 10000000,
}

results = calculate_all_formulas(
    issuer_data,
    formula_library="config/formula_library.csv"
)

print(results)
print(summarize_calculation_results(results))
```

## Output statuses

- `ready`: formula calculated successfully
- `missing`: one or more required raw fields are missing
- `manual`: qualitative/manual formula, not calculated by the engine
- `error`: all fields existed, but the formula failed, usually division by zero or unsupported expression

## Notes

The engine supports your current `formula_library.csv` columns:

```text
formula_id, formula_name, expression, required_data, category
```

It supports arithmetic formulas like:

```text
full_value / population
(operating_revenue - operating_expense) / operating_revenue
((enrollment_current/enrollment_3yr_prior)^(1/3))-1
```

It also supports `avg_3yr(...)` and `avg_5yr(...)`. If only scalar current-year values are available, it returns a current-year proxy with a warning.
