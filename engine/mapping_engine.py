
    is_percent = raw.endswith("%")
    negative = raw.startswith("(") and raw.endswith(")")

    text = raw.replace("$", "").replace(",", "").replace("%", "")
    text = text.replace("(", "").replace(")", "").strip()

    try:
        number = float(text)
        if negative:
            number = -number
        if is_percent:
            number = number / 100.0
        return number
    except ValueError:
        return raw


def extract_value_from_column(df: pd.DataFrame, column: str, row_index: int = 0) -> Any:
    """
    Extract one value from a matched column.

    MVP assumption: one issuer / one period per uploaded file, so the first
    non-empty value is usually the target value. If that fails, fall back to
    the requested row_index.
    """
    series = df[column]
    non_empty = series.dropna()
    if len(non_empty) > 0:
        return clean_numeric_value(non_empty.iloc[row_index if row_index < len(non_empty) else 0])
    return None


def map_uploaded_dataframe(
    df: pd.DataFrame,
    source_name: str,
    mapping: Union[pd.DataFrame, str, Path] = DEFAULT_MAPPING_PATH,
    fuzzy_threshold: float = 0.82,
    row_index: int = 0,
) -> Tuple[Dict[str, Any], pd.DataFrame]:
    """
    Map uploaded dataframe columns to canonical issuer_data.

    Args:
        df: uploaded CreditScope / IPEDS / ACFR / OS dataframe.
        source_name: source label, e.g. "CreditScope", "IPEDS", "ACFR".
        mapping: mapping dataframe or path to field_mapping.csv.
        fuzzy_threshold: minimum fuzzy score accepted.
        row_index: row index used when multiple non-empty rows exist.

    Returns:
        issuer_data: dict keyed by canonical field_name.
        match_report: dataframe with status, matched column, confidence, value.
    """
    if not isinstance(mapping, pd.DataFrame):
        mapping_df = load_mapping_table(mapping)
    else:
        mapping_df = mapping.copy()

    source_norm = normalize_label(source_name)
    source_rows = mapping_df[mapping_df["source_name"].map(normalize_label) == source_norm]

    # Allow generic/common mapping rows if you later add source_name = Any/Common.
    generic_rows = mapping_df[mapping_df["source_name"].map(normalize_label).isin({"any", "common", "all"})]
    source_rows = pd.concat([source_rows, generic_rows], ignore_index=True).drop_duplicates(
        subset=["field_name", "possible_column_names"], keep="first"
    )

    issuer_data: Dict[str, Any] = {}
    matches: List[FieldMatch] = []

    for _, row in source_rows.iterrows():
        field_name = str(row["field_name"]).strip()
        aliases = split_aliases(row.get("possible_column_names", ""))
        notes = "" if pd.isna(row.get("notes", "")) else str(row.get("notes", ""))

        matched_col, matched_alias, method, confidence = _best_column_match(
            aliases=aliases,
            columns=df.columns,
            fuzzy_threshold=fuzzy_threshold,
        )

        value = None
        if matched_col is not None:
            value = extract_value_from_column(df, matched_col, row_index=row_index)
            if value is not None and not (isinstance(value, float) and pd.isna(value)):
                issuer_data[field_name] = value

        matches.append(
            FieldMatch(
                field_name=field_name,
                source_name=source_name,
                matched_column=matched_col,
                matched_label=matched_alias,
                match_method=method,
                confidence=confidence,
                value=value,
                notes=notes,
            )
        )

    match_report = pd.DataFrame([asdict(m) | {"status": m.status} for m in matches])
    if not match_report.empty:
        match_report = match_report[
            [
                "field_name",
                "source_name",
                "status",
                "matched_column",
                "matched_label",
                "match_method",
                "confidence",
                "value",
                "notes",
            ]
        ]
    return issuer_data, match_report


def map_uploaded_file(
    uploaded_file: Any,
    source_name: str,
    mapping_path: Union[str, Path] = DEFAULT_MAPPING_PATH,
    fuzzy_threshold: float = 0.82,
    row_index: int = 0,
) -> Tuple[Dict[str, Any], pd.DataFrame]:
    """
    Convenience wrapper for Streamlit uploaders.
    """
    df = read_uploaded_file(uploaded_file)
    return map_uploaded_dataframe(
        df=df,
        source_name=source_name,
        mapping=mapping_path,
        fuzzy_threshold=fuzzy_threshold,
        row_index=row_index,
    )


def merge_issuer_data(
    *issuer_data_dicts: Dict[str, Any],
    overwrite: bool = False,
) -> Dict[str, Any]:
    """
    Merge mapped outputs from multiple sources.

    By default, earlier sources win. This is useful when you want source priority:
        CreditScope first, then IPEDS, then ACFR, then manual.
    Set overwrite=True if later sources should replace earlier values.
    """
    merged: Dict[str, Any] = {}
    for data in issuer_data_dicts:
        for key, value in data.items():
            if overwrite or key not in merged or merged[key] is None:
                merged[key] = value
    return merged


if __name__ == "__main__":
    # Tiny smoke test. Run from project root:
    # python engine/mapping_engine.py
    sample = pd.DataFrame(
        {
            "Population": [530000],
            "Full Value": ["$40,000,000,000"],
            "Net Direct Debt": ["700,000,000"],
        }
    )
    data, report = map_uploaded_dataframe(sample, source_name="CreditScope")
    print(data)
    print(report.head(10).to_string(index=False))
