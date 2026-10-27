"""Read the ESP32's serial output in a background thread.

Works with both firmware versions:
  * original sketch : one "Key:value" line about once per second;
  * firmware v2     : the same line (plus LiDAR / analog LDR if enabled) and
                      "A ..." lines carrying raw acceleration bursts.

The reader never raises into the application. If the port cannot be opened or
the cable is pulled out, it reports the problem in `status` / `message` and
keeps retrying until stop() is called.

Every row produced here is labelled real_hardware and stamped with the PC
clock at the moment the line arrived (the firmware sends no clock time).
"""
import threading
from collections import deque
from datetime import datetime

import pandas as pd

from config import (ACCEL_COLUMNS, ACCEL_LOG_COLUMNS, CORE_FIRMWARE_KEYS,
                    FIRMWARE_KEY_MAP, META_COLUMNS, SENSOR_COLUMNS, SERIAL_BAUD,
                    SOURCE_REAL)
from src.ingestion.line_parser import parse_accel_line, parse_line

try:
    import serial
    from serial.tools import list_ports
except ImportError:      # pyserial not installed: hardware mode is unavailable
    serial = None

_CORE_COLUMNS = [FIRMWARE_KEY_MAP[k] for k in CORE_FIRMWARE_KEYS]


def available_ports():
    """List of (device, description), e.g. ('COM5', 'Silicon Labs CP210x ...')."""
    if serial is None:
        return []
    return [(p.device, p.description) for p in list_ports.comports()]


class SerialReader:
    def __init__(self, port, baud=SERIAL_BAUD, session_id=None, opener=None,
                 retry_s=2.0):
        self.port = port
        self.baud = baud
        self.session_id = session_id or datetime.now().strftime("real_%Y%m%d_%H%M%S")
        self.retry_s = retry_s
        self._opener = opener or self._open_port
        self.status = "stopped"     # stopped | connecting | connected | disconnected
        self.message = ""
        self.lines_ok = 0
        self.lines_bad = 0
        self.last_line_time = None
        self.recent_problems = deque(maxlen=20)
        self._slow, self._accel = [], []
        self._burst_id, self._burst_no, self._burst_time = -1, None, None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    # ---- line handling (no hardware needed, used directly by the tests) ----
    def process_line(self, raw):
        """Parse one received line and queue the resulting row."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        line = raw.strip()
        if not line:
            return
        now = datetime.now()
        with self._lock:
            if line.startswith("A\t"):
                self._handle_accel(line, now)
            else:
                self._handle_slow(line, now)

    def _handle_slow(self, line, now):
        values, problems = parse_line(line)
        if all(values[c] is None for c in _CORE_COLUMNS):
            # e.g. ESP32 boot messages or a line garbled beyond use
            self.lines_bad += 1
            self.recent_problems.append(f"ignored line: {line[:60]!r}")
            return
        if problems:
            self.recent_problems.append("; ".join(problems))
        self._slow.append({"timestamp": now, "session_id": self.session_id,
                           "data_source": SOURCE_REAL, "scenario": "", **values})
        self.lines_ok += 1
        self.last_line_time = now

    def _handle_accel(self, line, now):
        row = parse_accel_line(line)
        if row is None:
            self.lines_bad += 1
            self.recent_problems.append(f"bad acceleration line: {line[:60]!r}")
            return
        if row["burst_no"] != self._burst_no:     # a new burst has started
            self._burst_no = row["burst_no"]
            self._burst_id += 1
            self._burst_time = now
        self._accel.append({"burst_id": self._burst_id, "burst_time": self._burst_time,
                            "t_us": row["t_us"], **{c: row[c] for c in ACCEL_COLUMNS},
                            "session_id": self.session_id, "data_source": SOURCE_REAL,
                            "scenario": ""})
        self.lines_ok += 1
        self.last_line_time = now

    def drain(self):
        """Return (slow_df, accel_df) with the rows received since the last call."""
        with self._lock:
            slow, accel = self._slow, self._accel
            self._slow, self._accel = [], []
        slow_df = pd.DataFrame(slow, columns=META_COLUMNS + SENSOR_COLUMNS)
        # Missing fields arrive as None; store them as NaN (blank in the CSV).
        slow_df[SENSOR_COLUMNS] = slow_df[SENSOR_COLUMNS].astype(float)
        return slow_df, pd.DataFrame(accel, columns=ACCEL_LOG_COLUMNS)

    def seconds_since_last_line(self):
        if self.last_line_time is None:
            return None
        return (datetime.now() - self.last_line_time).total_seconds()

    # ---- connection handling -------------------------------------------------
    def _open_port(self):
        if serial is None:
            raise RuntimeError("pyserial is not installed (pip install pyserial)")
        return serial.Serial(self.port, self.baud, timeout=1)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.status, self.message = "stopped", ""

    def _run(self):
        while not self._stop.is_set():
            self.status, self.message = "connecting", f"Opening {self.port}…"
            try:
                conn = self._opener()
            except Exception as err:
                self.status = "disconnected"
                self.message = f"Cannot open {self.port}: {err}. Retrying…"
                self._stop.wait(self.retry_s)
                continue
            self.status = "connected"
            self.message = f"Connected to {self.port} at {self.baud} baud."
            try:
                while not self._stop.is_set():
                    raw = conn.readline()
                    if raw:
                        self.process_line(raw)
            except Exception as err:
                self.status = "disconnected"
                self.message = f"Connection to {self.port} lost: {err}. Retrying…"
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            self._stop.wait(self.retry_s)
