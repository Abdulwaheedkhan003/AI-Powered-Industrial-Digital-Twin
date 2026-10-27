"""Configuration and secrets.

Secrets are never in source code. On first start a local `nexus/.env` file is
created with a random signing secret and random passwords; edit that file to
change them. Environment variables override the file.
"""
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                 # .../nexus
PM_DIR = ROOT.parent / "predictive_maintenance"            # existing analysis project
ENV_FILE = ROOT / ".env"


@dataclass
class Settings:
    secret: str
    admin_password: str
    operator_password: str
    data_dir: Path
    serial_port: str = ""
    serial_baud: int = 115200
    token_ttl_s: int = 8 * 3600
    stale_after_s: float = 5.0
    offline_after_s: float = 30.0
    command_timeout_s: float = 3.0
    demo_fill: bool = False      # generate plausible motor readings while the ESP32 is silent
    auto_faults: bool = False    # let one virtual machine develop a fault at random
    open_local: bool = False     # requests from this computer need no sign-in (full access)


def _read_env_file(path):
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def ensure_env_file(path=ENV_FILE):
    """Create the local secrets file on first run. Returns True if it was created."""
    if path.exists():
        return False
    path.write_text(
        "# Local secrets for NEXUS. Do not commit or share this file.\n"
        f"NEXUS_SECRET={secrets.token_urlsafe(32)}\n"
        f"NEXUS_ADMIN_PASSWORD={secrets.token_urlsafe(9)}\n"
        f"NEXUS_OPERATOR_PASSWORD={secrets.token_urlsafe(9)}\n"
        "# Optional: connect to the ESP32 automatically at start-up, e.g. COM5\n"
        "NEXUS_SERIAL_PORT=\n"
        "NEXUS_SERIAL_BAUD=115200\n", encoding="utf-8")
    return True


def load_settings(env_file=ENV_FILE):
    file_values = _read_env_file(env_file)

    def get(key, default=""):
        return os.environ.get(key, file_values.get(key, default))

    return Settings(
        secret=get("NEXUS_SECRET"),
        admin_password=get("NEXUS_ADMIN_PASSWORD"),
        operator_password=get("NEXUS_OPERATOR_PASSWORD"),
        data_dir=Path(get("NEXUS_DATA_DIR", str(ROOT / "data"))),
        serial_port=get("NEXUS_SERIAL_PORT"),
        serial_baud=int(get("NEXUS_SERIAL_BAUD", "115200") or 115200),
        demo_fill=get("NEXUS_DEMO_FILL", "1") != "0",
        auto_faults=get("NEXUS_AUTO_FAULTS", "1") != "0",
        open_local=get("NEXUS_OPEN_LOCAL", "1") != "0",
    )
