# Hardware verification checklist

Everything below is still open. Tick items off on the real rig before trusting
any reading, alert or score.

## 1. Electrical safety first

- [ ] Measure the ACS712 output voltage with a multimeter, motor off and motor stalled.
      If it can exceed 3.3 V there must be a voltage divider before GPIO34.
- [ ] If a divider is fitted, change `sensitivity` in the sketch to 0.185 × divider ratio.
- [ ] Same check for the MQ-5 analog output on GPIO36.

## 2. Original sketch with the dashboard

- [ ] Motor off, Serial Monitor closed, connect from the dashboard.
- [ ] Lines accepted counts up; lines rejected stays near zero after boot.
- [ ] Every sensor shows "Reporting" on the Overview page.
- [ ] Note the measured sample rate on the Overview page (expected roughly 1 Hz).
- [ ] Unplug the DS18B20: its status changes to "Invalid readings". Reconnect.
- [ ] Pull the USB cable: the sidebar shows the lost connection; plug in again and it resumes.

## 3. Firmware v2

- [ ] Compile `firmware/motor_monitor_v2/motor_monitor_v2.ino` in the Arduino IDE; fix any errors.
- [ ] Set the real TF-Luna RX/TX pins and `USE_LIDAR 1`; confirm the TF-Luna is in UART mode.
- [ ] Set the real LDR analog pin (ADC1 only) and `USE_LDR_ADC 1`.
- [ ] Vibration analysis page: sample rate reads about 500 Hz and no "unevenly spaced" warning.
- [ ] Slow lines arrive every 2 s without gaps (Data quality page).

## 4. Calibration

- [ ] Current: compare with a multimeter in series at three or more loads; record offset and scale.
- [ ] Temperature: compare with a reference thermometer at room temperature.
- [ ] Accelerometer: at rest each axis reads −1, 0 or +1 g within a few percent when turned.
- [ ] MQ-5: record how long the reading takes to settle after power-on (often 20 minutes or more).
- [ ] LDR: note whether brighter gives a higher or lower count.
- [ ] TF-Luna: check distance against a ruler at 30 cm, 1 m and 2 m.

## 5. Baseline and thresholds

- [ ] Record several healthy sessions from cold start, with operating conditions written down.
- [ ] Choose one as the statistical reference session (Settings).
- [ ] Set thresholds from what you observe; none are built in.
- [ ] Train a model on the healthy real sessions. Synthetic-trained models cannot score real data.

## 6. Detection performance

- [ ] Create controlled, safe, documented changes (added load, loosened mount, blocked airflow)
      and record them as separate sessions with a `label_anomaly` column.
- [ ] Evaluate on those sessions only (Model evaluation page). Until then there is no
      evidence the system detects real faults.
