"""Trend and baseline analysis for the slow sensors.

The same method is used for temperature, current, gas, light and distance:

  baseline  = median of the first `baseline_samples` valid readings of the
              recording, with a robust spread (1.4826 x median absolute
              deviation, comparable to a standard deviation).
  deviation = a reading more than `k` spreads away from the baseline.
  persistent deviation = at least `persist` deviating readings in a row.
  slope     = least-squares straight-line fit over the whole recording.

The baseline is simply "how this recording started". It is not a verified
healthy reference: if the start is unrepresentative (MQ-5 still warming up,
motor not yet at temperature) the comparison is biased. Nothing here applies
a universal limit, and a deviation is an observation, not a fault diagnosis.
"""
import numpy as np
import pandas as pd

from src.processing.validation import mask_invalid, run_mask


def _valid_series(df, col):
    clean = mask_invalid(df)[["timestamp", col]].dropna()
    return pd.Series(clean[col].to_numpy(dtype=float),
                     index=pd.to_datetime(clean["timestamp"]))


def trend_summary(df, col, baseline_samples=60, k=4.0, persist=5):
    """Return a dict describing how `col` moved relative to its own start."""
    if col not in df.columns:
        return {"valid": False, "reason": "sensor not present in this dataset"}
    s = _valid_series(df, col)
    if len(s) < baseline_samples + persist:
        return {"valid": False,
                "reason": f"only {len(s)} valid readings; need at least "
                          f"{baseline_samples + persist}"}
    base = s.iloc[:baseline_samples]
    median = float(base.median())
    spread = float(1.4826 * (base - median).abs().median())
    minutes = (s.index - s.index[0]).total_seconds() / 60.0
    slope = float(np.polyfit(minutes, s.to_numpy(), 1)[0]) if minutes[-1] > 0 else 0.0
    recent = float(s.iloc[-baseline_samples:].median())

    out = {"valid": True, "valid_readings": len(s), "baseline_median": median,
           "baseline_spread": spread, "recent_median": recent,
           "change_from_baseline": recent - median, "slope_per_min": slope,
           "peak": float(s.max())}
    if spread == 0:
        out.update(persistent_deviation_samples=None, first_persistent_deviation=None,
                   note="Baseline readings are all identical, so deviations "
                        "cannot be scaled. Compare change_from_baseline instead.")
        return out
    outside = pd.Series(((s - median).abs() > k * spread).to_numpy(), index=s.index)
    persistent = run_mask(outside, persist)
    out.update(persistent_deviation_samples=int(persistent.sum()),
               first_persistent_deviation=persistent.idxmax() if persistent.any() else None,
               note="")
    return out


def rolling_rate(df, col, window_s=60):
    """Rate of change (units per minute): straight-line slope in a moving window."""
    s = _valid_series(df, col)
    if len(s) < 3:
        return pd.Series(dtype=float)
    minutes = pd.Series((s.index - s.index[0]).total_seconds() / 60.0, index=s.index)
    window = f"{int(window_s)}s"
    rate = s.rolling(window).cov(minutes) / minutes.rolling(window).var()
    # The first window is only partly filled, so its slope is unreliable.
    rate[s.index < s.index[0] + pd.Timedelta(seconds=window_s)] = np.nan
    return rate.replace([np.inf, -np.inf], np.nan)
