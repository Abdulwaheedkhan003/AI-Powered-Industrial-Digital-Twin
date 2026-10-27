"""Tests for alerts, sessions, the anomaly model, evaluation and reporting.

Synthetic inputs only. These tests show the software behaves as documented.
They say nothing about detection performance on a real motor.
"""
import numpy as np
import pandas as pd
import pytest

import config
from src.detection.drift_monitor import drift_report
from src.detection.evaluation import (classification_metrics, detection_latency_s,
                                      evaluate_result, synthetic_pipeline_check)
from src.detection.isolation_forest import (ModelError, list_models, load_model,
                                            save_model, score_features, train_model)
from src.detection.statistical_detector import fit_baseline, statistical_levels
from src.detection.threshold_rules import (default_settings, load_settings,
                                           save_settings, threshold_levels,
                                           validate_settings)
from src.ingestion.data_generator import (SCENARIOS, generate_accel_bursts,
                                          generate_demo_data)
from src.monitoring import notes as notes_mod
from src.monitoring.alerts import (filter_alerts, load_alerts, persistence_events,
                                   sync_alerts, update_alert)
from src.monitoring.engine import get_signals, run_detectors
from src.monitoring.sensor_status import sensor_status
from src.monitoring.session_manager import (compare_sessions, describe_session,
                                            list_sessions, load_session,
                                            save_demo_session, set_session_meta)
from src.processing.feature_pipeline import build_features, feature_columns
from src.processing.statistics import correlation_matrix, summary_statistics
from src.processing.validation import sensor_quality_summary, timestamp_quality
from src.reporting.summary_report import build_report_html


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Point every storage location at a temporary folder."""
    for name in ("STATE_DIR", "MODELS_DIR", "RAW_DIR", "DEMO_DIR"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
    return tmp_path


def series(values, start="2026-01-01", freq="s"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=freq), dtype=float)


def demo(scenario="stable_baseline", duration_s=600, seed=42):
    return (generate_demo_data(scenario, duration_s, seed=seed),
            generate_accel_bursts(scenario, duration_s, seed=seed))


# ---- scenarios --------------------------------------------------------------
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_every_scenario_generates_labelled_synthetic_data(scenario):
    df, accel = demo(scenario, 300)
    assert (df["data_source"] == config.SOURCE_SYNTHETIC).all()
    assert (accel["data_source"] == config.SOURCE_SYNTHETIC).all()
    assert df["label_anomaly"].dtype == bool
    assert len(sensor_quality_summary(df)) == len(config.SENSORS)


def test_communication_interruption_is_a_timestamp_gap_not_an_anomaly():
    df, accel = demo("communication_interruption", 300)
    assert timestamp_quality(df)["gaps"] == 1
    assert not df["label_anomaly"].any()
    assert accel["burst_id"].nunique() < demo("stable_baseline", 300)[1]["burst_id"].nunique()


# ---- thresholds -------------------------------------------------------------
def test_threshold_levels_high_low_and_missing():
    s = series([1, 5, 9, np.nan])
    high = threshold_levels(s, {"direction": "high", "warning": 4, "critical": 8})
    assert high.tolist()[:3] == [0, 1, 2] and np.isnan(high.iloc[3])
    low = threshold_levels(s, {"direction": "low", "warning": 6, "critical": 2})
    assert low.tolist()[:3] == [2, 1, 0]
    only_warning = threshold_levels(s, {"direction": "high", "warning": 4, "critical": None})
    assert only_warning.tolist()[:3] == [0, 1, 1]


def test_settings_validation_and_storage(state):
    settings = default_settings()
    assert validate_settings(settings) == []
    assert not any(rule["enabled"] for rule in settings["thresholds"].values())

    bad = default_settings()
    bad["thresholds"]["temperature_c"].update(enabled=True, warning=60, critical=50)
    bad["thresholds"]["current_a"].update(enabled=True)
    bad["thresholds"]["gas_adc"].update(warning=9999)
    bad["combined_rules"].append({"name": "x", "signals": ["current_a", "nope"], "min_active": 1})
    errors = " ".join(validate_settings(bad))
    for expected in ["critical must not be below", "no warning level", "outside what the sensor",
                     "unknown signal", "min_active"]:
        assert expected in errors
    with pytest.raises(ValueError):
        save_settings(bad)

    settings["thresholds"]["temperature_c"].update(enabled=True, warning=45.0, critical=55.0)
    save_settings(settings)
    assert load_settings()["thresholds"]["temperature_c"]["warning"] == 45.0
    (config.STATE_DIR / "settings.json").write_text("{broken")
    assert load_settings() == default_settings()


# ---- persistence ------------------------------------------------------------
def test_single_spikes_do_not_alert_but_sustained_exceedance_does():
    levels = series([0, 1, 0, 1, 0, 0, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0])
    events = persistence_events(levels, raise_after=3, clear_after=5)
    assert len(events) == 1
    e = events[0]
    assert e["first_exceeded"] == levels.index[6] and e["raised_at"] == levels.index[8]
    assert e["cleared_at"] == levels.index[14] and e["severity"] == "warning"


def test_one_alert_for_a_long_exceedance_and_missing_readings_are_neutral():
    levels = series([1] * 50)
    levels.iloc[10:20] = np.nan
    events = persistence_events(levels, raise_after=3, clear_after=5)
    assert len(events) == 1 and events[0]["cleared_at"] is None


def test_escalation_to_critical_and_repeat_window_rule():
    events = persistence_events(series([1, 1, 1, 2, 2, 2, 0]), raise_after=3, clear_after=5)
    assert events[0]["severity"] == "critical"
    flicker = series([1, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0])
    assert persistence_events(flicker, 3, 5) == []
    repeated = persistence_events(flicker, 3, 5, repeat_count=3, repeat_window_s=10)
    assert len(repeated) == 1 and repeated[0]["trigger"] == "repeated"


# ---- statistical detector ---------------------------------------------------
def test_statistical_detector_flags_shift_and_handles_flat_baseline():
    rng = np.random.default_rng(0)
    base = fit_baseline(rng.normal(10, 1, 200))
    assert base["median"] == pytest.approx(10, abs=0.3) and base["spread"] == pytest.approx(1, abs=0.3)
    levels = statistical_levels(series([10, 10.5, 20, np.nan]), base, k=4)
    assert levels.tolist()[:3] == [0, 0, 1] and np.isnan(levels.iloc[3])
    assert statistical_levels(series([1, 2]), fit_baseline([5.0] * 10), 4) is None
    coarse = fit_baseline([34.0] * 90 + [34.06] * 10)       # MAD is 0, std is not
    assert coarse["spread"] > 0
    assert fit_baseline([1.0, 2.0]) is None


# ---- engine: alerts with explanations --------------------------------------
def test_threshold_alert_has_one_entry_and_a_plain_explanation():
    df, accel = demo("rising_temperature", 900)
    settings = default_settings()
    settings["statistical"]["enabled"] = False
    settings["thresholds"]["temperature_c"].update(enabled=True, warning=38.0, critical=42.0)
    alerts = run_detectors(df, accel, settings)["alerts"]
    assert len(alerts) == 1
    a = alerts.iloc[0]
    assert a["alert_type"] == "threshold" and a["severity"] == "critical"
    assert "above the configured critical threshold (42 °C) for 3 consecutive" in a["explanation"]
    assert "fail" not in a["explanation"].lower()


def test_disabled_and_unavailable_sensors_do_not_break_detection():
    df, _ = demo("stable_baseline", 300)
    settings = default_settings()
    settings["thresholds"]["vib_rms_total_g"].update(enabled=True, warning=0.1)
    settings["thresholds"]["lidar_distance_cm"].update(enabled=True, warning=100)
    result = run_detectors(df.drop(columns=["lidar_distance_cm"]), None, settings)
    assert any("Vibration RMS per burst not evaluated" in n for n in result["notes"])
    assert any("TF-Luna" in n for n in result["notes"])
    assert not (result["alerts"]["alert_type"] == "threshold").any()


def test_environment_alerts_are_marked_as_not_motor_evidence():
    df, accel = demo("gas_increase", 900)
    settings = default_settings()
    settings["thresholds"]["gas_adc"].update(enabled=True, warning=800)
    alerts = run_detectors(df, accel, settings)["alerts"]
    gas = alerts[(alerts["signal"] == "gas_adc") & (alerts["alert_type"] == "threshold")]
    assert len(gas) == 1 and "not evidence of a motor fault" in gas["explanation"].iloc[0]


def test_combined_rule_needs_overlap_and_reports_missing_signals():
    df, accel = demo("increasing_vibration", 900)
    late = df["timestamp"] > df["timestamp"].iloc[700]
    df.loc[late, "current_a"] += 0.4
    settings = default_settings()
    settings["statistical"]["enabled"] = False
    settings["thresholds"]["vib_rms_total_g"].update(enabled=True, warning=0.08)
    settings["thresholds"]["current_a"].update(enabled=True, warning=0.4)
    settings["combined_rules"] = [
        {"name": "vibration+current", "signals": ["vib_rms_total_g", "current_a"], "min_active": 2},
        {"name": "needs lidar", "signals": ["current_a", "lidar_distance_cm"], "min_active": 2}]
    result = run_detectors(df, accel, settings)
    combined = result["alerts"][result["alerts"]["alert_type"] == "combined"]
    assert len(combined) == 1 and combined["signal"].iloc[0] == "vibration+current"
    assert combined["timestamp"].iloc[0] >= df["timestamp"].iloc[700]
    assert "not a diagnosis" in combined["explanation"].iloc[0]
    assert any("needs lidar" in n and "lidar_distance_cm" in n for n in result["notes"])

    settings["thresholds"]["current_a"]["enabled"] = False
    alone = run_detectors(df, accel, settings)["alerts"]
    assert not (alone["alert_type"] == "combined").any()


# ---- alert history ----------------------------------------------------------
def test_alert_history_keeps_operator_input_and_does_not_duplicate(state):
    df, accel = demo("rising_temperature", 900)
    settings = default_settings()
    settings["statistical"]["enabled"] = False
    settings["thresholds"]["temperature_c"].update(enabled=True, warning=38.0)
    alerts = run_detectors(df, accel, settings)["alerts"]
    assert len(sync_alerts(alerts)) == len(alerts) == 1
    alert_id = alerts["alert_id"].iloc[0]
    update_alert(alert_id, acknowledged=True, operator_note="checked fan", resolution="investigating")
    history = sync_alerts(alerts)                       # re-running detection
    assert len(history) == 1
    row = load_alerts().set_index("alert_id").loc[alert_id]
    assert row["acknowledged"] and row["operator_note"] == "checked fan"
    assert row["resolution"] == "investigating"
    with pytest.raises(ValueError):
        update_alert(alert_id, resolution="component failed")
    with pytest.raises(KeyError):
        update_alert("nope", acknowledged=True)

    only = filter_alerts(history, signals=["temperature_c"], types=["threshold"],
                         severities=["warning"], start="2026-01-01", end="2026-01-02")
    assert len(only) == 1
    assert filter_alerts(history, start="2027-01-01").empty
    assert filter_alerts(history, severities=["critical"]).empty


# ---- sensor status ----------------------------------------------------------
def test_sensor_status_distinguishes_unavailable_invalid_stale_and_frozen():
    df, _ = demo("stable_baseline", 300)
    ok = sensor_status(df)
    assert (ok["status"] == "Reporting — calibration not verified").all()
    assert "healthy" not in " ".join(ok["status"]).lower()

    broken = df.drop(columns=["ldr_adc"]).copy()
    broken.loc[100:, "temperature_c"] = 0.0
    broken.loc[250:, "gas_adc"] = 600
    status = sensor_status(broken)["status"]
    assert status["Light (LDR module, analog output)"] == "Unavailable"
    assert status["Temperature (DS18B20)"] == "Invalid readings"
    assert status["Gas sensor (MQ-5, raw ADC)"] == "Not updating"

    later = df["timestamp"].iloc[-1] + pd.Timedelta(seconds=60)
    assert (sensor_status(df, now=later)["status"] == "Stale").all()


# ---- sessions ---------------------------------------------------------------
def test_sessions_are_saved_listed_reloaded_and_compared(state):
    a, accel_a = demo("stable_baseline", 300, seed=1)
    b, accel_b = demo("unusual_current", 300, seed=2)
    path = save_demo_session(a, accel_a)
    save_demo_session(b, accel_b)
    assert path.name.startswith("SYNTHETIC_") and path.parent == config.DEMO_DIR
    sessions = list_sessions()
    assert len(sessions) == 2 and (sessions["data_source"] == config.SOURCE_SYNTHETIC).all()
    assert sessions["samples"].tolist() == [300, 300]

    df, accel, _ = load_session(path)
    assert len(df) == 300 and accel["burst_id"].nunique() == accel_a["burst_id"].nunique()
    assert df["label_anomaly"].dtype == bool

    table, warnings = compare_sessions(a, b)
    assert table.loc["current_a", "max_B"] > table.loc["current_a", "max_A"] + 0.05
    assert any("Operating conditions are not recorded" in w for w in warnings)
    set_session_meta(a["session_id"].iloc[0], "6 V, no load", "first run")
    assert describe_session(a)["operating_condition"] == "6 V, no load"
    _, warnings = compare_sessions(a, b, {"operating_condition": "6 V, no load"},
                                   {"operating_condition": "9 V, loaded"})
    assert any("not like-for-like" in w for w in warnings)
    _, warnings = compare_sessions(a, b.assign(data_source=config.SOURCE_REAL))
    assert any("not comparable" in w for w in warnings)


# ---- correlation ------------------------------------------------------------
def test_correlation_matrix_skips_constant_sensors():
    df, _ = demo("rising_temperature", 900)
    df["current_a"] = df["temperature_c"] * 0.01
    matrix, counts = correlation_matrix(df)
    assert "ldr_do" not in matrix.columns            # never changes
    assert matrix.loc["temperature_c", "current_a"] > 0.99
    assert counts.loc["temperature_c", "current_a"] == len(df)
    assert correlation_matrix(df[["timestamp", "data_source", "ldr_do"]])[0].empty


# ---- feature pipeline -------------------------------------------------------
def test_features_are_windowed_and_only_built_from_available_data():
    df, accel = demo("stable_baseline", 600)
    _, bursts = get_signals(df, accel)
    features = build_features(df, bursts)
    assert len(features) == 10
    cols = feature_columns(features)
    assert {"temp_mean_c", "temp_rate_c_per_min", "current_mean_a", "current_std_a",
            "vibration_mean_ms2", "vib_rms_g", "vib_dom_freq_hz"} <= set(cols)
    assert not any(c.startswith(("gas", "ldr", "lidar")) for c in cols)
    first_minute = df[df["timestamp"] < df["timestamp"].iloc[0] + pd.Timedelta(seconds=60)]
    assert features["temp_mean_c"].iloc[0] == pytest.approx(first_minute["temperature_c"].mean())

    slow_only = build_features(df.drop(columns=["current_a"]), None, include_environment=True)
    cols = feature_columns(slow_only)
    assert "current_mean_a" not in cols and "vib_rms_g" not in cols and "gas_mean_adc" in cols


def test_windows_with_too_few_valid_readings_are_left_missing():
    df, _ = demo("missing_sensor_data", 600)
    features = build_features(df, None)
    assert features["temp_mean_c"].isna().sum() >= 1          # sentinel block
    assert features["current_mean_a"].notna().all()
    assert features["temp_mean_c"].dropna().min() > 25        # no zeros averaged in


# ---- isolation forest -------------------------------------------------------
def train_on(df, accel, **kw):
    _, bursts = get_signals(df, accel)
    return train_model(build_features(df, bursts), data_source=df["data_source"].iloc[0],
                       training_id=df["session_id"].iloc[0], **kw)


def test_model_is_reproducible_and_survives_save_and_load(state):
    df, accel = demo("stable_baseline", 3600, seed=1)
    pipe_a, meta_a = train_on(df, accel)
    pipe_b, meta_b = train_on(df, accel)
    assert meta_a["threshold"] == meta_b["threshold"]
    assert meta_a["training_id"] == "demo_stable_baseline_seed1"
    assert any("SYNTHETIC" in w for w in meta_a["warnings"])
    assert any("Small training set" in w for w in meta_a["warnings"])

    version = save_model(pipe_a, meta_a)
    loaded, meta = load_model(version)
    assert meta["features"] == meta_a["features"] and list_models()[0]["model_version"] == version

    test, test_accel = demo("increasing_vibration", 900, seed=2)
    _, bursts = get_signals(test, test_accel)
    features = build_features(test, bursts)
    before, _ = score_features(pipe_a, meta_a, features, config.SOURCE_SYNTHETIC)
    after, _ = score_features(loaded, meta, features, config.SOURCE_SYNTHETIC)
    pd.testing.assert_frame_equal(before, after)
    assert (after["model_version"] == version).all()
    # windows 5-7 are steady and unchanged; the last three have 4x the vibration
    assert after["anomaly_score"].iloc[-3:].min() > after["anomaly_score"].iloc[5:8].max()


def test_model_refuses_bad_training_data_and_source_mismatch():
    df, accel = demo("stable_baseline", 1800)
    _, bursts = get_signals(df, accel)
    features = build_features(df, bursts)
    with pytest.raises(ModelError, match="at least"):
        train_model(features.head(5), data_source=config.SOURCE_SYNTHETIC, training_id="x")
    with pytest.raises(ModelError, match="entirely real or entirely synthetic"):
        train_model(features, data_source=config.SOURCE_UNKNOWN, training_id="x")
    pipeline, meta = train_model(features, data_source=config.SOURCE_SYNTHETIC, training_id="x")
    with pytest.raises(ModelError, match="cannot score 'real_hardware'"):
        score_features(pipeline, meta, features, config.SOURCE_REAL)

    # the engine reports the refusal instead of crashing or scoring anyway
    real = df.assign(data_source=config.SOURCE_REAL)
    result = run_detectors(real, None, default_settings(), (pipeline, meta))
    assert result["scores"] is None and "cannot score" in result["ai_message"]


def test_model_scores_data_with_a_missing_sensor():
    df, accel = demo("stable_baseline", 1800)
    pipeline, meta = train_on(df, accel)
    features = build_features(df.drop(columns=["current_a"]), None)
    scores, missing = score_features(pipeline, meta, features, config.SOURCE_SYNTHETIC)
    assert "current_mean_a" in missing and "vib_rms_g" in missing
    assert scores["anomaly_score"].notna().all()


# ---- drift ------------------------------------------------------------------
def test_drift_is_reported_without_calling_it_a_fault():
    df, accel = demo("stable_baseline", 1800, seed=1)
    pipeline, meta = train_on(df, accel)
    same, same_accel = demo("stable_baseline", 1800, seed=2)
    _, bursts = get_signals(same, same_accel)
    table, message = drift_report(build_features(same, bursts), meta)
    assert (table["status"] == "similar to training").all() and "No persistent" in message

    hot = same.copy()
    hot["temperature_c"] += 8
    table, message = drift_report(build_features(hot, bursts), meta)
    assert table.loc["temp_mean_c", "status"] == "drifted"
    assert table.loc["current_mean_a", "status"] == "similar to training"
    assert "not a fault diagnosis" in message
    table, message = drift_report(build_features(same.head(120), None), meta)
    assert "Not enough data" in message


# ---- evaluation -------------------------------------------------------------
def test_metrics_on_a_known_confusion_matrix():
    truth = [1, 1, 1, 0, 0, 0, 0, 0]
    pred = [1, 1, 0, 1, 0, 0, 0, 0]
    m = classification_metrics(truth, pred)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 1, 1, 4)
    assert m["precision"] == pytest.approx(2 / 3) and m["recall"] == pytest.approx(2 / 3)
    assert m["f1"] == pytest.approx(2 / 3)
    assert m["false_positive_rate"] == pytest.approx(0.2)
    assert m["false_negative_rate"] == pytest.approx(1 / 3)


def test_metrics_are_undefined_rather_than_invented_without_positives():
    m = classification_metrics([0, 0, 0], [0, 1, 0])
    assert m["recall"] is None and m["false_negative_rate"] is None
    assert m["false_positive_rate"] == pytest.approx(1 / 3)
    times = pd.date_range("2026-01-01", periods=5, freq="min")
    assert detection_latency_s(times, [0, 0, 0, 0, 0], [1, 0, 0, 0, 0]) is None
    assert detection_latency_s(times, [0, 1, 1, 1, 1], [1, 0, 0, 1, 1]) == 120
    assert detection_latency_s(times, [0, 1, 1, 1, 1], [0, 0, 0, 0, 0]) is None


def test_evaluation_needs_labels():
    df, accel = demo("stable_baseline", 300)
    result = run_detectors(df.drop(columns=["label_anomaly"]), accel, default_settings())
    assert evaluate_result(result) is None


def test_synthetic_pipeline_check_runs_end_to_end():
    table, meta = synthetic_pipeline_check()
    assert meta["data_source"] == config.SOURCE_SYNTHETIC and meta["n_windows"] == 180
    by = table.set_index(["scenario", "detector"])
    assert len(by) == 7 * 3
    # Multi-feature change: found by all three detectors.
    assert (by.loc["increasing_vibration", "recall"] > 0.8).all()
    # The statistical baseline finds single-signal changes ...
    for scenario in ("rising_temperature", "unusual_current"):
        assert by.loc[(scenario, "Statistical baseline"), "recall"] > 0.5
    # ... including environment changes, which the motor-only model ignores by design.
    assert by.loc[("lidar_change", "Statistical baseline"), "recall"] > 0.8
    assert by.loc[("lidar_change", "Isolation Forest"), "recall"] == 0
    assert by.loc[("stable_baseline", "Isolation Forest"), "false_positive_rate"] < 0.2


def test_training_set_cannot_mix_real_and_synthetic_sessions(state):
    from src.ingestion.csv_logger import save_log
    from src.monitoring.session_manager import training_features
    a, accel_a = demo("stable_baseline", 300, seed=1)
    b, _ = demo("stable_baseline", 300, seed=2)
    path_a, path_b = save_demo_session(a, accel_a), save_demo_session(b)
    features, source, ids = training_features([path_a, path_b])
    assert len(features) == 10 and source == config.SOURCE_SYNTHETIC and "seed2" in ids
    real = a.assign(data_source=config.SOURCE_REAL, session_id="real_x")
    real_path = save_log(real, config.RAW_DIR / "real_x.csv")
    with pytest.raises(ValueError, match="mix data sources"):
        training_features([path_a, real_path])


# ---- notes and report -------------------------------------------------------
def test_notes_are_unconfirmed_unless_explicitly_confirmed(state):
    notes_mod.add_note("s1", "Observed symptom", "rattling noise")
    notes_mod.add_note("s1", "Maintenance action", "tightened mount", confirmed_diagnosis=True)
    notes_mod.add_note("s2", "Session comment", "other session")
    notes = notes_mod.load_notes(session_id="s1")
    assert notes["confirmed_diagnosis"].tolist() == [False, True]
    with pytest.raises(ValueError):
        notes_mod.add_note("s1", "Diagnosis", "bearing failed")
    with pytest.raises(ValueError):
        notes_mod.add_note("s1", "Session comment", "   ")


def test_report_contains_every_section_and_labels_synthetic_data(state):
    df, accel = demo("rising_temperature", 900)
    settings = default_settings()
    settings["thresholds"]["temperature_c"].update(enabled=True, warning=38.0)
    result = run_detectors(df, accel, settings)
    common = dict(status=sensor_status(df), quality=sensor_quality_summary(df),
                  timing=timestamp_quality(df), statistics=summary_statistics(df),
                  model_meta=None)
    report = build_report_html(
        session=describe_session(df), burst_features=result["burst_features"],
        alerts=result["alerts"], ai_message=result["ai_message"],
        notes=notes_mod.load_notes(session_id="none"), **common)
    for expected in ["SYNTHETIC DEMO DATA", "Sensor status", "Data quality", "Statistics",
                     "Vibration analysis", "Alerts", "AI model", "No model selected",
                     "Maintenance notes", "Important limitations", "above the configured"]:
        assert expected in report
    real = build_report_html(
        session={**describe_session(df), "data_source": config.SOURCE_REAL},
        burst_features=None, alerts=result["alerts"].head(0), ai_message="",
        notes=notes_mod.load_notes(), **common)
    assert "SYNTHETIC" not in real and "No raw acceleration bursts" in real
