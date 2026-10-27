"""Monitoring sessions: list, load, annotate and compare recordings.

A session is one CSV log (plus an optional `<name>_accel.csv` with raw
acceleration bursts). Real sessions are created when the dashboard connects to
the ESP32 and end when it disconnects; they live in data/raw. Demo sessions
are saved on request into data/demo with a SYNTHETIC_ file name.

Operating-condition text and comments are kept in data/state/sessions.json.
Speed and load are free-text notes: neither is measured by the hardware.
"""
import json

import pandas as pd

import config
from config import SENSORS
from src.ingestion.csv_logger import default_log_path, save_log
from src.ingestion.csv_reader import DataImportError, load_csv
from src.processing.statistics import summary_statistics
from src.processing.validation import sensor_quality_summary, timestamp_quality


def _meta_path():
    return config.STATE_DIR / "sessions.json"


def _all_meta():
    path = _meta_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def get_session_meta(session_id):
    return _all_meta().get(session_id, {"operating_condition": "", "notes": ""})


def set_session_meta(session_id, operating_condition="", notes=""):
    meta = _all_meta()
    meta[session_id] = {"operating_condition": operating_condition, "notes": notes}
    path = _meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def accel_path_for(path):
    return path.with_name(path.stem + "_accel.csv")


def load_session(path):
    """Return (df, accel or None, cleaning report)."""
    df, report = load_csv(path)
    accel = None
    accel_file = accel_path_for(path)
    if accel_file.exists():
        accel = pd.read_csv(accel_file, parse_dates=["burst_time"])
    return df, accel, report


def describe_session(df, path=None):
    """Summary record for one recording."""
    session_id = df["session_id"].iloc[0] if "session_id" in df.columns else (path.stem if path else "unknown")
    quality = sensor_quality_summary(df)
    available = quality.index[quality.get("valid", pd.Series(dtype=float)).fillna(0) > 0].tolist()
    sources = sorted(df["data_source"].unique())
    record = {
        "session_id": session_id,
        "file": str(path) if path else "",
        "data_source": sources[0] if len(sources) == 1 else "mixed: " + ", ".join(sources),
        "start": df["timestamp"].min(),
        "end": df["timestamp"].max(),
        "samples": len(df),
        "available_sensors": ", ".join(available),
        "sensors_with_problems": int(quality["status"].str.contains("invalid|frozen").sum()),
        "median_interval_s": timestamp_quality(df).get("median_interval_s"),
    }
    record.update(get_session_meta(session_id))
    return record


def list_sessions():
    """One record per saved session (real first, then synthetic)."""
    records = []
    for folder in (config.RAW_DIR, config.DEMO_DIR):
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.csv")):
            if path.name.endswith("_accel.csv"):
                continue
            try:
                df, _ = load_csv(path)
            except DataImportError:
                continue
            records.append(describe_session(df, path))
    return pd.DataFrame(records)


def save_demo_session(df, accel=None):
    """Write a synthetic session to data/demo (replacing an older copy)."""
    path = default_log_path(df["data_source"].iloc[0], df["session_id"].iloc[0])
    save_log(df, path)
    if accel is not None and len(accel):
        save_log(accel, accel_path_for(path))
    return path


def compare_sessions(df_a, df_b, meta_a=None, meta_b=None):
    """Side-by-side statistics for two sessions. Returns (table, warnings)."""
    warnings = []
    source_a, source_b = set(df_a["data_source"]), set(df_b["data_source"])
    if source_a != source_b:
        warnings.append(f"Different data sources ({', '.join(sorted(source_a))} vs "
                        f"{', '.join(sorted(source_b))}). Synthetic and real sessions are "
                        "not comparable.")
    cond_a = (meta_a or {}).get("operating_condition", "").strip()
    cond_b = (meta_b or {}).get("operating_condition", "").strip()
    if not cond_a or not cond_b:
        warnings.append("Operating conditions are not recorded for both sessions. Differences "
                        "may come from speed, load, supply voltage or room temperature "
                        "rather than from the motor's condition.")
    elif cond_a.lower() != cond_b.lower():
        warnings.append(f"Operating conditions differ ('{cond_a}' vs '{cond_b}'). The sessions "
                        "are not like-for-like.")
    dt_a = timestamp_quality(df_a).get("median_interval_s")
    dt_b = timestamp_quality(df_b).get("median_interval_s")
    if dt_a and dt_b and abs(dt_a - dt_b) > 0.25 * max(dt_a, dt_b):
        warnings.append(f"Sampling intervals differ ({dt_a:.2f} s vs {dt_b:.2f} s).")

    stats_a, stats_b = summary_statistics(df_a), summary_statistics(df_b)
    rows = []
    for name in SENSORS:
        if name not in stats_a.index and name not in stats_b.index:
            continue
        a = stats_a.loc[name] if name in stats_a.index else pd.Series(dtype=float)
        b = stats_b.loc[name] if name in stats_b.index else pd.Series(dtype=float)
        rows.append({"sensor": name, "unit": SENSORS[name]["unit"],
                     "median_A": a.get("median"), "median_B": b.get("median"),
                     "median_change": (b.get("median") - a.get("median")
                                       if pd.notna(a.get("median")) and pd.notna(b.get("median")) else None),
                     "std_A": a.get("std"), "std_B": b.get("std"),
                     "max_A": a.get("max"), "max_B": b.get("max"),
                     "valid_A": a.get("valid", 0), "valid_B": b.get("valid", 0)})
    return pd.DataFrame(rows).set_index("sensor"), warnings


def training_features(paths, window_s=60):
    """Feature windows from one or more saved sessions, for model training.

    Returns (features, data_source, training_id). Raises ValueError if the
    sessions do not all come from the same data source: real and synthetic
    recordings are never combined into one training set.
    """
    from src.monitoring.engine import get_signals
    from src.processing.feature_pipeline import build_features
    tables, sources, ids = [], set(), []
    for path in paths:
        df, accel, _ = load_session(path)
        sources.update(df["data_source"].unique())
        ids.append(str(df["session_id"].iloc[0]))
        tables.append(build_features(df, get_signals(df, accel)[1], window_s=window_s))
    if len(sources) != 1:
        raise ValueError(f"Training sessions mix data sources: {sorted(sources)}.")
    return pd.concat(tables, ignore_index=True), sources.pop(), ", ".join(ids)
