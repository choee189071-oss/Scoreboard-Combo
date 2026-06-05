from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from utils.ui_helpers import current_context_card, formula_action, init_state, page_header, readiness_action

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from engine.calculator_engine import calculate_all_formulas, summarize_calculation_results
    from engine.factor_engine import load_factor_template
except Exception as exc:  # pragma: no cover - Streamlit display path
    st.set_page_config(page_title="Calculators", layout="wide")
    st.error("Could not import calculator/factor engines.")
    st.exception(exc)
    st.stop()


st.set_page_config(page_title="Calculators", layout="wide")
init_state()
page_header(
    "Calculators",
    "Run formula_library.csv against canonical issuer_data and save formula_results for Scoreboard.",
    "calculators",
)
current_context_card()

issuer_data = st.session_state.get("issuer_data", {}) or {}
if not issuer_data:
    st.warning("No issuer_data found. Go to Data Mapping first.")
    st.stop()

methodology_id = st.session_state.get("methodology_id", "moodys_ccd_go")


def _split_missing_fields(value: Any) -> list[str]:
    fields: list[str] = []
    for token in str(value or "").replace(",", ";").split(";"):
        field = token.strip()
        if field and field.lower() not in {"manual", "nan", "none"}:
            fields.append(field)
    return sorted(set(fields))


def _coerce_manual_value(value: Any) -> Any:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except Exception:
        return text

try:
    all_formula_df = calculate_all_formulas(issuer_data, formula_library="config/formula_library.csv")
    template = load_factor_template(methodology_id, templates_dir="templates")
except Exception as exc:
    st.error("Formula calculation failed.")
    st.exception(exc)
    st.stop()

template_formula_ids = set(template["formula_id"].dropna().astype(str))
try:
    thresholds = pd.read_csv("config/scoring_thresholds.csv")
    related_thresholds = thresholds[thresholds["methodology_id"].astype(str) == str(methodology_id)]
    secondary_ids = set(related_thresholds["secondary_formula_id"].dropna().astype(str))
    secondary_ids.discard("")
    secondary_ids.discard("nan")
    template_formula_ids |= secondary_ids
except Exception:
    pass
method_formula_df = all_formula_df[all_formula_df["formula_id"].astype(str).isin(template_formula_ids)].copy()

summary = summarize_calculation_results(method_formula_df)
cols = st.columns(4)
cols[0].metric("Ready", summary.get("ready", 0))
cols[1].metric("Missing", summary.get("missing", 0))
cols[2].metric("Manual", summary.get("manual", 0))
cols[3].metric("Error", summary.get("error", 0))
formula_action(summary)

source_report = st.session_state.get("source_report")
if source_report is not None:
    with st.expander("Source coverage feeding these formulas", expanded=False):
        readiness_action(source_report)

st.subheader("Methodology Formula Results")
st.caption("Filtered to formula_id values used by the selected template and related threshold logic.")
display_cols = [
    "formula_id",
    "formula_name",
    "category",
    "status",
    "value",
    "missing_fields",
    "warning",
    "error",
]
available_cols = [c for c in display_cols if c in method_formula_df.columns]
missing_df = method_formula_df[method_formula_df["status"] != "ready"].copy()
ready_df = method_formula_df[method_formula_df["status"] == "ready"].copy()

missing_raw_fields: list[str] = []
if "missing_fields" in missing_df.columns:
    for raw in missing_df["missing_fields"].tolist():
        missing_raw_fields.extend(_split_missing_fields(raw))
missing_raw_fields = sorted(set(missing_raw_fields))

if missing_raw_fields:
    with st.expander("Manual inputs for missing raw fields", expanded=True):
        st.caption("Use this only to unblock calculation when a field is not available from upload/API sources yet.")
        manual_patch_rows = [
            {
                "field_name": field,
                "value": st.session_state.get("issuer_data", {}).get(field, ""),
                "used_by_missing_formula": "; ".join(
                    sorted(
                        set(
                            missing_df[
                                missing_df["missing_fields"].astype(str).str.contains(field, regex=False, na=False)
                            ]["formula_id"].astype(str)
                        )
                    )
                ),
            }
            for field in missing_raw_fields
        ]
        manual_patch_df = pd.DataFrame(manual_patch_rows)
        edited_patch = st.data_editor(
            manual_patch_df,
            width="stretch",
            hide_index=True,
            num_rows="fixed",
            key=f"calculator_manual_patch_{methodology_id}",
            column_config={
                "field_name": st.column_config.TextColumn("field_name", disabled=True),
                "value": st.column_config.TextColumn("value"),
                "used_by_missing_formula": st.column_config.TextColumn("used_by_missing_formula", disabled=True),
            },
        )
        if st.button("Save manual inputs and recalculate"):
            patch = {
                str(row.get("field_name", "")).strip(): _coerce_manual_value(row.get("value"))
                for _, row in edited_patch.iterrows()
            }
            patch = {field: value for field, value in patch.items() if field and value is not None}
            st.session_state["issuer_data"] = {**(st.session_state.get("issuer_data", {}) or {}), **patch}
            st.success(f"Saved {len(patch)} manual fields. Recalculating.")
            st.rerun()

tab1, tab2, tab3 = st.tabs(["All Methodology Formulas", "Missing / Manual / Error", "Ready"])
with tab1:
    st.dataframe(method_formula_df[available_cols], width="stretch", hide_index=True)
with tab2:
    st.dataframe(missing_df[available_cols], width="stretch", hide_index=True) if not missing_df.empty else st.info("No missing, manual, or error formulas.")
with tab3:
    st.dataframe(ready_df[available_cols], width="stretch", hide_index=True) if not ready_df.empty else st.info("No ready formulas yet.")

with st.expander("All formula_library results", expanded=False):
    st.dataframe(all_formula_df, width="stretch", hide_index=True)

if st.button("Save formula results", type="primary"):
    st.session_state["formula_results"] = all_formula_df
    st.session_state["methodology_formula_results"] = method_formula_df
    st.success(f"Saved {len(all_formula_df)} formula results. Go to Scoreboard next.")

st.download_button(
    "Download methodology_formula_results.csv",
    method_formula_df.to_csv(index=False).encode("utf-8"),
    "methodology_formula_results.csv",
    "text/csv",
)
