"""Per-sensor status for the status panel.

A sensor is never shown as "healthy". The best possible status is
"Reporting — calibration not verified", because no sensor in this project has
been checked against a reference instrument.
"""
import pandas as pd

from config import SENSORS
from src.processing.validation import classify

STALE_AFTER_S = 10.0


def sensor_status(df, now=None):
    """Return a DataFrame: sensor, status, detail, last_valid_value, last_valid_time.

    now: current time for live data. When given, a sensor whose last valid
    reading is older than STALE_AFTER_S is reported as stale.
    """
    flags = classify(df)
    times = pd.to_datetime(df["timestamp"])
    rows = []
    for name, spec in SENSORS.items():
        row = {"sensor": spec["label"], "unit": spec["unit"], "last_valid_value": None,
               "last_valid_time": None}
        if name not in flags:
            row.update(status="Unavailable", detail="No such field in the data.")
            rows.append(row)
            continue
        f = flags[name]
        invalid = f["missing"] | f["out_of_range"] | f["sentinel"]
        valid = ~invalid
        if not valid.any():
            row.update(status="Unavailable", detail="No valid reading in this dataset.")
            rows.append(row)
            continue
        last = valid[valid].index[-1]
        row["last_valid_value"] = float(pd.to_numeric(df[name], errors="coerce").loc[last])
        row["last_valid_time"] = times.loc[last]
        recent = invalid.tail(10)
        if recent.all() or invalid.mean() > 0.05:
            parts = [f"{int(f[k].sum())} {k.replace('_', ' ')}" for k in
                     ("missing", "out_of_range", "sentinel") if f[k].any()]
            row.update(status="Invalid readings", detail=", ".join(parts) + f" of {len(df)}.")
        elif now is not None and (pd.Timestamp(now) - times.loc[last]).total_seconds() > STALE_AFTER_S:
            row.update(status="Stale",
                       detail=f"No valid reading for more than {STALE_AFTER_S:.0f} s.")
        elif f["frozen"].tail(1).item():
            row.update(status="Not updating",
                       detail="The latest readings are identical; the sensor may be stuck.")
        else:
            row.update(status="Reporting — calibration not verified", detail=spec["note"])
        rows.append(row)
    return pd.DataFrame(rows).set_index("sensor")
