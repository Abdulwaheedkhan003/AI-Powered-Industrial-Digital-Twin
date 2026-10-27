"""Run the dashboard script headlessly and check every page builds without errors.

This does not check how the pages look. All storage is redirected to a
temporary folder so the tests never touch real recordings or settings.
"""
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import config
from src.detection.threshold_rules import default_settings, save_settings
from src.ingestion.data_generator import (SCENARIOS, generate_accel_bursts,
                                          generate_demo_data, to_accel_lines,
                                          to_firmware_line)
from src.ingestion.serial_reader import SerialReader
from src.monitoring.alerts import load_alerts
from src.monitoring.engine import get_signals
from src.monitoring.session_manager import list_sessions, save_demo_session
from src.processing.feature_pipeline import build_features
from src.detection.isolation_forest import save_model, train_model

PAGES = ["Overview", "Live / demo monitoring", "Sensor charts", "Data quality",
         "Vibration analysis", "Anomaly detection", "Alert history",
         "Session comparison", "Model evaluation", "Reports and exports", "Settings"]


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    for name in ("STATE_DIR", "MODELS_DIR", "RAW_DIR", "DEMO_DIR"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
    return tmp_path


def app(page="Overview", **session):
    at = AppTest.from_file("../app.py", default_timeout=300)
    at.session_state["page"] = page
    for key, value in session.items():
        at.session_state[key] = value
    return at.run()


def synthetic_model():
    df = generate_demo_data(duration_s=3600, seed=5)
    _, bursts = get_signals(df, generate_accel_bursts(duration_s=3600, seed=5))
    pipeline, meta = train_model(build_features(df, bursts), data_source=config.SOURCE_SYNTHETIC,
                                 training_id="test", operating_notes="test")
    return save_model(pipeline, meta)


@pytest.mark.parametrize("page", PAGES)
def test_every_page_runs_on_demo_data(page):
    at = app(page)
    assert not at.exception
    assert "SYNTHETIC" in at.warning[0].value


@pytest.mark.parametrize("page", PAGES)
def test_every_page_runs_with_thresholds_rules_model_and_saved_sessions(page):
    settings = default_settings()
    settings["thresholds"]["temperature_c"].update(enabled=True, warning=33.0, critical=40.0)
    settings["thresholds"]["vib_rms_total_g"].update(enabled=True, warning=0.08)
    settings["combined_rules"] = [{"name": "both", "signals": ["temperature_c", "vib_rms_total_g"],
                                   "min_active": 2}]
    save_settings(settings)
    version = synthetic_model()
    for seed in (1, 2):
        save_demo_session(generate_demo_data(duration_s=300, seed=seed),
                          generate_accel_bursts(duration_s=300, seed=seed))
    at = app(page)
    at.sidebar.selectbox[0].set_value("rising_temperature").run()
    assert not at.exception
    assert version in at.sidebar.selectbox[1].options


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_overview_runs_for_every_scenario(scenario):
    at = app()
    at.sidebar.selectbox[0].set_value(scenario).run()
    assert not at.exception


def test_serial_mode_without_hardware_explains_and_settings_still_open():
    at = app()
    at.sidebar.radio[0].set_value("ESP32 (serial)").run()
    assert not at.exception
    assert any("Not connected" in i.value for i in at.info)
    at.sidebar.radio[1].set_value("Settings").run()
    assert not at.exception and any("thresholds" in h.value for h in at.header)


def test_empty_modes_are_quiet():
    at = app()
    for mode in ("Import CSV", "Saved session"):
        at.sidebar.radio[0].set_value(mode).run()
        assert not at.exception


def test_demo_session_can_be_saved_and_reopened():
    at = app("Live / demo monitoring")
    next(b for b in at.button if "Save this demo session" in b.label).click().run()
    assert not at.exception
    sessions = list_sessions()
    assert len(sessions) == 1 and sessions["data_source"].iloc[0] == config.SOURCE_SYNTHETIC
    at.sidebar.radio[0].set_value("Saved session").run()
    assert not at.exception and "SYNTHETIC" in at.warning[0].value


def test_saving_settings_from_the_page_writes_valid_thresholds():
    at = app("Settings")
    at.checkbox(key="en_temperature_c").check()
    at.number_input(key="w_temperature_c").set_value(45.0)
    at.number_input(key="c_temperature_c").set_value(40.0)      # invalid: below warning
    next(b for b in at.button if b.label == "Save settings").click().run()
    assert any("critical must not be below" in e.value for e in at.error)
    assert not (config.STATE_DIR / "settings.json").exists()
    at.number_input(key="c_temperature_c").set_value(55.0)
    next(b for b in at.button if b.label == "Save settings").click().run()
    assert not at.exception and any("saved" in s.value.lower() for s in at.success)
    from src.detection.threshold_rules import load_settings
    rule = load_settings()["thresholds"]["temperature_c"]
    assert rule == {"enabled": True, "direction": "high", "warning": 45.0, "critical": 55.0}


def test_model_can_be_trained_from_the_page_and_is_labelled_synthetic():
    at = app("Anomaly detection")
    at.sidebar.slider[0].set_value(3600).run()
    at.text_input[0].set_value("demo conditions")
    next(c for c in at.checkbox if "healthy operation" in c.label).check()
    at.run()
    next(b for b in at.button if b.label == "Train model").click().run()
    assert not at.exception
    assert any("Saved model" in s.value for s in at.success)
    assert any("SYNTHETIC" in w.value for w in at.warning)
    assert len(list(config.MODELS_DIR.glob("ifm_*.joblib"))) == 1


def test_live_mode_shows_logs_and_alerts_on_received_lines():
    """Feed the reader text lines (no port) and check the live pages and log."""
    settings = default_settings()
    settings["thresholds"]["temperature_c"].update(enabled=True, warning=31.0)
    save_settings(settings)
    reader = SerialReader("TEST", session_id="live_test")
    reader.status, reader.message = "connected", "Connected (test)."
    for _, row in generate_demo_data(duration_s=120).iterrows():
        reader.process_line(to_firmware_line(row))
    for _, burst in generate_accel_bursts(duration_s=20).groupby("burst_id"):
        for line in to_accel_lines(burst):
            reader.process_line(line)

    at = app("Live / demo monitoring", reader=reader, auto_refresh=False)
    assert not at.exception
    assert "REAL HARDWARE DATA" in at.success[0].value
    logged = pd.read_csv(config.RAW_DIR / "live_test.csv")
    assert len(logged) == 120 and (logged["data_source"] == config.SOURCE_REAL).all()
    assert len(pd.read_csv(config.RAW_DIR / "live_test_accel.csv")) == 3 * 256
    history = load_alerts()                    # live alerts are recorded automatically
    assert (history["data_source"] == config.SOURCE_REAL).all()
    assert (history["alert_type"] == "threshold").sum() == 1

    at.run()                                   # a refresh with no new lines
    assert not at.exception
    assert len(pd.read_csv(config.RAW_DIR / "live_test.csv")) == 120
    assert len(load_alerts()) == len(history)

    reader.status, reader.message = "disconnected", "Connection to TEST lost. Retrying…"
    at.run()                                   # cable pulled: page still works
    assert not at.exception
    assert any("lost" in e.value for e in at.sidebar.error)
    for page in ("Overview", "Anomaly detection", "Reports and exports"):
        at.sidebar.radio[1].set_value(page).run()
        assert not at.exception
