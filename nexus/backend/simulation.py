"""Deterministic simulation of virtual machines and fault scenarios.

Everything here is invented and is labelled SIMULATED wherever it is shown.
It never touches the physical telemetry pipeline.

Each machine is one small physical model so its signals move together:

    load, speed  ->  current  ->  heat  ->  temperature (first-order lag)
    speed        ->  rpm, vibration, and the machine's process value

A fault scenario does not set readings directly. It changes model factors
(bearing friction, cooling effectiveness, load, drive speed), ramping from 0 to
`severity` over the scenario duration, and the readings follow.

Repeatable: noise comes from a seeded generator that is re-seeded whenever a
machine is reset or a scenario starts, so the same scenario from a reset
machine produces the same numbers every time.
"""
import math
import random

LIMIT_SOURCE = "Simulation model configuration (illustrative; not a standard or manufacturer limit)"
BASE_LOAD = 0.62

# nominal operating point of each machine type
MODELS = {
    "cooling_fan": {"label": "Cooling fan", "ambient": 28.0, "temp_rise": 22.0, "tau": 45.0,
                    "i_nom": 3.2, "rpm_nom": 1450.0, "vib_nom": 1.8,
                    "extra": {"key": "airflow_pct", "label": "Airflow", "unit": "%", "nominal": 100.0, "law": "speed"}},
    "pump": {"label": "Centrifugal pump", "ambient": 28.0, "temp_rise": 30.0, "tau": 60.0,
             "i_nom": 7.5, "rpm_nom": 2900.0, "vib_nom": 2.2,
             "extra": {"key": "discharge_pressure_bar", "label": "Discharge pressure", "unit": "bar", "nominal": 4.2, "law": "speed2"}},
    "compressor": {"label": "Air compressor", "ambient": 28.0, "temp_rise": 45.0, "tau": 70.0,
                   "i_nom": 11.0, "rpm_nom": 1100.0, "vib_nom": 3.0,
                   "extra": {"key": "discharge_pressure_bar", "label": "Discharge pressure", "unit": "bar", "nominal": 7.5, "law": "speed"}},
    "gearbox": {"label": "Gearbox", "ambient": 28.0, "temp_rise": 26.0, "tau": 80.0,
                "i_nom": None, "rpm_nom": 365.0, "vib_nom": 2.0, "extra": None},
    "motor": {"label": "Induction motor", "ambient": 28.0, "temp_rise": 35.0, "tau": 60.0,
              "i_nom": 4.8, "rpm_nom": 1750.0, "vib_nom": 1.5, "extra": None},
}

SCENARIOS = {
    "bearing-degradation": {
        "name": "Bearing degradation", "fault": "bearing", "duration_s": 60,
        "asset_types": ["cooling_fan", "pump", "compressor", "gearbox", "motor"],
        "affected": ["vibration_mm_s", "current_a", "temperature_c", "rpm"],
        "expected": "Vibration rises strongly; friction raises current and temperature slightly; speed drops a little.",
        "reset": "Reset returns the machine to its baseline state immediately."},
    "cooling-degradation": {
        "name": "Abnormal temperature rise (cooling degradation)", "fault": "cooling", "duration_s": 60,
        "asset_types": ["cooling_fan", "pump", "compressor", "gearbox", "motor"],
        "affected": ["temperature_c"],
        "expected": "Temperature climbs with a thermal lag; vibration, current and speed stay near baseline.",
        "reset": "Reset returns the machine to its baseline state immediately."},
    "abnormal-load": {
        "name": "Abnormal load / current draw", "fault": "load", "duration_s": 60,
        "asset_types": ["pump", "compressor", "gearbox", "motor"],
        "affected": ["current_a", "temperature_c", "rpm", "load_pct"],
        "expected": "Load and current rise, speed sags slightly, temperature follows the current.",
        "reset": "Reset returns the machine to its baseline state immediately."},
    "reduced-speed": {
        "name": "Reduced rotational speed", "fault": "speed", "duration_s": 60,
        "asset_types": ["cooling_fan", "pump", "motor"],
        "affected": ["rpm", "current_a", "vibration_mm_s"],
        "expected": "Speed falls; current, vibration and the process value fall with it.",
        "reset": "Reset returns the machine to its baseline state immediately."},
    "imbalance": {
        "name": "Excessive vibration (imbalance)", "fault": "imbalance", "duration_s": 60,
        "asset_types": ["cooling_fan", "pump", "motor"],
        "affected": ["vibration_mm_s"],
        "expected": "Vibration rises; current, temperature and speed stay near baseline.",
        "reset": "Reset returns the machine to its baseline state immediately."},
    "bearing-and-cooling": {
        "name": "Combined: bearing degradation with cooling loss", "fault": "combined", "duration_s": 60,
        "asset_types": ["cooling_fan", "pump", "motor"],
        "affected": ["vibration_mm_s", "current_a", "temperature_c", "rpm"],
        "expected": "Vibration and temperature both rise markedly; current rises; speed drops a little.",
        "reset": "Reset returns the machine to its baseline state immediately."},
}


def fault_factors(fault, x):
    """Model factors for a fault at intensity x = severity x progress (0..1)."""
    f = {"vib": 1.0, "friction": 1.0, "cooling": 1.0, "load": 0.0, "speed": 1.0, "rpm": 1.0}
    if fault in ("bearing", "combined"):
        f.update(vib=1 + 5.0 * x, friction=1 + 0.22 * x, rpm=1 - 0.05 * x)
    if fault == "cooling":
        f["cooling"] = 1 + 1.4 * x
    if fault == "combined":
        f["cooling"] = 1 + 0.9 * x
    if fault == "load":
        f["load"] = 0.45 * x
    if fault == "speed":
        f["speed"] = 1 - 0.35 * x
    if fault == "imbalance":
        f["vib"] = 1 + 4.0 * x
    return f


def channel_definitions(model_key):
    """Channels of a simulated machine with their illustrative limits."""
    m = MODELS[model_key]
    steady = m["ambient"] + m["temp_rise"]
    channels = [
        {"key": "temperature_c", "label": "Temperature", "unit": "°C", "nominal": round(steady, 1),
         "warn": round(m["ambient"] + m["temp_rise"] * 1.35, 1), "crit": round(m["ambient"] + m["temp_rise"] * 1.7, 1),
         "direction": "high", "kind": "temperature"},
        {"key": "vibration_mm_s", "label": "Vibration (velocity RMS)", "unit": "mm/s", "nominal": m["vib_nom"],
         "warn": round(m["vib_nom"] * 2.5, 2), "crit": round(m["vib_nom"] * 4.0, 2), "direction": "high", "kind": "vibration"},
    ]
    if m["i_nom"]:
        channels.append({"key": "current_a", "label": "Current", "unit": "A", "nominal": m["i_nom"],
                         "warn": round(m["i_nom"] * 1.2, 2), "crit": round(m["i_nom"] * 1.4, 2),
                         "direction": "high", "kind": "current"})
    channels.append({"key": "rpm", "label": "Speed", "unit": "rpm", "nominal": m["rpm_nom"],
                     "warn": round(m["rpm_nom"] * 0.93), "crit": round(m["rpm_nom"] * 0.85),
                     "direction": "low", "kind": "rpm"})
    channels.append({"key": "load_pct", "label": "Load", "unit": "%", "nominal": round(BASE_LOAD * 100),
                     "warn": None, "crit": None, "direction": "high", "kind": "load"})
    if m["extra"]:
        e = m["extra"]
        channels.append({"key": e["key"], "label": e["label"], "unit": e["unit"], "nominal": e["nominal"],
                         "warn": None, "crit": None, "direction": "low", "kind": "process"})
    for c in channels:
        c["limit_source"] = LIMIT_SOURCE if c["warn"] is not None else None
    return channels


class SimulatedAsset:
    def __init__(self, asset_id, model_key, seed=1):
        if model_key not in MODELS:
            raise ValueError(f"No simulation model for asset type '{model_key}'")
        self.asset_id, self.model_key, self.seed = asset_id, model_key, seed
        self.model = MODELS[model_key]
        self.run_counter = 0
        self.reset()

    # ---- scenario control ---------------------------------------------------
    def reset(self):
        """Return to the normal baseline immediately."""
        self.rng = random.Random(self.seed)
        self.temp = self.model["ambient"] + self.model["temp_rise"]
        self.scenario = None
        self.values = {}
        self.running = True          # the simulated machine is switched on
        self.speed_state = 1.0       # 0 = at rest, 1 = at operating speed

    @property
    def steady(self):
        """True once the machine is running at operating speed (limits only apply then)."""
        return self.running and self.speed_state > 0.985

    def set_running(self, running):
        """Switch the simulated machine on or off. It spins up or coasts down over a few seconds."""
        self.running = bool(running)

    def start(self, scenario_id, severity):
        if scenario_id not in SCENARIOS:
            raise ValueError(f"Unknown scenario '{scenario_id}'")
        spec = SCENARIOS[scenario_id]
        if self.model_key not in spec["asset_types"]:
            raise ValueError(f"Scenario '{scenario_id}' is not defined for a {self.model['label'].lower()}")
        if not 0.1 <= severity <= 1.0:
            raise ValueError("severity must be between 0.1 and 1.0")
        if not self.running:
            raise ValueError("The simulated machine is stopped. Start it before running a fault scenario.")
        self.reset()                       # scenarios always start from the same baseline
        self.run_counter += 1
        self.scenario = {"scenario_id": scenario_id, "name": spec["name"], "fault": spec["fault"],
                         "severity": float(severity), "elapsed_s": 0.0, "duration_s": spec["duration_s"],
                         "paused": False, "run_id": f"{self.asset_id}-{scenario_id}-{self.run_counter}"}
        return self.scenario

    def pause(self):
        self._require_scenario()["paused"] = True

    def resume(self):
        self._require_scenario()["paused"] = False

    def stop(self):
        """End the scenario. Fault effects are removed and readings recover naturally."""
        ended = self._require_scenario()
        self.scenario = None
        return ended

    def _require_scenario(self):
        if self.scenario is None:
            raise ValueError("No scenario is running on this asset")
        return self.scenario

    # ---- model --------------------------------------------------------------
    def step(self, dt=1.0):
        m, s = self.model, self.scenario
        x = 0.0
        if s:
            if not s["paused"]:
                s["elapsed_s"] = min(s["elapsed_s"] + dt, 1e9)
            x = s["severity"] * min(1.0, s["elapsed_s"] / s["duration_s"])
        f = fault_factors(s["fault"] if s else None, x)
        noise = self.rng.gauss

        # spin-up / coast-down: first-order approach to the commanded state
        target_speed = 1.0 if self.running else 0.0
        self.speed_state += (target_speed - self.speed_state) * (1 - math.exp(-dt / 2.5))
        if abs(target_speed - self.speed_state) < 0.002:
            self.speed_state = target_speed

        load = BASE_LOAD + f["load"]
        speed = f["speed"] * self.speed_state
        load_ratio = load / BASE_LOAD
        rpm = m["rpm_nom"] * speed * f["rpm"] * (1 - 0.04 * (load_ratio - 1))
        vib = m["vib_nom"] * speed ** 2 * f["vib"]
        if m["i_nom"]:
            current = m["i_nom"] * load_ratio * f["friction"] * speed
            heat = (current / m["i_nom"]) ** 2
        else:
            current = None
            heat = load_ratio * f["friction"] * self.speed_state
        target = m["ambient"] + m["temp_rise"] * heat * f["cooling"]
        self.temp += (target - self.temp) * (1 - math.exp(-dt / m["tau"]))

        values = {
            "temperature_c": round(self.temp + noise(0, 0.08), 2),
            "vibration_mm_s": round(max(0.0, vib * (1 + noise(0, 0.03))), 3),
            "rpm": round(rpm * (1 + noise(0, 0.002)), 1),
            "load_pct": round(load * 100 * self.speed_state * (1 + noise(0, 0.004)), 1),
        }
        if current is not None:
            values["current_a"] = round(current * (1 + noise(0, 0.008)), 3)
        if m["extra"]:
            e = m["extra"]
            ratio = rpm / m["rpm_nom"]
            factor = ratio ** 2 if e["law"] == "speed2" else ratio
            values[e["key"]] = round(e["nominal"] * factor * (1 + noise(0, 0.004)), 2)
        self.values = values
        return values
