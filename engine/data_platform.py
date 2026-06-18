"""Data platform catalog helpers.

This module turns the project's source registry, field dictionary, field
aliases, source priority rules, methodology templates, and audit coverage into
reviewable tables. It is intentionally metadata-only: it does not fetch data or
calculate ratings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from engine.calculator_engine import load_formula_library, parse_required_fields
from engine.data_sourcing_engine import expand_source_priority, load_source_priority
from engine.factor_engine import load_factor_template
from engine.methodology_audit import AUDIT_METHODOLOGIES, methodology_formula_ids
from engine.source_registry import canonical_source_name, load_source_registry, split_pipe


DEFAULT_DATA_DICTIONARY_PATH = Path("config/data_dictionary.csv")
DEFAULT_FIELD_MAPPING_PATH = Path("config/field_mapping.csv")
DEFAULT_SOURCE_PRIORITY_PATH = Path("config/source_priority.csv")
DEFAULT_SOURCE_REGISTRY_PATH = Path("config/source_registry.csv")
DEFAULT_FORMULA_LIBRARY_PATH = Path("config/formula_library.csv")
DEFAULT_TEMPLATES_DIR = Path("templates")
DEFAULT_AUDIT_COVERAGE_PATH = Path("work/methodology_accuracy_matrix/field_coverage.csv")


def _read_csv(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _clean_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _join_unique(values: list[Any] | pd.Series) -> str:
    items: list[str] = []
    for value in values:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        for part in split_pipe(value):
            if part and part not in items:
                items.append(part)
    return "|".join(items)


def _join_unique_semicolon(values: list[Any] | pd.Series) -> str:
    items: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text.lower() != "nan" and text not in items:
            items.append(text)
    return "; ".join(items)


def _load_data_dictionary(path: str | Path = DEFAULT_DATA_DICTIONARY_PATH) -> pd.DataFrame:
    df = _read_csv(path)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "field_name",
                "field_category",
                "preferred_source",
                "fallback_source",
                "automation_level",
                "notes",
            ]
        )
    df = df.copy()
    for col in ["field_name", "field_category", "preferred_source", "fallback_source", "automation_level", "notes"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip()
    return df[df["field_name"].ne("")].drop_duplicates(subset=["field_name"], keep="first")


def _field_mapping_summary(path: str | Path = DEFAULT_FIELD_MAPPING_PATH) -> pd.DataFrame:
    mapping = _read_csv(path)
    if mapping.empty or "field_name" not in mapping.columns:
        return pd.DataFrame(
            columns=[
                "field_name",
                "mapped_sources",
                "alias_count",
                "aliases",
                "match_types",
                "mapping_notes",
            ]
        )
    mapping = mapping.copy()
    for col in ["field_name", "source_name", "possible_column_names", "match_type", "notes"]:
        if col not in mapping.columns:
            mapping[col] = ""
        mapping[col] = mapping[col].fillna("").astype(str).str.strip()

    rows: list[dict[str, Any]] = []
    for field_name, group in mapping[mapping["field_name"].ne("")].groupby("field_name", sort=True):
        aliases: list[str] = []
        for raw_aliases in group["possible_column_names"].tolist():
            for alias in split_pipe(raw_aliases):
                if alias not in aliases:
                    aliases.append(alias)
        rows.append(
            {
                "field_name": field_name,
                "mapped_sources": _join_unique(group["source_name"]),
                "alias_count": len(aliases),
                "aliases": "|".join(aliases),
                "match_types": _join_unique(group["match_type"]),
                "mapping_notes": _join_unique_semicolon(group["notes"]),
            }
        )
    return pd.DataFrame(rows)


def _formula_lookup(path: str | Path = DEFAULT_FORMULA_LIBRARY_PATH) -> dict[str, dict[str, Any]]:
    formulas = load_formula_library(path)
    out: dict[str, dict[str, Any]] = {}
    for _, row in formulas.iterrows():
        formula_id = _clean_text(row.get("formula_id"))
        if not formula_id:
            continue
        out[formula_id] = row.to_dict()
    return out


def build_methodology_field_needs(
    methodology_ids: list[str] | tuple[str, ...] = tuple(AUDIT_METHODOLOGIES),
    formula_library_path: str | Path = DEFAULT_FORMULA_LIBRARY_PATH,
    templates_dir: str | Path = DEFAULT_TEMPLATES_DIR,
) -> pd.DataFrame:
    """Build one row per methodology/formula/raw-field dependency."""
    formula_by_id = _formula_lookup(formula_library_path)
    rows: list[dict[str, Any]] = []

    for methodology_id in methodology_ids:
        template = load_factor_template(methodology_id, templates_dir=templates_dir)
        template_by_formula: dict[str, list[dict[str, Any]]] = {}
        for _, row in template.iterrows():
            formula_id = _clean_text(row.get("formula_id"))
            if not formula_id:
                continue
            template_by_formula.setdefault(formula_id, []).append(row.to_dict())

        ids = methodology_formula_ids(methodology_id, templates_dir=templates_dir)
        formula_ids = sorted(ids["all_ids"])
        for formula_id in formula_ids:
            formula = formula_by_id.get(formula_id, {})
            required_fields = parse_required_fields(formula.get("required_data", ""))
            if not required_fields:
                required_fields = ["no_raw_field_required"]
            template_rows = template_by_formula.get(formula_id, [{}])
            for field_name in required_fields:
                if field_name == "manual":
                    field_name = "manual_score"
                for template_row in template_rows:
                    rows.append(
                        {
                            "methodology_id": methodology_id,
                            "section": template_row.get("section", ""),
                            "factor": template_row.get("factor", ""),
                            "metric": template_row.get("metric", ""),
                            "formula_id": formula_id,
                            "formula_name": formula.get("formula_name", ""),
                            "field_name": field_name,
                            "template_source_priority": template_row.get("source_priority", ""),
                            "formula_expression": formula.get("expression", ""),
                            "formula_required_data": formula.get("required_data", ""),
                        }
                    )
    return pd.DataFrame(rows)


def _usage_summary(field_needs: pd.DataFrame) -> pd.DataFrame:
    if field_needs.empty:
        return pd.DataFrame(
            columns=["field_name", "methodology_count", "methodologies", "formula_count", "formula_ids", "metrics"]
        )
    useable = field_needs[
        ~field_needs["field_name"].astype(str).isin(["manual_score", "no_raw_field_required"])
    ].copy()
    if useable.empty:
        return pd.DataFrame(
            columns=["field_name", "methodology_count", "methodologies", "formula_count", "formula_ids", "metrics"]
        )
    grouped = useable.groupby("field_name", as_index=False).agg(
        methodology_count=("methodology_id", lambda s: len(set(_clean_text(v) for v in s if _clean_text(v)))),
        methodologies=("methodology_id", lambda s: "|".join(sorted(set(_clean_text(v) for v in s if _clean_text(v))))),
        formula_count=("formula_id", lambda s: len(set(_clean_text(v) for v in s if _clean_text(v)))),
        formula_ids=("formula_id", lambda s: "|".join(sorted(set(_clean_text(v) for v in s if _clean_text(v))))),
        metrics=("metric", _join_unique_semicolon),
    )
    return grouped


def _priority_summary(
    source_priority_path: str | Path = DEFAULT_SOURCE_PRIORITY_PATH,
    data_dictionary_path: str | Path = DEFAULT_DATA_DICTIONARY_PATH,
    formula_library_path: str | Path = DEFAULT_FORMULA_LIBRARY_PATH,
) -> pd.DataFrame:
    priority = load_source_priority(
        source_priority_path,
        data_dictionary_path=data_dictionary_path,
        formula_library_path=formula_library_path,
    )
    if priority.empty:
        return pd.DataFrame(
            columns=["field_name", "priority_sources", "methodology_specific_priority", "min_confidence", "manual_allowed"]
        )
    rows = []
    for field_name, group in priority.groupby("field_name", sort=True):
        rows.append(
            {
                "field_name": field_name,
                "priority_sources": _join_unique(group["priority_sources"]),
                "methodology_specific_priority": bool(
                    group["methodology_id"].fillna("").astype(str).str.strip().ne("default").any()
                ),
                "min_confidence": float(pd.to_numeric(group["min_confidence"], errors="coerce").min()),
                "manual_allowed": bool(group["manual_allowed"].astype(bool).any()),
                "priority_notes": _join_unique_semicolon(group.get("notes", pd.Series(dtype=str))),
            }
        )
    return pd.DataFrame(rows)


def _audit_coverage_summary(path: str | Path = DEFAULT_AUDIT_COVERAGE_PATH) -> pd.DataFrame:
    coverage = _read_csv(path)
    if coverage.empty or "field_name" not in coverage.columns:
        return pd.DataFrame(
            columns=[
                "field_name",
                "audit_case_count",
                "audit_available_primary_raw",
                "audit_missing_primary_raw",
                "audit_no_primary_raw_sheet",
                "audit_field_blocking",
                "audit_coverage_statuses",
            ]
        )
    coverage = coverage.copy()
    coverage["field_name"] = coverage["field_name"].fillna("").astype(str).str.strip()
    coverage = coverage[coverage["field_name"].ne("")]
    coverage["coverage_status"] = coverage.get("coverage_status", pd.Series(dtype=str)).fillna("").astype(str)
    coverage["field_blocking"] = coverage.get("field_blocking", pd.Series(dtype=object)).fillna(False).astype(str).str.lower().isin(
        ["true", "1", "yes"]
    )
    return coverage.groupby("field_name", as_index=False).agg(
        audit_case_count=("fixture_key", lambda s: len(set(_clean_text(v) for v in s if _clean_text(v)))),
        audit_available_primary_raw=("coverage_status", lambda s: int(s.eq("available_primary_raw").sum())),
        audit_missing_primary_raw=("coverage_status", lambda s: int(s.eq("missing_primary_raw").sum())),
        audit_no_primary_raw_sheet=("coverage_status", lambda s: int(s.eq("no_primary_raw_sheet").sum())),
        audit_field_blocking=("field_blocking", "sum"),
        audit_coverage_statuses=("coverage_status", lambda s: "|".join(sorted(set(_clean_text(v) for v in s if _clean_text(v))))),
    )


def build_field_dictionary_catalog(
    data_dictionary_path: str | Path = DEFAULT_DATA_DICTIONARY_PATH,
    field_mapping_path: str | Path = DEFAULT_FIELD_MAPPING_PATH,
    source_priority_path: str | Path = DEFAULT_SOURCE_PRIORITY_PATH,
    formula_library_path: str | Path = DEFAULT_FORMULA_LIBRARY_PATH,
    templates_dir: str | Path = DEFAULT_TEMPLATES_DIR,
    audit_coverage_path: str | Path = DEFAULT_AUDIT_COVERAGE_PATH,
) -> pd.DataFrame:
    dictionary = _load_data_dictionary(data_dictionary_path)
    aliases = _field_mapping_summary(field_mapping_path)
    field_needs = build_methodology_field_needs(formula_library_path=formula_library_path, templates_dir=templates_dir)
    usage = _usage_summary(field_needs)
    priority = _priority_summary(source_priority_path, data_dictionary_path, formula_library_path)
    audit = _audit_coverage_summary(audit_coverage_path)

    field_names = sorted(
        set(dictionary.get("field_name", pd.Series(dtype=str)).dropna().astype(str))
        | set(aliases.get("field_name", pd.Series(dtype=str)).dropna().astype(str))
        | set(usage.get("field_name", pd.Series(dtype=str)).dropna().astype(str))
        | set(priority.get("field_name", pd.Series(dtype=str)).dropna().astype(str))
        | set(audit.get("field_name", pd.Series(dtype=str)).dropna().astype(str))
    )
    base = pd.DataFrame({"field_name": field_names})
    out = base.merge(dictionary, on="field_name", how="left")
    out = out.merge(aliases, on="field_name", how="left")
    out = out.merge(priority, on="field_name", how="left")
    out = out.merge(usage, on="field_name", how="left")
    out = out.merge(audit, on="field_name", how="left")

    for col in [
        "field_category",
        "preferred_source",
        "fallback_source",
        "automation_level",
        "notes",
        "mapped_sources",
        "aliases",
        "match_types",
        "mapping_notes",
        "priority_sources",
        "priority_notes",
        "methodologies",
        "formula_ids",
        "metrics",
        "audit_coverage_statuses",
    ]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str)
    for col in [
        "alias_count",
        "methodology_count",
        "formula_count",
        "audit_case_count",
        "audit_available_primary_raw",
        "audit_missing_primary_raw",
        "audit_no_primary_raw_sheet",
        "audit_field_blocking",
    ]:
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int)
    if "min_confidence" not in out.columns:
        out["min_confidence"] = None
    out["dictionary_status"] = out["field_category"].apply(lambda value: "configured" if _clean_text(value) else "missing")
    out["alias_status"] = out["alias_count"].apply(lambda count: "configured" if int(count) > 0 else "missing")
    out["source_priority_status"] = out["priority_sources"].apply(lambda value: "configured" if _clean_text(value) else "missing")
    out["used_by_methodology"] = out["methodology_count"].gt(0)

    def readiness(row: pd.Series) -> str:
        if row["dictionary_status"] == "missing" and row["used_by_methodology"]:
            return "dictionary_missing"
        if row["source_priority_status"] == "missing" and row["used_by_methodology"]:
            return "source_priority_missing"
        if row["alias_status"] == "missing" and any(
            source in str(row.get("priority_sources", ""))
            for source in ["CreditScope", "OS", "ACFR", "MoodysWorkbook", "AnnualReport", "CountyAssessor", "RateStudy"]
        ):
            return "alias_mapping_missing"
        if int(row.get("audit_field_blocking", 0)) > 0:
            return "benchmark_blocking_gap"
        if int(row.get("audit_missing_primary_raw", 0)) > 0:
            return "benchmark_raw_missing"
        if row["used_by_methodology"]:
            return "configured"
        return "unused_reference_field"

    out["readiness_status"] = out.apply(readiness, axis=1)
    preferred = [
        "readiness_status",
        "field_name",
        "field_category",
        "preferred_source",
        "fallback_source",
        "priority_sources",
        "automation_level",
        "dictionary_status",
        "source_priority_status",
        "alias_status",
        "alias_count",
        "mapped_sources",
        "aliases",
        "methodology_count",
        "methodologies",
        "formula_count",
        "formula_ids",
        "metrics",
        "audit_available_primary_raw",
        "audit_missing_primary_raw",
        "audit_no_primary_raw_sheet",
        "audit_field_blocking",
        "audit_coverage_statuses",
        "min_confidence",
        "manual_allowed",
        "notes",
        "mapping_notes",
        "priority_notes",
    ]
    return out[[col for col in preferred if col in out.columns] + [col for col in out.columns if col not in preferred]]


def build_source_field_matrix(
    data_dictionary_path: str | Path = DEFAULT_DATA_DICTIONARY_PATH,
    source_priority_path: str | Path = DEFAULT_SOURCE_PRIORITY_PATH,
    source_registry_path: str | Path = DEFAULT_SOURCE_REGISTRY_PATH,
    formula_library_path: str | Path = DEFAULT_FORMULA_LIBRARY_PATH,
) -> pd.DataFrame:
    registry = load_source_registry(source_registry_path)
    priority = load_source_priority(
        source_priority_path,
        data_dictionary_path=data_dictionary_path,
        formula_library_path=formula_library_path,
    )
    expanded = expand_source_priority(priority, registry=registry)
    if expanded.empty:
        return expanded
    dictionary = _load_data_dictionary(data_dictionary_path)
    source_meta = registry.set_index("source_name").to_dict(orient="index")
    out = expanded.merge(
        dictionary[["field_name", "field_category", "automation_level", "notes"]],
        on="field_name",
        how="left",
    )
    out["source_type"] = out["source_name"].map(lambda source: source_meta.get(source, {}).get("source_type", ""))
    out["access_method"] = out["source_name"].map(lambda source: source_meta.get(source, {}).get("access_method", ""))
    out["structured"] = out["source_name"].map(lambda source: source_meta.get(source, {}).get("structured", False))
    out["requires_upload"] = out["source_name"].map(lambda source: source_meta.get(source, {}).get("requires_upload", False))
    out["requires_api_key"] = out["source_name"].map(lambda source: source_meta.get(source, {}).get("requires_api_key", False))
    out["default_confidence"] = out["source_name"].map(lambda source: source_meta.get(source, {}).get("default_confidence", None))
    preferred = [
        "source_name",
        "source_type",
        "access_method",
        "field_name",
        "field_category",
        "methodology_id",
        "priority_rank",
        "min_confidence",
        "manual_allowed",
        "structured",
        "requires_upload",
        "requires_api_key",
        "default_confidence",
        "automation_level",
        "priority_notes",
        "notes",
    ]
    return out[[col for col in preferred if col in out.columns] + [col for col in out.columns if col not in preferred]]


def build_source_catalog(
    data_dictionary_path: str | Path = DEFAULT_DATA_DICTIONARY_PATH,
    field_mapping_path: str | Path = DEFAULT_FIELD_MAPPING_PATH,
    source_priority_path: str | Path = DEFAULT_SOURCE_PRIORITY_PATH,
    source_registry_path: str | Path = DEFAULT_SOURCE_REGISTRY_PATH,
    formula_library_path: str | Path = DEFAULT_FORMULA_LIBRARY_PATH,
    templates_dir: str | Path = DEFAULT_TEMPLATES_DIR,
) -> pd.DataFrame:
    registry = load_source_registry(source_registry_path)
    source_field = build_source_field_matrix(
        data_dictionary_path=data_dictionary_path,
        source_priority_path=source_priority_path,
        source_registry_path=source_registry_path,
        formula_library_path=formula_library_path,
    )
    aliases = _field_mapping_summary(field_mapping_path)
    dictionary = build_field_dictionary_catalog(
        data_dictionary_path=data_dictionary_path,
        field_mapping_path=field_mapping_path,
        source_priority_path=source_priority_path,
        formula_library_path=formula_library_path,
        templates_dir=templates_dir,
    )

    alias_rows: list[dict[str, Any]] = []
    for _, row in aliases.iterrows():
        field_name = row.get("field_name", "")
        for source in split_pipe(row.get("mapped_sources", "")):
            alias_rows.append({"source_name": canonical_source_name(source, registry=registry), "field_name": field_name})
    alias_df = pd.DataFrame(alias_rows)

    rows: list[dict[str, Any]] = []
    for _, source in registry.iterrows():
        source_name = _clean_text(source.get("source_name"))
        priority_fields = (
            source_field[source_field["source_name"].astype(str).eq(source_name)]["field_name"].dropna().astype(str).unique().tolist()
            if not source_field.empty
            else []
        )
        mapped_fields = (
            alias_df[alias_df["source_name"].astype(str).eq(source_name)]["field_name"].dropna().astype(str).unique().tolist()
            if not alias_df.empty
            else []
        )
        dictionary_fields = dictionary[
            dictionary["priority_sources"].fillna("").astype(str).str.contains(source_name, regex=False)
        ]["field_name"].dropna().astype(str).unique().tolist()
        used_fields = dictionary[
            dictionary["field_name"].astype(str).isin(set(priority_fields) | set(mapped_fields) | set(dictionary_fields))
            & dictionary["used_by_methodology"].astype(bool)
        ]["field_name"].dropna().astype(str).unique().tolist()
        blocking_fields = dictionary[
            dictionary["field_name"].astype(str).isin(set(priority_fields) | set(mapped_fields) | set(dictionary_fields))
            & pd.to_numeric(dictionary["audit_field_blocking"], errors="coerce").fillna(0).gt(0)
        ]["field_name"].dropna().astype(str).unique().tolist()
        rows.append(
            {
                **source.to_dict(),
                "priority_field_count": len(priority_fields),
                "mapped_field_count": len(mapped_fields),
                "methodology_field_count": len(used_fields),
                "benchmark_blocking_field_count": len(blocking_fields),
                "priority_fields": "|".join(sorted(priority_fields)),
                "mapped_fields": "|".join(sorted(mapped_fields)),
                "methodology_fields": "|".join(sorted(used_fields)),
                "benchmark_blocking_fields": "|".join(sorted(blocking_fields)),
            }
        )
    return pd.DataFrame(rows)


def build_methodology_field_matrix(
    data_dictionary_path: str | Path = DEFAULT_DATA_DICTIONARY_PATH,
    field_mapping_path: str | Path = DEFAULT_FIELD_MAPPING_PATH,
    source_priority_path: str | Path = DEFAULT_SOURCE_PRIORITY_PATH,
    formula_library_path: str | Path = DEFAULT_FORMULA_LIBRARY_PATH,
    templates_dir: str | Path = DEFAULT_TEMPLATES_DIR,
    audit_coverage_path: str | Path = DEFAULT_AUDIT_COVERAGE_PATH,
) -> pd.DataFrame:
    needs = build_methodology_field_needs(formula_library_path=formula_library_path, templates_dir=templates_dir)
    dictionary = build_field_dictionary_catalog(
        data_dictionary_path=data_dictionary_path,
        field_mapping_path=field_mapping_path,
        source_priority_path=source_priority_path,
        formula_library_path=formula_library_path,
        templates_dir=templates_dir,
        audit_coverage_path=audit_coverage_path,
    )
    if needs.empty:
        return needs
    grouped = needs.groupby(["methodology_id", "field_name"], as_index=False).agg(
        formula_count=("formula_id", lambda s: len(set(_clean_text(v) for v in s if _clean_text(v)))),
        formula_ids=("formula_id", lambda s: "|".join(sorted(set(_clean_text(v) for v in s if _clean_text(v))))),
        metrics=("metric", _join_unique_semicolon),
        factors=("factor", _join_unique_semicolon),
        template_source_priority=("template_source_priority", _join_unique),
    )
    out = grouped.merge(
        dictionary[
            [
                "field_name",
                "readiness_status",
                "field_category",
                "preferred_source",
                "fallback_source",
                "priority_sources",
                "automation_level",
                "dictionary_status",
                "source_priority_status",
                "alias_status",
                "alias_count",
                "mapped_sources",
                "audit_available_primary_raw",
                "audit_missing_primary_raw",
                "audit_no_primary_raw_sheet",
                "audit_field_blocking",
                "audit_coverage_statuses",
            ]
        ],
        on="field_name",
        how="left",
    )
    out["methodology_gap_status"] = out.apply(
        lambda row: (
            "manual_or_not_applicable"
            if row["field_name"] in {"manual_score", "no_raw_field_required"}
            else row.get("readiness_status", "needs_configuration")
        ),
        axis=1,
    )
    preferred = [
        "methodology_gap_status",
        "methodology_id",
        "field_name",
        "field_category",
        "formula_count",
        "formula_ids",
        "factors",
        "metrics",
        "preferred_source",
        "fallback_source",
        "priority_sources",
        "template_source_priority",
        "automation_level",
        "dictionary_status",
        "source_priority_status",
        "alias_status",
        "alias_count",
        "mapped_sources",
        "audit_available_primary_raw",
        "audit_missing_primary_raw",
        "audit_no_primary_raw_sheet",
        "audit_field_blocking",
        "audit_coverage_statuses",
    ]
    return out[[col for col in preferred if col in out.columns] + [col for col in out.columns if col not in preferred]]


def build_gap_report(
    field_dictionary: pd.DataFrame | None = None,
    source_catalog: pd.DataFrame | None = None,
    methodology_field_matrix: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if field_dictionary is None:
        field_dictionary = build_field_dictionary_catalog()
    if source_catalog is None:
        source_catalog = build_source_catalog()
    if methodology_field_matrix is None:
        methodology_field_matrix = build_methodology_field_matrix()

    rows: list[dict[str, Any]] = []
    if not field_dictionary.empty:
        used = field_dictionary[field_dictionary["used_by_methodology"].astype(bool)].copy()
        for status, severity in [
            ("dictionary_missing", "blocking"),
            ("source_priority_missing", "blocking"),
            ("alias_mapping_missing", "review"),
            ("benchmark_blocking_gap", "blocking"),
            ("benchmark_raw_missing", "review"),
        ]:
            matches = used[used["readiness_status"].astype(str).eq(status)]
            for _, row in matches.iterrows():
                rows.append(
                    {
                        "gap_type": status,
                        "severity": severity,
                        "field_name": row.get("field_name", ""),
                        "methodology_id": row.get("methodologies", ""),
                        "source_name": "",
                        "details": row.get("metrics", "") or row.get("notes", ""),
                        "recommended_action": _recommended_action(status),
                    }
                )

    if not source_catalog.empty:
        idle_sources = source_catalog[pd.to_numeric(source_catalog["methodology_field_count"], errors="coerce").fillna(0).eq(0)]
        for _, row in idle_sources.iterrows():
            rows.append(
                {
                    "gap_type": "source_not_mapped_to_methodology_fields",
                    "severity": "review",
                    "field_name": "",
                    "methodology_id": "",
                    "source_name": row.get("source_name", ""),
                    "details": row.get("preferred_for", ""),
                    "recommended_action": "Confirm whether this source is future-facing or map it to active canonical fields.",
                }
            )

    if not methodology_field_matrix.empty:
        manual = methodology_field_matrix[
            methodology_field_matrix["field_name"].astype(str).ne("manual_score")
            & methodology_field_matrix["priority_sources"].fillna("").astype(str).eq("Manual")
        ]
        for _, row in manual.iterrows():
            rows.append(
                {
                    "gap_type": "manual_only_dependency",
                    "severity": "review",
                    "field_name": row.get("field_name", ""),
                    "methodology_id": row.get("methodology_id", ""),
                    "source_name": "Manual",
                    "details": row.get("metrics", ""),
                    "recommended_action": "Add API/upload/document source priority before relying on manual entry.",
                }
            )

    return pd.DataFrame(rows).drop_duplicates().reset_index(drop=True)


def _recommended_action(status: str) -> str:
    return {
        "dictionary_missing": "Add the canonical field to config/data_dictionary.csv with category and source policy.",
        "source_priority_missing": "Add field-level source priority in config/source_priority.csv.",
        "alias_mapping_missing": "Add upload/document aliases in config/field_mapping.csv.",
        "benchmark_blocking_gap": "Prioritize sourcing or normalization; this field blocks official benchmark replication.",
        "benchmark_raw_missing": "Add a source connector, alias, or PDF extraction rule for this benchmark gap.",
    }.get(status, "Review the field/source metadata.")


def build_data_platform_tables() -> dict[str, pd.DataFrame]:
    """Build all tables needed by the Data Platform page."""
    field_dictionary = build_field_dictionary_catalog()
    source_catalog = build_source_catalog()
    source_field_matrix = build_source_field_matrix()
    methodology_field_matrix = build_methodology_field_matrix()
    gap_report = build_gap_report(field_dictionary, source_catalog, methodology_field_matrix)
    return {
        "source_catalog": source_catalog,
        "source_field_matrix": source_field_matrix,
        "field_dictionary": field_dictionary,
        "methodology_field_matrix": methodology_field_matrix,
        "gap_report": gap_report,
    }
