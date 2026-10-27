"""Isolation Forest anomaly detector: training, saving, loading and scoring.

What the model does: it learns what combinations of feature values were common
in the training windows. A new window gets an anomaly score; a higher score
means "less like the training data". It does NOT know what a fault looks like
and cannot name a cause. If the training data did not cover an operating
condition (another speed, load or room temperature), that condition will look
anomalous even when the motor is fine.

Decision threshold: the 99th percentile (configurable) of the training
windows' own scores. By construction about 1 % of training windows exceed it.

Every saved model has a JSON record next to it (version, date, features,
training data, settings) so any score can be traced to the model behind it.
"""
import json
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import config
from config import SOURCE_REAL, SOURCE_SYNTHETIC
from src.processing.feature_pipeline import feature_columns

MIN_TRAIN_WINDOWS = 20
SMALL_TRAIN_WINDOWS = 100


class ModelError(ValueError):
    """Training or scoring was refused; the message explains why."""


def train_model(features, *, data_source, training_id, operating_notes="",
                window_s=60, n_estimators=200, threshold_quantile=0.99, seed=42):
    """Fit on windows believed to be healthy. Returns (pipeline, meta)."""
    if data_source not in (SOURCE_REAL, SOURCE_SYNTHETIC):
        raise ModelError(f"Training data must be entirely real or entirely synthetic, "
                         f"not '{data_source}'.")
    cols = [c for c in feature_columns(features) if features[c].notna().any()]
    if not cols:
        raise ModelError("No usable features in the training data.")
    X = features[cols].dropna(how="all")
    if len(X) < MIN_TRAIN_WINDOWS:
        raise ModelError(f"Only {len(X)} usable windows; at least {MIN_TRAIN_WINDOWS} are "
                         f"needed ({MIN_TRAIN_WINDOWS * window_s / 60:.0f} minutes of data).")

    warnings = []
    if len(X) < SMALL_TRAIN_WINDOWS:
        warnings.append(f"Small training set ({len(X)} windows). The model has seen little "
                        "normal variation and will raise false alarms.")
    if data_source == SOURCE_SYNTHETIC:
        warnings.append("Trained on SYNTHETIC data. This model is for software testing "
                        "only and cannot be used on real measurements.")
    flat = [c for c in cols if X[c].std(skipna=True) == 0]
    if flat:
        warnings.append(f"Feature(s) never changed in training: {', '.join(flat)}.")

    pipeline = Pipeline([
        ("impute", SimpleImputer(strategy="median")),   # fill gaps with the training median
        ("scale", StandardScaler()),
        ("forest", IsolationForest(n_estimators=n_estimators, random_state=seed)),
    ])
    pipeline.fit(X)
    train_scores = -pipeline.score_samples(X)

    now = datetime.now()
    meta = {
        "model_version": now.strftime("ifm_%Y%m%d_%H%M%S_%f")[:-3],
        "trained_at": now.isoformat(timespec="seconds"),
        "data_source": data_source,
        "training_id": training_id,
        "operating_notes": operating_notes,
        "features": cols,
        "n_windows": int(len(X)),
        "threshold": float(np.quantile(train_scores, threshold_quantile)),
        "config": {"window_s": window_s, "n_estimators": n_estimators, "seed": seed,
                   "threshold_quantile": threshold_quantile,
                   "preprocessing": "median imputation, then standard scaling",
                   "sklearn_version": sklearn.__version__},
        # Used by the drift monitor.
        "feature_baseline": {c: {"median": float(X[c].median()),
                                 "spread": float(1.4826 * (X[c] - X[c].median()).abs().median()),
                                 "p01": float(X[c].quantile(0.01)),
                                 "p99": float(X[c].quantile(0.99))} for c in cols},
        "warnings": warnings,
        "evaluation": None,
    }
    return pipeline, meta


def _dir(models_dir=None):
    return models_dir or config.MODELS_DIR


def save_model(pipeline, meta, models_dir=None):
    folder = _dir(models_dir)
    folder.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, folder / f"{meta['model_version']}.joblib")
    save_meta(meta, models_dir)
    return meta["model_version"]


def save_meta(meta, models_dir=None):
    path = _dir(models_dir) / f"{meta['model_version']}.json"
    path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")


def load_model(version, models_dir=None):
    folder = _dir(models_dir)
    meta = json.loads((folder / f"{version}.json").read_text(encoding="utf-8"))
    return joblib.load(folder / f"{version}.joblib"), meta


def list_models(models_dir=None):
    """Metadata of every saved model, newest first."""
    folder = _dir(models_dir)
    if not folder.exists():
        return []
    metas = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("ifm_*.json")
             if p.with_suffix(".joblib").exists()]
    return sorted(metas, key=lambda m: m["model_version"], reverse=True)


def score_features(pipeline, meta, features, data_source):
    """Score windows. Returns (scores_df, missing_features).

    scores_df columns: window_start, anomaly_score, anomaly_prediction (1 = above
    the decision threshold, 0 = not, NaN = window had no usable data),
    model_version.
    """
    if data_source != meta["data_source"]:
        raise ModelError(
            f"Model {meta['model_version']} was trained on '{meta['data_source']}' data and "
            f"cannot score '{data_source}' data. Train a model on a matching recording.")
    cols = meta["features"]
    X = features.reindex(columns=cols)
    missing = [c for c in cols if X[c].isna().all()]
    usable = X.notna().any(axis=1)
    scores = pd.Series(np.nan, index=features.index)
    if usable.any():
        scores[usable] = -pipeline.score_samples(X[usable])
    return pd.DataFrame({
        "window_start": features["window_start"],
        "anomaly_score": scores,
        "anomaly_prediction": (scores > meta["threshold"]).astype(float).where(scores.notna()),
        "model_version": meta["model_version"],
    }), missing
