"""Central configuration: paths, data-source labels and the sensor schema.

Everything that depends on the ESP32 firmware (field names, baud rate,
sentinel values) lives here so the rest of the code stays hardware-independent.
Values marked UNVERIFIED must be checked against the real hardware in Phase 6.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"              # real hardware recordings only
PROCESSED_DIR = DATA_DIR / "processed"  # cleaned exports
DEMO_DIR = DATA_DIR / "demo"            # synthetic data only
STATE_DIR = DATA_DIR / "state"          # settings, alert history, notes, session notes
MODELS_DIR = BASE_DIR / "models"        # trained models + their metadata

# --- Data-source labels (never mixed in one file) ---------------------------
SOURCE_REAL = "real_hardware"
SOURCE_SYNTHETIC = "synthetic_demo"
SOURCE_UNKNOWN = "unknown"   # imported file that does not say where it came from
SYNTHETIC_FILE_PREFIX = "SYNTHETIC_"

# --- Serial settings, taken from the inspected firmware ---------------------
SERIAL_BAUD = 115200
# UNVERIFIED: loop = delay(200) + a blocking DS18B20 conversion (up to ~750 ms
# at the library's default 12-bit resolution), so roughly 1 sample per second.
NOMINAL_SAMPLE_PERIOD_S = 1.0

# Firmware prints "Key:value" pairs separated by tabs. Map key -> CSV column.
FIRMWARE_KEY_MAP = {
    "Vibration": "vibration_ms2",
    "Temperature_C": "temperature_c",
    "Gas_ADC": "gas_adc",
    "Current_A": "current_a",
    "LDR_DO": "ldr_do",
    # Sent only by firmware v2 (firmware/motor_monitor_v2), and only if enabled:
    "LDR_ADC": "ldr_adc",
    "Lidar_cm": "lidar_distance_cm",
}
# Fields every firmware version sends; a line without them is reported.
CORE_FIRMWARE_KEYS = ["Vibration", "Temperature_C", "Gas_ADC", "Current_A", "LDR_DO"]

# Raw acceleration bursts (firmware v2): "A<TAB>burst<TAB>micros<TAB>ax<TAB>ay<TAB>az"
ACCEL_LSB_PER_G = 4096.0   # MPU6050 at +/-8 g, the range set by the firmware
ACCEL_COLUMNS = ["accel_x_g", "accel_y_g", "accel_z_g"]
ACCEL_LOG_COLUMNS = ["burst_id", "burst_time", "t_us", *ACCEL_COLUMNS,
                     "session_id", "data_source", "scenario"]

# --- Sensor schema -----------------------------------------------------------
# min/max     : physically possible range for this sensor + firmware. These are
#               plausibility limits, NOT alarm thresholds.
# sentinels   : exact values the firmware/sensor emits on failure.
# frozen_run  : identical consecutive readings before we call it "possibly
#               frozen" (None = check disabled).
# check_jumps : look for abrupt discontinuities.
SENSORS = {
    "vibration_ms2": {
        "label": "Vibration (firmware-derived)",
        "unit": "m/s²",
        "min": 0.0,
        "max": 130.0,  # ±8 g range on 3 axes, minus 1 g
        "sentinels": {},
        "frozen_run": 10,
        "check_jumps": True,
        "note": "|‖a‖ − 1 g| of ONE sample per line. Not an RMS and not usable "
                "for FFT. Firmware prints 0.00 when the MPU6050 read fails.",
    },
    "temperature_c": {
        "label": "Temperature (DS18B20)",
        "unit": "°C",
        "min": -55.0,
        "max": 125.0,
        "sentinels": {
            0.0: "firmware prints 0 when the DS18B20 is disconnected",
            85.0: "DS18B20 power-on reset value",
            -127.0: "DallasTemperature 'device disconnected' value",
        },
        "frozen_run": 120,  # 0.0625 °C resolution: repeats are normal
        "check_jumps": True,
        "note": "Accuracy about ±0.5 °C. Measures wherever the probe is mounted.",
    },
    "gas_adc": {
        "label": "Gas sensor (MQ-5, raw ADC)",
        "unit": "ADC counts (0–4095)",
        "min": 0.0,
        "max": 4095.0,
        "sentinels": {},
        "frozen_run": 30,
        "check_jumps": True,
        "note": "Uncalibrated raw counts, not ppm. Needs warm-up. "
                "Environmental observation, not a motor-health signal.",
    },
    "current_a": {
        "label": "Current (ACS712-5A)",
        "unit": "A (uncalibrated)",
        "min": 0.0,
        "max": 5.0,
        "sentinels": {},
        "frozen_run": 10,
        "check_jumps": True,
        "note": "Absolute value, single ADC sample, zero offset taken at boot. "
                "Not verified against a reference meter.",
    },
    "ldr_do": {
        "label": "Light (LDR module, digital output)",
        "unit": "0 / 1",
        "min": 0.0,
        "max": 1.0,
        "sentinels": {},
        "frozen_run": None,  # a binary signal is expected to stay constant
        "check_jumps": False,
        "note": "Digital comparator output inverted by the firmware. Only "
                "'above/below the module's trim-pot level', not a light level.",
    },
    "ldr_adc": {
        "label": "Light (LDR module, analog output)",
        "unit": "ADC counts (0–4095)",
        "min": 0.0,
        "max": 4095.0,
        "sentinels": {},
        "frozen_run": 60,
        "check_jumps": True,
        "note": "Relative light level only. Whether brighter means a higher or "
                "lower count depends on the module and must be checked. "
                "Not a motor-health signal.",
    },
    "lidar_distance_cm": {
        "label": "Distance (TF-Luna LiDAR)",
        "unit": "cm",
        "min": 20.0,    # TF-Luna datasheet range 0.2 m – 8 m
        "max": 800.0,
        "sentinels": {0.0: "TF-Luna reading with unusable return signal (UNVERIFIED)"},
        "frozen_run": None,  # 1 cm resolution: a still target repeats legitimately
        "check_jumps": True,
        "jump_floor": 5.0,   # cm; steps smaller than this are within sensor accuracy
        "note": "1 cm resolution, about ±6 cm accuracy below 3 m. Far too coarse "
                "to measure motor vibration. Meaning depends on what it points at.",
    },
}

SENSOR_COLUMNS = list(SENSORS)

# Which readings describe the motor itself. Everything else describes the
# surroundings and is never treated as motor-fault evidence on its own.
MOTOR_SENSORS = ["vibration_ms2", "temperature_c", "current_a"]
for _name, _spec in SENSORS.items():
    _spec["category"] = "motor" if _name in MOTOR_SENSORS else "environment"
META_COLUMNS = ["timestamp", "session_id", "data_source", "scenario"]

# Not measured by any firmware version, so not in the schema: gyroscope, RPM.
