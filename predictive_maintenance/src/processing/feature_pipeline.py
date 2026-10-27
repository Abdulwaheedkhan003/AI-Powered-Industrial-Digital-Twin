"""Turn raw recordings into the feature table used by the anomaly model.

The recording is cut into fixed windows (60 s by default) and each window is
summarised. This one function is used for both training and scoring, so the
transformations are guaranteed identical.

Features (only those whose sensor has data are produced):
  temp_mean_c, temp_rate_c_per_min       DS18B20
  current_mean_a, current_std_a          ACS712
  vibration_mean_ms2, vibration_max_ms2  firmware's one-sample vibration value
  vib_rms_g, vib_peak_g, vib_crest,      raw acceleration bursts (firmware v2)
  vib_dom_freq_hz
  gas_mean_adc, ldr_mean_adc,            only with include_environment=True
  lidar_mean_cm

Gas, light and distance describe the surroundings, not the motor, so they are
left out of the motor model unless explicitly requested.

A window with fewer than `min_samples` valid readings of a sensor gets a
missing value for that sensor's features (nothing is filled in here).
"""
import numpy as np
import pandas as pd

from src.processing.validation import mask_invalid

NON_FEATURE_COLUMNS = ["window_start", "label_anomaly"]


def feature_columns(features):
    return [c for c in features.columns if c not in NON_FEATURE_COLUMNS]


def _slope_per_min(timestamps, values):
    ok = values.notna()
    if ok.sum() < 3:
        return np.nan
    minutes = (timestamps[ok] - timestamps[ok].iloc[0]).dt.total_seconds() / 60.0
    if minutes.iloc[-1] <= 0:
        return np.nan
    return float(np.polyfit(minutes, values[ok], 1)[0])


def build_features(df, burst_features=None, window_s=60, include_environment=False,
                   min_samples=5):
    """Return one row per window: window_start, features, optional label_anomaly."""
    clean = mask_invalid(df).copy()
    clean["timestamp"] = pd.to_datetime(clean["timestamp"])
    t0 = clean["timestamp"].iloc[0]
    clean["_win"] = ((clean["timestamp"] - t0).dt.total_seconds() // window_s).astype(int)
    groups = clean.groupby("_win")
    out = pd.DataFrame(index=pd.Index(sorted(clean["_win"].unique()), name="_win"))

    def add(col, aggregations):
        if col not in clean.columns or not clean[col].notna().any():
            return False
        enough = groups[col].count() >= min_samples
        for name, how in aggregations.items():
            out[name] = groups[col].agg(how).where(enough)
        return True

    if add("temperature_c", {"temp_mean_c": "mean"}):
        slopes = pd.Series({w: _slope_per_min(g["timestamp"], g["temperature_c"])
                            for w, g in groups})
        out["temp_rate_c_per_min"] = slopes.where(out["temp_mean_c"].notna())
    add("current_a", {"current_mean_a": "mean", "current_std_a": "std"})
    add("vibration_ms2", {"vibration_mean_ms2": "mean", "vibration_max_ms2": "max"})

    if burst_features is not None and len(burst_features) and "valid" in burst_features:
        good = burst_features[burst_features["valid"].astype(bool)]
        if len(good):
            win = ((pd.to_datetime(good["burst_time"]) - t0).dt.total_seconds()
                   // window_s).astype(int)
            bursts = good.groupby(win.to_numpy())
            out["vib_rms_g"] = bursts["rms_total_g"].mean()
            out["vib_peak_g"] = bursts["peak_g"].max()
            out["vib_crest"] = bursts["crest_factor"].mean()
            out["vib_dom_freq_hz"] = bursts["dominant_freq_hz"].mean()

    if include_environment:
        add("gas_adc", {"gas_mean_adc": "mean"})
        add("ldr_adc", {"ldr_mean_adc": "mean"})
        add("lidar_distance_cm", {"lidar_mean_cm": "mean"})

    if "label_anomaly" in clean.columns:
        out["label_anomaly"] = groups["label_anomaly"].mean() > 0.5

    out.insert(0, "window_start", t0 + pd.to_timedelta(out.index * window_s, unit="s"))
    return out.reset_index(drop=True)
