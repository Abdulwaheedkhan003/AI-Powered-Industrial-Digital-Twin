"""Maintenance notes kept in a local CSV file.

A note is the operator's observation. It is stored as unconfirmed unless the
operator explicitly marks it as a confirmed diagnosis, and only confirmed
notes may ever be used as ground truth for evaluating the detectors.
"""
import uuid
from datetime import datetime

import pandas as pd

import config

NOTE_KINDS = ["Observed symptom", "Inspection note", "Maintenance action", "Session comment"]
NOTE_COLUMNS = ["note_id", "created_at", "session_id", "kind", "text", "confirmed_diagnosis"]


def _path(path=None):
    return path or config.STATE_DIR / "notes.csv"


def load_notes(path=None, session_id=None):
    path = _path(path)
    if not path.exists():
        return pd.DataFrame(columns=NOTE_COLUMNS)
    notes = pd.read_csv(path, dtype=str, keep_default_na=False)
    notes["confirmed_diagnosis"] = notes["confirmed_diagnosis"] == "True"
    if session_id is not None:
        notes = notes[notes["session_id"] == session_id]
    return notes.reset_index(drop=True)


def add_note(session_id, kind, text, confirmed_diagnosis=False, path=None):
    if kind not in NOTE_KINDS:
        raise ValueError(f"kind must be one of {NOTE_KINDS}")
    if not text.strip():
        raise ValueError("The note is empty.")
    path = _path(path)
    note = {"note_id": uuid.uuid4().hex[:10],
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "session_id": session_id, "kind": kind, "text": text.strip(),
            "confirmed_diagnosis": bool(confirmed_diagnosis)}
    notes = pd.concat([load_notes(path), pd.DataFrame([note])], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    notes.to_csv(path, index=False)
    return note
