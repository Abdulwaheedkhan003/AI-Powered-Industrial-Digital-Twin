"""Parse one text line in the format printed by the existing ESP32 firmware.

Example line (tab-separated "Key:value" pairs, not JSON):

    Vibration:0.12\tTemperature_C:30.25\tGas_ADC:612\tCurrent_A:0.148\tLDR_DO:1

This module never touches the serial port, so it can be tested without
hardware. serial_reader.py feeds received lines into these functions.
"""
import math

from config import (ACCEL_COLUMNS, ACCEL_LSB_PER_G, CORE_FIRMWARE_KEYS,
                    FIRMWARE_KEY_MAP, SENSOR_COLUMNS)


def parse_line(line):
    """Return (values, problems).

    values   : dict with every sensor column; None where the field was missing
               or not a number. Missing fields are never replaced with 0.
    problems : list of human-readable strings (empty if the line was clean).
    Never raises on bad input.
    """
    values = {col: None for col in SENSOR_COLUMNS}
    problems = []

    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    line = (line or "").strip()
    if not line:
        return values, ["empty line"]

    for part in line.split("\t"):
        if ":" not in part:
            problems.append(f"unreadable fragment '{part}'")
            continue
        key, text = part.split(":", 1)
        key = key.strip()
        if key not in FIRMWARE_KEY_MAP:
            problems.append(f"unknown field '{key}'")
            continue
        try:
            number = float(text)
        except ValueError:
            problems.append(f"{key}: '{text}' is not a number")
            continue
        if not math.isfinite(number):
            problems.append(f"{key}: non-finite value")
            continue
        values[FIRMWARE_KEY_MAP[key]] = number

    for key in CORE_FIRMWARE_KEYS:
        col = FIRMWARE_KEY_MAP[key]
        if values[col] is None and not any(p.startswith(f"{key}:") for p in problems):
            problems.append(f"{key}: field missing")

    return values, problems


def parse_accel_line(line):
    """Parse one raw-acceleration line sent by firmware v2.

    Format: A<TAB>burst_no<TAB>micros<TAB>ax<TAB>ay<TAB>az  (raw int16 counts)
    Returns a dict with burst_no, t_us and the three axes in g, or None if the
    line is damaged.
    """
    parts = line.strip().split("\t")
    if len(parts) != 6 or parts[0] != "A":
        return None
    try:
        burst_no, t_us, *counts = (int(p) for p in parts[1:])
    except ValueError:
        return None
    if burst_no < 0 or t_us < 0 or any(abs(c) > 32768 for c in counts):
        return None
    row = {"burst_no": burst_no, "t_us": t_us}
    row.update({col: c / ACCEL_LSB_PER_G for col, c in zip(ACCEL_COLUMNS, counts)})
    return row
