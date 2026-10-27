# Motor condition monitoring and anomaly detection

Local Windows software for an ESP32-based multi-sensor rig on a small DC geared
motor. It records sensor data, checks data quality, analyses vibration and
trends, raises explainable alerts, trains and applies an anomaly model, and
produces session reports. No cloud, database server, GPU or paid service.

## What this is, and is not

**Software: complete and tested.** Every feature listed below is implemented
and covered by 128 automated tests.

**Validation on hardware: not done.** The software was developed without access
to the rig. Nothing has been run against a real ESP32, no sensor is
calibrated, the firmware update has not been compiled, and detection
performance on real faults has not been measured. This is a working prototype
that is ready for hardware trials. It is not a validated industrial product,
and it must not be relied on for safety or for maintenance decisions until
the checklist in [HARDWARE_CHECKLIST.md](HARDWARE_CHECKLIST.md) has been worked through.

It does not predict a failure date or remaining useful life, and it does not
name the failed component.

## Start

Double-click `run_dashboard.bat`, or:

```
python -m pip install -r requirements.txt
python -m streamlit run app.py
python -m pytest -q
```

## Using it

| Data source (sidebar) | What it is |
|---|---|
| ESP32 (serial) | Live data from the board. Connecting starts a session; every line is shown and written to `data/raw/` as `real_hardware`. Disconnecting ends it. |
| Saved session | Any earlier recording, real or demo. |
| Synthetic demo | Generated data for trying the software. 11 scenarios. Always labelled synthetic. |
| Import CSV | A file recorded elsewhere. Unlabelled files are marked `unknown`, never assumed real. |

Connecting the ESP32: motor off, close the Arduino Serial Monitor, choose
*ESP32 (serial)*, pick the COM port, press *Connect*.

| Page | Contents |
|---|---|
| Overview | Source, session, sensor status, operating-condition notes |
| Live / demo monitoring | Connection, latest readings, active alerts, latest AI score |
| Sensor charts | Time-range filter, alert markers, trends, sensor correlation |
| Data quality | Missing, impossible, stuck and jumping readings; sampling gaps; statistics |
| Vibration analysis | RMS, peak, crest factor per burst; FFT spectrum |
| Anomaly detection | Thresholds, statistical baseline and Isolation Forest shown separately; drift; model training |
| Alert history | Filter, acknowledge, annotate, resolve |
| Session comparison | Two sessions side by side with comparability warnings |
| Model evaluation | Metrics when labels exist; honest explanation when they do not |
| Reports and exports | CSV exports, HTML report (print to PDF), maintenance notes |
| Settings | Thresholds, persistence, statistical baseline, combined rules |

## Which firmware gives which data

| Sketch on the ESP32 | Temperature, gas, current, LDR on/off, vibration number | Raw acceleration (RMS, FFT) | TF-Luna distance, analog light |
|---|---|---|---|
| Original sketch | yes | no | no |
| `firmware/motor_monitor_v2` | yes | yes | after setting the pins and `USE_...` flags |

`motor_monitor_v2.ino` has **not been compiled or flashed**. The TF-Luna and
analog-LDR pin numbers in it are placeholders.

## Data format

Slow sensors, one row per reading (`data/raw/<session>.csv`):

| Column | Unit | Notes |
|---|---|---|
| timestamp | PC clock | Stamped when the line arrives; the firmware sends no clock time |
| session_id, data_source, scenario | text | `data_source` is `real_hardware`, `synthetic_demo` or `unknown`; one per file |
| vibration_ms2 | m/s² | `abs(‖a‖ − 1 g)` of one sample; not an RMS |
| temperature_c | °C | DS18B20; 0.00 / 85.00 / −127 are treated as failure values |
| gas_adc | ADC counts | MQ-5, uncalibrated; not ppm |
| current_a | A | ACS712-5A, absolute value, uncalibrated |
| ldr_do | 0 / 1 | Comparator output of the LDR module |
| ldr_adc | ADC counts | Firmware v2; relative light level |
| lidar_distance_cm | cm | Firmware v2; TF-Luna, 20–800 cm, 1 cm steps |
| label_anomaly | true / false | Synthetic scenarios only: injected change active |

Acceleration bursts, one row per sample (`<session>_accel.csv`): `burst_id,
burst_time, t_us, accel_x_g, accel_y_g, accel_z_g, session_id, data_source,
scenario`. `t_us` is the ESP32's microsecond clock, used to measure the true
sample rate. Slow readings are never copied onto these rows.

Missing readings are empty cells, never zero. Synthetic files are named
`SYNTHETIC_*.csv` in `data/demo`; the logger refuses to mix sources.

Gyroscope and RPM are not measured by either sketch and are not in the format.

## How the detectors work

| Detector | Rule | Good at | Weak at |
|---|---|---|---|
| Thresholds | Your own warning / critical limits, held for N readings | Clear, explainable limits | Needs limits you have chosen; none are built in |
| Statistical baseline | More than 4 robust spreads from a baseline | Any single signal moving away | Flags normal warm-up unless a reference session is chosen |
| Isolation Forest | Window features unlike the training windows | Several features changing together | One signal drifting beyond its trained range; unseen operating conditions |

Gas, light and distance are tagged as environmental. They are excluded from the
motor model and their alerts say they are not motor-fault evidence.

### Synthetic pipeline check (software test, not performance evidence)

Trained on 12 synthetic baseline sessions, tested on separately generated
scenario sessions, per 60-second window. Recall = share of affected windows
with an active alert; false alarms = share of unaffected windows with one.

| Scenario (synthetic) | Thresholds: recall / false alarms | Statistical: recall / false alarms | Isolation Forest: recall / false alarms |
|---|---|---|---|
| Stable baseline | – / 0 % | – / 27 % | – / 0 % |
| Rising temperature | 100 % / 0 % | 100 % / 80 % | 0 % / 0 % |
| Increasing vibration | 100 % / 12 % | 100 % / 62 % | 86 % / 0 % |
| Unusual current | 100 % / 0 % | 100 % / 40 % | 0 % / 0 % |
| Gas increase (environment) | not configured | 100 % / 40 % | 0 % (excluded by design) |
| LiDAR change (environment) | not configured | 100 % / 62 % | 0 % (excluded by design) |
| Light change (environment) | not configured | 100 % / 62 % | 0 % (excluded by design) |

On this invented data the simple thresholds did best, the statistical detector
found everything but with many false alarms, and the Isolation Forest only
found the vibration change. Nothing here says how any of them will do on the
real motor. Reproduce it on the *Model evaluation* page.

## Project layout

```
app.py                     dashboard
config.py                  paths, sensor schema, firmware field names
run_dashboard.bat          Windows launcher
firmware/motor_monitor_v2  proposed ESP32 sketch (uncompiled)
src/ingestion              serial reader, line parser, CSV logger/reader, demo generator
src/processing             validation, statistics, trends, vibration features, FFT, feature pipeline
src/detection              thresholds, statistical detector, Isolation Forest, drift, evaluation
src/monitoring             alert logic + history, detector engine, sessions, sensor status, notes
src/reporting              HTML summary report
tests                      128 automated tests (synthetic inputs only)
data/raw, data/demo        recordings (real / synthetic), created on first use
data/state                 settings, alert history, notes, session notes
models                     trained models with a JSON record each
```

## Known limitations

- No sensor is calibrated; all values are indicative.
- ACS712-5A noise is comparable to a small motor's current; current may be a weak signal.
- Vibration is visible up to 250 Hz at most (500 Hz sampling) and depends on mounting.
- Speed and load are not measured, so sessions can only be compared using your notes.
- The MPU6050 gyroscope is not used.
- All tests use synthetic inputs. Passing tests says nothing about the hardware.
