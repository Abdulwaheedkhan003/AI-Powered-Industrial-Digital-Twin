"""User-configured warning / critical limits, stored in a local JSON file.

There are no built-in limits: every threshold starts disabled and empty,
because a safe level depends on this motor, its load and where each sensor is
mounted. suggest_thresholds() can propose numbers from a recording's own
baseline, but the user has to review and enable them.

Sensor limits here are completely separate from the AI anomaly score.
"""
import json

import pandas as pd

import config
from config import SENSORS

# Everything a rule can watch: the slow sensors plus two per-burst indicators.
SIGNALS = {name: {"label": spec["label"], "unit": spec["unit"],
                  "category": spec["category"], "min": spec["min"], "max": spec["max"]}
           for name, spec in SENSORS.items()}
SIGNALS["vib_rms_total_g"] = {"label": "Vibration RMS per burst", "unit": "g",
                              "category": "motor", "min": 0.0, "max": 16.0}
SIGNALS["vib_peak_g"] = {"label": "Vibration peak per burst", "unit": "g",
                         "category": "motor", "min": 0.0, "max": 16.0}


def default_settings():
    return {
        "thresholds": {name: {"enabled": False, "direction": "high",
                              "warning": None, "critical": None} for name in SIGNALS},
        # raise_after : consecutive valid readings beyond a limit before alerting
        # clear_after : consecutive normal readings before the alert clears
        # repeat_count: if > 0, also alert after this many exceedances within
        #               repeat_window_s seconds, even if not consecutive
        "persistence": {"raise_after": 3, "clear_after": 5,
                        "repeat_count": 0, "repeat_window_s": 60},
        # Statistical detector: baseline_s seconds at the start of the session
        # (or a reference session), alert beyond k robust spreads.
        "statistical": {"enabled": True, "baseline_s": 60, "k": 4.0,
                        "reference_session": None},
        "ai": {"raise_after": 2, "clear_after": 2},
        # Each rule: {"name": str, "signals": [..], "min_active": int}
        "combined_rules": [],
    }


def validate_settings(settings):
    """Return a list of problems (empty list = settings are usable)."""
    errors = []
    for name, rule in settings.get("thresholds", {}).items():
        if name not in SIGNALS:
            errors.append(f"'{name}' is not a known signal.")
            continue
        info = SIGNALS[name]
        if rule.get("direction") not in ("high", "low"):
            errors.append(f"{name}: direction must be 'high' or 'low'.")
            continue
        warning, critical = rule.get("warning"), rule.get("critical")
        if rule.get("enabled") and warning is None:
            errors.append(f"{name}: enabled but no warning level is set.")
        for level_name, value in (("warning", warning), ("critical", critical)):
            if value is not None and not info["min"] <= value <= info["max"]:
                errors.append(f"{name}: {level_name} level {value:g} is outside what the "
                              f"sensor can report ({info['min']:g} to {info['max']:g} {info['unit']}).")
        if warning is not None and critical is not None:
            if rule["direction"] == "high" and critical < warning:
                errors.append(f"{name}: critical must not be below warning for a 'high' limit.")
            if rule["direction"] == "low" and critical > warning:
                errors.append(f"{name}: critical must not be above warning for a 'low' limit.")
    p = settings.get("persistence", {})
    if p.get("raise_after", 1) < 1 or p.get("clear_after", 1) < 1:
        errors.append("raise_after and clear_after must be at least 1.")
    if p.get("repeat_count", 0) < 0 or p.get("repeat_window_s", 1) <= 0:
        errors.append("repeat_count must be 0 or more and repeat_window_s positive.")
    s = settings.get("statistical", {})
    if s.get("k", 1) <= 0 or s.get("baseline_s", 1) <= 0:
        errors.append("Statistical k and baseline_s must be positive.")
    for rule in settings.get("combined_rules", []):
        unknown = [x for x in rule.get("signals", []) if x not in SIGNALS]
        if unknown:
            errors.append(f"Combined rule '{rule.get('name')}': unknown signal(s) {unknown}.")
        if not 2 <= rule.get("min_active", 0) <= max(len(rule.get("signals", [])), 2):
            errors.append(f"Combined rule '{rule.get('name')}': min_active must be between 2 "
                          "and the number of signals.")
    return errors


def _settings_path(path=None):
    return path or config.STATE_DIR / "settings.json"


def load_settings(path=None):
    """Read settings, filling anything missing from the defaults."""
    settings = default_settings()
    path = _settings_path(path)
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return settings          # unreadable file: fall back to safe defaults
        for key, value in stored.items():
            if isinstance(settings.get(key), dict) and isinstance(value, dict):
                if key == "thresholds":
                    for name, rule in value.items():
                        if name in settings[key]:
                            settings[key][name].update(rule)
                else:
                    settings[key].update(value)
            elif key in settings:
                settings[key] = value
    return settings


def save_settings(settings, path=None):
    errors = validate_settings(settings)
    if errors:
        raise ValueError("Settings not saved: " + " ".join(errors))
    path = _settings_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return path


def threshold_levels(series, rule):
    """0 = normal, 1 = beyond warning, 2 = beyond critical, NaN = no valid reading."""
    levels = pd.Series(0.0, index=series.index)
    sign = 1 if rule["direction"] == "high" else -1
    if rule.get("warning") is not None:
        levels[sign * series > sign * rule["warning"]] = 1.0
    if rule.get("critical") is not None:
        levels[sign * series > sign * rule["critical"]] = 2.0
    return levels.where(series.notna())


def suggest_thresholds(baseline, k_warning=4.0, k_critical=8.0):
    """Propose 'high' limits = baseline median + k x robust spread, or None.

    A statistical starting point only. It says where readings would be unusual
    for the baseline recording, not where the motor becomes unsafe.
    """
    if baseline is None or baseline["spread"] == 0:
        return None
    return {"direction": "high",
            "warning": float(baseline["median"] + k_warning * baseline["spread"]),
            "critical": float(baseline["median"] + k_critical * baseline["spread"])}
