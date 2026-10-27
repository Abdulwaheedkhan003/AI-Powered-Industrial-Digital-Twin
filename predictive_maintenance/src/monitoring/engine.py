"""Run every detector on one recording and collect the results.

Three detectors, always reported separately:
  threshold   : the user's configured warning / critical limits
  statistical : robust deviation from a baseline
  ai_model    : Isolation Forest anomaly score (only if a model is selected)
plus the user's combined multi-signal rules.

The dashboard, the report and the tests all call run_detectors(), so they
cannot disagree about what was detected.
"""
import pandas as pd

from config import SENSORS, SOURCE_UNKNOWN
from src.detection.isolation_forest import ModelError, score_features
from src.detection.statistical_detector import fit_baselines, statistical_levels
from src.detection.threshold_rules import SIGNALS, threshold_levels
from src.monitoring.alerts import (alerts_frame, combined_events, make_alert,
                                   persistence_events)
from src.processing.feature_pipeline import build_features
from src.processing.validation import mask_invalid
from src.processing.vibration_features import features_per_burst

ENVIRONMENT_NOTE = (" This is an observation about the surroundings, not evidence "
                    "of a motor fault.")


def get_signals(df, accel=None):
    """Return ({signal name: Series indexed by time}, burst_features or None)."""
    clean = mask_invalid(df)
    index = pd.to_datetime(clean["timestamp"])
    signals = {}
    for col in SENSORS:
        if col in clean.columns and clean[col].notna().any():
            signals[col] = pd.Series(clean[col].to_numpy(dtype=float), index=index)
    bursts = None
    if accel is not None and len(accel):
        bursts = features_per_burst(accel)
        good = bursts[bursts["valid"].astype(bool)]
        if len(good):
            times = pd.to_datetime(good["burst_time"])
            signals["vib_rms_total_g"] = pd.Series(good["rms_total_g"].to_numpy(float), index=times)
            signals["vib_peak_g"] = pd.Series(good["peak_g"].to_numpy(float), index=times)
    return signals, bursts


def data_source_of(df):
    sources = df["data_source"].unique() if "data_source" in df.columns else []
    return sources[0] if len(sources) == 1 else SOURCE_UNKNOWN


def _suffix(signal):
    return ENVIRONMENT_NOTE if SIGNALS[signal]["category"] == "environment" else ""


def run_detectors(df, accel, settings, model=None, stat_baselines=None):
    """model: (pipeline, meta) or None. stat_baselines: from a reference session."""
    signals, bursts = get_signals(df, accel)
    session_id = df["session_id"].iloc[0] if "session_id" in df.columns else "unknown"
    source = data_source_of(df)
    persist = settings["persistence"]
    alerts, notes = [], []
    events = {"threshold": {}, "statistical": {}, "ai_model": {}, "combined": {}}

    def value_at(series, ts):
        return float(series.loc[ts]) if ts in series.index else float("nan")

    # ---- 1. configured thresholds ------------------------------------------
    for name, rule in settings["thresholds"].items():
        if not rule["enabled"]:
            continue
        if name not in signals:
            notes.append(f"Threshold for {SIGNALS[name]['label']} not evaluated: no valid data.")
            continue
        info = SIGNALS[name]
        found = persistence_events(threshold_levels(signals[name], rule), **persist)
        events["threshold"][name] = found
        side = "above" if rule["direction"] == "high" else "below"
        for e in found:
            how = (f"for {e['count']} consecutive valid readings" if e["trigger"] == "consecutive"
                   else f"{e['count']} times within {persist['repeat_window_s']} s")
            text = (f"{info['label']} was {side} the configured {e['severity']} threshold "
                    f"({rule[e['severity']]:g} {info['unit']}) {how}." + _suffix(name))
            alerts.append(make_alert(session_id, source, "threshold", name, e,
                                     value_at(signals[name], e["raised_at"]), text))

    # ---- 2. statistical baseline -------------------------------------------
    stat = settings["statistical"]
    baselines, baseline_kind = stat_baselines, "the reference session"
    if baselines is None:
        baselines = fit_baselines(signals, first_seconds=stat["baseline_s"])
        baseline_kind = f"the first {stat['baseline_s']} s of this session"
    if stat["enabled"]:
        for name, series in signals.items():
            base = baselines.get(name)
            levels = statistical_levels(series, base, stat["k"])
            if levels is None:
                notes.append(f"Statistical check for {SIGNALS[name]['label']} skipped: "
                             "no usable baseline (too few readings or no variation).")
                continue
            found = persistence_events(levels, **persist)
            events["statistical"][name] = found
            info = SIGNALS[name]
            for e in found:
                text = (f"{info['label']} was more than {stat['k']:g} robust spreads from the "
                        f"baseline ({base['median']:.4g} {info['unit']}, taken from "
                        f"{baseline_kind}) for {e['count']} consecutive valid readings."
                        + _suffix(name))
                alerts.append(make_alert(session_id, source, "statistical", name, e,
                                         value_at(series, e["raised_at"]), text))

    # ---- 3. AI model --------------------------------------------------------
    window_s = model[1]["config"]["window_s"] if model else 60
    features = build_features(df, bursts, window_s=window_s)
    scores, ai_message = None, "No model selected."
    if model:
        pipeline, meta = model
        try:
            scores, missing = score_features(pipeline, meta, features, source)
            ai_message = f"Scored with model {meta['model_version']}."
            if missing:
                ai_message += (" Feature(s) with no data, replaced by the training median: "
                               + ", ".join(missing) + ".")
            levels = pd.Series(scores["anomaly_prediction"].to_numpy(),
                               index=pd.to_datetime(scores["window_start"]))
            found = persistence_events(levels, settings["ai"]["raise_after"],
                                       settings["ai"]["clear_after"])
            events["ai_model"]["anomaly_score"] = found
            by_time = scores.set_index("window_start")["anomaly_score"]
            for e in found:
                text = (f"The AI anomaly score exceeded the model's decision threshold "
                        f"({meta['threshold']:.3f}) in {e['count']} consecutive {window_s}-second "
                        f"windows (model {meta['model_version']}). The feature combination is "
                        "unusual compared with the training data; the model does not identify a cause.")
                alerts.append(make_alert(session_id, source, "ai_model", "anomaly_score", e,
                                         value_at(by_time, e["raised_at"]), text))
        except ModelError as err:
            ai_message = str(err)

    # ---- 4. combined rules --------------------------------------------------
    per_signal = {}
    for kind in ("threshold", "statistical"):
        for name, found in events[kind].items():
            per_signal.setdefault(name, []).extend(found)
    timeline = pd.DatetimeIndex(pd.to_datetime(df["timestamp"]))
    for rule in settings["combined_rules"]:
        found, missing = combined_events(rule, per_signal, timeline)
        events["combined"][rule["name"]] = found
        if missing:
            notes.append(f"Combined rule '{rule['name']}': not evaluated for "
                         + ", ".join(missing) + " (no data or no rule/baseline).")
        for e in found:
            labels = ", ".join(SIGNALS[s]["label"] for s in e["involved"])
            text = (f"Combined rule '{rule['name']}': {len(e['involved'])} of its signals had "
                    f"an active alert at the same time ({labels}). Worth investigating; "
                    "this is not a diagnosis.")
            if any(SIGNALS[s]["category"] == "environment" for s in e["involved"]):
                text += " Includes environmental signals, which are not motor-fault evidence."
            alerts.append(make_alert(session_id, source, "combined", rule["name"], e,
                                     float("nan"), text))

    return {"signals": signals, "burst_features": bursts, "baselines": baselines,
            "baseline_kind": baseline_kind, "events": events, "alerts": alerts_frame(alerts),
            "notes": notes, "window_features": features, "scores": scores,
            "ai_message": ai_message, "session_id": session_id, "data_source": source}
