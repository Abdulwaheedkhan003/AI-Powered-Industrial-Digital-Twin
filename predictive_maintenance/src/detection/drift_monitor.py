"""Basic drift check: do new windows still look like the training data?

For each model feature:
  shift            = (median of new windows - training median) / training spread
  outside_fraction = share of new windows outside the training 1 %–99 % range
A feature is reported as drifted when |shift| > 3 AND more than half of the new
windows fall outside the training range, i.e. the change is large and
persistent rather than a few odd windows.

Drift is not a fault. It also appears when the motor speed or load changed,
the sensor was moved or remounted, the room got warmer or cooler, or the
supply voltage changed. It means the model's idea of "normal" may no longer
apply and its anomaly scores should be read with caution.
"""
import pandas as pd

MIN_WINDOWS = 10
SHIFT_LIMIT = 3.0
OUTSIDE_LIMIT = 0.5


def drift_report(features, meta):
    """Return (table, message). table is None when there is too little data."""
    rows = []
    for name, base in meta["feature_baseline"].items():
        if name not in features.columns:
            rows.append({"feature": name, "status": "not available in new data"})
            continue
        values = features[name].dropna()
        if len(values) < MIN_WINDOWS:
            rows.append({"feature": name, "windows": len(values),
                         "status": f"too few windows (need {MIN_WINDOWS})"})
            continue
        outside = float(((values < base["p01"]) | (values > base["p99"])).mean())
        row = {"feature": name, "windows": len(values),
               "training_median": base["median"], "new_median": float(values.median()),
               "outside_fraction": outside}
        if base["spread"] == 0:
            row["shift"] = float("nan")
            drifted = outside > OUTSIDE_LIMIT
        else:
            row["shift"] = (row["new_median"] - base["median"]) / base["spread"]
            drifted = abs(row["shift"]) > SHIFT_LIMIT and outside > OUTSIDE_LIMIT
        row["status"] = "drifted" if drifted else "similar to training"
        rows.append(row)
    table = pd.DataFrame(rows).set_index("feature")
    drifted = table.index[table["status"] == "drifted"].tolist()
    if drifted:
        message = ("Persistent difference from the training data in: " + ", ".join(drifted)
                   + ". Possible reasons include a change of speed, load, sensor position, "
                     "ambient conditions, or the motor's condition. This is not a fault "
                     "diagnosis.")
    elif (table["status"] == "similar to training").any():
        message = "No persistent difference from the training data was found."
    else:
        message = "Not enough data to assess drift."
    return table, message
