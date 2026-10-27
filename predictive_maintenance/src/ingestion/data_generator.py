"""Synthetic demo data for developing and testing the software without hardware.

EVERYTHING produced here is invented. Noise levels, resolution and ranges are
taken from the sensor datasheets so the data behaves like the real stream, but
the values are not measurements of the real motor and must not be used as a
"healthy baseline" for it.

Two tables are produced, matching what firmware v2 sends:
  * slow table  : one row per second with every slow sensor;
  * accel table : short bursts of raw 3-axis acceleration at 500 Hz.

The slow table has a `label_anomaly` column: True while a scenario's injected
process change is active. It exists so the detectors can be scored in software
tests. Data-problem scenarios (missing, frozen, noisy, interrupted) are not
labelled as anomalies: they are sensor/communication faults, not motor events.
"""
import numpy as np
import pandas as pd

from config import (ACCEL_LSB_PER_G, FIRMWARE_KEY_MAP, NOMINAL_SAMPLE_PERIOD_S,
                    SENSOR_COLUMNS, SOURCE_SYNTHETIC)

SCENARIOS = {
    "stable_baseline": "Steady operation, nothing injected.",
    "rising_temperature": "Process change: temperature ramps upward after the first third.",
    "increasing_vibration": "Process change: vibration amplitude grows during the second half.",
    "unusual_current": "Process change: current steps up and oscillates in the last third.",
    "gas_increase": "Environment change: gas reading climbs in the last third.",
    "lidar_change": "Environment change: LiDAR distance jumps by 40 cm at the halfway point.",
    "ldr_change": "Environment change: light level drops at the halfway point.",
    "missing_sensor_data": "Data problem: DS18B20 'disconnects' (firmware prints 0) and "
                           "some current fields are missing.",
    "frozen_sensor": "Data problem: gas ADC stops updating; MPU6050 read fails (prints 0.00).",
    "noisy_measurements": "Data problem: much noisier readings in the second half.",
    "communication_interruption": "Data problem: no data at all for part of the session.",
}
PROCESS_SCENARIOS = ["rising_temperature", "increasing_vibration", "unusual_current",
                     "gas_increase", "lidar_change", "ldr_change"]

# Arbitrary demo choice. The real motor's vibration frequencies are unknown.
DEMO_VIBRATION_HZ = 110.0


def _check(scenario):
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{scenario}'. Choose from {list(SCENARIOS)}")


def _vibration_growth(scenario, fraction):
    """Amplitude multiplier at a given fraction (0..1) of the recording."""
    if scenario != "increasing_vibration":
        return np.ones_like(fraction, dtype=float)
    return 1.0 + 3.0 * np.clip((fraction - 0.5) * 2.0, 0.0, 1.0)


def _gap(fraction):
    """Part of the session with no data in the communication scenario."""
    return (fraction >= 0.5) & (fraction < 0.65)


def generate_demo_data(scenario="stable_baseline", duration_s=300,
                       sample_period_s=NOMINAL_SAMPLE_PERIOD_S, seed=42,
                       start_time="2026-01-01 00:00:00"):
    """Return the slow-sensor DataFrame. Same arguments -> same output."""
    _check(scenario)
    n = int(duration_s / sample_period_s)
    if n < 1:
        raise ValueError("duration_s must cover at least one sample")

    rng = np.random.default_rng(seed)
    t = np.arange(n) * sample_period_s
    fraction = t / t[-1] if n > 1 else t
    # Real loops are not perfectly periodic: add a few ms of jitter.
    t_jittered = t + rng.normal(0, 0.01, n)
    t_jittered[0] = 0.0
    t_jittered = np.maximum.accumulate(t_jittered)

    # Temperature: warm-up towards a plateau, quantised like a DS18B20.
    temp = 30.0 + 4.0 * (1 - np.exp(-t / 120.0)) + rng.normal(0, 0.03, n)
    # Vibration: firmware sends a rectified value, so it is always >= 0.
    vib = (np.abs(rng.normal(0, 0.35, n)) + 0.02) * _vibration_growth(scenario, fraction)
    # Current: small motor, noise comparable to an uncalibrated ACS712.
    cur = np.abs(0.15 + rng.normal(0, 0.03, n))
    # Gas: slow downward settling (warm-up) plus ADC noise.
    gas = 560 + 80 * np.exp(-t / 90.0) + rng.normal(0, 8, n)
    ldr = np.ones(n)
    # Analog light level: slow ambient drift plus ADC noise.
    ldr_adc = 2400 + 30 * np.sin(2 * np.pi * t / 600.0) + rng.normal(0, 12, n)
    # TF-Luna looking at a still target: 1 cm resolution.
    lidar = 35 + rng.normal(0, 0.4, n)

    label = np.zeros(n, dtype=bool)
    third, half, late = n // 3, n // 2, 2 * n // 3
    if scenario == "rising_temperature":
        temp[third:] += 0.02 * (t[third:] - t[third])
        label[third:] = True
    elif scenario == "increasing_vibration":
        label = fraction > 0.5
    elif scenario == "unusual_current":
        cur[late:] = cur[late:] * 1.6 + 0.05 * np.sin(2 * np.pi * t[late:] / 7.0)
        label[late:] = True
    elif scenario == "gas_increase":
        gas[late:] += 400 * (1 - np.exp(-(t[late:] - t[late]) / 60.0))
        label[late:] = True
    elif scenario == "lidar_change":
        lidar[half:] += 40
        label[half:] = True
    elif scenario == "ldr_change":
        ldr_adc[half:] -= 1500
        ldr[half:] = 0
        label[half:] = True
    elif scenario == "missing_sensor_data":
        temp[third:half] = 0.0                 # what the firmware prints on disconnect
        cur[rng.random(n) < 0.05] = np.nan     # field missing from the line
    elif scenario == "frozen_sensor":
        gas[half:] = gas[half]
        vib[n // 4: n // 4 + 20] = 0.0         # what the firmware prints on I2C failure
    elif scenario == "noisy_measurements":
        m = n - half
        temp[half:] += rng.normal(0, 0.4, m)
        vib[half:] += np.abs(rng.normal(0, 1.0, m))
        cur[half:] = np.abs(cur[half:] + rng.normal(0, 0.12, m))
        gas[half:] += rng.normal(0, 60, m)

    is_sentinel_zero = temp == 0.0
    temp = np.round(np.round(temp / 0.0625) * 0.0625, 2)
    temp[is_sentinel_zero] = 0.0

    df = pd.DataFrame({
        "timestamp": pd.Timestamp(start_time) + pd.to_timedelta(t_jittered, unit="s"),
        "session_id": f"demo_{scenario}_seed{seed}",
        "data_source": SOURCE_SYNTHETIC,
        "scenario": scenario,
        "vibration_ms2": np.round(vib, 2),
        "temperature_c": temp,
        "gas_adc": np.clip(np.round(gas), 0, 4095),
        "current_a": np.round(cur, 3),
        "ldr_do": ldr,
        "ldr_adc": np.clip(np.round(ldr_adc), 0, 4095),
        "lidar_distance_cm": np.round(lidar),
        "label_anomaly": label,
    })
    if scenario == "communication_interruption":
        df = df[~_gap(fraction)].reset_index(drop=True)
    return df


def generate_accel_bursts(scenario="stable_baseline", duration_s=300,
                          burst_period_s=6.0, samples_per_burst=256,
                          sample_rate_hz=500.0, seed=42,
                          start_time="2026-01-01 00:00:00"):
    """Return synthetic raw-acceleration bursts (values in g, gravity included).

    Each burst: a tone at DEMO_VIBRATION_HZ plus a weaker second harmonic,
    MPU6050-like noise (about 5 mg RMS) and quantisation, with gravity mostly
    on the Z axis.
    """
    _check(scenario)
    rng = np.random.default_rng(seed + 1)
    n_bursts = max(1, int(duration_s // burst_period_s))
    session_id = f"demo_{scenario}_seed{seed}"
    frames = []
    for b in range(n_bursts):
        fraction = np.array(b / max(n_bursts - 1, 1))
        t = np.arange(samples_per_burst) / sample_rate_hz
        t = t + rng.normal(0, 20e-6, samples_per_burst)   # timing jitter
        t[0] = 0.0
        growth = float(_vibration_growth(scenario, fraction))
        p1, p2 = rng.uniform(0, 2 * np.pi, 2)
        tone = growth * (0.05 * np.sin(2 * np.pi * DEMO_VIBRATION_HZ * t + p1)
                         + 0.015 * np.sin(2 * np.pi * 2 * DEMO_VIBRATION_HZ * t + p2))
        sigma = 0.02 if scenario == "noisy_measurements" and fraction >= 0.5 else 0.005
        noise = rng.normal(0, sigma, (samples_per_burst, 3))
        if scenario == "communication_interruption" and _gap(fraction):
            continue
        axes = np.column_stack([0.02 + 0.6 * tone, -0.03 + 0.3 * tone, 1.0 + tone]) + noise
        axes = np.round(axes * ACCEL_LSB_PER_G) / ACCEL_LSB_PER_G
        frames.append(pd.DataFrame({
            "burst_id": b,
            "burst_time": pd.Timestamp(start_time) + pd.Timedelta(seconds=b * burst_period_s),
            "t_us": np.round((b * burst_period_s + t) * 1e6).astype("int64"),
            "accel_x_g": axes[:, 0],
            "accel_y_g": axes[:, 1],
            "accel_z_g": axes[:, 2],
            "session_id": session_id,
            "data_source": SOURCE_SYNTHETIC,
            "scenario": scenario,
        }))
    return pd.concat(frames, ignore_index=True)


_LINE_FORMATS = {"vibration_ms2": "{:.2f}", "temperature_c": "{:.2f}",
                 "gas_adc": "{:.0f}", "current_a": "{:.3f}", "ldr_do": "{:.0f}",
                 "ldr_adc": "{:.0f}", "lidar_distance_cm": "{:.0f}"}


def to_firmware_line(row):
    """Format one slow row exactly like the firmware's Serial output.

    Missing values are left out of the line, which is how a truncated or
    corrupted serial message would look to the parser.
    """
    parts = []
    for key, col in FIRMWARE_KEY_MAP.items():
        value = row[col]
        if pd.notna(value):
            parts.append(f"{key}:{_LINE_FORMATS[col].format(value)}")
    return "\t".join(parts)


def to_accel_lines(burst):
    """Format one burst like firmware v2 does (raw int16 counts)."""
    return [f"A\t{int(r.burst_id)}\t{int(r.t_us)}\t"
            f"{round(r.accel_x_g * ACCEL_LSB_PER_G)}\t"
            f"{round(r.accel_y_g * ACCEL_LSB_PER_G)}\t"
            f"{round(r.accel_z_g * ACCEL_LSB_PER_G)}"
            for r in burst.itertuples()]


assert set(FIRMWARE_KEY_MAP.values()) == set(SENSOR_COLUMNS) == set(_LINE_FORMATS)
