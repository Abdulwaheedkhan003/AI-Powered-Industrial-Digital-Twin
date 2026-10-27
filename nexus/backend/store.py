"""Local SQLite storage: telemetry history, events, diagnoses, work orders,
device commands and settings.

SQLite is a single local file with no server to run. Records are ordinary
rows: this is an activity log, not a tamper-proof audit trail.
"""
import json
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (asset_id TEXT, ts REAL, data_mode TEXT, vals TEXT, quality TEXT);
CREATE INDEX IF NOT EXISTS telemetry_idx ON telemetry (asset_id, ts);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, asset_id TEXT,
    category TEXT, severity TEXT, data_mode TEXT, origin TEXT, title TEXT, detail TEXT,
    scenario_id TEXT, ref TEXT, ack_by TEXT, ack_ts REAL);
CREATE TABLE IF NOT EXISTS diagnoses (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, asset_id TEXT,
    data_mode TEXT, status TEXT, payload TEXT, reviewed_by TEXT, reviewed_ts REAL);
CREATE TABLE IF NOT EXISTS work_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, updated REAL,
    asset_id TEXT, data_mode TEXT, diagnosis_id INTEGER, title TEXT, state TEXT, priority TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS commands (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, device_id TEXT,
    name TEXT, params TEXT, state TEXT, issued_by TEXT, detail TEXT, event_ref TEXT, seq INTEGER, done_ts REAL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""
JSON_COLUMNS = {"vals", "quality", "payload", "params"}


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)
            self.db.commit()

    # ---- helpers ------------------------------------------------------------
    def _run(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid

    def _rows(self, sql, args=()):
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            for key in JSON_COLUMNS & item.keys():
                item[key] = json.loads(item[key]) if item[key] else None
            out.append(item)
        return out

    def ping(self):
        self._rows("SELECT 1")
        return True

    # ---- settings -----------------------------------------------------------
    def get_setting(self, key, default=None):
        rows = self._rows("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key, value):
        self._run("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value)))

    # ---- telemetry ----------------------------------------------------------
    def add_telemetry(self, asset_id, ts, data_mode, values, quality=None):
        self._run("INSERT INTO telemetry VALUES(?,?,?,?,?)",
                  (asset_id, ts, data_mode, json.dumps(values), json.dumps(quality or [])))

    def history(self, asset_id, since, until=None, limit=20000):
        until = until or time.time() + 1
        rows = self._rows("SELECT ts, data_mode, vals, quality FROM telemetry WHERE asset_id=? AND ts>=? AND ts<=? "
                          "ORDER BY ts DESC LIMIT ?", (asset_id, since, until, limit))
        return rows[::-1]

    def prune_telemetry(self, older_than, data_modes=("SIMULATED", "REPLAY")):
        marks = ",".join("?" * len(data_modes))
        self._run(f"DELETE FROM telemetry WHERE ts<? AND data_mode IN ({marks})", (older_than, *data_modes))

    # ---- events -------------------------------------------------------------
    def add_event(self, asset_id, category, severity, data_mode, origin, title, detail="",
                  scenario_id=None, ref=None, ts=None):
        return self._run("INSERT INTO events(ts,asset_id,category,severity,data_mode,origin,title,detail,scenario_id,ref) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (ts or time.time(), asset_id, category, severity, data_mode, origin, title, detail,
                          scenario_id, ref))

    def events(self, limit=200, **filters):
        where, args = [], []
        for column in ("asset_id", "category", "severity", "data_mode", "origin", "scenario_id"):
            if filters.get(column):
                where.append(f"{column}=?")
                args.append(filters[column])
        if filters.get("since"):
            where.append("ts>=?")
            args.append(filters["since"])
        if filters.get("until"):
            where.append("ts<=?")
            args.append(filters["until"])
        if filters.get("acknowledged") is True:
            where.append("ack_ts IS NOT NULL")
        if filters.get("acknowledged") is False:
            where.append("ack_ts IS NULL")
        if filters.get("search"):
            where.append("(title LIKE ? OR detail LIKE ?)")
            args += [f"%{filters['search']}%"] * 2
        sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
        return self._rows(sql, (*args, limit))

    def ack_event(self, event_id, user):
        self._run("UPDATE events SET ack_by=?, ack_ts=? WHERE id=? AND ack_ts IS NULL", (user, time.time(), event_id))
        rows = self._rows("SELECT * FROM events WHERE id=?", (event_id,))
        return rows[0] if rows else None

    # ---- diagnoses ----------------------------------------------------------
    def add_diagnosis(self, asset_id, data_mode, status, payload):
        return self._run("INSERT INTO diagnoses(ts,asset_id,data_mode,status,payload) VALUES(?,?,?,?,?)",
                         (time.time(), asset_id, data_mode, status, json.dumps(payload)))

    def diagnoses(self, asset_id=None, limit=50, diagnosis_id=None):
        if diagnosis_id is not None:
            return self._rows("SELECT * FROM diagnoses WHERE id=?", (diagnosis_id,))
        if asset_id:
            return self._rows("SELECT * FROM diagnoses WHERE asset_id=? ORDER BY id DESC LIMIT ?", (asset_id, limit))
        return self._rows("SELECT * FROM diagnoses ORDER BY id DESC LIMIT ?", (limit,))

    def review_diagnosis(self, diagnosis_id, user):
        self._run("UPDATE diagnoses SET reviewed_by=?, reviewed_ts=? WHERE id=?", (user, time.time(), diagnosis_id))

    # ---- work orders --------------------------------------------------------
    def add_work_order(self, asset_id, data_mode, diagnosis_id, title, priority, payload):
        now = time.time()
        return self._run("INSERT INTO work_orders(created,updated,asset_id,data_mode,diagnosis_id,title,state,priority,payload) "
                         "VALUES(?,?,?,?,?,?,?,?,?)",
                         (now, now, asset_id, data_mode, diagnosis_id, title, "NEW", priority, json.dumps(payload)))

    def work_orders(self, work_order_id=None):
        if work_order_id is not None:
            return self._rows("SELECT * FROM work_orders WHERE id=?", (work_order_id,))
        return self._rows("SELECT * FROM work_orders ORDER BY id DESC")

    def update_work_order(self, work_order_id, state, payload):
        self._run("UPDATE work_orders SET state=?, payload=?, updated=? WHERE id=?",
                  (state, json.dumps(payload), time.time(), work_order_id))

    # ---- device commands ----------------------------------------------------
    def add_command(self, device_id, name, params, state, issued_by, detail="", event_ref=None, seq=None):
        return self._run("INSERT INTO commands(ts,device_id,name,params,state,issued_by,detail,event_ref,seq) "
                         "VALUES(?,?,?,?,?,?,?,?,?)",
                         (time.time(), device_id, name, json.dumps(params), state, issued_by, detail, event_ref, seq))

    def update_command(self, command_id, state, detail):
        self._run("UPDATE commands SET state=?, detail=?, done_ts=? WHERE id=?", (state, detail, time.time(), command_id))

    def commands(self, limit=50, command_id=None):
        if command_id is not None:
            return self._rows("SELECT * FROM commands WHERE id=?", (command_id,))
        return self._rows("SELECT * FROM commands ORDER BY id DESC LIMIT ?", (limit,))
