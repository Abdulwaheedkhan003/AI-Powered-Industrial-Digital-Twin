"""NEXUS backend: REST API, WebSocket stream and the built web app.

Run from this folder:   python -m uvicorn main:app --port 8000
"""
import asyncio
import contextvars
import csv
import io
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent))
from device import available_ports                      # noqa: E402
from engine import WORK_ORDER_FLOW, Engine, EngineError  # noqa: E402
from security import AuthError, Security                # noqa: E402
from settings import PM_DIR, ROOT, ensure_env_file, load_settings   # noqa: E402
from simulation import SCENARIOS                        # noqa: E402
from store import Store                                 # noqa: E402
import analysis                                         # noqa: E402

CLIENT = contextvars.ContextVar("client_host", default="")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}

DIST = ROOT / "frontend" / "dist"


class Login(BaseModel):
    username: str = Field(max_length=40)
    password: str = Field(max_length=200)


class SimStart(BaseModel):
    scenario_id: str = Field(max_length=60)
    severity: float = Field(0.7, ge=0.1, le=1.0)


class Command(BaseModel):
    name: str = Field(max_length=40)
    duration_ms: Optional[int] = None


class Connect(BaseModel):
    port: str = Field(max_length=60)
    baud: int = Field(115200, ge=1200, le=2000000)


class WorkOrderNew(BaseModel):
    asset_id: str = Field(max_length=40)
    title: str = Field(min_length=3, max_length=200)
    priority: str = Field("MEDIUM", pattern="^(LOW|MEDIUM|HIGH)$")
    diagnosis_id: Optional[int] = None


class WorkOrderUpdate(BaseModel):
    state: Optional[str] = Field(None, max_length=40)
    note: Optional[str] = Field(None, max_length=2000)


class Notifications(BaseModel):
    enabled: bool


class Limits(BaseModel):
    limits: dict


class DemoRequest(BaseModel):
    scenario: str = Field(max_length=60)
    duration_s: int = Field(900, ge=60, le=3600)
    seed: int = Field(42)


class TrainRequest(BaseModel):
    session_file: str = Field(max_length=500)
    conditions: str = Field("", max_length=500)


class PMSettings(BaseModel):
    settings: dict


def default_replay_file(data_dir):
    """A real recording if one exists, otherwise a clearly labelled synthetic one."""
    real = sorted((PM_DIR / "data" / "raw").glob("*.csv")) if (PM_DIR / "data" / "raw").exists() else []
    real = [p for p in real if not p.name.endswith("_accel.csv")]
    if real:
        return real[-1]
    path = Path(data_dir) / "replay" / "SYNTHETIC_replay_demo.csv"
    if not path.exists():
        from src.ingestion.data_generator import generate_demo_data
        path.parent.mkdir(parents=True, exist_ok=True)
        generate_demo_data("increasing_vibration", duration_s=600, seed=7).to_csv(path, index=False)
    return path


def create_app(settings=None, link=None, run_loop=True, raw_export_dir=None, replay_file=None):
    if settings is None:
        if ensure_env_file():
            print("Created nexus/.env with a random signing secret and passwords. Open that file to see them.")
        settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.data_dir / "nexus.sqlite3")
    engine = Engine(settings, store, link, raw_export_dir, replay_file)
    security = Security(settings)
    clients = set()

    async def loop():
        while True:
            started = time.time()
            engine.tick()
            message = engine.snapshot()
            for ws in list(clients):
                try:
                    await ws.send_json(message)
                except Exception:
                    clients.discard(ws)
            await asyncio.sleep(max(0.05, 1.0 - (time.time() - started)))

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(loop()) if run_loop else None
        if run_loop and settings.serial_port:
            engine.link.connect(settings.serial_port, settings.serial_baud)
        yield
        if task:
            task.cancel()
        engine.link.disconnect()

    app = FastAPI(title="NEXUS Industrial Intelligence", lifespan=lifespan)
    app.state.engine, app.state.store = engine, store

    @app.middleware("http")
    async def remember_client(request, call_next):
        CLIENT.set(request.client.host if request.client else "")
        return await call_next(request)
    # the Vite dev server is the only other origin allowed to call the API
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(AuthError)
    async def auth_error(_request, err):
        return JSONResponse({"detail": err.message}, status_code=err.status)

    @app.exception_handler(EngineError)
    async def engine_error(_request, err):
        return JSONResponse({"detail": err.message}, status_code=err.status)

    def who(authorization, role="viewer"):
        identity = security.identify(authorization)
        if identity["role"] == "viewer" and getattr(settings, "open_local", False) and CLIENT.get() in LOOPBACK:
            return "local user"              # no sign-in on this computer; other machines still need one
        security.require(identity, role)
        return identity["user"]

    # ---- auth -----------------------------------------------------------------
    @app.post("/api/auth/login")
    def login(body: Login, request: Request):
        return security.login(body.username, body.password, request.client.host if request.client else "local")

    @app.get("/api/auth/me")
    def me(authorization: Optional[str] = Header(None)):
        return security.identify(authorization)

    # ---- read -----------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {**engine.health(), "websocket_clients": len(clients),
                "frontend_build": "PRESENT" if DIST.exists() else "NOT BUILT"}

    @app.get("/api/site")
    def site():
        return {"site": engine.site, "assets": [engine.describe(i) for i in engine.assets],
                "components": engine.components, "component_note": engine.component_note,
                "scenarios": [{"scenario_id": k, **v} for k, v in SCENARIOS.items()],
                "work_order_flow": WORK_ORDER_FLOW}

    @app.get("/api/snapshot")
    def snapshot():
        return engine.snapshot()

    @app.get("/api/assets/{asset_id}/history")
    def history(asset_id: str, minutes: float = 10, until: Optional[float] = None):
        if asset_id not in engine.assets:
            raise EngineError(404, f"Unknown asset {asset_id}")
        end = until or time.time()
        rows = store.history(asset_id, end - min(minutes, 24 * 60) * 60, end)
        return {"asset_id": asset_id, "data_mode": engine.assets[asset_id]["data_mode"], "rows": rows}

    @app.get("/api/assets/{asset_id}/export.csv")
    def export(asset_id: str, minutes: float = 60):
        if asset_id not in engine.assets:
            raise EngineError(404, f"Unknown asset {asset_id}")
        rows = store.history(asset_id, time.time() - minutes * 60)
        keys = [c["key"] for c in engine.channels(asset_id)]
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["timestamp", "asset_id", "data_mode", *keys, "data_quality_flags"])
        for r in rows:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"]))
            writer.writerow([stamp, asset_id, r["data_mode"], *["" if r["vals"].get(k) is None else r["vals"][k] for k in keys],
                             ";".join(r["quality"] or [])])
        name = f"{engine.assets[asset_id]['data_mode']}_{asset_id}.csv"
        return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/events")
    def events(asset_id: Optional[str] = None, category: Optional[str] = None, severity: Optional[str] = None,
               data_mode: Optional[str] = None, origin: Optional[str] = None, scenario_id: Optional[str] = None,
               acknowledged: Optional[bool] = None, search: Optional[str] = None, hours: Optional[float] = None,
               limit: int = 200):
        return store.events(min(limit, 1000), asset_id=asset_id, category=category, severity=severity,
                            data_mode=data_mode, origin=origin, scenario_id=scenario_id, acknowledged=acknowledged,
                            search=search, since=time.time() - hours * 3600 if hours else None)

    @app.get("/api/diagnostics")
    def diagnoses(asset_id: Optional[str] = None):
        return store.diagnoses(asset_id)

    @app.get("/api/diagnostics/{asset_id}/preview")
    def preview_diagnosis(asset_id: str):
        """Live, read-only assessment for the fault-analysis panel. Nothing is stored."""
        return {"id": None, "ts": time.time(), "asset_id": asset_id, "reviewed_by": None,
                "payload": engine.preview_diagnosis(asset_id)}

    @app.get("/api/autopsy/{asset_id}")
    def autopsy(asset_id: str):
        """Read-only fault autopsy: evidence, causes, cause-and-effect chain, suspected component."""
        return engine.autopsy(asset_id)

    @app.post("/api/faults/{asset_id}/repair")
    def repair(asset_id: str):
        """Clear the fault on a virtual machine. Only virtual machines can be repaired this way."""
        return engine.repair(asset_id, "operator")

    @app.get("/api/workorders")
    def work_orders():
        return store.work_orders()

    @app.get("/api/analytics/summary")
    def analytics(hours: float = 6):
        since = time.time() - hours * 3600
        evs = store.events(1000, since=since)[::-1]
        per_asset = {}
        for asset_id, a in engine.assets.items():
            mine = [e for e in evs if e["asset_id"] == asset_id]
            raised = [e for e in mine if e["category"] in ("SENSOR_ANOMALY", "SIMULATED_FAULT")]
            duration, start = 0.0, None
            for e in mine:
                if e["category"] in ("SENSOR_ANOMALY", "SIMULATED_FAULT") and start is None:
                    start = e["ts"]
                elif e["category"] == "RETURN_TO_NORMAL" and start is not None:
                    duration += e["ts"] - start
                    start = None
            if start is not None:
                duration += time.time() - start
            rows = store.history(asset_id, since)
            per_asset[asset_id] = {"name": a["name"], "data_mode": a["data_mode"], "anomaly_events": len(raised),
                                   "alert_duration_s": round(duration), "samples": len(rows),
                                   "first_ts": rows[0]["ts"] if rows else None, "last_ts": rows[-1]["ts"] if rows else None}
        return {"hours": hours, "assets": per_asset, "work_orders": store.work_orders(),
                "note": "Counts and durations are computed from stored events and telemetry in the selected period."}

    @app.get("/api/device/ports")
    def ports(authorization: Optional[str] = Header(None)):
        who(authorization, "admin")
        return available_ports()

    @app.get("/api/device/commands")
    def commands():
        return store.commands()

    @app.get("/api/settings")
    def get_settings():
        return {"demo_notifications": store.get_setting("demo_notifications", {"enabled": False}),
                "limits": store.get_setting(f"limits:{engine.real_id}", {}),
                "real_asset": engine.real_id, "replay": engine.replay_source,
                "accounts": ["admin", "operator"], "secrets_file": "nexus/.env (local, not shared)"}

    # ---- actions (authorised) -------------------------------------------------
    @app.post("/api/sim/{asset_id}/{action}")
    def simulate(asset_id: str, action: str, body: Optional[SimStart] = None, authorization: Optional[str] = Header(None)):
        user = who(authorization, "operator")
        if action == "start" and body is None:
            raise EngineError(400, "scenario_id is required")
        scenario = engine.sim_action(user, asset_id, action, body.scenario_id if body else None,
                                     body.severity if body else 0.7)
        return {"asset_id": asset_id, "simulation": scenario}

    @app.post("/api/events/{event_id}/ack")
    def acknowledge(event_id: int, authorization: Optional[str] = Header(None)):
        user = who(authorization, "operator")
        row = store.ack_event(event_id, user)
        if not row:
            raise EngineError(404, "Unknown event")
        return row

    @app.post("/api/diagnostics/{asset_id}/run")
    def run_diagnosis(asset_id: str, authorization: Optional[str] = Header(None)):
        return engine.run_diagnosis(asset_id, who(authorization, "operator"))

    @app.post("/api/diagnostics/{diagnosis_id}/review")
    def review(diagnosis_id: int, authorization: Optional[str] = Header(None)):
        user = who(authorization, "operator")
        rows = store.diagnoses(diagnosis_id=diagnosis_id)
        if not rows:
            raise EngineError(404, "Unknown diagnosis")
        store.review_diagnosis(diagnosis_id, user)
        engine.event(rows[0]["asset_id"], "DIAGNOSIS", "INFO", "OPERATOR", "Diagnosis reviewed", f"by {user}", ref=f"DX-{diagnosis_id}")
        return store.diagnoses(diagnosis_id=diagnosis_id)[0]

    @app.post("/api/workorders")
    def new_work_order(body: WorkOrderNew, authorization: Optional[str] = Header(None)):
        return engine.create_work_order(who(authorization, "operator"), body.asset_id, body.title, body.priority, body.diagnosis_id)

    @app.post("/api/workorders/{work_order_id}")
    def change_work_order(work_order_id: int, body: WorkOrderUpdate, authorization: Optional[str] = Header(None)):
        return engine.update_work_order(who(authorization, "operator"), work_order_id, body.state, body.note)

    @app.post("/api/device/connect")
    def connect(body: Connect, authorization: Optional[str] = Header(None)):
        user = who(authorization, "admin")
        engine.link.connect(body.port, body.baud)
        engine.event(engine.real_id, "DEVICE_CONNECTION", "INFO", "OPERATOR", f"Connection to {body.port} requested", f"by {user}")
        return engine.link_status()

    @app.post("/api/device/disconnect")
    def disconnect(authorization: Optional[str] = Header(None)):
        user = who(authorization, "admin")
        engine.link.disconnect()
        store.set_setting("demo_notifications", {"enabled": False})     # unpaired -> notifications off
        engine.event(engine.real_id, "DEVICE_CONNECTION", "INFO", "OPERATOR", "Device disconnected on request", f"by {user}")
        return engine.link_status()

    @app.post("/api/device/command")
    def command(body: Command, authorization: Optional[str] = Header(None)):
        user = who(authorization, "admin")
        return engine.issue_command(user, body.name, body.duration_ms)

    @app.put("/api/settings/notifications")
    def notifications(body: Notifications, authorization: Optional[str] = Header(None)):
        user = who(authorization, "admin")
        if body.enabled and engine.link.status != "CONNECTED":
            raise EngineError(409, "Connect the ESP32 before enabling physical demo notifications.")
        store.set_setting("demo_notifications", {"enabled": body.enabled, "by": user, "ts": time.time()})
        engine.event(engine.real_id, "SETTINGS", "INFO", "OPERATOR",
                     f"Physical demo notifications {'enabled' if body.enabled else 'disabled'}", f"by {user}")
        return store.get_setting("demo_notifications")

    @app.put("/api/settings/limits")
    def limits(body: Limits, authorization: Optional[str] = Header(None)):
        user = who(authorization, "admin")
        known = {c["key"] for c in engine.channels(engine.real_id)}
        cleaned = {}
        for key, lim in body.limits.items():
            if key not in known or not isinstance(lim, dict):
                raise EngineError(400, f"Unknown channel '{key}'")
            warn, crit, direction = lim.get("warn"), lim.get("crit"), lim.get("direction", "high")
            if warn is None:
                continue
            if direction not in ("high", "low") or not isinstance(warn, (int, float)) or \
                    (crit is not None and not isinstance(crit, (int, float))):
                raise EngineError(400, f"{key}: limits must be numbers and direction 'high' or 'low'")
            if crit is not None and (crit < warn if direction == "high" else crit > warn):
                raise EngineError(400, f"{key}: the critical limit must be beyond the warning limit")
            cleaned[key] = {"warn": warn, "crit": crit, "direction": direction,
                            "source": f"Configured by {user} on {time.strftime('%Y-%m-%d')}"}
        store.set_setting(f"limits:{engine.real_id}", cleaned)
        engine.event(engine.real_id, "SETTINGS", "INFO", "OPERATOR", "Operating limits changed", f"by {user}")
        return cleaned

    # ---- analysis (predictive_maintenance bridge) --------------------------
    @app.get("/api/analysis/sessions")
    def analysis_sessions():
        return analysis.api_list_sessions()

    @app.get("/api/analysis/sessions/{session_file:path}")
    def analysis_session(session_file: str):
        try:
            return analysis.api_load_session(session_file)
        except Exception as err:
            raise EngineError(400, f"Could not load session: {err}") from None

    @app.get("/api/analysis/quality/{session_file:path}")
    def analysis_quality(session_file: str):
        try:
            return analysis.api_data_quality(session_file)
        except Exception as err:
            raise EngineError(400, f"Quality check failed: {err}") from None

    @app.post("/api/analysis/detect/{session_file:path}")
    def analysis_detect(session_file: str, model_version: Optional[str] = None):
        try:
            return analysis.api_run_detectors(session_file, model_version)
        except Exception as err:
            raise EngineError(400, f"Detection failed: {err}") from None

    @app.get("/api/analysis/vibration/{session_file:path}")
    def analysis_vibration(session_file: str):
        try:
            return analysis.api_vibration(session_file)
        except Exception as err:
            raise EngineError(400, f"Vibration analysis failed: {err}") from None

    @app.get("/api/analysis/models")
    def analysis_models():
        return analysis.api_list_models()

    @app.post("/api/analysis/train")
    def analysis_train(body: TrainRequest, authorization: Optional[str] = Header(None)):
        who(authorization, "operator")
        try:
            return analysis.api_train_model(body.session_file, body.conditions)
        except Exception as err:
            raise EngineError(400, f"Training failed: {err}") from None

    @app.get("/api/analysis/compare")
    def analysis_compare(file_a: str, file_b: str):
        try:
            return analysis.api_compare(file_a, file_b)
        except Exception as err:
            raise EngineError(400, f"Comparison failed: {err}") from None

    @app.get("/api/analysis/demo/scenarios")
    def analysis_demo_scenarios():
        return analysis.api_demo_scenarios()

    @app.post("/api/analysis/demo/generate")
    def analysis_demo_generate(body: DemoRequest):
        try:
            return analysis.api_generate_demo(body.scenario, body.duration_s, body.seed)
        except Exception as err:
            raise EngineError(400, f"Demo generation failed: {err}") from None

    @app.get("/api/analysis/settings")
    def analysis_settings():
        return analysis.api_get_pm_settings()

    @app.put("/api/analysis/settings")
    def analysis_settings_update(body: PMSettings, authorization: Optional[str] = Header(None)):
        who(authorization, "admin")
        result = analysis.api_save_pm_settings(body.settings)
        if not result["saved"]:
            raise EngineError(400, "; ".join(result["errors"]))
        return result

    @app.get("/api/analysis/report/{session_file:path}")
    def analysis_report(session_file: str, model_version: Optional[str] = None):
        try:
            html = analysis.api_report_html(session_file, model_version)
            return StreamingResponse(iter([html]), media_type="text/html",
                                     headers={"Content-Disposition": 'attachment; filename="analysis_report.html"'})
        except Exception as err:
            raise EngineError(400, f"Report generation failed: {err}") from None

    # ---- live stream ----------------------------------------------------------
    @app.websocket("/ws")
    async def stream(ws: WebSocket):
        await ws.accept()
        clients.add(ws)
        try:
            await ws.send_json(engine.snapshot())
            while True:
                await ws.receive_text()        # the stream is one-way; incoming text is ignored
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(ws)

    # ---- web app ---------------------------------------------------------------
    if DIST.exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            if path.startswith(("api/", "ws")):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            file = DIST / path
            return FileResponse(file if path and file.is_file() else DIST / "index.html")

    return app


def _production_app():
    settings_first = ensure_env_file()
    if settings_first:
        print("Created nexus/.env with a random signing secret and passwords. Open that file to see them.")
    settings = load_settings()
    return create_app(settings, raw_export_dir=PM_DIR / "data" / "raw", replay_file=default_replay_file(settings.data_dir))


app = _production_app() if "pytest" not in sys.modules else None
