"""Import a recorded CSV and report every cleaning step that was applied.

Cleaning never invents measurements: bad cells become missing (NaN), bad rows
are dropped, and each operation is listed in the returned report.
"""
import pandas as pd

from config import SENSOR_COLUMNS, SOURCE_UNKNOWN


class DataImportError(ValueError):
    """The file cannot be used at all (empty, no timestamp column, ...)."""


def load_csv(path_or_buffer):
    """Return (df, report). report is a list of plain-English strings."""
    report = []
    try:
        df = pd.read_csv(path_or_buffer, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        raise DataImportError("The file is empty.") from None
    if df.empty:
        raise DataImportError("The file has a header but no data rows.")
    if "timestamp" not in df.columns:
        raise DataImportError(
            f"No 'timestamp' column found. Columns present: {list(df.columns)}")
    n_start = len(df)

    # Timestamps
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    bad_time = int(df["timestamp"].isna().sum())
    if bad_time:
        df = df.dropna(subset=["timestamp"])
        report.append(f"Dropped {bad_time} row(s) with an unreadable timestamp.")
    if df.empty:
        raise DataImportError("No row has a readable timestamp.")

    # Sensor columns
    missing_cols = [c for c in SENSOR_COLUMNS if c not in df.columns]
    if missing_cols:
        report.append("Sensor column(s) not in file, treated as unavailable: "
                      + ", ".join(missing_cols) + ".")
    for col in SENSOR_COLUMNS:
        if col not in df.columns:
            continue
        text = df[col].str.strip()
        numbers = pd.to_numeric(text, errors="coerce")
        not_numeric = int(((text != "") & numbers.isna()).sum())
        if not_numeric:
            report.append(f"{col}: {not_numeric} non-numeric value(s) set to missing.")
        df[col] = numbers

    # Optional ground-truth column (present in synthetic scenario files)
    if "label_anomaly" in df.columns:
        df["label_anomaly"] = df["label_anomaly"].str.strip().str.lower().isin(["true", "1"])

    # Data source: never assume a file is real
    if "data_source" not in df.columns:
        df["data_source"] = SOURCE_UNKNOWN
        report.append("No data_source column: rows labelled 'unknown' "
                      "(not assumed to be real measurements).")
    else:
        blank = df["data_source"].str.strip() == ""
        if blank.any():
            df.loc[blank, "data_source"] = SOURCE_UNKNOWN
            report.append(f"{int(blank.sum())} row(s) had a blank data_source, labelled 'unknown'.")
        sources = sorted(df["data_source"].unique())
        if len(sources) > 1:
            report.append(f"WARNING: file mixes data sources {sources}. "
                          "Do not analyse them together as one dataset.")

    # Order and duplicates
    if not df["timestamp"].is_monotonic_increasing:
        df = df.sort_values("timestamp", kind="stable")
        report.append("Rows were out of time order and have been sorted.")
    dupes = int(df.duplicated(subset="timestamp").sum())
    if dupes:
        df = df.drop_duplicates(subset="timestamp", keep="first")
        report.append(f"Removed {dupes} row(s) with a duplicate timestamp (kept the first).")

    df = df.reset_index(drop=True)
    if not report:
        report.append("No cleaning was needed.")
    report.append(f"{len(df)} of {n_start} rows kept.")
    return df, report


def export_csv(df, path):
    """Write a cleaned/processed dataset. Missing values stay as empty cells."""
    df.to_csv(path, index=False)
    return path
