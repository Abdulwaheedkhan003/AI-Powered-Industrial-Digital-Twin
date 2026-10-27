# NEXUS Industrial Intelligence

An industrial digital-twin and predictive-maintenance platform: a navigable 3D
factory, one physical ESP32-monitored motor, a simulated fleet with repeatable
fault scenarios, evidence-based diagnosis, exploded component views, and a
maintenance workflow.

## Presentation mode (current default)

The Command Centre (`/app/command`) is one page, top to bottom: plant floor in 3D, one card per
machine, the selected machine in 3D with live readings, then trend and events.

- **No data-source labels on screen.** REAL / SIMULATED / REPLAY chips, the ESP32 status chip and
  the status bar are hidden.
- **The drive motor always has readings.** While the ESP32 sends data, those values are used.
  While it is silent, the backend generates plausible values for a small geared DC motor
  (about 118 rpm, 0.31 A, 33 °C). In the database these rows are stored with mode `GENERATED`,
  never `REAL`, so they cannot be mistaken for measurements later.
  Turn this off with `NEXUS_DEMO_FILL=0` in `nexus/.env`.
- **Faults happen by themselves.** About 20 s after start, and 40 to 90 s after each repair, one
  virtual machine picked at random develops a randomly chosen fault. It glows deep red on the
  floor, on its card and in its tag. Turn this off with `NEXUS_AUTO_FAULTS=0`.
- **Fault autopsy.** Click the faulted machine (or the red banner). Five steps: scan the signals
  against the baseline, open the machine, locate the suspected part, trace cause and effect,
  recommended action. "Mark as repaired" clears the fault. The location and cause come from the
  transparent rules in `backend/diagnostics.py` applied to the readings; the cause-and-effect text
  is textbook engineering knowledge, not a measurement.

- **No sign-in.** Requests from this computer get full access (`NEXUS_OPEN_LOCAL=1`, default).
  The server only listens on this computer (127.0.0.1); requests from other machines would still
  need a sign-in token. Set `NEXUS_OPEN_LOCAL=0` to bring sign-in back.

If you show this to anyone as a product, say that the machines other than the ESP32 motor are
virtual and that the motor's values are generated whenever the ESP32 is not connected.

## What is real and what is not

| | State |
|---|---|
| Backend, simulation engine, diagnosis, maintenance, security rules | Implemented; 27 automated tests pass |
| Web application (landing page + 11 app pages, 3D factory) | Implemented; builds; every page opened in a browser |
| Physical ESP32 link (telemetry in, commands out) | Implemented; tested with text lines and a fake port. **Never run against a real ESP32** |
| `firmware/nexus_motor_node` (commands, buzzer, RPM) | Written. **Not compiled, not flashed** |
| Simulated machines (fan, pump, compressor, gearbox, motor) | Invented data, labelled SIMULATED everywhere |
| Replay asset | Plays a recording; with no real recording yet it plays a synthetic one and says so |
| 3D models | Conceptual shapes, not manufacturer geometry |
| Diagnosis | Transparent rules. Not a trained or validated model |
| OPC UA, Modbus, PLC/SCADA, MQTT, CMMS, multi-site | Not implemented (roadmap) |
| TLS, per-user accounts, signed device commands | Not implemented (required before any network deployment) |

This is a proof of concept for demonstration. It is not a validated industrial
product and the ESP32 is not a safety controller.

## Run it

Double-click `run_nexus.bat`, then open <http://localhost:8000>.

Or from this folder:

```
cd backend
python -m uvicorn main:app --port 8000
```

On first start the backend creates `nexus/.env` with a random signing secret
and random passwords for the two accounts, `admin` and `operator`. Open that
file to read them. Viewing needs no account.

Requirements: Python 3.13 with `fastapi`, `uvicorn`, `pyserial`, `pandas`,
`numpy` (see `backend/requirements.txt`), and the sibling
`predictive_maintenance` folder, whose telemetry parser and sensor limits are
reused. Node.js is only needed to rebuild the web app:

```
cd frontend
npm install
npm run build        # output in frontend/dist, served by the backend
npm run dev          # optional: hot-reload dev server on http://localhost:5173
```

## Tests

```
cd backend
python -m pytest -q
```

Not tested, because hardware was unavailable: the real serial link, sensor
values and calibration, the firmware, the buzzer, and RPM-driven conveyor sync
on real data. Not automated: the 3D scene and the browser interface (checked
by hand).

## Environment variables (`nexus/.env`)

| Variable | Meaning |
|---|---|
| `NEXUS_SECRET` | Signs sign-in tokens |
| `NEXUS_ADMIN_PASSWORD`, `NEXUS_OPERATOR_PASSWORD` | Account passwords |
| `NEXUS_SERIAL_PORT` | Optional. Connect to this port at start-up, e.g. `COM5` |
| `NEXUS_SERIAL_BAUD` | Default 115200 |
| `NEXUS_DATA_DIR` | Optional. Where the database and replay file live (default `nexus/data`) |

`.env.example` shows the format. `.env` and `data/` are in `.gitignore`.

## Roles

| Role | Can do |
|---|---|
| viewer (no sign-in) | Read everything |
| operator | Run simulations, run and review diagnoses, acknowledge events, manage work orders |
| admin | Operator rights, plus connect the ESP32, send device commands, change settings |

Every action is checked by the backend. Disabled buttons are a convenience.

## Connecting the ESP32

1. Motor off. Plug in the board. Close the Arduino Serial Monitor.
2. Sign in as `admin`, open **Settings**, choose the COM port, press **Connect**.
3. **System Health** shows lines accepted and rejected; `MOTOR-001` turns LIVE.

What you see depends on the sketch on the board:

| Sketch | Telemetry | Raw vibration bursts | RPM / conveyor sync | Commands and buzzer |
|---|---|---|---|---|
| Your original sketch | yes | no | no | no (commands time out) |
| `predictive_maintenance/firmware/motor_monitor_v2` | yes | yes | no | no |
| `firmware/nexus_motor_node` (v3) | yes | yes | if `USE_HALL_RPM` | yes; buzzer if `USE_BUZZER` |

In the v3 sketch every optional feature is off and its pin is a placeholder.
Set the real pin, set the `USE_...` flag to 1, compile, flash.

Real samples are also appended to `predictive_maintenance/data/raw/nexus_<date>.csv`,
labelled `real_hardware`, so the Streamlit analysis tool can open them.

## Telemetry format (device to backend)

One text line per sample, fields separated by TAB:

```
Vibration:0.12	Temperature_C:30.25	Gas_ADC:612	Current_A:0.148	LDR_DO:1	RPM:1480	Seq:123
A	7	1000200	4096	-2048	12
ACK	CMD-12	OK	buzzer on
STATUS	device=ESP32-NODE-01	fw=3.0.0	buzzer=1	rpm=1
```

- A field is omitted when its sensor failed. Missing is never sent as 0.
- `A` lines are raw acceleration (burst number, microseconds, three axes in counts; 4096 = 1 g).
- The device has no clock. Each sample is stamped with the PC time on arrival.
- Values outside what a sensor can report are dropped and flagged, not displayed.

## Command format (backend to device, v3 firmware only)

```
CMD	<seq>	<command_id>	<NAME>	<argument>
```

| Command | Argument | Effect |
|---|---|---|
| `PING` | none | Device answers |
| `REQUEST_STATUS` | none | Device sends a `STATUS` line |
| `BUZZER_ALERT` | 50–2000 ms | Buzzer sounds, then switches itself off |

Nothing else exists: no motor control and no general pin access. The backend
rejects unknown names and out-of-range arguments before sending, refuses a
second buzzer within 3 seconds, and uses an always-increasing sequence number.
The firmware checks the same things again. A command becomes ACKNOWLEDGED only
when the device replies; otherwise it becomes TIMEOUT after 3 seconds, with a
note that it is unknown whether it ran.

## Asset modes

Assets are defined in `backend/assets.json`, not in interface code.

| Mode | Source | To add one |
|---|---|---|
| `REAL` | The ESP32 | One physical node is supported |
| `SIMULATED` | `backend/simulation.py` | Add an entry with a `type` that has a model (`cooling_fan`, `pump`, `compressor`, `gearbox`, `motor`), a `seed` and a `position` |
| `REPLAY` | A recorded CSV | Uses the newest file in `predictive_maintenance/data/raw`; with none, a synthetic recording in `data/replay` |

A new machine type needs a model in `simulation.py` and a part list in
`frontend/src/three/models.ts`.

## Demonstration walk-through

1. **Digital Twin**: one asset is tagged REAL, five SIM, one REPLAY.
2. Click `MOTOR-001`: live readings if the ESP32 is connected, otherwise OFFLINE with no values.
3. **Fault Simulator** (sign in as operator): choose `FAN-201`, *Bearing degradation*, **Start scenario**. Vibration, current and temperature rise and speed falls. After about 20 seconds the fan shows a SIMULATED WARNING.
4. Optional buzzer: as admin, with the ESP32 connected and v3 firmware, enable *Sound the physical buzzer on simulated faults*. The first alert of the scenario sends one 2-second `BUZZER_ALERT`.
5. **AI Diagnostics**: **Run diagnosis now**. The result lists the evidence, ranks *bearing wear* first, names alternatives, and gives checks.
6. Click the suspected component: the exploded view opens with the bearing marked SUSPECTED.
7. **Create work order**, move it through the states in **Maintenance**. Marking it VERIFIED captures "after" readings.
8. **Reset to baseline** in the simulator. The scenario can be run again and gives the same numbers.

## Verifying the 2-second buzzer

1. Flash `nexus_motor_node` with `USE_BUZZER 1` and the real `BUZZER_PIN`.
2. Connect in Settings. **Request status** should report firmware 3.0.0.
3. **Test buzzer · 2 s**. The buzzer should sound for two seconds and the command should show ACKNOWLEDGED.
4. With the original sketch the same button ends in TIMEOUT. That is the correct, honest result.

## Layout

```
backend/
  main.py          API, WebSocket stream, serves the web app
  engine.py        authoritative asset state, anomalies, events, commands, work orders
  simulation.py    machine models and fault scenarios
  diagnostics.py   rules-based diagnosis
  device.py        serial link, telemetry validation, command allowlist
  security.py      sign-in, roles, rate limiting
  store.py         SQLite storage
  assets.json      site, assets, component library
  tests/           27 tests
frontend/src/
  lib/store.ts     live state mirror and API client
  three/           factory scene, machine renderer, part lists
  components/      shell, charts, shared UI
  pages/           landing page and application pages
firmware/nexus_motor_node/   ESP32 sketch v3 (uncompiled)
```

## Security and deployment limits

- Tokens are HMAC-signed with a local secret; passwords are compared in constant time but stored in plain text in `.env`.
- No TLS. Run on `localhost` only. Do not expose port 8000 to a network.
- Commands travel unencrypted and unsigned over USB, which is acceptable for a cable and not for a network.
- The event history is ordinary database rows, not a tamper-proof audit trail.
- No backups are configured for `data/nexus.sqlite3`.
- No language model is used and no data leaves the machine.

## Roadmap to a customer deployment

1. **Bench validation**: compile and flash v3, calibrate each sensor against a reference, record healthy runs and deliberately altered runs.
2. **Measure detection**: false-alarm rate on healthy data, detection on the altered runs. Until then there is no performance figure to quote.
3. **Industrial hardware**: rated sensors, a gateway, and a standard protocol (OPC UA or Modbus) in place of USB serial.
4. **Hardening**: TLS, real user accounts and identity provider, signed device commands, backups, security review.
5. **Pilot on one line** with limits agreed with the maintenance team.
6. **Plant integration**: maintenance-system connector, multiple sites.
7. **Models**: replace rules with trained models only where labelled plant data shows they do better.
