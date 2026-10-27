"""Tests for serial ingestion and signal analysis. Synthetic inputs only.

No test here touches real hardware: the serial reader is driven with text
lines and a fake port object.
"""
import time

import numpy as np
import pandas as pd
import pytest

import config
from src.ingestion.csv_logger import save_log
from src.ingestion.data_generator import (DEMO_VIBRATION_HZ, generate_accel_bursts,
                                          generate_demo_data, to_accel_lines,
                                          to_firmware_line)
from src.ingestion.line_parser import parse_accel_line
from src.ingestion.serial_reader import SerialReader
from src.processing.fft_analysis import (dominant_frequency, sampling_info,
                                         spectrum)
from src.processing.trends import rolling_rate, trend_summary
from src.processing.validation import sensor_quality_summary
from src.processing.vibration_features import burst_features, features_per_burst

ORIGINAL_FIRMWARE_LINE = "Vibration:0.12\tTemperature_C:30.25\tGas_ADC:612\tCurrent_A:0.148\tLDR_DO:1"


def make_burst(x, y=None, z=None, fs=500.0):
    n = len(x)
    zeros = np.zeros(n)
    return pd.DataFrame({"burst_id": 0, "burst_time": pd.Timestamp("2026-01-01"),
                         "t_us": (np.arange(n) / fs * 1e6).astype("int64"),
                         "accel_x_g": x, "accel_y_g": zeros if y is None else y,
                         "accel_z_g": zeros if z is None else z})


# ---- acceleration line parser ----------------------------------------------
def test_parse_accel_line():
    row = parse_accel_line("A\t3\t1000200\t4096\t-2048\t0")
    assert row == {"burst_no": 3, "t_us": 1000200, "accel_x_g": 1.0,
                   "accel_y_g": -0.5, "accel_z_g": 0.0}


@pytest.mark.parametrize("line", ["A\t1\t2\t3\t4", "A\t1\t2\tx\t4\t5", "B\t1\t2\t3\t4\t5",
                                  "A\t1\t2\t99999\t4\t5", "A\t1\t-5\t1\t1\t1"])
def test_parse_accel_line_rejects_damage(line):
    assert parse_accel_line(line) is None


# ---- serial reader ----------------------------------------------------------
def test_reader_accepts_original_firmware_and_labels_rows_real():
    reader = SerialReader("TEST", session_id="s1")
    reader.process_line(ORIGINAL_FIRMWARE_LINE.encode() + b"\r\n")
    slow, accel = reader.drain()
    assert len(slow) == 1 and accel.empty
    assert slow["data_source"].iloc[0] == config.SOURCE_REAL
    assert slow["temperature_c"].iloc[0] == 30.25
    # fields the original firmware does not send stay missing, not zero
    assert slow[["ldr_adc", "lidar_distance_cm"]].isna().all().all()
    assert reader.lines_ok == 1 and not reader.recent_problems
    assert len(reader.drain()[0]) == 0


def test_reader_ignores_boot_noise_and_keeps_partial_lines():
    reader = SerialReader("TEST")
    for junk in [b"ets Jun  8 2016 00:22:57\r\n", b"\xff\xfe\x00garbage\n", b"\n",
                 b"A\tbroken\n"]:
        reader.process_line(junk)
    reader.process_line("Vibration:0.10\tTemperature_C:31.0\tGas_AD")
    slow, _ = reader.drain()
    assert reader.lines_bad == 3
    assert len(slow) == 1 and np.isnan(slow["gas_adc"].iloc[0])
    assert slow["temperature_c"].iloc[0] == 31.0


def test_reader_round_trips_full_v2_stream_and_logs_to_csv(tmp_path):
    slow_in = generate_demo_data(duration_s=20)
    accel_in = generate_accel_bursts(duration_s=20, burst_period_s=6)
    reader = SerialReader("TEST", session_id="s2")
    for _, row in slow_in.iterrows():
        reader.process_line(to_firmware_line(row))
    for _, burst in accel_in.groupby("burst_id"):
        for line in to_accel_lines(burst):
            reader.process_line(line)
    slow, accel = reader.drain()
    np.testing.assert_allclose(slow[config.SENSOR_COLUMNS].to_numpy(float),
                               slow_in[config.SENSOR_COLUMNS].to_numpy(float))
    np.testing.assert_allclose(accel[config.ACCEL_COLUMNS], accel_in[config.ACCEL_COLUMNS])
    assert accel["burst_id"].nunique() == accel_in["burst_id"].nunique() == 3
    # real rows can be logged, and appended to, but never under a SYNTHETIC name
    path = save_log(slow, tmp_path / "s2.csv", append=True)
    save_log(slow, path, append=True)
    assert len(pd.read_csv(path)) == 40
    with pytest.raises(ValueError):
        save_log(slow, tmp_path / "SYNTHETIC_s2.csv")


def test_reader_survives_missing_port_and_reconnects():
    class FakePort:
        def __init__(self):
            self.lines = [ORIGINAL_FIRMWARE_LINE.encode() + b"\n"]

        def readline(self):
            if self.lines:
                return self.lines.pop()
            raise OSError("device unplugged")

        def close(self):
            pass

    attempts = []

    def opener():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("port not found")
        return FakePort()

    reader = SerialReader("COM_FAKE", opener=opener, retry_s=0.05)
    reader.start()
    deadline = time.time() + 5
    while len(attempts) < 3 and time.time() < deadline:
        time.sleep(0.02)
    reader.stop()
    assert len(attempts) >= 3              # failed open, then lost link, then retry
    assert reader.lines_ok >= 1
    assert reader.status == "stopped"


def test_reader_with_nonexistent_real_port_does_not_crash():
    reader = SerialReader("COM_DOES_NOT_EXIST_99", retry_s=0.05)
    reader.start()
    deadline = time.time() + 5
    while reader.status != "disconnected" and time.time() < deadline:
        time.sleep(0.02)
    assert reader.status == "disconnected" and "Cannot open" in reader.message
    reader.stop()
    assert reader.drain()[0].empty


# ---- vibration RMS / peaks --------------------------------------------------
def test_rms_of_sine_ignores_gravity_offset():
    t = np.arange(2000) / 2000.0
    burst = make_burst(0.2 * np.sin(2 * np.pi * 50 * t), z=np.full(2000, 1.0), fs=2000.0)
    f = burst_features(burst)
    assert f["valid"]
    assert f["rms_x_g"] == pytest.approx(0.2 / np.sqrt(2), rel=1e-3)
    assert f["rms_z_g"] == pytest.approx(0.0, abs=1e-12)     # pure gravity -> no vibration
    assert f["rms_total_g"] == pytest.approx(0.2 / np.sqrt(2), rel=1e-3)
    assert f["peak_g"] == pytest.approx(0.2, rel=1e-3)
    assert f["peak_to_peak_x_g"] == pytest.approx(0.4, rel=1e-3)
    assert f["crest_factor"] == pytest.approx(np.sqrt(2), rel=1e-3)
    assert f["dominant_freq_hz"] == pytest.approx(50.0)


def test_constant_and_zero_bursts_give_zero_rms_and_undefined_crest():
    for value in (0.0, 1.0, 0.7):
        f = burst_features(make_burst(np.full(128, value)))
        assert f["valid"] and f["rms_total_g"] == 0.0
        assert np.isnan(f["crest_factor"]) and np.isnan(f["dominant_freq_hz"])


def test_invalid_bursts_are_reported_not_computed():
    assert not burst_features(make_burst(np.zeros(10)))["valid"]
    x = np.zeros(64)
    x[5] = np.nan
    assert not burst_features(make_burst(x))["valid"]


def test_increasing_vibration_scenario_raises_rms_over_time():
    feats = features_per_burst(generate_accel_bursts("increasing_vibration", duration_s=300))
    assert feats["valid"].all()
    assert feats["rms_total_g"].iloc[-1] > 3 * feats["rms_total_g"].iloc[0]
    flat = features_per_burst(generate_accel_bursts(duration_s=300))
    assert flat["rms_total_g"].max() < 1.3 * flat["rms_total_g"].min()
    assert (flat["dominant_freq_hz"] - DEMO_VIBRATION_HZ).abs().max() <= 2.0


# ---- FFT --------------------------------------------------------------------
def test_fft_frequency_axis_and_amplitude():
    fs, n = 500.0, 250
    t = np.arange(n) / fs
    freqs, amp = spectrum(0.1 * np.sin(2 * np.pi * 50 * t) + 3.0, fs)
    assert freqs[0] == 0 and freqs[1] == pytest.approx(fs / n)
    assert freqs[-1] == pytest.approx(fs / 2)
    assert dominant_frequency(freqs, amp) == pytest.approx(50.0)
    assert amp.max() == pytest.approx(0.1, rel=0.02)
    assert amp[0] < 1e-5                                      # 3.0 offset removed


def test_fft_of_flat_and_noise_signals_has_no_dominant_frequency():
    freqs, amp = spectrum(np.full(128, 0.7), 500.0)
    assert dominant_frequency(freqs, amp) is None
    noise = np.random.default_rng(0).normal(0, 1, 512)
    assert dominant_frequency(*spectrum(noise, 500.0)) is None


def test_fft_rejects_short_or_incomplete_signals():
    with pytest.raises(ValueError):
        spectrum(np.zeros(8), 500.0)
    with pytest.raises(ValueError):
        spectrum(np.array([0.0] * 31 + [np.nan]), 500.0)


def test_sampling_info_detects_rate_and_irregular_timing():
    t_us = np.arange(256) * 2000
    info = sampling_info(t_us)
    assert info["regular"] and info["sample_rate_hz"] == pytest.approx(500.0)
    assert info["resolution_hz"] == pytest.approx(500 / 256)
    assert info["nyquist_hz"] == pytest.approx(250.0)
    jittery = t_us + np.random.default_rng(1).integers(0, 1500, 256)
    assert not sampling_info(np.sort(jittery))["regular"]
    assert not sampling_info(t_us[::-1])["valid"]
    assert not sampling_info(t_us[:5])["valid"]


# ---- trends -----------------------------------------------------------------
def test_trend_detects_rising_temperature_but_not_stable_light():
    rising = generate_demo_data("rising_temperature", duration_s=900)
    s = trend_summary(rising, "temperature_c")
    assert s["valid"] and s["slope_per_min"] > 0.5
    assert s["persistent_deviation_samples"] > 100
    assert rolling_rate(rising, "temperature_c").iloc[-1] == pytest.approx(1.2, abs=0.2)

    stable = generate_demo_data(duration_s=900)
    assert trend_summary(stable, "current_a")["persistent_deviation_samples"] == 0
    assert abs(trend_summary(stable, "current_a")["slope_per_min"]) < 0.001


def test_trend_handles_short_absent_and_constant_data():
    df = generate_demo_data(duration_s=30)
    assert not trend_summary(df, "temperature_c")["valid"]
    assert not trend_summary(df.drop(columns="gas_adc"), "gas_adc")["valid"]
    long = generate_demo_data(duration_s=300)
    s = trend_summary(long, "ldr_do")                 # constant binary signal
    assert s["valid"] and s["persistent_deviation_samples"] is None and s["note"]


def test_trend_ignores_sentinel_values():
    df = generate_demo_data("missing_sensor_data", duration_s=600)
    s = trend_summary(df, "temperature_c")
    assert s["valid_readings"] == 500 and s["baseline_median"] > 25


# ---- new slow sensors -------------------------------------------------------
def test_lidar_step_and_bad_readings_are_flagged():
    df = generate_demo_data(duration_s=300)
    df.loc[150:, "lidar_distance_cm"] += 40      # target moved
    df.loc[10, "lidar_distance_cm"] = 0          # no usable return
    df.loc[20, "lidar_distance_cm"] = 1200       # beyond TF-Luna range
    q = sensor_quality_summary(df).loc["lidar_distance_cm"]
    assert q["jumps"] >= 1 and q["sentinel"] == 1 and q["out_of_range"] == 1
    assert q["frozen"] == 0
