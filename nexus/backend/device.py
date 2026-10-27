"""Two-way link to the ESP32 over USB serial.

Device -> backend lines
  telemetry : Vibration:0.12<TAB>Temperature_C:30.25<TAB>...[<TAB>RPM:1480][<TAB>Seq:123]
  burst     : A<TAB>burst_no<TAB>micros<TAB>ax<TAB>ay<TAB>az         (firmware v2+)
  ack       : ACK<TAB>command_id<TAB>OK|ERR<TAB>detail               (firmware v3)
  status    : STATUS<TAB>device=ESP32-NODE-01<TAB>fw=3.0.0<TAB>...   (firmware v3)

Backend -> device line (firmware v3 only)
  CMD<TAB>seq<TAB>command_id<TAB>NAME<TAB>argument

The telemetry parser and the sensor limits are reused from the existing
predictive_maintenance project, so both tools read the board identically.
The device has no clock: every sample is stamped with the PC time at arrival.

Trust model: a USB cable is a point-to-point link, so commands are not
encrypted or signed on the wire. The backend decides who may send what. A
network link (Wi-Fi/MQTT) would need TLS and signed commands first.
"""
import sys
import threading
import time
from collections import deque

from settings import PM_DIR

sys.path.insert(0, str(PM_DIR))
from config import SENSORS                                   # noqa: E402  (existing project)
from src.ingestion.line_parser import parse_accel_line, parse_line   # noqa: E402
from src.processing.vibration_features import burst_features  # noqa: E402

try:
    import pandas as pd
    import serial
    from serial.tools import list_ports
except ImportError:                                           # pragma: no cover
    serial = None

EXTRA_FIELDS = {"RPM": ("rpm", 0.0, 30000.0), "Seq": ("seq", 0.0, 4e9)}
COMMANDS = {"PING": None, "REQUEST_STATUS": None, "BUZZER_ALERT": (50, 2000)}   # name -> allowed argument range


def available_ports():
    if serial is None:
        return []
    return [{"device": p.device, "description": p.description} for p in list_ports.comports()]


def validate_command(name, argument=None):
    """Return the cleaned argument, or raise ValueError. Unknown commands are refused."""
    if name not in COMMANDS:
        raise ValueError(f"Command '{name}' is not on the allowlist")
    bounds = COMMANDS[name]
    if bounds is None:
        if argument not in (None, 0, ""):
            raise ValueError(f"{name} takes no argument")
        return 0
    if isinstance(argument, bool) or not isinstance(argument, int):
        raise ValueError(f"{name} needs a whole number of milliseconds")
    if not bounds[0] <= argument <= bounds[1]:
        raise ValueError(f"{name} duration must be between {bounds[0]} and {bounds[1]} ms")
    return argument


def parse_telemetry(line):
    """Return (values, invalid, problems). Invalid readings are removed, not shown."""
    extras, parts = {}, []
    for part in line.split("\t"):
        key = part.split(":", 1)[0].strip()
        if key in EXTRA_FIELDS:
            extras[key] = part.split(":", 1)[1] if ":" in part else ""
        else:
            parts.append(part)
    values, problems = parse_line("\t".join(parts))
    invalid = []
    for name, value in list(values.items()):
        if value is None:
            continue
        spec = SENSORS[name]
        if value in spec["sentinels"] or not spec["min"] <= value <= spec["max"]:
            invalid.append(name)
            values[name] = None
    for key, (name, low, high) in EXTRA_FIELDS.items():
        if key not in extras:
            continue
        try:
            number = float(extras[key])
            if not low <= number <= high:
                raise ValueError
            values[name] = number
        except ValueError:
            values[name] = None
            invalid.append(name)
    return values, invalid, problems


class DeviceLink:
    def __init__(self, opener=None, retry_s=2.0):
        self.opener = opener
        self.retry_s = retry_s
        self.status = "DISCONNECTED"      # DISCONNECTED | CONNECTING | CONNECTED | RECONNECTING
        self.message = "Not connected."
        self.port, self.baud = None, None
        self.lines_ok = self.lines_bad = self.missed_sequences = 0
        self.last_line_ts = None
        self.firmware_version = None       # only known if the device reports it
        self.reported_device_id = None
        self.capabilities = {}
        self.recent_problems = deque(maxlen=30)
        self._samples, self._acks = [], []
        self._burst, self._burst_no, self._last_burst = [], None, None
        self._last_seq = None
        self._conn = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    # ---- incoming -----------------------------------------------------------
    def process_line(self, raw, now=None):
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        line = raw.strip()
        if not line:
            return
        now = now or time.time()
        with self._lock:
            if line.startswith("ACK\t"):
                parts = line.split("\t")
                if len(parts) >= 3 and parts[2] in ("OK", "ERR"):
                    self._acks.append({"command_id": parts[1], "ok": parts[2] == "OK",
                                       "detail": parts[3] if len(parts) > 3 else "", "ts": now})
                    self.last_line_ts = now
                else:
                    self._bad(line)
            elif line.startswith("STATUS\t"):
                info = dict(p.split("=", 1) for p in line.split("\t")[1:] if "=" in p)
                self.firmware_version = info.get("fw", self.firmware_version)
                self.reported_device_id = info.get("device", self.reported_device_id)
                self.capabilities = {k: v for k, v in info.items() if k not in ("fw", "device")}
                self.last_line_ts = now
            elif line.startswith("A\t"):
                self._handle_burst(line, now)
            else:
                self._handle_telemetry(line, now)

    def _bad(self, line):
        self.lines_bad += 1
        self.recent_problems.append(f"ignored: {line[:60]!r}")

    def _handle_telemetry(self, line, now):
        values, invalid, problems = parse_telemetry(line)
        if all(v is None for k, v in values.items() if k != "seq") and not invalid:
            self._bad(line)                # boot text or a line garbled beyond use
            return
        seq = values.pop("seq", None)
        if seq is not None and self._last_seq is not None and seq > self._last_seq + 1:
            self.missed_sequences += int(seq - self._last_seq - 1)
        self._last_seq = seq if seq is not None else self._last_seq
        if self._last_burst is not None:   # per-burst vibration RMS from raw acceleration
            values["vib_rms_g"] = self._last_burst
            self._last_burst = None
        if problems:
            self.recent_problems.append("; ".join(problems))
        self._samples.append({"ts": now, "values": values, "invalid": invalid, "problems": problems})
        self.lines_ok += 1
        self.last_line_ts = now

    def _handle_burst(self, line, now):
        row = parse_accel_line(line)
        if row is None:
            self._bad(line)
            return
        if row["burst_no"] != self._burst_no:
            self._finish_burst()
            self._burst_no = row["burst_no"]
        self._burst.append(row)
        self.lines_ok += 1
        self.last_line_ts = now

    def _finish_burst(self):
        if len(self._burst) >= 32:
            features = burst_features(pd.DataFrame(self._burst))
            if features["valid"]:
                self._last_burst = round(features["rms_total_g"], 5)
        self._burst = []

    def drain(self):
        with self._lock:
            if self._burst and self.last_line_ts and time.time() - self.last_line_ts > 0.5:
                self._finish_burst()
            samples, acks = self._samples, self._acks
            self._samples, self._acks = [], []
        return samples, acks

    # ---- outgoing -----------------------------------------------------------
    def send_command(self, seq, command_id, name, argument):
        """Write one command line. Returns (written, detail). Never claims execution."""
        conn = self._conn
        if self.status != "CONNECTED" or conn is None:
            return False, "device not connected"
        try:
            conn.write(f"CMD\t{seq}\t{command_id}\t{name}\t{argument}\n".encode())
            return True, "sent, waiting for device acknowledgement"
        except Exception as err:
            return False, f"write failed: {err}"

    # ---- connection ---------------------------------------------------------
    def connect(self, port, baud=115200):
        self.disconnect()
        self.port, self.baud = port, baud
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def disconnect(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None
        self.status, self.message = "DISCONNECTED", "Not connected."

    def _open(self):
        if self.opener:
            return self.opener()
        if serial is None:
            raise RuntimeError("pyserial is not installed")
        return serial.Serial(self.port, self.baud, timeout=1)

    def _run(self):
        first = True
        while not self._stop.is_set():
            self.status = "CONNECTING" if first else "RECONNECTING"
            self.message = f"Opening {self.port}…"
            first = False
            try:
                conn = self._open()
            except Exception as err:
                self.status, self.message = "RECONNECTING", f"Cannot open {self.port}: {err}"
                self._stop.wait(self.retry_s)
                continue
            self._conn = conn
            self.status, self.message = "CONNECTED", f"Port {self.port} open at {self.baud} baud."
            try:
                while not self._stop.is_set():
                    raw = conn.readline()
                    if raw:
                        self.process_line(raw)
            except Exception as err:
                self.status, self.message = "RECONNECTING", f"Link to {self.port} lost: {err}"
            finally:
                self._conn = None
                try:
                    conn.close()
                except Exception:
                    pass
            self._stop.wait(self.retry_s)
