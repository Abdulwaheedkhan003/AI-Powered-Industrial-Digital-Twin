"""Basic descriptive statistics per sensor.

Statistics are computed on valid readings only (missing, out-of-range and
sentinel values are excluded and counted separately).

  min / max : smallest and largest valid reading
  mean      : arithmetic average
  median    : middle value; less affected by spikes than the mean
  std       : sample standard deviation (spread around the mean)
  p05 / p95 : 5 % of valid readings lie below p05, 5 % above p95
All of these are in the sensor's own unit (see the 'unit' column).
"""
import pandas as pd

from config import SENSORS
from src.processing.validation import mask_invalid


def summary_statistics(df):
    """Return a DataFrame with one row per sensor present in df."""
    clean = mask_invalid(df)
    rows = []
    for col, spec in SENSORS.items():
        if col not in clean.columns:
            continue
        s = clean[col]
        valid = s.dropna()
        row = {
            "sensor": col,
            "unit": spec["unit"],
            "valid": len(valid),
            "missing_or_invalid": len(s) - len(valid),
        }
        if len(valid):
            row.update({
                "min": valid.min(),
                "max": valid.max(),
                "mean": valid.mean(),
                "median": valid.median(),
                "std": valid.std() if len(valid) > 1 else float("nan"),
                "p05": valid.quantile(0.05),
                "p95": valid.quantile(0.95),
            })
        rows.append(row)
    columns = ["unit", "valid", "missing_or_invalid", "min", "max", "mean",
               "median", "std", "p05", "p95"]
    return pd.DataFrame(rows).set_index("sensor").reindex(columns=columns)


def correlation_matrix(df, min_pairs=20):
    """Spearman rank correlation between sensors (valid readings only).

    Spearman is used because it does not assume a straight-line relationship
    and is not dominated by a few spikes. Sensors that never change are left
    out. Returns (matrix, pair_counts); both empty if fewer than two sensors
    qualify.

    Correlation is not causation. Two readings can move together because both
    follow a third thing that is not measured here: motor speed, load, supply
    voltage, room temperature, or simply time since power-on (warm-up).
    """
    clean = mask_invalid(df)
    cols = [c for c in SENSORS if c in clean.columns
            and clean[c].notna().sum() >= min_pairs and clean[c].nunique(dropna=True) > 1]
    if len(cols) < 2:
        return pd.DataFrame(), pd.DataFrame()
    data = clean[cols].astype(float)
    present = data.notna().astype(int)
    return data.corr(method="spearman", min_periods=min_pairs), present.T @ present
