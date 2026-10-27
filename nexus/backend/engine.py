"""The backend's authoritative state: assets, modes, anomalies, events, commands.

One `tick()` per second:
  1. advance every simulated machine,
  2. take whatever the ESP32 sent and update the physical asset,
  3. advance the replay asset by one recorded sample,
  4. settle device commands (acknowledged / failed / timed out),
  5. evaluate limits and raise or clear anomalies.

Real, simulated and replayed data live in separate per-asset states and are
stored with their mode. Nothing in the simulation path can write to the
physical asset, and fault scenarios are refused on anything not SIMULATED.
"""
import csv
import json
import math
import random
import statistics
import time
from collections import deque
from pathlib import Path

import diagnostics
from device import COMMANDS, DeviceLink, validate_command
from simulation import SCENARIOS, SimulatedAsset, channel_definitions

ASSETS_FILE = Path(__file__).with_name("assets.json")
RAISE_AFTER, CLEAR_AFTER = 3, 5
BASELINE_SAMPLES = 60
BUZZER_MS = 2000
RPM_SMOOTHING = 0.3
LEVELS = ["NORMAL", "WARNING", "CRITICAL"]
FAULT_ONSET_S = 20            # an automatic fault grows to full size over this long
FAULT_HOLD_S = 300            # ... and clears by itself if nobody repairs it

REAL_CHANNELS = [
    {"key": "temperature_c", "label": "Temperature (DS18B20)", "unit": "°C", "kind": "temperature"},
    {"key": "vibration_ms2", "label": "Vibration (one-sample acceleration deviation)", "unit": "m/s²", "kind": "vibration"},
    {"key": "vib_rms_g", "label": "Vibration RMS (acceleration burst)", "unit": "g", "kind": "vibration"},
    {"key": "current_a", "label": "Current (ACS712)", "unit": "A", "kind": "current"},
    {"key": "rpm", "label": "Speed (Hall sensor)", "unit": "rpm", "kind": "rpm"},
    {"key": "gas_adc", "label": "Gas sensor (MQ-5, raw)", "unit": "ADC counts", "kind": "environment"},
    {"key": "ldr_do", "label": "Light (on/off)", "unit": "0 / 1", "kind": "environment"},
    {"key": "ldr_adc", "label": "Light level (raw)", "unit": "ADC counts", "kind": "environment"},
    {"key": "lidar_distance_cm", "label": "Distance (TF-Luna)", "unit": "cm", "kind": "environment"},
]
WORK_ORDER_FLOW = {
    "NEW": ["REVIEW REQUIRED", "INSPECTION PLANNED", "CLOSED"],
    "REVIEW REQUIRED": ["INSPECTION PLANNED", "CLOSED"],
    "INSPECTION PLANNED": ["IN PROGRESS", "CLOSED"],
    "IN PROGRESS": ["COMPLETED"],
    "COMPLETED": ["VERIFIED", "IN PROGRESS"],
    "VERIFIED": ["CLOSED"],
    "CLOSED": [],
}


class EngineError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


class Engine:
    def __init__(self, settings, store, link=None, raw_export_dir=None, replay_file=None):
        cfg = json.loads(ASSETS_FILE.read_text(encoding="utf-8"))
        self.settings, self.store = settings, store
        self.site = cfg["site"]
        self.assets = {a["asset_id"]: a for a in cfg["assets"]}
        self.components = {k: v for k, v in cfg["component_library"].items() if not k.startswith("_")}
        self.component_note = cfg["component_library"]["_note"]
        self.link = link or DeviceLink()
        self.raw_export_dir = raw_export_dir
        self.errors = deque(maxlen=50)
        self.started = time.time()
        self.tick_count, self.last_tick = 0, None
        self.cmd_seq = int(time.time())          # always increasing, even across restarts
        self.pending = {}
        self.last_buzzer = 0.0
        self.notified_runs = set()
        self.fill = getattr(settings, "demo_fill", False)
        self.auto = getattr(settings, "auto_faults", False)
        self.rng = random.Random()
        self.next_fault_ts = time.time() + 20
        self.last_device_ts, self.gen_t, self.generated = None, 0, False
        self.sims, self.state = {}, {}
        for asset_id, a in self.assets.items():
            if a["data_mode"] == "SIMULATED":
                self.sims[asset_id] = SimulatedAsset(asset_id, a["type"], a.get("seed", 1))
            self.state[asset_id] = {"values": {}, "last_seen": None, "quality": [], "sensor_status": {},
                                    "anomaly": "NORMAL", "faults": {}, "counters": {}, "freshness": None,
                                    "baseline": {}, "baseline_buffer": {}, "ever_seen": False}
        self.real_id = next(i for i, a in self.assets.items() if a["data_mode"] == "REAL" and a["type"] != "conveyor")
        self.replay_id = next((i for i, a in self.assets.items() if a["data_mode"] == "REPLAY"), None)
        self.replay_rows, self.replay_pos, self.replay_source = [], 0, None
        if self.replay_id and replay_file:
            self.load_replay(replay_file)
        self.conveyor = {"smoothed_rpm": None, "last_valid_rpm": None, "last_valid_ts": None}
        conveyor = next((a for a in self.assets.values() if a["type"] == "conveyor"), {})
        # Conceptual drive of the virtual conveyor. Belt speed = drum surface speed:
        #   belt (m/s) = measured RPM / reduction x 2 x pi x drum radius / 60
        self.drive = {"drum_radius_m": 0.10, "reduction": 1.0, **conveyor.get("drive", {})}
        self.belt_m_per_rev = 2 * math.pi * self.drive["drum_radius_m"] / self.drive["reduction"]

    # ---- definitions ----------------------------------------------------------
    def channels(self, asset_id):
        a = self.assets[asset_id]
        if a["type"] == "conveyor":
            return []
        if a["data_mode"] == "SIMULATED":
            return channel_definitions(a["type"])
        limits = self.store.get_setting(f"limits:{asset_id}", {}) if a["data_mode"] == "REAL" else {}
        out = []
        for ch in REAL_CHANNELS:
            lim = limits.get(ch["key"], {})
            out.append({**ch, "nominal": None, "warn": lim.get("warn"), "crit": lim.get("crit"),
                        "direction": lim.get("direction", "high"),
                        "limit_source": lim.get("source") if lim.get("warn") is not None else None,
                        "calibration": "NOT VERIFIED"})
        return out

    def describe(self, asset_id):
        a = self.assets[asset_id]
        return {**a, "channels": self.channels(asset_id),
                "scenarios": [{"scenario_id": k, **v} for k, v in SCENARIOS.items() if a["type"] in v["asset_types"]]
                if a["data_mode"] == "SIMULATED" else [],
                "replay_source": self.replay_source if asset_id == self.replay_id else None}

    # ---- events ---------------------------------------------------------------
    def event(self, asset_id, category, severity, origin, title, detail="", scenario_id=None, ref=None):
        mode = self.assets[asset_id]["data_mode"] if asset_id in self.assets else "SYSTEM"
        return self.store.add_event(asset_id, category, severity, mode, origin, title, detail, scenario_id, ref)

    # ---- tick -----------------------------------------------------------------
    def tick(self, now=None):
        now = now or time.time()
        try:
            for asset_id, sim in self.sims.items():
                self._update(asset_id, sim.step(1.0), now, "SIMULATED", [])
            samples, acks = self.link.drain()
            for s in samples:
                quality = [f"{name}:invalid" for name in s["invalid"]] + (["line:incomplete"] if s["problems"] else [])
                values = {k: v for k, v in s["values"].items() if v is not None}
                if self.generated:                 # real data takes over from generated data: new baseline
                    self._new_baseline(self.real_id)
                self.generated, self.last_device_ts = False, s["ts"]
                self._update(self.real_id, values, s["ts"], "REAL", quality, all_keys=s["values"].keys())
                self._export_real(s)
            if self.fill and not self._device_live(now):
                if not self.generated:
                    self._new_baseline(self.real_id)
                self.generated = True
                values = self._generated_motor()
                self._update(self.real_id, values, now, "REAL", [], all_keys=values.keys(), stored_as="GENERATED")
            self._auto_faults(now)
            self._refresh_real(now)
            self._step_replay(now)
            self._settle_commands(acks, now)
            if self.tick_count % 600 == 0:
                self.store.prune_telemetry(now - 6 * 3600)
        except Exception as err:                       # keep the loop alive and visible in System Health
            self.errors.append({"ts": now, "where": "engine.tick", "error": repr(err)})
        self.tick_count += 1
        self.last_tick = now

    def _device_live(self, now):
        return (self.last_device_ts is not None and self.link.status != "DISCONNECTED"
                and now - self.last_device_ts <= self.settings.stale_after_s)

    def _new_baseline(self, asset_id):
        self.state[asset_id].update(baseline={}, baseline_buffer={}, counters={}, faults={}, anomaly="NORMAL")

    def _generated_motor(self):
        """Plausible readings for a small geared DC motor, used only while the ESP32 sends nothing.
        They are stored with mode GENERATED so they can never be mistaken for measurements later."""
        self.gen_t += 1
        r, wave = self.rng, math.sin(self.gen_t / 47.0)
        return {"temperature_c": round(33.5 + 0.8 * math.sin(self.gen_t / 180.0) + r.gauss(0, 0.05), 2),
                "vibration_ms2": round(abs(0.42 + 0.03 * wave + r.gauss(0, 0.012)), 3),
                "vib_rms_g": round(abs(0.045 + 0.003 * wave + r.gauss(0, 0.0015)), 4),
                "current_a": round(0.31 + 0.012 * wave + r.gauss(0, 0.004), 3),
                "rpm": round(118 + 1.5 * wave + r.gauss(0, 0.4), 1),
                "gas_adc": int(402 + r.gauss(0, 5)), "ldr_do": 1,
                "ldr_adc": int(1850 + 30 * wave + r.gauss(0, 10)),
                "lidar_distance_cm": round(42 + r.gauss(0, 0.3), 1)}

    def _auto_faults(self, now):
        """One virtual machine at a time develops a randomly chosen fault and keeps it until repaired."""
        if not self.auto:
            return
        active = [(i, s) for i, s in self.sims.items() if s.scenario]
        if active:
            for asset_id, sim in active:
                if sim.scenario.get("auto") and sim.scenario["elapsed_s"] > FAULT_HOLD_S:
                    self.repair(asset_id, "automatic recovery")
            return
        ready = [i for i, s in self.sims.items() if s.steady]
        if now < self.next_fault_ts or not ready:
            return
        asset_id = self.rng.choice(ready)
        sim = self.sims[asset_id]
        options = [k for k, v in SCENARIOS.items() if sim.model_key in v["asset_types"]]
        scenario = sim.start(self.rng.choice(options), round(self.rng.uniform(0.88, 1.0), 2))
        scenario.update(auto=True, duration_s=FAULT_ONSET_S, started_ts=now, detected_ts=None)
        self.state[asset_id].update(counters={}, faults={}, anomaly="NORMAL")

    def repair(self, asset_id, user="operator"):
        """Clear the fault on a virtual machine and return it to its baseline."""
        sim = self._sim(asset_id)
        run = sim.scenario["run_id"] if sim.scenario else None
        sim.reset()
        self.state[asset_id].update(counters={}, faults={}, anomaly="NORMAL")
        self.event(asset_id, "MAINTENANCE", "INFO", "OPERATOR",
                   f"{self.assets[asset_id]['name']} repaired and returned to service", f"by {user}", run)
        self.next_fault_ts = time.time() + self.rng.uniform(40, 90)
        return {"asset_id": asset_id, "repaired": True}

    def autopsy(self, asset_id):
        """Everything the fault autopsy shows: evidence, ranked causes, the cause-and-effect chain,
        the suspected component and the readings since the fault began. Read-only."""
        d = self.preview_diagnosis(asset_id)
        st, sim = self.state[asset_id], self.sims.get(asset_id)
        scenario = sim.scenario if sim else None
        now = time.time()
        onset = (scenario or {}).get("started_ts") or min([f["since"] for f in st["faults"].values()] or [now - 60])
        rows = self.store.history(asset_id, onset - 30)
        keys = [e["channel"] for e in d.get("evidence", [])]
        top = d["hypotheses"][0] if d.get("hypotheses") else None
        return {**d, "onset_ts": onset, "detected_ts": (scenario or {}).get("detected_ts"),
                "active_faults": list(st["faults"].values()), "anomaly_state": st["anomaly"],
                "series": {k: [[r["ts"], r["vals"][k]] for r in rows if r["vals"].get(k) is not None] for k in keys},
                "mechanism": diagnostics.MECHANISMS.get(top["id"]) if top else None,
                "component": ({"id": top["component_id"], **self.components.get(top["component_id"], {})}
                              if top and top.get("component_id") else None)}

    def _update(self, asset_id, values, ts, mode, quality, all_keys=None, stored_as=None):
        st = self.state[asset_id]
        st["values"] = {**st["values"], **values} if mode == "REAL" else dict(values)
        st["last_seen"], st["quality"], st["ever_seen"] = ts, quality, True
        if mode == "REAL":
            for key in (all_keys or []):
                st["sensor_status"][key] = "OK" if key in values else "INVALID"
                if key not in values:              # never keep showing an old value for a failed sensor
                    st["values"].pop(key, None)
            for key, value in values.items():      # session baseline from the first valid samples
                buf = st["baseline_buffer"].setdefault(key, [])
                if len(buf) < BASELINE_SAMPLES:
                    buf.append(value)
                    if len(buf) == BASELINE_SAMPLES:
                        med = statistics.median(buf)
                        st["baseline"][key] = {"median": med, "spread": statistics.pstdev(buf),
                                               "source": f"first {BASELINE_SAMPLES} valid readings of this connection"}
        self.store.add_telemetry(asset_id, ts, stored_as or mode, values, quality)
        self._evaluate(asset_id, values, ts)

    def _evaluate(self, asset_id, values, ts):
        st, a = self.state[asset_id], self.assets[asset_id]
        sim = self.sims.get(asset_id)
        if sim and not sim.steady:
            # a machine that is stopped or still changing speed is not judged against running limits
            st["counters"], st["faults"] = {}, {}
            if st["anomaly"] != "NORMAL":
                st["anomaly"] = "NORMAL"
                self.event(asset_id, "RETURN_TO_NORMAL", "INFO", "SYSTEM",
                           f"Alerts on {a['name']} cleared: machine not at operating speed")
            return
        for ch in self.channels(asset_id):
            key = ch["key"]
            if key not in values:
                continue
            value, level, basis = values[key], 0, None
            if ch.get("warn") is not None:
                high = ch["direction"] == "high"
                if ch.get("crit") is not None and (value > ch["crit"] if high else value < ch["crit"]):
                    level, basis = 2, f"beyond critical limit {ch['crit']} {ch['unit']}"
                elif value > ch["warn"] if high else value < ch["warn"]:
                    level, basis = 1, f"beyond warning limit {ch['warn']} {ch['unit']}"
            elif a["data_mode"] in ("REAL", "REPLAY") and key in st["baseline"] and ch["kind"] in diagnostics.TOLERANCE_PCT:
                base = st["baseline"][key]
                if base["median"] and base["spread"]:
                    pct = abs(value - base["median"]) / abs(base["median"]) * 100
                    z = abs(value - base["median"]) / base["spread"]
                    if z > 4 and pct > diagnostics.TOLERANCE_PCT[ch["kind"]]:
                        level, basis = 1, f"more than 4 spreads from the session baseline {base['median']:.4g} {ch['unit']}"
            c = st["counters"].setdefault(key, {"bad": 0, "crit": 0, "ok": 0})
            if level:
                c["bad"] += 1
                c["crit"] = c["crit"] + 1 if level == 2 else 0
                c["ok"] = 0
                if c["bad"] >= RAISE_AFTER:
                    fault = st["faults"].setdefault(key, {"channel": key, "label": ch["label"], "since": ts, "level": "WARNING"})
                    fault.update(value=value, basis=basis)
                    if c["crit"] >= RAISE_AFTER:
                        fault["level"] = "CRITICAL"
            else:
                c["bad"] = c["crit"] = 0
                c["ok"] += 1
                if c["ok"] >= CLEAR_AFTER:
                    st["faults"].pop(key, None)
        new = "CRITICAL" if any(f["level"] == "CRITICAL" for f in st["faults"].values()) else \
            "WARNING" if st["faults"] else "NORMAL"
        if new != st["anomaly"]:
            self._anomaly_changed(asset_id, st["anomaly"], new)
            st["anomaly"] = new

    def _anomaly_changed(self, asset_id, old, new):
        st, a = self.state[asset_id], self.assets[asset_id]
        sim = self.sims.get(asset_id)
        scenario = sim.scenario if sim else None
        if new == "NORMAL":
            self.event(asset_id, "RETURN_TO_NORMAL", "INFO", "SYSTEM", f"{a['name']} returned to normal",
                       scenario_id=scenario["run_id"] if scenario else None)
            return
        detail = "; ".join(f"{f['label']} = {f['value']} ({f['basis']})" for f in st["faults"].values())
        if scenario and scenario.get("auto") and not scenario.get("detected_ts"):
            scenario["detected_ts"] = time.time()
        if a["data_mode"] == "SIMULATED":
            category, origin, title = "SIMULATED_FAULT", "SIMULATION", f"SIMULATED {new.lower()} on {a['name']}"
        else:
            category, origin, title = "SENSOR_ANOMALY", "SYSTEM", f"{new.title()} on {a['name']}"
        event_id = self.event(asset_id, category, new, origin, title, detail,
                              scenario_id=scenario["run_id"] if scenario else None)
        if scenario and LEVELS.index(new) > LEVELS.index(old) and scenario["run_id"] not in self.notified_runs:
            notify = self.store.get_setting("demo_notifications", {"enabled": False})
            if notify.get("enabled"):
                self.notified_runs.add(scenario["run_id"])
                self.issue_command(f"system (simulated-fault notification, enabled by {notify.get('by', '?')})",
                                   "BUZZER_ALERT", BUZZER_MS, event_ref=f"EVT-{event_id}",
                                   note=f"Signals a SIMULATED fault on {asset_id}; no physical {a['type']} exists.")

    def _refresh_real(self, now):
        st = self.state[self.real_id]
        if self.generated:
            new = "LIVE"
        elif not st["ever_seen"]:
            new = "OFFLINE"
        else:
            age = now - st["last_seen"]
            new = "OFFLINE" if age > self.settings.offline_after_s or self.link.status == "DISCONNECTED" else \
                "STALE" if age > self.settings.stale_after_s else "LIVE"
        if new != st["freshness"]:
            if (st["freshness"] is not None or new == "LIVE") and not self.generated:
                titles = {"LIVE": ("Physical device is sending data", "INFO"),
                          "STALE": ("Telemetry from the physical device is stale", "WARNING"),
                          "OFFLINE": ("Physical device is offline", "WARNING")}
                self.event(self.real_id, "DEVICE_CONNECTION", titles[new][1], "SYSTEM", titles[new][0], self.link.message)
            if new == "OFFLINE":                 # a new connection gets a new baseline
                st["baseline"], st["baseline_buffer"], st["counters"], st["faults"] = {}, {}, {}, {}
                st["anomaly"] = "NORMAL"
            st["freshness"] = new
        rpm = st["values"].get("rpm") if new == "LIVE" and st["sensor_status"].get("rpm") == "OK" else None
        c = self.conveyor
        if rpm is not None:
            c["smoothed_rpm"] = rpm if c["smoothed_rpm"] is None else c["smoothed_rpm"] + RPM_SMOOTHING * (rpm - c["smoothed_rpm"])
            c["last_valid_rpm"], c["last_valid_ts"] = rpm, st["last_seen"]
        else:
            c["smoothed_rpm"] = None

    def _export_real(self, sample):
        """Also append real samples to the existing analysis project's data/raw folder."""
        if not self.raw_export_dir:
            return
        columns = ["timestamp", "session_id", "data_source", "scenario", "vibration_ms2", "temperature_c", "gas_adc",
                   "current_a", "ldr_do", "ldr_adc", "lidar_distance_cm"]
        day = time.strftime("%Y%m%d", time.localtime(sample["ts"]))
        path = Path(self.raw_export_dir) / f"nexus_{day}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sample["ts"])) + f".{int(sample['ts'] % 1 * 1000):03d}"
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if new:
                writer.writerow(columns)
            v = sample["values"]
            writer.writerow([stamp, f"nexus_{day}", "real_hardware", ""] +
                            ["" if v.get(c) is None else v[c] for c in columns[4:]])

    # ---- replay ---------------------------------------------------------------
    def load_replay(self, path):
        path = Path(path)
        with path.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            raise EngineError(400, "The replay file has no rows.")
        keys = [c["key"] for c in REAL_CHANNELS]
        self.replay_rows = [{k: float(r[k]) for k in keys if r.get(k) not in (None, "")} for r in rows]
        origin = rows[0].get("data_source", "unknown")
        self.replay_source = {"file": path.name, "rows": len(rows), "origin": origin,
                              "note": "Recorded from the physical motor." if origin == "real_hardware"
                              else "This recording was itself generated by software; it is not a measurement."}
        self.replay_pos = 0

    def _step_replay(self, now):
        if not self.replay_id or not self.replay_rows:
            return
        if self.replay_pos >= len(self.replay_rows):
            self.replay_pos = 0
            self.state[self.replay_id].update(baseline={}, baseline_buffer={}, counters={}, faults={})
            self.event(self.replay_id, "REPLAY", "INFO", "SYSTEM", "Replay restarted from the beginning")
        values = self.replay_rows[self.replay_pos]
        self.replay_pos += 1
        st = self.state[self.replay_id]
        for key, value in values.items():
            buf = st["baseline_buffer"].setdefault(key, [])
            if len(buf) < BASELINE_SAMPLES:
                buf.append(value)
                if len(buf) == BASELINE_SAMPLES:
                    st["baseline"][key] = {"median": statistics.median(buf), "spread": statistics.pstdev(buf),
                                           "source": f"first {BASELINE_SAMPLES} samples of the recording"}
        self._update(self.replay_id, values, now, "REPLAY", [])

    # ---- snapshot -------------------------------------------------------------
    def freshness(self, asset_id):
        mode = self.assets[asset_id]["data_mode"]
        if self.assets[asset_id]["type"] == "conveyor":
            return "DERIVED"
        if mode == "REAL":
            return self.state[asset_id]["freshness"] or "OFFLINE"
        if mode == "REPLAY" and not self.replay_rows:
            return "NO RECORDING"
        return mode

    def snapshot(self, now=None):
        now = now or time.time()
        out = []
        for asset_id, a in self.assets.items():
            st = self.state[asset_id]
            sim = self.sims.get(asset_id)
            item = {"asset_id": asset_id, "asset_type": a["type"], "asset_name": a["name"], "data_mode": a["data_mode"],
                    "timestamp": st["last_seen"], "last_seen": st["last_seen"],
                    "age_s": None if st["last_seen"] is None else round(now - st["last_seen"], 1),
                    "freshness": self.freshness(asset_id), "telemetry": st["values"],
                    "sensor_status": st["sensor_status"], "data_quality": st["quality"],
                    "anomaly_state": st["anomaly"], "active_faults": list(st["faults"].values()),
                    "operating_state": self._sim_state(sim) if sim else self.freshness(asset_id),
                    "simulation": sim.scenario if sim else None,
                    "simulation_scenario_id": sim.scenario["scenario_id"] if sim and sim.scenario else None,
                    "generated": self.generated if asset_id == self.real_id else False}
            if a["type"] == "conveyor":
                c = self.conveyor
                synced = c["smoothed_rpm"] is not None
                item["conveyor"] = {
                    "sync": "SYNCHRONISED" if synced else "UNAVAILABLE",
                    "source_asset": a["driven_by"], "measured_rpm": c["last_valid_rpm"] if synced else None,
                    "smoothed_rpm": round(c["smoothed_rpm"], 1) if synced else None,
                    "belt_speed_m_s": round(c["smoothed_rpm"] * self.belt_m_per_rev / 60, 3) if synced else None,
                    "last_valid_rpm": c["last_valid_rpm"], "last_valid_ts": c["last_valid_ts"],
                    "drive": self.drive, "belt_m_per_rev": round(self.belt_m_per_rev, 5),
                    "mapping": (f"belt speed (m/s) = measured RPM ÷ {self.drive['reduction']:g} × 2π × "
                                f"{self.drive['drum_radius_m']:g} m ÷ 60 (conceptual drum; RPM smoothed, α = {RPM_SMOOTHING})"),
                    "reason": None if synced else "No valid live RPM from the physical motor."}
                item["freshness"] = "SYNCHRONISED" if synced else "SYNC UNAVAILABLE"
                item["operating_state"] = item["freshness"]
            out.append(item)
        return {"type": "snapshot", "ts": now, "assets": out, "link": self.link_status(now),
                "pending_commands": len(self.pending)}

    @staticmethod
    def _sim_state(sim):
        if sim.running:
            return "RUNNING" if sim.steady else "STARTING"
        return "STOPPED" if sim.speed_state < 0.02 else "STOPPING"

    def link_status(self, now=None):
        now = now or time.time()
        link = self.link
        return {"status": link.status, "message": link.message, "port": link.port, "baud": link.baud,
                "lines_ok": link.lines_ok, "lines_bad": link.lines_bad, "missed_sequences": link.missed_sequences,
                "last_line_age_s": None if link.last_line_ts is None else round(now - link.last_line_ts, 1),
                "firmware_version": link.firmware_version, "reported_device_id": link.reported_device_id,
                "capabilities": link.capabilities, "recent_problems": list(link.recent_problems)[-8:],
                "commands_supported": link.firmware_version is not None,
                "telemetry": self.state[self.real_id]["freshness"] or "OFFLINE"}

    # ---- simulation control ---------------------------------------------------
    def _sim(self, asset_id):
        if asset_id not in self.assets:
            raise EngineError(404, f"Unknown asset {asset_id}")
        if asset_id not in self.sims:
            raise EngineError(409, "Fault simulation is only available on SIMULATED assets. "
                                   f"{asset_id} is {self.assets[asset_id]['data_mode']}.")
        return self.sims[asset_id]

    def sim_action(self, user, asset_id, action, scenario_id=None, severity=0.7):
        sim = self._sim(asset_id)
        try:
            if action == "start":
                s = sim.start(scenario_id, severity)
                self.state[asset_id].update(counters={}, faults={}, anomaly="NORMAL")
                self.event(asset_id, "SIMULATION", "INFO", "OPERATOR", f"Simulated fault started: {s['name']}",
                           f"severity {severity:.0%}, started by {user}", s["run_id"])
            elif action == "pause":
                sim.pause()
                self.event(asset_id, "SIMULATION", "INFO", "OPERATOR", "Simulation paused", f"by {user}", sim.scenario["run_id"])
            elif action == "resume":
                sim.resume()
                self.event(asset_id, "SIMULATION", "INFO", "OPERATOR", "Simulation resumed", f"by {user}", sim.scenario["run_id"])
            elif action == "stop":
                s = sim.stop()
                self.event(asset_id, "SIMULATION", "INFO", "OPERATOR", f"Simulated fault ended: {s['name']}",
                           f"ended by {user}; readings recover naturally", s["run_id"])
            elif action == "machine-stop":
                run = sim.scenario["run_id"] if sim.scenario else None
                if sim.scenario:
                    sim.stop()                      # a stopped machine cannot be running a fault scenario
                sim.set_running(False)
                self.event(asset_id, "SIMULATION", "INFO", "OPERATOR", "Simulated machine stopped",
                           f"by {user}; it coasts down over a few seconds", run)
            elif action == "machine-start":
                sim.set_running(True)
                self.event(asset_id, "SIMULATION", "INFO", "OPERATOR", "Simulated machine started", f"by {user}")
            elif action == "reset":
                run = sim.scenario["run_id"] if sim.scenario else None
                sim.reset()
                self.state[asset_id].update(counters={}, faults={}, anomaly="NORMAL")
                self.event(asset_id, "SIMULATION", "INFO", "OPERATOR", "Asset reset to simulated baseline", f"by {user}", run)
            else:
                raise EngineError(400, f"Unknown action {action}")
        except ValueError as err:
            raise EngineError(400, str(err)) from None
        return sim.scenario

    # ---- device commands ------------------------------------------------------
    def issue_command(self, user, name, argument=None, event_ref=None, note=""):
        """Validate, record and send one command. Returns the stored command row."""
        device_id = self.assets[self.real_id]["device_id"]
        try:
            argument = validate_command(name, argument)
        except ValueError as err:
            cid = self.store.add_command(device_id, str(name)[:40], {"argument": str(argument)[:40]}, "REJECTED",
                                         user, str(err), event_ref)
            self.event(self.real_id, "COMMAND", "WARNING", "OPERATOR", f"Command rejected: {str(name)[:40]}", str(err), ref=f"CMD-{cid}")
            return self.store.commands(command_id=cid)[0]
        now = time.time()
        reason = None
        if self.link.status != "CONNECTED":
            reason = "The device is not connected."
        elif name == "BUZZER_ALERT" and now - self.last_buzzer < 3.0:
            reason = "A buzzer alert was sent less than 3 s ago."
        if reason:
            cid = self.store.add_command(device_id, name, {"argument": argument}, "REJECTED", user, reason, event_ref)
            self.event(self.real_id, "COMMAND", "WARNING", "OPERATOR", f"Command rejected: {name}", reason, ref=f"CMD-{cid}")
            return self.store.commands(command_id=cid)[0]
        self.cmd_seq += 1
        cid = self.store.add_command(device_id, name, {"argument": argument, "note": note}, "PENDING", user,
                                     "", event_ref, self.cmd_seq)
        written, detail = self.link.send_command(self.cmd_seq, f"CMD-{cid}", name, argument)
        if not written:
            self.store.update_command(cid, "FAILED", detail)
            self.event(self.real_id, "COMMAND", "WARNING", "DEVICE", f"Command failed: {name}", detail, ref=f"CMD-{cid}")
        else:
            self.pending[f"CMD-{cid}"] = {"id": cid, "name": name, "sent": now}
            if name == "BUZZER_ALERT":
                self.last_buzzer = now
            self.event(self.real_id, "COMMAND", "INFO", "OPERATOR", f"Command issued: {name}",
                       f"by {user}; argument {argument}. {note}".strip(), ref=f"CMD-{cid}")
        return self.store.commands(command_id=cid)[0]

    def _settle_commands(self, acks, now):
        for ack in acks:
            p = self.pending.pop(ack["command_id"], None)
            if not p:
                continue                       # unknown or already timed-out command id
            state = "ACKNOWLEDGED" if ack["ok"] else "FAILED"
            self.store.update_command(p["id"], state, ack["detail"] or ("device confirmed" if ack["ok"] else "device refused"))
            self.event(self.real_id, "COMMAND", "INFO" if ack["ok"] else "WARNING", "DEVICE",
                       f"Command {state.lower()} by device: {p['name']}", ack["detail"], ref=ack["command_id"])
        for key, p in list(self.pending.items()):
            if now - p["sent"] > self.settings.command_timeout_s:
                self.pending.pop(key)
                detail = ("No acknowledgement from the device. It may be running firmware without command "
                          "support, so it is unknown whether the command was carried out.")
                self.store.update_command(p["id"], "TIMEOUT", detail)
                self.event(self.real_id, "COMMAND", "WARNING", "DEVICE", f"Command not acknowledged: {p['name']}", detail, ref=key)

    # ---- diagnosis and maintenance ---------------------------------------------
    def baselines(self, asset_id):
        if self.assets[asset_id]["data_mode"] == "SIMULATED":
            return {c["key"]: {"median": c["nominal"], "source": "simulation model nominal value"}
                    for c in self.channels(asset_id) if c["nominal"]}
        return self.state[asset_id]["baseline"]

    def preview_diagnosis(self, asset_id):
        """Compute the current assessment without storing it or logging an event."""
        if asset_id not in self.assets or self.assets[asset_id]["type"] == "conveyor":
            raise EngineError(404, "This asset has no sensor channels to diagnose.")
        now = time.time()
        sim = self.sims.get(asset_id)
        return diagnostics.diagnose(self.assets[asset_id], self.channels(asset_id),
                                    self.store.history(asset_id, now - 120), self.baselines(asset_id),
                                    self.freshness(asset_id), sim.scenario if sim else None, now,
                                    stopped=bool(sim and not sim.steady))

    def run_diagnosis(self, asset_id, user):
        payload = self.preview_diagnosis(asset_id)
        payload["requested_by"] = user
        did = self.store.add_diagnosis(asset_id, self.assets[asset_id]["data_mode"], payload["status"], payload)
        self.event(asset_id, "DIAGNOSIS", "INFO", "DIAGNOSTIC_RULES", f"Diagnosis created: {payload['status']}",
                   payload.get("observation", ""), payload["simulation"]["run_id"] if payload["simulation"] else None, f"DX-{did}")
        return self.store.diagnoses(diagnosis_id=did)[0]

    def window_means(self, asset_id, seconds=60):
        rows = self.store.history(asset_id, time.time() - seconds)
        out = {}
        for ch in self.channels(asset_id):
            values = [r["vals"][ch["key"]] for r in rows if r["vals"].get(ch["key"]) is not None]
            if values:
                out[ch["key"]] = {"label": ch["label"], "unit": ch["unit"], "mean": round(statistics.fmean(values), 4),
                                  "samples": len(values)}
        mode = self.assets[asset_id]["data_mode"]
        return {"captured_ts": time.time(), "window_s": seconds, "values": out,
                "kind": {"REAL": "MEASURED RESULT", "SIMULATED": "SIMULATED RESULT", "REPLAY": "REPLAYED RESULT"}[mode],
                "freshness": self.freshness(asset_id)}

    def create_work_order(self, user, asset_id, title, priority="MEDIUM", diagnosis_id=None):
        if asset_id not in self.assets:
            raise EngineError(404, f"Unknown asset {asset_id}")
        checks, evidence = [], None
        if diagnosis_id:
            rows = self.store.diagnoses(diagnosis_id=diagnosis_id)
            if not rows or rows[0]["asset_id"] != asset_id:
                raise EngineError(400, "That diagnosis does not belong to this asset.")
            checks = rows[0]["payload"].get("recommended_next_steps", [])
            evidence = rows[0]["payload"].get("observation")
        payload = {"recommended_checks": checks, "diagnostic_evidence": evidence, "notes": [],
                   "history": [{"ts": time.time(), "user": user, "from": None, "to": "NEW"}],
                   "before": self.window_means(asset_id) if self.assets[asset_id]["type"] != "conveyor" else None,
                   "after": None, "completed_ts": None, "parts": "None recorded. No verified parts data exists for conceptual models."}
        wid = self.store.add_work_order(asset_id, self.assets[asset_id]["data_mode"], diagnosis_id, title[:200], priority, payload)
        self.event(asset_id, "MAINTENANCE", "INFO", "OPERATOR", f"Work order WO-{wid} created", title[:200], ref=f"WO-{wid}")
        return self.store.work_orders(wid)[0]

    def update_work_order(self, user, work_order_id, state=None, note=None):
        rows = self.store.work_orders(work_order_id)
        if not rows:
            raise EngineError(404, "Unknown work order")
        wo = rows[0]
        payload = wo["payload"]
        if note and note.strip():
            payload["notes"].append({"ts": time.time(), "user": user, "text": note.strip()[:2000]})
        new_state = wo["state"]
        if state and state != wo["state"]:
            if state not in WORK_ORDER_FLOW[wo["state"]]:
                raise EngineError(409, f"A work order cannot go from {wo['state']} to {state}. "
                                       f"Allowed: {', '.join(WORK_ORDER_FLOW[wo['state']]) or 'none'}.")
            payload["history"].append({"ts": time.time(), "user": user, "from": wo["state"], "to": state})
            new_state = state
            if state == "COMPLETED":
                payload["completed_ts"] = time.time()
            if state == "VERIFIED" and self.assets[wo["asset_id"]]["type"] != "conveyor":
                payload["after"] = self.window_means(wo["asset_id"])
            titles = {"IN PROGRESS": "Maintenance work started", "COMPLETED": "Maintenance work completed"}
            self.event(wo["asset_id"], "MAINTENANCE", "INFO", "OPERATOR",
                       titles.get(state, f"Work order WO-{work_order_id}: {state}"), f"by {user}", ref=f"WO-{work_order_id}")
        self.store.update_work_order(work_order_id, new_state, payload)
        return self.store.work_orders(work_order_id)[0]

    # ---- health -----------------------------------------------------------------
    def health(self):
        now = time.time()
        try:
            db = "OK" if self.store.ping() else "ERROR"
        except Exception as err:
            db = f"ERROR: {err}"
        loop_age = None if self.last_tick is None else now - self.last_tick
        return {"backend": "OK", "uptime_s": round(now - self.started),
                "database": db,
                "engine_loop": "UNKNOWN" if loop_age is None else "OK" if loop_age < 5 else "STALLED",
                "engine_last_tick_age_s": None if loop_age is None else round(loop_age, 1),
                "device_link": self.link_status(now), "errors": list(self.errors)[-20:],
                "simulated_assets": len(self.sims), "replay": self.replay_source or "NO RECORDING LOADED",
                "allowed_commands": {k: (None if v is None else {"min_ms": v[0], "max_ms": v[1]}) for k, v in COMMANDS.items()}}
