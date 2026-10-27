"""Data-quality checks.

These checks describe the DATA (missing, impossible, stuck, jumping, badly
timed). A problem found here points to a sensor, wiring or communication issue
and says nothing about the condition of the motor.
"""
import numpy as np
import pandas as pd

from config import SENSORS

JUMP_SIGMA = 8.0          # a step this many robust sigmas from typical is "abrupt"
GAP_FACTOR = 2.5          # interval > 2.5 x median  -> gap
IRREGULAR_TOLERANCE = 0.25  # interval off by > 25 % of median -> irregular


def _frozen_mask(series, min_run):
    """True for samples inside a run of >= min_run identical valid readings."""
    if min_run is None:
        return pd.Series(False, index=series.index)
    run_id = (series != series.shift()).cumsum()   # NaN always starts a new run
    run_len = series.groupby(run_id).transform("count")
    return (run_len >= min_run) & series.notna()


def run_mask(flag, min_run):
    """True for samples inside a run of >= min_run consecutive True values."""
    flag = flag.astype(bool)
    run_id = (flag != flag.shift()).cumsum()
    return flag & (flag.groupby(run_id).transform("size") >= min_run)


def _jump_mask(series, floor=0.0):
    """True where the step from the previous valid sample is abnormally large.

    "Abnormal" = more than JUMP_SIGMA robust standard deviations from the
    typical step, and at least `floor` (for coarse sensors whose typical step
    is zero).
    """
    diffs = series.dropna().diff()
    centre = diffs.median()
    mad = (diffs - centre).abs().median()
    mask = pd.Series(False, index=series.index)
    if pd.isna(mad):
        return mask
    threshold = max(JUMP_SIGMA * 1.4826 * mad, floor)
    if threshold == 0:
        return mask
    is_jump = (diffs - centre).abs() > threshold
    mask.loc[is_jump[is_jump].index] = True
    return mask


def classify(df):
    """Return {sensor: DataFrame of boolean columns}, one row per sample.

    Columns: missing, out_of_range, sentinel, frozen, jump.
    Sensors whose column is absent from df are skipped.
    """
    result = {}
    for col, spec in SENSORS.items():
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        missing = s.isna()
        sentinel = s.isin(list(spec["sentinels"]))
        out_of_range = ((s < spec["min"]) | (s > spec["max"])) & ~sentinel
        usable = s.mask(sentinel | out_of_range)
        result[col] = pd.DataFrame({
            "missing": missing,
            "out_of_range": out_of_range,
            "sentinel": sentinel,
            "frozen": _frozen_mask(usable, spec["frozen_run"]),
            "jump": (_jump_mask(usable, spec.get("jump_floor", 0.0))
                     if spec["check_jumps"] else False),
        })
    return result


def mask_invalid(df):
    """Copy of df with out-of-range and sentinel readings replaced by NaN.

    Use this before computing statistics so that, for example, the firmware's
    "0 °C = disconnected" value does not drag the mean temperature down.
    """
    clean = df.copy()
    for col, flags in classify(df).items():
        bad = flags["out_of_range"] | flags["sentinel"]
        clean[col] = pd.to_numeric(df[col], errors="coerce").mask(bad)
    return clean


def quality_flags(df):
    """Per-row text flags such as 'temperature_c:sentinel;gas_adc:frozen'."""
    parts = pd.Series("", index=df.index)
    for col, flags in classify(df).items():
        for name in flags.columns:
            hit = flags[name].astype(bool)
            parts[hit] = parts[hit] + f"{col}:{name};"
    return parts.str.rstrip(";")


def sensor_quality_summary(df):
    """One row per sensor with counts and an honest status string."""
    flags_by_sensor = classify(df)
    rows = []
    for col, spec in SENSORS.items():
        row = {"sensor": col, "unit": spec["unit"], "samples": len(df)}
        if col not in flags_by_sensor:
            row.update(status="unavailable (column not present)")
            rows.append(row)
            continue
        f = flags_by_sensor[col]
        invalid = f["missing"] | f["out_of_range"] | f["sentinel"]
        row.update(
            valid=int((~invalid).sum()),
            missing=int(f["missing"].sum()),
            out_of_range=int(f["out_of_range"].sum()),
            sentinel=int(f["sentinel"].sum()),
            frozen=int(f["frozen"].sum()),
            jumps=int(f["jump"].sum()),
        )
        if row["valid"] == 0:
            status = "unavailable (no valid readings)"
        elif invalid.mean() > 0.05:
            status = "returning invalid or missing readings"
        elif row["frozen"] > 0:
            status = "possibly frozen for part of the recording"
        else:
            status = "readings present; calibration not verified"
        row["status"] = status
        rows.append(row)
    return pd.DataFrame(rows).set_index("sensor")


def timestamp_quality(df):
    """Summarise sampling intervals. Returns a dict (intervals in seconds)."""
    if "timestamp" not in df.columns or len(df) < 2:
        return {"samples": len(df), "note": "Not enough timestamps to assess sampling."}
    dt = pd.to_datetime(df["timestamp"]).diff().dt.total_seconds().iloc[1:]
    median = float(dt.median())
    out = {
        "samples": len(df),
        "median_interval_s": median,
        "min_interval_s": float(dt.min()),
        "max_interval_s": float(dt.max()),
        "non_increasing": int((dt <= 0).sum()),
    }
    if median > 0:
        out["gaps"] = int((dt > GAP_FACTOR * median).sum())
        out["irregular_intervals"] = int(
            ((dt - median).abs() > IRREGULAR_TOLERANCE * median).sum())
        out["approx_sample_rate_hz"] = 1.0 / median
    return out
