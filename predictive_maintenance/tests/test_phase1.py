"""Phase 1 software tests. All inputs are deterministic synthetic data.

Passing these tests shows the software handles its inputs correctly. It says
nothing about whether the physical sensors work or are calibrated.
"""
import io

import numpy as np
import pandas as pd
import pytest

import config
from src.ingestion.csv_logger import DataSourceError, save_log
from src.ingestion.csv_reader import DataImportError, load_csv
from src.ingestion.data_generator import generate_demo_data, to_firmware_line
from src.ingestion.line_parser import parse_line
from src.processing.statistics import summary_statistics
from src.processing.validation import (mask_invalid, quality_flags,
                                       sensor_quality_summary, timestamp_quality)

GOOD_LINE = "Vibration:0.12\tTemperature_C:30.25\tGas_ADC:612\tCurrent_A:0.148\tLDR_DO:1"


# ---- line parser ------------------------------------------------------------
def test_parse_good_line():
    values, problems = parse_line(GOOD_LINE)
    assert problems == []
    assert values == {"vibration_ms2": 0.12, "temperature_c": 30.25,
                      "gas_adc": 612.0, "current_a": 0.148, "ldr_do": 1.0,
                      "ldr_adc": None, "lidar_distance_cm": None}


def test_parse_truncated_line_leaves_missing_as_none():
    values, problems = parse_line("Vibration:0.12\tTemperature_C:30.")
    assert values["temperature_c"] == 30.0
    assert values["gas_adc"] is None and values["current_a"] is None
    assert any("Gas_ADC" in p for p in problems)


@pytest.mark.parametrize("line", ["", "garbage", "Vibration:abc", b"\xff\xfe\x00",
                                  "Temperature_C:nan", None])
def test_parse_bad_input_never_raises(line):
    values, problems = parse_line(line)
    assert problems
    assert all(v is None for v in values.values())


# ---- generator --------------------------------------------------------------
def test_generator_is_deterministic_and_labelled():
    a = generate_demo_data(seed=7, duration_s=60)
    b = generate_demo_data(seed=7, duration_s=60)
    pd.testing.assert_frame_equal(a, b)
    assert (a["data_source"] == config.SOURCE_SYNTHETIC).all()
    assert not a.equals(generate_demo_data(seed=8, duration_s=60))


def test_generator_roundtrips_through_firmware_format():
    df = generate_demo_data(duration_s=30)
    for _, row in df.iterrows():
        values, problems = parse_line(to_firmware_line(row))
        assert problems == []
        for col in config.SENSOR_COLUMNS:
            assert values[col] == pytest.approx(row[col])


def test_generator_rejects_unknown_scenario():
    with pytest.raises(ValueError):
        generate_demo_data(scenario="bearing_failure")


# ---- logger: real / synthetic separation -----------------------------------
def test_synthetic_needs_prefix_and_cannot_go_in_raw(tmp_path):
    df = generate_demo_data(duration_s=10)
    with pytest.raises(DataSourceError):
        save_log(df, tmp_path / "session1.csv")
    with pytest.raises(DataSourceError):
        save_log(df, config.RAW_DIR / "SYNTHETIC_x.csv")
    assert not (config.RAW_DIR / "SYNTHETIC_x.csv").exists()
    save_log(df, tmp_path / "SYNTHETIC_session1.csv")


def test_cannot_append_real_to_synthetic_or_mix(tmp_path):
    demo = generate_demo_data(duration_s=10)
    path = save_log(demo, tmp_path / "SYNTHETIC_a.csv")
    save_log(demo, path, append=True)
    assert len(pd.read_csv(path)) == 20

    real = demo.assign(data_source=config.SOURCE_REAL)
    with pytest.raises(DataSourceError):
        save_log(real, path, append=True)
    with pytest.raises(DataSourceError):
        save_log(pd.concat([demo, real]), tmp_path / "SYNTHETIC_b.csv")

    real_path = save_log(real, tmp_path / "real.csv")
    with pytest.raises(DataSourceError):
        save_log(demo, real_path, append=True)


# ---- CSV import -------------------------------------------------------------
def test_csv_roundtrip(tmp_path):
    df = generate_demo_data(scenario="missing_sensor_data", duration_s=120)
    path = save_log(df, tmp_path / "SYNTHETIC_r.csv")
    loaded, report = load_csv(path)
    assert len(loaded) == len(df)
    assert loaded["current_a"].isna().sum() == df["current_a"].isna().sum() > 0
    assert (loaded["data_source"] == config.SOURCE_SYNTHETIC).all()
    assert report[0] == "No cleaning was needed."


def test_csv_cleaning_is_reported():
    text = ("timestamp,temperature_c,current_a\n"
            "2026-01-01 00:00:02,30.5,0.15\n"
            "2026-01-01 00:00:00,30.0,abc\n"
            "not a time,30.1,0.15\n"
            "2026-01-01 00:00:02,99.0,0.99\n"
            "2026-01-01 00:00:03,,0.16\n")
    df, report = load_csv(io.StringIO(text))
    joined = " ".join(report)
    assert len(df) == 3
    assert df["timestamp"].is_monotonic_increasing
    assert df.loc[df["timestamp"] == "2026-01-01 00:00:02", "temperature_c"].item() == 30.5
    assert np.isnan(df["current_a"].iloc[0]) and np.isnan(df["temperature_c"].iloc[2])
    assert (df["data_source"] == config.SOURCE_UNKNOWN).all()
    for expected in ["unreadable timestamp", "non-numeric", "duplicate timestamp",
                     "sorted", "gas_adc", "unknown"]:
        assert expected in joined


@pytest.mark.parametrize("text", ["", "timestamp,temperature_c\n", "a,b\n1,2\n",
                                  "timestamp,temperature_c\nxx,1\n"])
def test_unusable_files_raise_clear_error(text):
    with pytest.raises(DataImportError):
        load_csv(io.StringIO(text))


# ---- validation -------------------------------------------------------------
def test_baseline_has_no_quality_problems():
    q = sensor_quality_summary(generate_demo_data(duration_s=600))
    assert (q[["missing", "out_of_range", "sentinel", "frozen"]] == 0).all().all()


def test_missing_and_sentinel_values_detected():
    df = generate_demo_data(scenario="missing_sensor_data", duration_s=300)
    q = sensor_quality_summary(df)
    assert q.loc["temperature_c", "sentinel"] == 50
    assert q.loc["current_a", "missing"] == df["current_a"].isna().sum() > 0
    assert "invalid" in q.loc["temperature_c", "status"]
    assert mask_invalid(df)["temperature_c"].isna().sum() == 50
    assert "temperature_c:sentinel" in quality_flags(df).iloc[120]


def test_frozen_values_detected():
    q = sensor_quality_summary(generate_demo_data(scenario="frozen_sensor", duration_s=300))
    assert q.loc["gas_adc", "frozen"] == 150
    assert q.loc["vibration_ms2", "frozen"] == 20
    assert "frozen" in q.loc["gas_adc", "status"]


def test_out_of_range_jump_and_absent_sensor():
    df = generate_demo_data(duration_s=200).drop(columns="ldr_do")
    df.loc[50, "current_a"] = 12.0        # beyond the ACS712-5A range
    df.loc[100:, "gas_adc"] += 900        # abrupt step
    q = sensor_quality_summary(df)
    assert q.loc["current_a", "out_of_range"] == 1
    assert q.loc["gas_adc", "jumps"] >= 1
    assert "unavailable" in q.loc["ldr_do", "status"]


def test_all_missing_sensor_is_unavailable_not_zero():
    df = generate_demo_data(duration_s=60)
    df["temperature_c"] = np.nan
    assert "unavailable" in sensor_quality_summary(df).loc["temperature_c", "status"]
    stats = summary_statistics(df)
    assert stats.loc["temperature_c", "valid"] == 0
    assert np.isnan(stats.loc["temperature_c", "mean"])


def test_timestamp_quality_detects_gap():
    df = generate_demo_data(duration_s=100)
    t = timestamp_quality(df)
    assert t["median_interval_s"] == pytest.approx(1.0, abs=0.05)
    assert t["gaps"] == 0 and t["irregular_intervals"] == 0
    gappy = df.drop(index=range(40, 60))
    assert timestamp_quality(gappy)["gaps"] == 1
    assert "note" in timestamp_quality(df.head(1))


# ---- statistics -------------------------------------------------------------
def test_statistics_known_values_and_sentinel_excluded():
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=6, freq="s"),
        "temperature_c": [10.0, 20.0, 30.0, 40.0, 0.0, np.nan],  # 0.0 = sentinel
    })
    row = summary_statistics(df).loc["temperature_c"]
    assert row["valid"] == 4 and row["missing_or_invalid"] == 2
    assert row["min"] == 10 and row["max"] == 40
    assert row["mean"] == 25 and row["median"] == 25
    assert row["std"] == pytest.approx(np.std([10, 20, 30, 40], ddof=1))


def test_statistics_constant_signal():
    df = pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=5, freq="s"),
                       "current_a": [0.2] * 5})
    row = summary_statistics(df).loc["current_a"]
    assert row["std"] == 0 and row["min"] == row["max"] == 0.2
