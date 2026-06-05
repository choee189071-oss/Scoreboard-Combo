from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pandas as pd
import streamlit as st


def manual_score_candidates(
    methodology_id: str,
    template: pd.DataFrame,
    thresholds_path: str | Path = "config/scoring_thresholds.csv",
) -> pd.DataFrame:
    template_rows = pd.DataFrame()
    if isinstance(template, pd.DataFrame) and not template.empty and "source_priority" in template.columns:
        mask = template["source_priority"].astype(str).str.contains("Manual", case=False, na=False)
        template_rows = template.loc[mask, ["section", "factor", "metric", "formula_id"]].copy()

    threshold_rows = pd.DataFrame()
    path = Path(thresholds_path)
    if path.exists():
        thresholds = pd.read_csv(path)
        if {"methodology_id", "rule_type", "formula_id"}.issubset(thresholds.columns):
            manual_mask = (
                thresholds["methodology_id"].astype(str).eq(str(methodology_id))
                & thresholds["rule_type"].astype(str).str.contains("manual", case=False, na=False)
            )
            rename_map = {"metric_name": "metric"}
            cols = ["section", "factor", "metric_name", "formula_id"]
            threshold_rows = thresholds.loc[manual_mask, [c for c in cols if c in thresholds.columns]].rename(
                columns=rename_map
            )

    rows = pd.concat([template_rows, threshold_rows], ignore_index=True)
    if rows.empty:
        return pd.DataFrame(columns=["section", "factor", "metric", "formula_id"])
    for col in ["section", "factor", "metric", "formula_id"]:
        if col not in rows.columns:
            rows[col] = ""
    return rows[["section", "factor", "metric", "formula_id"]].drop_duplicates("formula_id").sort_values(
        ["section", "factor", "formula_id"]
    )


def manual_scores_from_editor(edited_manual: pd.DataFrame) -> Dict[str, Any]:
    manual_scores: Dict[str, Any] = {}
    if not isinstance(edited_manual, pd.DataFrame) or edited_manual.empty:
        return manual_scores
    for _, row in edited_manual.iterrows():
        fid = str(row.get("formula_id", "")).strip()
        if not fid:
            continue
        numeric = pd.to_numeric(row.get("numeric_score"), errors="coerce")
        label = str(row.get("score_label", "") or "").strip()
        if pd.notna(numeric):
            manual_scores[fid] = {"numeric_score": float(numeric), "score_label": label}
        elif label:
            manual_scores[fid] = {"score_label": label}
    return manual_scores


def render_manual_score_editor(
    methodology_id: str,
    template: pd.DataFrame,
    formula_results: pd.DataFrame | None = None,
    key_prefix: str = "manual_scores",
) -> Dict[str, Any]:
    candidates = manual_score_candidates(methodology_id, template)
    stored = st.session_state.setdefault("manual_scores", {})
    if candidates.empty:
        st.info("No manual qualitative or methodology anchor fields are required for this methodology.")
        return {}
    candidate_ids = set(candidates["formula_id"].astype(str))

    formula_status = {}
    if isinstance(formula_results, pd.DataFrame) and not formula_results.empty and "status" in formula_results.columns:
        formula_status = formula_results.set_index("formula_id")["status"].to_dict()

    rows = candidates.copy()
    rows["formula_status"] = rows["formula_id"].map(formula_status).fillna("manual")
    rows["numeric_score"] = rows["formula_id"].map(
        lambda fid: stored.get(fid, {}).get("numeric_score") if isinstance(stored.get(fid), dict) else stored.get(fid)
    )
    rows["score_label"] = rows["formula_id"].map(
        lambda fid: stored.get(fid, {}).get("score_label", "") if isinstance(stored.get(fid), dict) else ""
    )

    if "institutional_framework_rating" in set(rows["formula_id"].astype(str)):
        st.info(
            "S&P Local Government needs Institutional Framework Rating in addition to the ICP score. "
            "For the West Sacramento fixture, use 2 if you are reproducing the official sample."
        )

    edited = st.data_editor(
        rows[["section", "factor", "metric", "formula_id", "formula_status", "numeric_score", "score_label"]],
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        key=f"{key_prefix}_{methodology_id}",
        column_config={
            "section": st.column_config.TextColumn("section", disabled=True),
            "factor": st.column_config.TextColumn("factor", disabled=True),
            "metric": st.column_config.TextColumn("metric", disabled=True),
            "formula_id": st.column_config.TextColumn("formula_id", disabled=True),
            "formula_status": st.column_config.TextColumn("formula_status", disabled=True),
            "numeric_score": st.column_config.NumberColumn(
                "numeric_score", min_value=0.0, max_value=30.0, step=0.01
            ),
            "score_label": st.column_config.TextColumn("score_label"),
        },
    )

    scores = manual_scores_from_editor(edited)
    for fid in candidate_ids:
        stored.pop(fid, None)
    stored.update(scores)
    return {fid: stored[fid] for fid in candidate_ids if fid in stored}
