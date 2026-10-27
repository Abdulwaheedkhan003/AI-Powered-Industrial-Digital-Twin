"""Evaluation of detector output against labelled data.

Anomaly detection vs fault classification:
  * The detectors here answer "is this window unusual?". They are trained (or
    configured) on normal data only and have never seen a fault.
  * A fault classifier answers "which fault is this?". That needs many
    recorded, verified examples of each fault, which this project does not have.

What can be measured:
  * With healthy data only: the false-alarm rate on healthy data. Nothing else.
    Recall, precision, F1, missed-detection rate and detection latency all need
    examples where something was verifiably wrong.
  * With labelled data: everything below, per 60-second window.

Rules followed to avoid flattering numbers:
  * the test session is a different recording from the training session;
  * thresholds and model settings are fixed before the test data is looked at.
"""
import numpy as np
import pandas as pd

from config import MOTOR_SENSORS
from src.detection.isolation_forest import train_model
from src.detection.statistical_detector import fit_baselines
from src.detection.threshold_rules import (SIGNALS, default_settings,
                                           suggest_thresholds)
from src.ingestion.data_generator import (PROCESS_SCENARIOS, generate_accel_bursts,
                                          generate_demo_data)
from src.monitoring.alerts import active_mask
from src.monitoring.engine import get_signals, run_detectors
from src.processing.feature_pipeline import build_features

NO_LABELS_MESSAGE = (
    "This dataset has no ground-truth labels (a `label_anomaly` column), so precision, "
    "recall, F1, the missed-detection rate and detection latency cannot be calculated. "
    "If the whole recording is known to be healthy, the only meaningful number is how "
    "often each detector raised an alarm on it (its false-alarm behaviour).")


def _ratio(a, b):
    return float(a / b) if b else None


def classification_metrics(y_true, y_pred):
    """Counts and rates for boolean arrays. A rate is None when undefined."""
    t = np.asarray(y_true, dtype=bool)
    p = np.asarray(y_pred, dtype=bool)
    tp, fp = int((t & p).sum()), int((~t & p).sum())
    fn, tn = int((t & ~p).sum()), int((~t & ~p).sum())
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else (0.0 if precision == 0 or recall == 0 else None))
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision,
            "recall": recall, "f1": f1, "false_positive_rate": _ratio(fp, fp + tn),
            "false_negative_rate": _ratio(fn, fn + tp)}


def detection_latency_s(times, y_true, y_pred):
    """Seconds from the first true anomaly to the first detection at or after it."""
    times = pd.to_datetime(pd.Series(times)).reset_index(drop=True)
    t = np.asarray(y_true, dtype=bool)
    p = np.asarray(y_pred, dtype=bool)
    if not t.any():
        return None
    onset = int(np.argmax(t))
    hits = np.flatnonzero(p[onset:])
    if len(hits) == 0:
        return None
    return float((times[onset + hits[0]] - times[onset]).total_seconds())


def window_flags(events_by_name, window_starts, window_s):
    """True for each window that overlaps an active alert from these events."""
    starts = pd.DatetimeIndex(pd.to_datetime(window_starts))
    flags = np.zeros(len(starts), dtype=bool)
    for found in events_by_name.values():
        for e in found:
            end = e["cleared_at"] if e["cleared_at"] is not None else pd.Timestamp.max
            flags |= (starts + pd.Timedelta(seconds=window_s) > e["raised_at"]) & (starts < end)
    return flags


def evaluate_result(result, window_s=60):
    """Score each detector's alerts against label_anomaly. Returns a DataFrame or None."""
    features = result["window_features"]
    if "label_anomaly" not in features.columns:
        return None
    truth = features["label_anomaly"].to_numpy(dtype=bool)
    rows = []
    for name, label in (("threshold", "Configured thresholds"),
                        ("statistical", "Statistical baseline"),
                        ("ai_model", "Isolation Forest")):
        found = result["events"][name]
        if not found:
            rows.append({"detector": label, "note": "not configured / not run"})
            continue
        pred = window_flags(found, features["window_start"], window_s)
        rows.append({"detector": label, **classification_metrics(truth, pred),
                     "detection_latency_s": detection_latency_s(features["window_start"], truth, pred),
                     "note": ""})
    return pd.DataFrame(rows).set_index("detector")


def synthetic_pipeline_check(n_train_sessions=12, duration_s=900, seed_test=2):
    """End-to-end software check on SYNTHETIC scenarios.

    Trains on several independent synthetic baseline sessions (so start-up
    warm-up is represented) and tests on separately generated scenario
    sessions. Thresholds and the statistical baseline come from the training
    sessions only. The numbers show the software pipeline works and how the
    three detectors differ on invented data. They are NOT estimates of
    fault-detection performance on the real motor.
    """
    pooled, feature_tables = {}, []
    for seed in range(100, 100 + n_train_sessions):
        train = generate_demo_data("stable_baseline", duration_s, seed=seed)
        signals, bursts = get_signals(train, generate_accel_bursts("stable_baseline", duration_s, seed=seed))
        feature_tables.append(build_features(train, bursts))
        for name, series in signals.items():
            pooled.setdefault(name, []).append(series)
    baselines = fit_baselines({name: pd.concat(parts) for name, parts in pooled.items()})

    settings = default_settings()
    for name in MOTOR_SENSORS + ["vib_rms_total_g"]:
        suggestion = suggest_thresholds(baselines[name])
        if suggestion:
            settings["thresholds"][name].update(enabled=True, **suggestion)
    model = train_model(pd.concat(feature_tables, ignore_index=True),
                        data_source=train["data_source"].iloc[0],
                        training_id=f"{n_train_sessions} synthetic baseline sessions "
                                    f"(seeds 100-{99 + n_train_sessions})",
                        operating_notes="synthetic pipeline check")

    frames = []
    for scenario in ["stable_baseline"] + PROCESS_SCENARIOS:
        test = generate_demo_data(scenario, duration_s, seed=seed_test)
        test_accel = generate_accel_bursts(scenario, duration_s, seed=seed_test)
        result = run_detectors(test, test_accel, settings, model, stat_baselines=baselines)
        table = evaluate_result(result).reset_index()
        table.insert(0, "scenario", scenario)
        table.insert(1, "changed_signal_type",
                     "none" if scenario == "stable_baseline" else
                     "environment" if scenario in ("gas_increase", "lidar_change", "ldr_change")
                     else "motor")
        frames.append(table)
    return pd.concat(frames, ignore_index=True), model[1]


assert all(s in SIGNALS for s in MOTOR_SENSORS)
