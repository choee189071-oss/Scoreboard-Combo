"""
Compatibility wrapper for the Calculator Engine.

Keep importing from engine.formula_engine if your Streamlit pages already point here.
The actual implementation lives in engine.calculator_engine.
"""

from engine.calculator_engine import (  # noqa: F401
    STATUS_ERROR,
    STATUS_MANUAL,
    STATUS_MISSING,
    STATUS_READY,
    calculate_all_formulas,
    calculate_formula,
    clean_numeric,
    get_missing_reasons,
    get_ready_metrics,
    load_formula_library,
    parse_required_fields,
    summarize_calculation_results,
)
