"""Vibration indicators computed from raw 3-axis acceleration bursts.

Gravity handling: for each burst the mean of every axis is subtracted. Gravity
and the sensor's constant offset are steady during a ~0.5 s burst, so this
removes them and leaves the vibrating ("dynamic") part. It is only valid if
the sensor's orientation does not change during the burst.

What the indicators mean (all in g, 1 g = 9.80665 m/s²):
  rms_x/y/z_g       : average vibration strength on one axis
  rms_total_g       : RMS of the dynamic 3-axis vector = sqrt(rms_x²+rms_y²+rms_z²)
  peak_g            : largest dynamic vector magnitude in the burst
  peak_to_peak_*_g  : max minus min on one axis
  variance_total_g2 : rms_total_g squared (after mean removal, the standard
                      deviation equals the RMS, so it is not listed twice)
  crest_factor      : peak_g / rms_total_g. About 1.4–2 for a smooth tone,
                      higher when there are short spikes. Undefined at zero RMS.

What they cannot tell you: which component is responsible, or whether a level
is "bad". They only become meaningful compared with the same motor, at the
same speed and mounting, at an earlier time.
"""
import numpy as np
import pandas as pd

from config import ACCEL_COLUMNS
from src.processing.fft_analysis import (dominant_frequency, is_flat,
                                         sampling_info, spectrum)

MIN_BURST_SAMPLES = 32


def burst_features(burst):
    """Indicators for one burst (a DataFrame with ACCEL_COLUMNS and t_us)."""
    a = burst[ACCEL_COLUMNS].to_numpy(dtype=float)
    if len(a) < MIN_BURST_SAMPLES:
        return {"valid": False, "reason": f"only {len(a)} samples"}
    if not np.isfinite(a).all():
        return {"valid": False, "reason": "burst contains missing samples"}

    dynamic = a - a.mean(axis=0)                     # gravity + offset removed
    for i in range(3):                               # exact zero for a flat axis
        if is_flat(a[:, i]):
            dynamic[:, i] = 0.0
    rms_axes = np.sqrt((dynamic ** 2).mean(axis=0))
    magnitude = np.sqrt((dynamic ** 2).sum(axis=1))
    rms_total = float(np.sqrt((magnitude ** 2).mean()))
    peak = float(magnitude.max())
    p2p = dynamic.max(axis=0) - dynamic.min(axis=0)

    out = {"valid": True, "reason": "", "n_samples": len(a)}
    for name, r, p in zip("xyz", rms_axes, p2p):
        out[f"rms_{name}_g"] = float(r)
        out[f"peak_to_peak_{name}_g"] = float(p)
    out.update(rms_total_g=rms_total, peak_g=peak,
               variance_total_g2=rms_total ** 2,
               crest_factor=peak / rms_total if rms_total > 0 else float("nan"))

    # Dominant frequency on the axis that vibrates most, if timing allows.
    info = sampling_info(burst["t_us"])
    out["sample_rate_hz"] = info.get("sample_rate_hz", float("nan"))
    out["sampling_regular"] = bool(info.get("regular", False))
    out["dominant_freq_hz"] = float("nan")
    if out["sampling_regular"] and rms_total > 0:
        axis = ACCEL_COLUMNS[int(np.argmax(rms_axes))]
        peak_hz = dominant_frequency(*spectrum(burst[axis], info["sample_rate_hz"]))
        if peak_hz is not None:
            out["dominant_freq_hz"] = peak_hz
    return out


def features_per_burst(accel_df):
    """One row of indicators per burst, in time order."""
    rows = []
    for burst_id, burst in accel_df.groupby("burst_id", sort=True):
        rows.append({"burst_id": burst_id,
                     "burst_time": burst["burst_time"].iloc[0],
                     **burst_features(burst)})
    return pd.DataFrame(rows)
