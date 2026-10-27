"""Statistical anomaly detection with a robust baseline.

Method (a robust version of a control chart):
  baseline median  = typical value of the baseline data
  baseline spread  = 1.4826 x median absolute deviation (comparable to a
                     standard deviation, but not thrown off by a few spikes)
  z                = (reading - median) / spread
  a reading is flagged when |z| > k (k = 4 by default, fixed in advance)

Where the baseline comes from, in order of preference:
  1. a reference session the user designated as comparable normal operation;
  2. otherwise the first `baseline_s` seconds of the session being analysed.
Option 2 is weak: anything that changes after start-up (motor warming up,
MQ-5 settling) will be flagged even though it is normal.

k is not tuned on test data. Changing it after looking at results makes the
reported performance optimistic.
"""
import pandas as pd


def fit_baseline(values):
    """Return {"median", "spread", "n"} or None if there is too little data."""
    v = pd.Series(values, dtype=float).dropna()
    if len(v) < 5:
        return None
    median = float(v.median())
    spread = float(1.4826 * (v - median).abs().median())
    if spread == 0:
        # Coarse sensors (DS18B20 steps, 1 cm LiDAR) often repeat one value so
        # the robust spread collapses to zero; use the ordinary standard
        # deviation instead. Still zero means the signal never changed.
        spread = float(v.std())
    return {"median": median, "spread": spread, "n": int(len(v))}


def fit_baselines(signals, first_seconds=None):
    """Baseline for every signal; optionally only from the first N seconds."""
    out = {}
    for name, series in signals.items():
        s = series.dropna()
        if first_seconds is not None and len(s):
            s = s[s.index <= s.index[0] + pd.Timedelta(seconds=first_seconds)]
        out[name] = fit_baseline(s)
    return out


def robust_z(series, baseline):
    """z-score against the baseline, or None when it cannot be computed."""
    if baseline is None or baseline["spread"] == 0:
        return None
    return (series - baseline["median"]) / baseline["spread"]


def statistical_levels(series, baseline, k=4.0):
    """1 where |z| > k, 0 otherwise, NaN for missing readings. None if no baseline."""
    z = robust_z(series, baseline)
    if z is None:
        return None
    return (z.abs() > k).astype(float).where(series.notna())
