"""Write timestamped measurement logs to CSV, keeping real and synthetic apart.

Rules enforced here:
  * every file contains exactly one data_source;
  * synthetic data can only go to a file whose name starts with "SYNTHETIC_"
    and can never be written into data/raw;
  * real data can never be written into data/demo;
  * appending to a file with a different data_source is refused.
"""
from pathlib import Path

import pandas as pd

import config
from config import SOURCE_REAL, SOURCE_SYNTHETIC, SYNTHETIC_FILE_PREFIX


class DataSourceError(ValueError):
    """Raised when a write would mislabel or mix real and synthetic data."""


def default_log_path(data_source, session_id):
    """Suggested file location for a session log."""
    if data_source == SOURCE_SYNTHETIC:
        return config.DEMO_DIR / f"{SYNTHETIC_FILE_PREFIX}{session_id}.csv"
    if data_source == SOURCE_REAL:
        return config.RAW_DIR / f"{session_id}.csv"
    raise DataSourceError(f"Unknown data source '{data_source}'")


def _single_source(df):
    if "data_source" not in df.columns:
        raise DataSourceError("Rows have no data_source column")
    sources = df["data_source"].dropna().unique()
    if len(sources) != 1 or df["data_source"].isna().any():
        raise DataSourceError(f"Expected exactly one data_source, found {list(sources)}")
    return sources[0]


def _is_inside(path, folder):
    return folder.resolve() in path.resolve().parents


def save_log(df, path, append=False):
    """Write df to path. Missing sensor values are written as empty cells."""
    path = Path(path)
    source = _single_source(df)

    if source == SOURCE_SYNTHETIC:
        if not path.name.startswith(SYNTHETIC_FILE_PREFIX):
            raise DataSourceError(
                f"Synthetic data must be saved to a file named {SYNTHETIC_FILE_PREFIX}*.csv")
        if _is_inside(path, config.RAW_DIR):
            raise DataSourceError("Synthetic data may not be saved in data/raw")
    elif source == SOURCE_REAL:
        if path.name.startswith(SYNTHETIC_FILE_PREFIX) or _is_inside(path, config.DEMO_DIR):
            raise DataSourceError("Real data may not be saved as a synthetic/demo file")
    else:
        raise DataSourceError(f"Unknown data source '{source}'")

    exists = path.exists() and path.stat().st_size > 0
    if append and exists:
        existing = pd.read_csv(path, nrows=0).columns.tolist()
        if existing != df.columns.tolist():
            raise ValueError("Columns do not match the existing log file")
        # Every write goes through this function, so a file is single-source
        # by construction and checking its first row is enough.
        existing_sources = pd.read_csv(path, usecols=["data_source"], nrows=1)["data_source"].unique()
        if list(existing_sources) != [source]:
            raise DataSourceError(
                f"Refusing to append {source} rows to a file containing {list(existing_sources)}")
        df.to_csv(path, mode="a", header=False, index=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
    return path
