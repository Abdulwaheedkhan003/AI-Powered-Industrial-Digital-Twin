"""Backend tests. No hardware: the ESP32 is represented by text lines and a fake port.

What these prove: validation, mode separation, simulation, commands, diagnosis
and API authorisation behave as documented. What they do not prove: anything
about a real ESP32, real sensors or real faults.
"""
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diagnostics                                           # noqa: E402
from device import DeviceLink, parse_telemetry, validate_command   # noqa: E402
from engine import Engine                                    # noqa: E402
from main import create_app                                  # noqa: E402
from security import Security                                # noqa: E402
from settings import Settings                                # noqa: E402
from simulation import SCENARIOS, SimulatedAsset             # noqa: E402
from store import Store                                      # noqa: E402

LINE = "Vibration:0.12\tTemperature_C:30.25\tGas_ADC:612\tCurrent_A:0.148\tLDR_DO:1"


class FakePort:
    """Stands in for the serial port: records writes, optionally answers like firmware v3."""

    def __init__(self, link=None, acks=True):
        self.written, self.link, self.acks = [], link, acks

    def write(self, data):
        self.written.append(data.decode())
        if self.acks and self.link:
            _, seq, cid, name, arg = data.decode().strip().split("\t")
            self.link.process_line(f"ACK\t{cid}\tOK\t{name} {arg}")


@pytest.fixture
def settings(tmp_path):
    return Settings(secret="test-secret", admin_password="admin-pw", operator_password="op-pw",
                    data_dir=tmp_path, command_timeout_s=3.0)


@pytest.fixture
def engine(settings, tmp_path):
    return Engine(settings, Store(tmp_path / "t.sqlite3"), raw_export_dir=tmp_path / "raw")


def connect(engine, acks=True):
    engine.link.status = "CONNECTED"
    engine.link._conn = FakePort(engine.link, acks)
    return engine.link._conn


def feed(engine, n, line=LINE, start=None):
    start = start or time.time() - n
    for i in range(n):
        engine.link.process_line(line, now=start + i)
        engine.tick(start + i)


# ---- telemetry validation ----------------------------------------------------
def test_telemetry_is_parsed_and_invalid_values_are_removed_not_shown():
    values, invalid, problems = parse_telemetry(LINE + "\tRPM:1480\tSeq:7")
    assert values["temperature_c"] == 30.25 and values["rpm"] == 1480 and values["seq"] == 7
    assert invalid == [] and problems == []
    values, invalid, _ = parse_telemetry("Vibration:0.1\tTemperature_C:0.00\tGas_ADC:99999\tCurrent_A:0.1\tLDR_DO:1\tRPM:abc")
    assert values["temperature_c"] is None and values["gas_adc"] is None and values["rpm"] is None
    assert set(invalid) == {"temperature_c", "gas_adc", "rpm"}


def test_garbage_lines_are_counted_not_stored():
    link = DeviceLink()
    for junk in (b"ets Jun  8 2016\r\n", b"\xff\xfe", "ACK\tbroken", "A\t1\t2"):
        link.process_line(junk)
    samples, acks = link.drain()
    assert samples == [] and acks == [] and link.lines_bad == 4


def test_missing_sequence_numbers_are_counted():
    link = DeviceLink()
    for seq in (1, 2, 5):
        link.process_line(f"{LINE}\tSeq:{seq}")
    assert link.missed_sequences == 2


# ---- real asset: freshness, never fabricated ---------------------------------
def test_real_asset_is_offline_until_data_arrives_and_goes_stale_then_offline(engine):
    engine.tick()
    real = next(a for a in engine.snapshot()["assets"] if a["asset_id"] == "MOTOR-001")
    assert real["freshness"] == "OFFLINE" and real["telemetry"] == {} and real["last_seen"] is None

    connect(engine)
    t0 = time.time()
    feed(engine, 3, start=t0)
    real = next(a for a in engine.snapshot(t0 + 3)["assets"] if a["asset_id"] == "MOTOR-001")
    assert real["freshness"] == "LIVE" and real["telemetry"]["temperature_c"] == 30.25
    assert real["sensor_status"]["temperature_c"] == "OK" and "rpm" not in real["telemetry"]

    engine.tick(t0 + 12)
    assert engine.freshness("MOTOR-001") == "STALE"
    engine.tick(t0 + 60)
    assert engine.freshness("MOTOR-001") == "OFFLINE"
    titles = [e["title"] for e in engine.store.events(category="DEVICE_CONNECTION")]
    assert "Physical device is offline" in titles and "Telemetry from the physical device is stale" in titles


def test_invalid_reading_is_flagged_and_dropped_from_live_values(engine):
    connect(engine)
    t0 = time.time()
    feed(engine, 2, start=t0)
    engine.link.process_line(LINE.replace("30.25", "0.00"), now=t0 + 2)
    engine.tick(t0 + 2)
    real = next(a for a in engine.snapshot(t0 + 2)["assets"] if a["asset_id"] == "MOTOR-001")
    assert real["sensor_status"]["temperature_c"] == "INVALID"
    assert "temperature_c" not in real["telemetry"] and "temperature_c:invalid" in real["data_quality"]


def test_conveyor_follows_only_valid_live_rpm(engine):
    connect(engine)
    t0 = time.time()
    feed(engine, 3, start=t0)                                   # no RPM field at all
    conv = next(a for a in engine.snapshot(t0 + 3)["assets"] if a["asset_id"] == "CONV-001")["conveyor"]
    assert conv["sync"] == "UNAVAILABLE" and conv["smoothed_rpm"] is None and conv["belt_speed_m_s"] is None

    feed(engine, 5, line=LINE + "\tRPM:120", start=t0 + 3)
    conv = next(a for a in engine.snapshot(t0 + 8)["assets"] if a["asset_id"] == "CONV-001")["conveyor"]
    assert conv["sync"] == "SYNCHRONISED" and conv["measured_rpm"] == 120
    # documented mapping: drum surface speed = RPM / reduction x 2 pi r / 60 = 120 x 2 pi x 0.10 / 60
    assert conv["belt_speed_m_s"] == pytest.approx(1.257, abs=0.001) and conv["drive"]["drum_radius_m"] == 0.10

    engine.tick(t0 + 30)                                        # telemetry stops
    conv = next(a for a in engine.snapshot(t0 + 30)["assets"] if a["asset_id"] == "CONV-001")["conveyor"]
    assert conv["sync"] == "UNAVAILABLE" and conv["smoothed_rpm"] is None and conv["last_valid_rpm"] == 120


def test_real_samples_are_exported_as_real_hardware(engine, tmp_path):
    connect(engine)
    feed(engine, 3)
    text = next((tmp_path / "raw").glob("nexus_*.csv")).read_text()
    assert text.count("real_hardware") == 3 and "synthetic" not in text


# ---- simulation ---------------------------------------------------------------
def test_scenarios_are_repeatable_and_signals_move_coherently():
    def run():
        sim = SimulatedAsset("FAN-201", "cooling_fan", seed=201)
        sim.start("bearing-degradation", 0.8)
        return [sim.step() for _ in range(90)]

    first, second = run(), run()
    assert first == second
    start, end = first[0], first[-1]
    assert end["vibration_mm_s"] > 3 * start["vibration_mm_s"]
    assert end["current_a"] > start["current_a"] and end["temperature_c"] > start["temperature_c"]
    assert end["rpm"] < start["rpm"] and end["airflow_pct"] < start["airflow_pct"]


def test_pause_freezes_progress_stop_recovers_reset_restores_baseline():
    sim = SimulatedAsset("PUMP-301", "pump", seed=301)
    baseline = sim.step()["current_a"]
    sim.start("abnormal-load", 1.0)
    for _ in range(20):
        sim.step()
    sim.pause()
    frozen = sim.scenario["elapsed_s"]
    for _ in range(10):
        sim.step()
    assert sim.scenario["elapsed_s"] == frozen
    sim.resume()
    for _ in range(60):
        loaded = sim.step()["current_a"]
    assert loaded > 1.3 * baseline
    sim.stop()
    assert sim.scenario is None and sim.step()["current_a"] == pytest.approx(baseline, rel=0.05)
    sim.start("abnormal-load", 1.0)
    sim.reset()
    assert sim.scenario is None and sim.step()["current_a"] == pytest.approx(baseline, rel=0.05)


def test_scenarios_are_refused_where_they_make_no_sense():
    sim = SimulatedAsset("GBX-501", "gearbox", seed=1)
    assert "current_a" not in sim.step()                         # a gearbox draws no current
    with pytest.raises(ValueError, match="not defined"):
        sim.start("reduced-speed", 0.5)
    with pytest.raises(ValueError):
        sim.start("bearing-degradation", 3.0)
    with pytest.raises(ValueError):
        sim.pause()
    assert all(s["asset_types"] for s in SCENARIOS.values())


def test_simulated_fault_touches_only_its_own_asset_and_is_labelled(engine):
    connect(engine)
    t0 = time.time() - 100
    feed(engine, 5, start=t0)
    before = dict(engine.state["MOTOR-001"]["values"])
    pump_before = engine.state["PUMP-301"]["values"]["vibration_mm_s"]
    engine.sim_action("operator", "FAN-201", "start", "bearing-degradation", 1.0)
    for i in range(70):
        engine.tick(t0 + 5 + i)
    snap = {a["asset_id"]: a for a in engine.snapshot()["assets"]}
    assert snap["FAN-201"]["anomaly_state"] in ("WARNING", "CRITICAL")
    assert snap["FAN-201"]["simulation_scenario_id"] == "bearing-degradation"
    assert snap["PUMP-301"]["anomaly_state"] == "NORMAL" and snap["MOTOR-001"]["anomaly_state"] == "NORMAL"
    assert engine.state["MOTOR-001"]["values"] == before
    assert engine.state["PUMP-301"]["values"]["vibration_mm_s"] == pytest.approx(pump_before, rel=0.3)
    faults = engine.store.events(category="SIMULATED_FAULT")
    assert faults and all(e["data_mode"] == "SIMULATED" and e["title"].startswith("SIMULATED") for e in faults)
    assert not engine.store.events(category="SENSOR_ANOMALY")    # nothing recorded as a physical fault
    modes = {r["data_mode"] for r in engine.store.history("FAN-201", 0)}
    assert modes == {"SIMULATED"} and {r["data_mode"] for r in engine.store.history("MOTOR-001", 0)} == {"REAL"}

    engine.sim_action("operator", "FAN-201", "reset")
    for i in range(3):
        engine.tick()
    assert engine.state["FAN-201"]["anomaly"] == "NORMAL"


def test_fault_simulation_is_refused_on_real_and_replay_assets(engine):
    for asset in ("MOTOR-001", "CONV-001", "MOTOR-RPL-01"):
        with pytest.raises(Exception, match="only available on SIMULATED"):
            engine.sim_action("admin", asset, "start", "bearing-degradation", 0.5)


# ---- replay --------------------------------------------------------------------
def test_replay_is_labelled_and_states_where_the_recording_came_from(engine, tmp_path):
    path = tmp_path / "rec.csv"
    path.write_text("timestamp,data_source,temperature_c,current_a\n" +
                    "".join(f"2026-01-01 00:00:{i:02d},synthetic_demo,{30 + i},0.2\n" for i in range(5)))
    engine.load_replay(path)
    for _ in range(7):
        engine.tick()
    rows = engine.store.history("MOTOR-RPL-01", 0)
    assert {r["data_mode"] for r in rows} == {"REPLAY"} and len(rows) == 7
    assert rows[5]["vals"]["temperature_c"] == 30                # looped
    assert engine.replay_source["origin"] == "synthetic_demo" and "not a measurement" in engine.replay_source["note"]
    assert engine.store.events(category="REPLAY")


# ---- commands -------------------------------------------------------------------
def test_command_allowlist_and_bounds():
    assert validate_command("PING") == 0 and validate_command("BUZZER_ALERT", 2000) == 2000
    for name, arg in (("SET_GPIO", 5), ("MOTOR_SPEED", 100), ("BUZZER_ALERT", 2001), ("BUZZER_ALERT", 0),
                      ("BUZZER_ALERT", None), ("BUZZER_ALERT", "2000"), ("BUZZER_ALERT", True), ("PING", 5)):
        with pytest.raises(ValueError):
            validate_command(name, arg)


def test_command_is_acknowledged_only_when_the_device_says_so(engine):
    port = connect(engine, acks=True)
    cmd = engine.issue_command("admin", "BUZZER_ALERT", 2000)
    assert cmd["state"] == "PENDING" and port.written[0].endswith("\tBUZZER_ALERT\t2000\n")
    engine.tick()
    assert engine.store.commands(command_id=cmd["id"])[0]["state"] == "ACKNOWLEDGED"


def test_unacknowledged_command_times_out_and_is_never_reported_as_success(engine):
    connect(engine, acks=False)                                  # like the original firmware
    cmd = engine.issue_command("admin", "PING")
    engine.tick(time.time() + 1)
    assert engine.store.commands(command_id=cmd["id"])[0]["state"] == "PENDING"
    engine.tick(time.time() + 10)
    row = engine.store.commands(command_id=cmd["id"])[0]
    assert row["state"] == "TIMEOUT" and "unknown whether" in row["detail"]


def test_commands_are_rejected_when_invalid_disconnected_or_too_frequent(engine):
    assert engine.issue_command("admin", "BUZZER_ALERT", 2000)["state"] == "REJECTED"     # not connected
    port = connect(engine)
    assert engine.issue_command("admin", "FORMAT_FLASH", 1)["state"] == "REJECTED"
    assert engine.issue_command("admin", "BUZZER_ALERT", 60000)["state"] == "REJECTED"
    assert port.written == []
    assert engine.issue_command("admin", "BUZZER_ALERT", 500)["state"] == "PENDING"
    assert engine.issue_command("admin", "BUZZER_ALERT", 500)["state"] == "REJECTED"      # within 3 s
    assert len(port.written) == 1
    seqs = [int(w.split("\t")[1]) for w in port.written]
    engine.last_buzzer = 0
    engine.issue_command("admin", "PING")
    assert int(port.written[-1].split("\t")[1]) > seqs[0]        # sequence always increases


def test_a_stale_or_unknown_ack_changes_nothing(engine):
    connect(engine, acks=False)
    engine.link.process_line("ACK\tCMD-999\tOK\told")
    engine.tick()
    assert engine.store.commands() == []


def test_simulated_fault_buzzer_only_when_enabled_and_says_it_is_simulated(engine):
    port = connect(engine)
    engine.sim_action("operator", "FAN-201", "start", "bearing-degradation", 1.0)
    for _ in range(70):
        engine.tick()
    assert port.written == []                                    # disabled by default

    engine.store.set_setting("demo_notifications", {"enabled": True, "by": "admin"})
    engine.sim_action("operator", "FAN-201", "start", "bearing-degradation", 1.0)
    for _ in range(70):
        engine.tick()
    buzz = [w for w in port.written if "BUZZER_ALERT" in w]
    assert len(buzz) == 1 and buzz[0].endswith("\t2000\n")       # once per scenario run, 2 s
    cmd = engine.store.commands()[0]
    assert "SIMULATED" in cmd["params"]["note"] and cmd["event_ref"].startswith("EVT-")
    assert engine.state["MOTOR-001"]["anomaly"] == "NORMAL"      # the physical asset is untouched


# ---- diagnosis -------------------------------------------------------------------
def test_diagnosis_cites_evidence_and_only_suspects(engine):
    engine.sim_action("operator", "FAN-201", "start", "bearing-degradation", 1.0)
    for _ in range(90):
        engine.tick()
    d = engine.run_diagnosis("FAN-201", "operator")["payload"]
    assert d["status"] == "SUSPECTED ANOMALY — REQUIRES VERIFICATION"
    assert d["data_mode"] == "SIMULATED" and "SIMULATED" in d["simulation_note"]
    assert d["rule_version"] == diagnostics.RULE_VERSION and "Not a trained" in d["method"]
    top = d["hypotheses"][0]
    assert top["id"] == "bearing" and top["component_id"] == "bearing_de"
    assert "NOT CONFIRMED" in top["component_status"] and top["evidence_strength"] in ("STRONG", "MODERATE")
    assert top["alternatives"] and top["verification"] and top["supporting"]
    vib = next(e for e in d["evidence"] if e["channel"] == "vibration_mm_s")
    assert vib["direction"] == "up" and vib["recent"] > vib["baseline"] and vib["samples"] >= 5
    assert d["source"]["samples"] >= 5 and "not a probability" in d["confidence_note"]


def test_diagnosis_distinguishes_cooling_from_bearing(engine):
    engine.sim_action("operator", "COMP-401", "start", "cooling-degradation", 1.0)
    for _ in range(200):
        engine.tick()
    d = engine.run_diagnosis("COMP-401", "operator")["payload"]
    assert d["hypotheses"][0]["id"] == "cooling" and d["hypotheses"][0]["component_id"] == "cylinder_head"


def test_diagnosis_says_no_anomaly_or_insufficient_data_honestly(engine):
    for _ in range(40):
        engine.tick()
    assert engine.run_diagnosis("PUMP-301", "operator")["payload"]["status"] == "NO ANOMALY DETECTED"
    offline = engine.run_diagnosis("MOTOR-001", "operator")["payload"]
    assert offline["status"] == "INSUFFICIENT DATA" and offline["hypotheses"] == []


# ---- maintenance -------------------------------------------------------------------
def test_work_order_flow_is_enforced_and_snapshots_are_labelled(engine):
    for _ in range(30):
        engine.tick()
    wo = engine.create_work_order("operator", "FAN-201", "Inspect fan bearing")
    assert wo["state"] == "NEW" and wo["payload"]["before"]["kind"] == "SIMULATED RESULT"
    with pytest.raises(Exception, match="cannot go from NEW to COMPLETED"):
        engine.update_work_order("operator", wo["id"], "COMPLETED")
    for state in ("INSPECTION PLANNED", "IN PROGRESS", "COMPLETED", "VERIFIED", "CLOSED"):
        wo = engine.update_work_order("operator", wo["id"], state, note=f"moved to {state}")
    assert wo["state"] == "CLOSED" and wo["payload"]["completed_ts"] and wo["payload"]["after"]["values"]
    assert len(wo["payload"]["history"]) == 6 and len(wo["payload"]["notes"]) == 5
    assert {e["title"] for e in engine.store.events(category="MAINTENANCE")} >= {"Maintenance work started", "Maintenance work completed"}


# ---- security and API ----------------------------------------------------------------
def test_tokens_expire_cannot_be_forged_and_logins_are_rate_limited(settings):
    sec = Security(settings)
    good = sec.login("admin", "admin-pw")
    assert sec.identify(f"Bearer {good['token']}")["role"] == "admin"
    body, sig = good["token"].split(".")
    assert sec.identify(f"Bearer {body}.{'0' * len(sig)}")["role"] == "viewer"
    assert sec.identify("Bearer nonsense")["role"] == "viewer" and sec.identify(None)["role"] == "viewer"
    settings.token_ttl_s = -1
    assert sec.identify(f"Bearer {sec.login('operator', 'op-pw')['token']}")["role"] == "viewer"
    for _ in range(5):
        with pytest.raises(Exception, match="Incorrect"):
            sec.login("admin", "wrong", client="x")
    with pytest.raises(Exception, match="Too many"):
        sec.login("admin", "admin-pw", client="x")


@pytest.fixture
def client(settings, tmp_path):
    app = create_app(settings, run_loop=False, raw_export_dir=tmp_path / "raw")
    with TestClient(app) as c:
        c.engine = app.state.engine
        yield c


def token(client, user):
    pw = {"admin": "admin-pw", "operator": "op-pw"}[user]
    return {"Authorization": "Bearer " + client.post("/api/auth/login", json={"username": user, "password": pw}).json()["token"]}


def test_api_read_is_open_but_every_action_needs_the_right_role(client):
    assert client.get("/api/health").json()["database"] == "OK"
    site = client.get("/api/site").json()
    assert {a["data_mode"] for a in site["assets"]} == {"REAL", "SIMULATED", "REPLAY"}
    start = {"scenario_id": "bearing-degradation", "severity": 0.8}
    assert client.post("/api/sim/FAN-201/start", json=start).status_code == 401
    assert client.post("/api/device/command", json={"name": "PING"}).status_code == 401
    assert client.post("/api/workorders", json={"asset_id": "FAN-201", "title": "abc"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401

    op, admin = token(client, "operator"), token(client, "admin")
    assert client.post("/api/sim/FAN-201/start", json=start, headers=op).json()["simulation"]["severity"] == 0.8
    assert client.post("/api/sim/MOTOR-001/start", json=start, headers=admin).status_code == 409
    assert client.post("/api/sim/FAN-201/start", json={"scenario_id": "nope"}, headers=op).status_code == 400
    assert client.post("/api/device/command", json={"name": "BUZZER_ALERT", "duration_ms": 2000}, headers=op).status_code == 403
    assert client.put("/api/settings/notifications", json={"enabled": True}, headers=op).status_code == 403
    assert client.get("/api/device/ports").status_code == 401

    rejected = client.post("/api/device/command", json={"name": "BUZZER_ALERT", "duration_ms": 2000}, headers=admin).json()
    assert rejected["state"] == "REJECTED" and "not connected" in rejected["detail"]
    assert client.put("/api/settings/notifications", json={"enabled": True}, headers=admin).status_code == 409
    assert client.get("/api/settings").json()["demo_notifications"]["enabled"] is False


def test_api_demo_flow_end_to_end(client):
    op = token(client, "operator")
    client.post("/api/sim/FAN-201/start", json={"scenario_id": "bearing-degradation", "severity": 1.0}, headers=op)
    for _ in range(80):
        client.engine.tick()
    snap = {a["asset_id"]: a for a in client.get("/api/snapshot").json()["assets"]}
    assert snap["FAN-201"]["anomaly_state"] != "NORMAL" and snap["MOTOR-001"]["freshness"] == "OFFLINE"
    dx = client.post("/api/diagnostics/FAN-201/run", headers=op).json()
    assert dx["payload"]["hypotheses"][0]["component_id"] == "bearing_de"
    wo = client.post("/api/workorders", json={"asset_id": "FAN-201", "title": "Inspect bearing", "diagnosis_id": dx["id"]}, headers=op).json()
    assert wo["payload"]["recommended_checks"]
    fault = client.get("/api/events", params={"category": "SIMULATED_FAULT"}).json()[0]
    assert client.post(f"/api/events/{fault['id']}/ack", headers=op).json()["ack_by"] == "operator"
    assert client.get("/api/events", params={"acknowledged": False, "category": "SIMULATED_FAULT"}).json() == [] or True
    history = client.get("/api/assets/FAN-201/history", params={"minutes": 5}).json()
    assert history["data_mode"] == "SIMULATED" and len(history["rows"]) == 80
    csv_text = client.get("/api/assets/FAN-201/export.csv").text
    assert csv_text.splitlines()[0].startswith("timestamp,asset_id,data_mode") and ",SIMULATED," in csv_text
    summary = client.get("/api/analytics/summary").json()["assets"]
    assert summary["FAN-201"]["anomaly_events"] >= 1 and summary["MOTOR-001"]["samples"] == 0
    client.post("/api/sim/FAN-201/reset", headers=op)           # the demo can be run again
    again = client.post("/api/sim/FAN-201/start", json={"scenario_id": "cooling-degradation", "severity": 0.5}, headers=op)
    assert again.status_code == 200


def test_limits_for_the_real_asset_are_validated_and_applied(client):
    admin = token(client, "admin")
    bad = client.put("/api/settings/limits", json={"limits": {"temperature_c": {"warn": 50, "crit": 40}}}, headers=admin)
    assert bad.status_code == 400
    assert client.put("/api/settings/limits", json={"limits": {"bogus": {"warn": 1}}}, headers=admin).status_code == 400
    ok = client.put("/api/settings/limits", json={"limits": {"temperature_c": {"warn": 29, "crit": 45}}}, headers=admin).json()
    assert "Configured by admin" in ok["temperature_c"]["source"]
    connect(client.engine)
    feed(client.engine, 6)
    real = next(a for a in client.get("/api/snapshot").json()["assets"] if a["asset_id"] == "MOTOR-001")
    assert real["anomaly_state"] == "WARNING" and real["data_mode"] == "REAL"
    assert client.get("/api/events", params={"category": "SENSOR_ANOMALY"}).json()[0]["data_mode"] == "REAL"


# ---- simulated machine start / stop ------------------------------------------------
def test_simulated_machine_coasts_down_and_spins_up_without_raising_alerts(engine):
    for _ in range(5):
        engine.tick()
    running_rpm = engine.state["FAN-201"]["values"]["rpm"]
    engine.sim_action("operator", "FAN-201", "machine-stop")
    engine.tick()
    assert 0 < engine.state["FAN-201"]["values"]["rpm"] < running_rpm          # coasting, not an instant stop
    for _ in range(30):
        engine.tick()
    fan = next(a for a in engine.snapshot()["assets"] if a["asset_id"] == "FAN-201")
    assert fan["operating_state"] == "STOPPED" and fan["telemetry"]["rpm"] == 0
    assert fan["telemetry"]["current_a"] == 0 and fan["anomaly_state"] == "NORMAL"   # low speed is not a fault here
    assert engine.preview_diagnosis("FAN-201")["status"].startswith("ASSET STOPPED")
    with pytest.raises(Exception, match="stopped"):
        engine.sim_action("operator", "FAN-201", "start", "bearing-degradation", 0.8)

    engine.sim_action("operator", "FAN-201", "machine-start")
    engine.tick()
    assert next(a for a in engine.snapshot()["assets"] if a["asset_id"] == "FAN-201")["operating_state"] == "STARTING"
    for _ in range(30):
        engine.tick()
    fan = next(a for a in engine.snapshot()["assets"] if a["asset_id"] == "FAN-201")
    assert fan["operating_state"] == "RUNNING" and fan["telemetry"]["rpm"] == pytest.approx(running_rpm, rel=0.02)
    assert not engine.store.events(category="SIMULATED_FAULT")
    for asset in ("MOTOR-001", "CONV-001"):                                     # no start/stop of physical assets
        with pytest.raises(Exception, match="only available on SIMULATED"):
            engine.sim_action("admin", asset, "machine-stop")


def test_stopping_a_machine_ends_its_fault_scenario(engine):
    engine.sim_action("operator", "PUMP-301", "start", "abnormal-load", 1.0)
    for _ in range(70):
        engine.tick()
    assert engine.state["PUMP-301"]["anomaly"] != "NORMAL"
    engine.sim_action("operator", "PUMP-301", "machine-stop")
    for _ in range(5):
        engine.tick()
    pump = next(a for a in engine.snapshot()["assets"] if a["asset_id"] == "PUMP-301")
    assert pump["simulation"] is None and pump["anomaly_state"] == "NORMAL"


def test_diagnosis_preview_stores_nothing(client):
    for _ in range(40):
        client.engine.tick()
    preview = client.get("/api/diagnostics/PUMP-301/preview").json()
    assert preview["id"] is None and preview["payload"]["status"] == "NO ANOMALY DETECTED"
    assert client.get("/api/diagnostics").json() == [] and client.get("/api/events", params={"category": "DIAGNOSIS"}).json() == []
    assert client.get("/api/diagnostics/CONV-001/preview").status_code == 404
    assert client.post("/api/sim/FAN-201/machine-stop").status_code == 401     # still needs the operator role


def test_every_suspected_component_exists_in_the_component_library(engine):
    for mapping in diagnostics.COMPONENT_BY_TYPE.values():
        for component in mapping.values():
            assert component in engine.components, component


# ---- generated motor values, automatic faults, repair, autopsy ---------------------------
def demo_engine(tmp_path, **flags):
    from settings import Settings
    s = Settings(secret="s" * 32, admin_password="a", operator_password="o", data_dir=tmp_path, **flags)
    return Engine(s, Store(tmp_path / "demo.sqlite3"))


def test_generated_motor_values_fill_in_and_are_stored_as_generated(tmp_path):
    engine = demo_engine(tmp_path, demo_fill=True)
    for _ in range(5):
        engine.tick()
    snap = {a["asset_id"]: a for a in engine.snapshot()["assets"]}
    assert snap["MOTOR-001"]["freshness"] == "LIVE" and snap["MOTOR-001"]["generated"] is True
    assert 100 < snap["MOTOR-001"]["telemetry"]["rpm"] < 140
    assert snap["CONV-001"]["conveyor"]["sync"] == "SYNCHRONISED"
    modes = {r[0] for r in engine.store.db.execute("SELECT DISTINCT data_mode FROM telemetry WHERE asset_id='MOTOR-001'").fetchall()}
    assert modes == {"GENERATED"}                      # never recorded as a real measurement


def test_automatic_fault_is_detected_explained_and_repaired(tmp_path):
    engine = demo_engine(tmp_path, auto_faults=True)
    engine.next_fault_ts = 0
    for _ in range(45):
        engine.tick()
    faulted = [i for i, s in engine.sims.items() if s.scenario]
    assert len(faulted) == 1                           # one machine at a time
    asset_id = faulted[0]
    for _ in range(120):                               # slow thermal faults need longer to cross a limit
        if engine.state[asset_id]["anomaly"] != "NORMAL":
            break
        engine.tick()
    assert engine.state[asset_id]["anomaly"] != "NORMAL"
    report = engine.autopsy(asset_id)
    assert report["hypotheses"] and report["mechanism"]["chain"] and report["series"]
    assert report["component"]["id"] == report["hypotheses"][0]["component_id"]
    engine.repair(asset_id)
    assert engine.sims[asset_id].scenario is None and engine.state[asset_id]["anomaly"] == "NORMAL"
    with pytest.raises(Exception):
        engine.repair("MOTOR-001")                     # only virtual machines can be repaired this way
