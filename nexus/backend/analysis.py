"""Bridge between the Nexus backend and the predictive_maintenance analysis pipeline.

All heavy analysis — anomaly detection (thresholds, statistical, Isolation Forest),
vibration FFT, data quality, model training — lives in the sibling
``predictive_maintenance/src`` package.  This module imports those functions,
wraps them into JSON-serialisable outputs that the FastAPI endpoints return,
and keeps the two codebases loosely coupled.
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from settings import PM_DIR

# Make the predictive_maintenance package importable.
if str(PM_DIR) not in sys.path:
    sys.path.insert(0, str(PM_DIR))

import config as pm_config                                 # noqa: E402
from config import SENSORS, SOURCE_SYNTHETIC                # noqa: E402
from src.detection.isolation_forest import (                # noqa: E402
    ModelError, list_models, load_model, save_model, train_model)
from src.detection.threshold_rules import (                 # noqa: E402
    SIGNALS, load_settings as pm_load_settings,
    save_settings as pm_save_settings, suggest_thresholds,
    validate_settings)
from src.ingestion.data_generator import (                  # noqa: E402
    SCENARIOS as DEMO_SCENARIOS, generate_accel_bursts, generate_demo_data)
from src.monitoring.engine import get_signals, run_detectors  # noqa: E402
from src.monitoring.sensor_status import sensor_status      # noqa: E402
from src.monitoring.session_manager import (                # noqa: E402
    compare_sessions, describe_session, get_session_meta,
    list_sessions, load_session, save_demo_session,
    set_session_meta)
from src.processing.fft_analysis import (                   # noqa: E402
    dominant_frequency, sampling_info, spectrum)
from src.processing.statistics import (                     # noqa: E402
    correlation_matrix, summary_statistics)
from src.processing.validation import (                     # noqa: E402
    mask_invalid, quality_flags, sensor_quality_summary,
    timestamp_quality)
from src.reporting.summary_report import build_report_html  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(val):
    """Convert pandas Timestamp to ISO string for JSON."""
    if val is None or pd.isna(val):
        return None
    if isinstance(val, pd.Timestamp):
        return val.isoformat()
    return str(val)


def _clean_dict(d):
    """Recursively replace NaN / Inf with None so json.dumps won't fail."""
    if isinstance(d, dict):
        return {k: _clean_dict(v) for k, v in d.items()}
    if isinstance(d, (list, tuple)):
        return [_clean_dict(v) for v in d]
    if isinstance(d, float) and (np.isnan(d) or np.isinf(d)):
        return None
    if isinstance(d, (np.integer,)):
        return int(d)
    if isinstance(d, (np.floating,)):
        return float(d) if np.isfinite(d) else None
    if isinstance(d, np.bool_):
        return bool(d)
    if isinstance(d, pd.Timestamp):
        return d.isoformat()
    return d


def _df_to_records(df, max_rows=10000):
    """DataFrame → list of plain dicts, NaN replaced by None."""
    if df is None or df.empty:
        return []
    chunk = df.head(max_rows).copy()
    for col in chunk.columns:
        if pd.api.types.is_datetime64_any_dtype(chunk[col]):
            chunk[col] = chunk[col].apply(_ts)
    return _clean_dict(chunk.where(chunk.notna(), None).to_dict(orient="records"))


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def api_list_sessions():
    """All saved sessions as a JSON-serialisable list."""
    sessions = list_sessions()
    if sessions.empty:
        return []
    return _df_to_records(sessions)


def api_load_session(session_file):
    """Load a session by its file path and return the dataframe + metadata."""
    path = Path(session_file)
    df, accel, report = load_session(path)
    session_id = df["session_id"].iloc[0] if "session_id" in df.columns else path.stem
    sources = sorted(df["data_source"].unique())
    return {
        "session_id": session_id,
        "data_source": sources[0] if len(sources) == 1 else "mixed",
        "samples": len(df),
        "start": _ts(df["timestamp"].min()),
        "end": _ts(df["timestamp"].max()),
        "sensors": [c for c in SENSORS if c in df.columns and df[c].notna().any()],
        "report": report,
        "meta": get_session_meta(session_id),
        "data": _df_to_records(df),
        "has_accel": accel is not None and len(accel) > 0,
    }


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------

def api_data_quality(session_file):
    """Data quality report for a session."""
    df, _, _ = load_session(Path(session_file))
    quality = sensor_quality_summary(df)
    timing = timestamp_quality(df)
    stats = summary_statistics(df)
    status = sensor_status(df)
    return _clean_dict({
        "quality": _df_to_records(quality.reset_index()),
        "timing": timing,
        "statistics": _df_to_records(stats.reset_index()),
        "status": _df_to_records(status.reset_index()),
    })


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------

def api_run_detectors(session_file, model_version=None):
    """Run all three detectors on a session. Returns JSON-safe result summary."""
    df, accel, _ = load_session(Path(session_file))
    settings = pm_load_settings()

    model = None
    if model_version:
        try:
            model = load_model(model_version)
        except Exception:
            pass

    result = run_detectors(df, accel, settings, model)
    alerts = result["alerts"]

    # Build a simple alert list
    alert_rows = _df_to_records(alerts) if not alerts.empty else []

    # AI scores
    scores_rows = []
    if result["scores"] is not None:
        scores_rows = _df_to_records(result["scores"])

    return _clean_dict({
        "session_id": result["session_id"],
        "data_source": result["data_source"],
        "baseline_kind": result["baseline_kind"],
        "ai_message": result["ai_message"],
        "notes": result["notes"],
        "alert_count": len(alerts),
        "alerts": alert_rows,
        "scores": scores_rows,
        "alert_summary": {
            atype: int((alerts["alert_type"] == atype).sum())
            for atype in ["threshold", "statistical", "ai_model", "combined"]
        } if not alerts.empty else {},
    })


# ---------------------------------------------------------------------------
# Vibration analysis
# ---------------------------------------------------------------------------

def api_vibration(session_file):
    """Vibration burst features + FFT for one session."""
    df, accel, _ = load_session(Path(session_file))
    from src.processing.vibration_features import features_per_burst
    if accel is None or accel.empty:
        return {"has_bursts": False, "bursts": [], "message": "No raw acceleration bursts in this session."}

    bursts = features_per_burst(accel)
    good = bursts[bursts["valid"].astype(bool)]
    burst_rows = _df_to_records(good)

    # FFT of the latest valid burst
    fft_data = None
    if len(good):
        last_id = good["burst_id"].iloc[-1]
        burst = accel[accel["burst_id"] == last_id]
        info = sampling_info(burst["t_us"])
        if info["valid"]:
            freqs, amp = spectrum(burst["accel_z_g"], info["sample_rate_hz"])
            peak_hz = dominant_frequency(freqs, amp)
            fft_data = _clean_dict({
                "burst_id": int(last_id),
                "sample_rate_hz": info["sample_rate_hz"],
                "resolution_hz": info["resolution_hz"],
                "nyquist_hz": info["nyquist_hz"],
                "dominant_hz": peak_hz,
                "regular": info["regular"],
                "spectrum": [{"frequency_hz": float(f), "amplitude_g": float(a)} for f, a in zip(freqs, amp)]
            })

    return _clean_dict({
        "has_bursts": True,
        "burst_count": len(good),
        "bursts": burst_rows,
        "fft": fft_data,
    })


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------

def api_list_models():
    return _clean_dict(list_models())


def api_train_model(session_file, conditions=""):
    """Train an Isolation Forest model on a single session."""
    df, accel, _ = load_session(Path(session_file))
    settings = pm_load_settings()
    source = sorted(df["data_source"].unique())
    source = source[0] if len(source) == 1 else "unknown"
    session_id = df["session_id"].iloc[0] if "session_id" in df.columns else "unknown"

    result = run_detectors(df, accel, settings)
    features = result["window_features"]

    pipeline, meta = train_model(features, data_source=source,
                                  training_id=session_id,
                                  operating_notes=conditions.strip())
    save_model(pipeline, meta)
    return _clean_dict(meta)


# ---------------------------------------------------------------------------
# Session comparison
# ---------------------------------------------------------------------------

def api_compare(file_a, file_b):
    """Compare two saved sessions."""
    df_a, _, _ = load_session(Path(file_a))
    df_b, _, _ = load_session(Path(file_b))
    id_a = df_a["session_id"].iloc[0] if "session_id" in df_a.columns else "A"
    id_b = df_b["session_id"].iloc[0] if "session_id" in df_b.columns else "B"
    table, warnings = compare_sessions(
        df_a, df_b,
        get_session_meta(id_a),
        get_session_meta(id_b),
    )
    return _clean_dict({
        "session_a": id_a,
        "session_b": id_b,
        "warnings": warnings,
        "comparison": _df_to_records(table.reset_index()),
    })


# ---------------------------------------------------------------------------
# Demo generation
# ---------------------------------------------------------------------------

def api_demo_scenarios():
    return [{"id": k, "description": v} for k, v in DEMO_SCENARIOS.items()]


def api_generate_demo(scenario, duration_s=900, seed=42):
    """Generate and save a synthetic demo session. Returns session metadata."""
    df = generate_demo_data(scenario, duration_s=duration_s, seed=seed)
    accel = generate_accel_bursts(scenario, duration_s=duration_s, seed=seed)
    path = save_demo_session(df, accel)
    session_id = df["session_id"].iloc[0]
    return {
        "session_id": session_id,
        "scenario": scenario,
        "samples": len(df),
        "file": str(path),
        "data_source": SOURCE_SYNTHETIC,
    }


# ---------------------------------------------------------------------------
# Settings bridge
# ---------------------------------------------------------------------------

def api_get_pm_settings():
    settings = pm_load_settings()
    signals_info = {name: {"label": info["label"], "unit": info["unit"],
                           "category": info["category"]}
                    for name, info in SIGNALS.items()}
    return _clean_dict({"settings": settings, "signals": signals_info})


def api_save_pm_settings(settings):
    errors = validate_settings(settings)
    if errors:
        return {"saved": False, "errors": errors}
    pm_save_settings(settings)
    return {"saved": True, "errors": []}


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def api_report_html(session_file, model_version=None):
    """Generate an HTML summary report for a session."""
    df, accel, _ = load_session(Path(session_file))
    settings = pm_load_settings()
    model = None
    if model_version:
        try:
            model = load_model(model_version)
        except Exception:
            pass

    result = run_detectors(df, accel, settings, model)
    quality = sensor_quality_summary(df)
    timing = timestamp_quality(df)
    status = sensor_status(df)
    stats = summary_statistics(df)
    session = describe_session(df)

    html = build_report_html(
        session=session, status=status, quality=quality,
        timing=timing, statistics=stats,
        burst_features=result["burst_features"],
        alerts=result["alerts"],
        model_meta=model[1] if model else None,
        ai_message=result["ai_message"],
        notes=pd.DataFrame(),
    )
    return html
