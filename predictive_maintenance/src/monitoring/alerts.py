"""Alert logic and alert history.

Persistence rules (so one noisy reading never raises an alert):
  RAISE  when the condition holds for `raise_after` consecutive valid readings,
         or, if repeat_count > 0, when it has held `repeat_count` times within
         the last `repeat_window_s` seconds.
  ESCALATE to critical when the critical limit holds for `raise_after`
         consecutive valid readings.
  CLEAR  when `clear_after` consecutive valid readings are back to normal.
Missing readings neither count towards an alert nor clear one.

Combined rules: a named list of signals and a minimum count. The rule is
active while at least that many of its signals have an active alert at the
same time. No weights, no health index: the explanation lists exactly which
signals were involved.

Alerts describe what was measured. They never state that a component failed.
"""
import hashlib
from collections import deque

import pandas as pd

import config

ALERT_COLUMNS = ["alert_id", "timestamp", "cleared_at", "session_id", "data_source",
                 "alert_type", "signal", "severity", "value", "explanation",
                 "acknowledged", "operator_note", "resolution"]
RESOLUTIONS = ["open", "investigating", "resolved", "false alarm"]


def persistence_events(levels, raise_after=3, clear_after=5, repeat_count=0,
                       repeat_window_s=60):
    """Turn a series of levels (0/1/2/NaN) into a list of alert events."""
    events, active = [], None
    run_bad = run_critical = run_ok = 0
    first_bad = None
    recent = deque()
    for ts, level in levels.dropna().items():
        if level >= 1:
            run_ok = 0
            run_bad += 1
            run_critical = run_critical + 1 if level >= 2 else 0
            if run_bad == 1:
                first_bad = ts
            recent.append(ts)
            while (ts - recent[0]).total_seconds() > repeat_window_s:
                recent.popleft()
            if active is None:
                if run_bad >= raise_after:
                    active = {"first_exceeded": first_bad, "trigger": "consecutive",
                              "count": run_bad}
                elif repeat_count and len(recent) >= repeat_count:
                    active = {"first_exceeded": recent[0], "trigger": "repeated",
                              "count": len(recent)}
                if active:
                    active.update(raised_at=ts, cleared_at=None, severity="warning")
            if active and run_critical >= raise_after:
                active["severity"] = "critical"
        else:
            run_bad = run_critical = 0
            run_ok += 1
            if active and run_ok >= clear_after:
                active["cleared_at"] = ts
                events.append(active)
                active = None
    if active:
        events.append(active)
    return events


def active_mask(events, timeline):
    """Boolean Series over `timeline`: True while any of the events is active."""
    mask = pd.Series(False, index=timeline)
    for e in events:
        end = e["cleared_at"] if e["cleared_at"] is not None else timeline[-1] + pd.Timedelta(seconds=1)
        mask |= (timeline >= e["raised_at"]) & (timeline < end)
    return mask


def combined_events(rule, events_by_signal, timeline):
    """Events for one combined rule. Returns (events, not_evaluated_signals)."""
    available = [s for s in rule["signals"] if s in events_by_signal]
    missing = [s for s in rule["signals"] if s not in events_by_signal]
    if len(available) < rule["min_active"] or len(timeline) == 0:
        return [], missing
    masks = pd.DataFrame({s: active_mask(events_by_signal[s], timeline) for s in available})
    on = masks.sum(axis=1) >= rule["min_active"]
    events = []
    run_id = (on != on.shift()).cumsum()
    for _, run in on.groupby(run_id):
        if not run.iloc[0]:
            continue
        after = on.index[on.index > run.index[-1]]
        involved = [s for s in available if masks.loc[run.index[0], s]]
        events.append({"first_exceeded": run.index[0], "raised_at": run.index[0],
                       "cleared_at": after[0] if len(after) else None,
                       "severity": "warning", "trigger": "combined",
                       "count": len(involved), "involved": involved})
    return events, missing


def make_alert(session_id, data_source, alert_type, signal, event, value, explanation):
    key = f"{session_id}|{alert_type}|{signal}|{event['raised_at']}"
    return {"alert_id": hashlib.sha1(key.encode()).hexdigest()[:10],
            "timestamp": event["raised_at"], "cleared_at": event["cleared_at"],
            "session_id": session_id, "data_source": data_source,
            "alert_type": alert_type, "signal": signal, "severity": event["severity"],
            "value": value, "explanation": explanation, "acknowledged": False,
            "operator_note": "", "resolution": "open"}


def alerts_frame(alerts):
    df = pd.DataFrame(alerts, columns=ALERT_COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["cleared_at"] = pd.to_datetime(df["cleared_at"])
    return df.sort_values("timestamp").reset_index(drop=True)


# ---- history (a local CSV file) --------------------------------------------
def _path(path=None):
    return path or config.STATE_DIR / "alerts.csv"


def load_alerts(path=None):
    path = _path(path)
    if not path.exists():
        return alerts_frame([])
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df["acknowledged"] = df["acknowledged"] == "True"
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return alerts_frame(df)


def _write(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def sync_alerts(new_alerts, path=None):
    """Add alerts to the history without duplicating or losing operator input."""
    path = _path(path)
    history = load_alerts(path)
    if len(new_alerts) == 0:
        return history
    new_alerts = new_alerts.set_index("alert_id")
    history = history.set_index("alert_id")
    for alert_id in new_alerts.index.intersection(history.index):
        # the detector may have updated these; operator fields are kept
        history.loc[alert_id, ["cleared_at", "severity"]] = new_alerts.loc[alert_id, ["cleared_at", "severity"]]
    added = new_alerts.loc[new_alerts.index.difference(history.index)]
    frames = [f for f in (history, added) if len(f)]
    merged = alerts_frame(pd.concat(frames).reset_index())
    _write(merged, path)
    return merged


def update_alert(alert_id, acknowledged=None, operator_note=None, resolution=None, path=None):
    path = _path(path)
    history = load_alerts(path)
    row = history["alert_id"] == alert_id
    if not row.any():
        raise KeyError(f"No alert with id {alert_id}")
    if resolution is not None and resolution not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of {RESOLUTIONS}")
    for column, value in (("acknowledged", acknowledged), ("operator_note", operator_note),
                          ("resolution", resolution)):
        if value is not None:
            history.loc[row, column] = value
    _write(history, path)
    return history


def filter_alerts(alerts, start=None, end=None, signals=None, severities=None, types=None):
    keep = pd.Series(True, index=alerts.index)
    if start is not None:
        keep &= alerts["timestamp"] >= pd.Timestamp(start)
    if end is not None:
        keep &= alerts["timestamp"] <= pd.Timestamp(end)
    for column, wanted in (("signal", signals), ("severity", severities), ("alert_type", types)):
        if wanted:
            keep &= alerts[column].isin(wanted)
    return alerts[keep]
