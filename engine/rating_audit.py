from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pandas as pd

from engine.calculator_engine import load_formula_library, parse_required_fields
from engine.factor_engine import load_factor_template, resolve_methodology_id
from engine.rating_engine import _clean_float, _clean_bool, _in_range, load_scoring_thresholds


def _records_by_id(frame: Any, key: str = "formula_id") -> Dict[str, Dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or key not in frame.columns:
        return {}
    return {
        str(row.get(key, "")).strip(): row.to_dict()
        for _, row in frame.iterrows()
        if str(row.get(key, "")).strip()
    }


def _selected_source_rows(source_report: Any) -> pd.DataFrame:
    if not isinstance(source_report, pd.DataFrame) or source_report.empty:
        return pd.DataFrame()
    if "selected" not in source_report.columns:
        return source_report.copy()
    return source_report[source_report["selected"].astype(bool)].copy()


def _format_number(value: Any, *, as_percent: bool = False) -> str:
    num = _clean_float(value)
    if num is None:
        return ""
    if as_percent:
        return f"{num * 100:.1f}%"
    if abs(num) >= 1000:
        return f"{num:,.0f}"
    if abs(num) >= 10:
        return f"{num:,.2f}".rstrip("0").rstrip(".")
    return f"{num:,.3f}".rstrip("0").rstrip(".")


def _looks_like_percent_metric(formula_id: str, metric: str, value_scale: str) -> bool:
    text = f"{formula_id} {metric}".lower()
    if str(value_scale).lower() in {"percent", "percentage"}:
        return True
    return any(token in text for token in ["ratio", "margin", "burden", "growth", "rate", "coverage"])


def _format_bucket(rule: Mapping[str, Any], metric: str = "") -> str:
    raw_label = rule.get("score_label", "")
    label = "" if pd.isna(raw_label) else str(raw_label or "").strip()
    if label:
        return label
    value_scale = str(rule.get("value_scale", "") or "")
    as_percent = _looks_like_percent_metric(str(rule.get("formula_id", "")), metric, value_scale)
    min_v = _clean_float(rule.get("min_value"))
    max_v = _clean_float(rule.get("max_value"))
    min_inc = _clean_bool(rule.get("min_inclusive"), True)
    max_inc = _clean_bool(rule.get("max_inclusive"), True)
    if min_v is None and max_v is None:
        notes = rule.get("notes", "")
        return "" if pd.isna(notes) else str(notes or "").strip()
    if min_v is None:
        return f"{'<=' if max_inc else '<'} {_format_number(max_v, as_percent=as_percent)}"
    if max_v is None:
        return f"{'>=' if min_inc else '>'} {_format_number(min_v, as_percent=as_percent)}"
    return (
        f"{_format_number(min_v, as_percent=as_percent)} "
        f"{'to' if min_inc and max_inc else '-'} "
        f"{_format_number(max_v, as_percent=as_percent)}"
    )


def _rule_matches_value(value: Any, rule: Mapping[str, Any]) -> bool:
    num = _clean_float(value)
    if num is None:
        return False
    min_v = _clean_float(rule.get("min_value"))
    max_v = _clean_float(rule.get("max_value"))
    min_inc = _clean_bool(rule.get("min_inclusive"), True)
    max_inc = _clean_bool(rule.get("max_inclusive"), False if min_v is not None else True)
    return _in_range(float(num), min_v, max_v, min_inc, max_inc)


def _matched_threshold(
    methodology_id: str,
    formula_id: str,
    raw_value: Any,
    numeric_score: Any,
    thresholds: pd.DataFrame,
) -> Dict[str, Any]:
    if thresholds.empty:
        return {}
    rows = thresholds[
        (thresholds["methodology_id"].astype(str) == str(methodology_id))
        & (thresholds["formula_id"].astype(str) == str(formula_id))
        & (thresholds["rule_type"].astype(str) != "overall_rating_bucket")
    ].copy()
    if rows.empty:
        return {}

    score = _clean_float(numeric_score)
    if score is not None:
        scored = rows[rows["score"].map(_clean_float).eq(score)]
        for _, row in scored.iterrows():
            if str(row.get("rule_type", "")) in {"manual_score", "manual_categorical", "manual_or_external_scale"}:
                return row.to_dict()
            if _rule_matches_value(raw_value, row):
                return row.to_dict()
        if not scored.empty:
            return scored.iloc[0].to_dict()

    for _, row in rows.iterrows():
        if _rule_matches_value(raw_value, row):
            return row.to_dict()
    return {}


def _source_lookup(source_report: Any) -> Dict[str, Dict[str, Any]]:
    selected = _selected_source_rows(source_report)
    if selected.empty or "field_name" not in selected.columns:
        return {}
    return {
        str(row.get("field_name", "")).strip(): row.to_dict()
        for _, row in selected.iterrows()
        if str(row.get("field_name", "")).strip()
    }


def _source_label(
    formula_id: str,
    formula_record: Mapping[str, Any],
    formula_required_fields: Mapping[str, list[str]],
    source_by_field: Mapping[str, Mapping[str, Any]],
    manual_scores: Optional[Mapping[str, Any]],
) -> str:
    if manual_scores and formula_id in manual_scores:
        return "Manual Input"
    direct_row = source_by_field.get(formula_id)
    if direct_row:
        source_name = str(direct_row.get("source_name") or direct_row.get("canonical_source") or "").strip()
        if "creditscope" in source_name.lower():
            return "Workbook Direct Metric"
        return source_name or "Direct Source Metric"
    warning = str(formula_record.get("warning", "") or "")
    if "Direct source metric value supplied" in warning:
        return "Workbook Direct Metric"
    input_sources = []
    for field in formula_required_fields.get(formula_id, []):
        source_row = source_by_field.get(field)
        if not source_row:
            continue
        source_name = str(source_row.get("source_name") or source_row.get("canonical_source") or "").strip()
        if source_name:
            input_sources.append(source_name)
    if input_sources:
        unique_sources = list(dict.fromkeys(input_sources))
        if any(name.lower() in {"censusacs", "bea"} or "api" in name.lower() for name in unique_sources):
            return f"Formula Derived from API Source ({', '.join(unique_sources)})"
        return f"Formula Derived ({', '.join(unique_sources)})"
    status = str(formula_record.get("status", "") or "").lower()
    if status == "ready":
        return "Formula Derived"
    return "Not available"


def _formula_required_fields(formula_library_path: str | Path) -> Dict[str, list[str]]:
    formulas = load_formula_library(formula_library_path)
    if formulas.empty or "formula_id" not in formulas.columns:
        return {}
    return {
        str(row.get("formula_id", "")).strip(): [
            field for field in parse_required_fields(row.get("required_data", "")) if field != "manual"
        ]
        for _, row in formulas.iterrows()
        if str(row.get("formula_id", "")).strip()
    }


def build_rating_audit_trail(
    methodology_id: str,
    rating_output: Mapping[str, Any],
    formula_results: Any = None,
    source_report: Any = None,
    issuer_data: Optional[Mapping[str, Any]] = None,
    manual_scores: Optional[Mapping[str, Any]] = None,
    thresholds_path: str | Path = "config/scoring_thresholds.csv",
    formula_library_path: str | Path = "config/formula_library.csv",
    templates_dir: str | Path = "templates",
) -> Dict[str, pd.DataFrame]:
    methodology_id = resolve_methodology_id(methodology_id)
    issuer_data = issuer_data or {}
    manual_scores = manual_scores or {}
    thresholds = load_scoring_thresholds(thresholds_path) if Path(thresholds_path).exists() else pd.DataFrame()
    template = load_factor_template(methodology_id, templates_dir=templates_dir)
    formula_by_id = _records_by_id(formula_results)
    source_by_field = _source_lookup(source_report)
    required_fields = _formula_required_fields(formula_library_path)

    factor_output = rating_output.get("factor_engine_output", {}) if isinstance(rating_output, Mapping) else {}
    metric_df = factor_output.get("metric_scores", pd.DataFrame()) if isinstance(factor_output, Mapping) else pd.DataFrame()
    factor_df = factor_output.get("factor_scores", pd.DataFrame()) if isinstance(factor_output, Mapping) else pd.DataFrame()
    section_df = factor_output.get("section_scores", pd.DataFrame()) if isinstance(factor_output, Mapping) else pd.DataFrame()
    rr = rating_output.get("rating_result", {}) if isinstance(rating_output, Mapping) else {}

    if not isinstance(metric_df, pd.DataFrame) or metric_df.empty:
        metric_df = template.copy()

    metric_rows = []
    for _, row in metric_df.iterrows():
        formula_id = str(row.get("formula_id", "")).strip()
        formula_record = formula_by_id.get(formula_id, {})
        raw_value = row.get("raw_value")
        if _clean_float(raw_value) is None:
            raw_value = formula_record.get("value", issuer_data.get(formula_id))
        numeric_score = row.get("numeric_score")
        rule = _matched_threshold(methodology_id, formula_id, raw_value, numeric_score, thresholds)
        metric_name = str(row.get("metric", "") or formula_id)
        metric_rows.append(
            {
                "section": row.get("section", ""),
                "factor": row.get("factor", ""),
                "metric": metric_name,
                "formula_id": formula_id,
                "raw_metric_value": raw_value,
                "bucket_used": _format_bucket(rule, metric_name) if rule else "",
                "numeric_score": numeric_score,
                "metric_weight": row.get("metric_weight"),
                "factor_weight": row.get("factor_weight"),
                "weighted_contribution": row.get("metric_weighted_score"),
                "source_used": _source_label(formula_id, formula_record, required_fields, source_by_field, manual_scores),
                "formula_status": row.get("formula_status", formula_record.get("status", "")),
                "score_status": row.get("status", ""),
                "threshold_source": "" if not rule or pd.isna(rule.get("source_location", "")) else rule.get("source_location", ""),
                "methodology_source_file": "" if not rule or pd.isna(rule.get("source_file", "")) else rule.get("source_file", ""),
                "reason": "" if rule and pd.isna(rule.get("notes", "")) else (rule.get("notes", "") if rule else row.get("missing_reason", "")),
            }
        )

    metric_trace = pd.DataFrame(metric_rows)

    factor_trace = pd.DataFrame()
    if isinstance(factor_df, pd.DataFrame) and not factor_df.empty:
        factor_trace = factor_df.copy()
        factor_trace["weighted_contribution"] = factor_trace.get("weighted_factor_score")
        factor_trace["calculation"] = factor_trace.apply(
            lambda r: (
                f"{_format_number(r.get('factor_score'))} x "
                f"{_format_number(r.get('factor_weight'))} = "
                f"{_format_number(r.get('weighted_factor_score'))}"
            ),
            axis=1,
        )

    section_trace = pd.DataFrame()
    if isinstance(section_df, pd.DataFrame) and not section_df.empty:
        section_trace = section_df.copy()
        section_trace["calculation"] = section_trace.apply(
            lambda r: (
                f"{_format_number(r.get('section_score'))} x "
                f"{_format_number(r.get('section_weight'))} = "
                f"{_format_number(r.get('weighted_section_score'))}"
            ),
            axis=1,
        )

    final_trace = pd.DataFrame(
        [
            {"step": "Weighted Score", "value": rr.get("overall_score")},
            {"step": "ICP Score", "value": rr.get("icp_score")},
            {"step": "Institutional Framework", "value": rr.get("institutional_framework_score")},
            {"step": "Enterprise Profile", "value": rr.get("enterprise_score")},
            {"step": "Financial Profile", "value": rr.get("financial_score")},
            {"step": "Anchor", "value": rr.get("anchor")},
            {"step": "Final Rating", "value": rr.get("indicative_rating")},
            {"step": "Coverage", "value": rr.get("coverage_status")},
        ]
    )
    final_trace = final_trace[final_trace["value"].notna() & final_trace["value"].astype(str).ne("")]

    return {
        "metric_trace": metric_trace,
        "factor_trace": factor_trace,
        "section_trace": section_trace,
        "final_trace": final_trace,
    }


def audit_trail_to_json(audit: Mapping[str, pd.DataFrame]) -> str:
    payload = {
        name: frame.to_dict(orient="records") if isinstance(frame, pd.DataFrame) else []
        for name, frame in audit.items()
    }
    return json.dumps(payload, indent=2, default=str)


def audit_trail_to_markdown(audit: Mapping[str, pd.DataFrame], title: str = "Rating Audit Trail") -> str:
    lines = [f"# {title}", ""]
    final_trace = audit.get("final_trace", pd.DataFrame())
    if isinstance(final_trace, pd.DataFrame) and not final_trace.empty:
        lines.extend(["## Final Result", ""])
        for _, row in final_trace.iterrows():
            lines.append(f"- {row.get('step')}: {row.get('value')}")
        lines.append("")

    factor_trace = audit.get("factor_trace", pd.DataFrame())
    if isinstance(factor_trace, pd.DataFrame) and not factor_trace.empty:
        lines.extend(["## Factor Contributions", ""])
        for _, row in factor_trace.iterrows():
            lines.append(
                f"- {row.get('factor')}: score {row.get('factor_score')}, "
                f"weight {row.get('factor_weight')}, contribution {row.get('weighted_contribution')}"
            )
        lines.append("")

    metric_trace = audit.get("metric_trace", pd.DataFrame())
    if isinstance(metric_trace, pd.DataFrame) and not metric_trace.empty:
        lines.extend(["## Metric Trace", ""])
        for _, row in metric_trace.iterrows():
            lines.append(f"### {row.get('factor')} - {row.get('metric')}")
            lines.append(f"- Raw input: {row.get('raw_metric_value')}")
            lines.append(f"- Bucket: {row.get('bucket_used')}")
            lines.append(f"- Score: {row.get('numeric_score')}")
            lines.append(f"- Metric weight: {row.get('metric_weight')}")
            lines.append(f"- Source used: {row.get('source_used')}")
            source = row.get("threshold_source")
            if source:
                lines.append(f"- Methodology source: {source}")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def audit_metric_csv(audit: Mapping[str, pd.DataFrame]) -> bytes:
    metric_trace = audit.get("metric_trace", pd.DataFrame())
    if not isinstance(metric_trace, pd.DataFrame):
        metric_trace = pd.DataFrame()
    return metric_trace.to_csv(index=False).encode("utf-8")


def audit_pdf_bytes(markdown_text: str) -> bytes:
    """Create a compact text PDF without optional third-party dependencies."""
    lines = []
    for line in markdown_text.splitlines():
        text = line.replace("#", "").replace("*", "").strip()
        if len(text) <= 96:
            lines.append(text)
            continue
        while len(text) > 96:
            split_at = text.rfind(" ", 0, 96)
            split_at = split_at if split_at > 20 else 96
            lines.append(text[:split_at].strip())
            text = text[split_at:].strip()
        if text:
            lines.append(text)

    pages = [lines[i : i + 48] for i in range(0, len(lines), 48)] or [[]]
    objects: list[bytes] = []

    def add_object(payload: str) -> int:
        objects.append(payload.encode("latin-1", errors="replace"))
        return len(objects)

    catalog_id = add_object("<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add_object("PLACEHOLDER")
    font_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    content_ids = []
    for page_lines in pages:
        text_parts = ["BT", "/F1 10 Tf", "50 760 Td", "14 TL"]
        for raw_line in page_lines:
            escaped = raw_line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            text_parts.append(f"({escaped}) Tj")
            text_parts.append("T*")
        text_parts.append("ET")
        stream = "\n".join(text_parts)
        content_id = add_object(f"<< /Length {len(stream.encode('latin-1', errors='replace'))} >>\nstream\n{stream}\nendstream")
        page_id = add_object(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        )
        content_ids.append(content_id)
        page_ids.append(page_id)

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, payload in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{idx} 0 obj\n".encode("latin-1"))
        pdf.extend(payload)
        pdf.extend(b"\nendobj\n")
    xref_at = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(
        (
            "trailer\n"
            f"<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            "startxref\n"
            f"{xref_at}\n"
            "%%EOF\n"
        ).encode("latin-1")
    )
    return bytes(pdf)
