"""Evidence-based diagnosis using transparent rules.

This is NOT a trained or validated fault classifier. It compares recent
readings with a baseline, lists which signals changed and by how much, and
matches that pattern against a short table of textbook cause patterns. Every
statement in the output can be traced to a number in the evidence list.

Evidence strength is a count of agreeing signals, not a probability:
  STRONG   three or more signals changed the way the cause predicts, none against
  MODERATE two signals agree
  LIMITED  one signal agrees, or signals are missing or contradict

No language model is involved, so nothing here can invent evidence.
"""
import statistics
import time

RULE_VERSION = "rules-1.0"
METHOD = "Transparent rules baseline (rules-1.0). Not a trained or validated model."
WINDOW_S = 30
MIN_SAMPLES = 5

# change (as % of baseline) that counts as a real deviation, per signal kind
TOLERANCE_PCT = {"temperature": 5.0, "vibration": 25.0, "current": 8.0, "rpm": 2.5, "load": 8.0, "process": 4.0}

HYPOTHESES = [
    {"id": "bearing", "title": "Bearing wear or lubrication loss", "requires": ("vibration", "up"),
     "expects": {"vibration": "up", "current": "up", "temperature": "up", "rpm": "down"},
     "rationale": "A degrading bearing adds roughness (more vibration) and friction (slightly more current and heat, slightly less speed).",
     "alternatives": ["Mechanical looseness or misalignment at the mounting or coupling",
                      "Rotor or impeller imbalance", "Vibration sensor has come loose"],
     "verification": ["Check mounting bolts and coupling alignment first; they are quicker to rule out.",
                      "With power isolated, turn the shaft by hand and feel for roughness or play.",
                      "Check bearing lubrication following the approved procedure."]},
    {"id": "imbalance", "title": "Imbalance or mechanical looseness", "requires": ("vibration", "up"),
     "expects": {"vibration": "up", "current": "normal", "temperature": "normal", "rpm": "normal"},
     "rationale": "Vibration has risen on its own. Friction-related signals (current, temperature, speed) have not moved, which points away from a bearing and towards imbalance or something loose.",
     "alternatives": ["Early-stage bearing wear", "Vibration sensor has come loose", "Resonance at the current speed"],
     "verification": ["Check mounting bolts and guards for looseness.",
                      "Inspect the rotating part for dirt build-up or damage.",
                      "Confirm the vibration sensor is firmly attached."]},
    {"id": "cooling", "title": "Reduced cooling", "requires": ("temperature", "up"),
     "expects": {"temperature": "up", "vibration": "normal", "current": "normal", "rpm": "normal"},
     "rationale": "Temperature has risen while the electrical and mechanical load look unchanged, so the same heat is being removed less effectively.",
     "alternatives": ["Higher room temperature", "Temperature sensor moved or poorly attached",
                      "Normal warm-up that has not yet levelled off"],
     "verification": ["Check that air intakes, fins and guards are clear.",
                      "Check the cooling fan turns freely and is undamaged.",
                      "Compare with room temperature."]},
    {"id": "overload", "title": "Increased mechanical load", "requires": ("current", "up"),
     "expects": {"current": "up", "load": "up", "temperature": "up", "rpm": "down", "vibration": "normal"},
     "rationale": "Current has risen and speed has sagged, the pattern of a machine working harder against its load.",
     "alternatives": ["Binding or blockage in the driven equipment", "Low supply voltage",
                      "A genuine, intended increase in demand"],
     "verification": ["Check whether process demand was changed on purpose.",
                      "With power isolated, check the driven load turns freely.",
                      "Measure supply voltage."]},
    {"id": "reduced_drive", "title": "Reduced drive input", "requires": ("rpm", "down"),
     "expects": {"rpm": "down", "current": "down", "vibration": "down"},
     "rationale": "Speed, current and vibration have all fallen together, the pattern of less power reaching the machine rather than a mechanical fault.",
     "alternatives": ["Speed setpoint was lowered on purpose", "Supply or controller problem", "Loose electrical connection"],
     "verification": ["Check the speed setpoint and controller.",
                      "With power isolated, check terminal connections.", "Measure supply voltage."]},
]

# Textbook cause-and-effect chain for each cause pattern: what is physically happening,
# step by step, and what usually starts it. General engineering knowledge, not a finding.
MECHANISMS = {
    "bearing": {
        "headline": "Rolling surfaces in the bearing are breaking down",
        "chain": [
            ["Lubricant film fails", "Grease has aged, leaked or been contaminated, so the film that separates balls and races becomes too thin."],
            ["Metal touches metal", "Balls run directly on the races. Microscopic pits (spalls) form and every ball that rolls over one produces an impact."],
            ["Vibration climbs", "Those repeated impacts shake the housing. Vibration is the first and strongest signal to move."],
            ["Friction turns to heat", "Rolling resistance rises, so the motor draws more current and the bearing seat runs hotter."],
            ["Speed sags", "Part of the shaft power is now lost in the bearing, so the shaft turns slightly slower under the same load."]],
        "root_causes": ["Lubrication starved or contaminated", "Misalignment loading one side of the bearing",
                        "Overload or belt tension too high", "Normal end-of-life fatigue"],
        "if_ignored": "Damage accelerates: the cage breaks up, the shaft drops and the rotor can rub the stator."},
    "imbalance": {
        "headline": "The rotating mass is no longer centred on its axis",
        "chain": [
            ["Mass moves off-centre", "Dirt build-up, a lost balance weight, a damaged blade or something working loose shifts the centre of mass."],
            ["A rotating force appears", "Every revolution the off-centre mass pulls the shaft outwards. The force grows with the square of speed."],
            ["Vibration climbs", "The whole machine is shaken once per revolution, so vibration rises sharply."],
            ["Nothing else changes yet", "Friction is unchanged, so current, temperature and speed stay at baseline. That is what separates this from a bearing fault."]],
        "root_causes": ["Deposit build-up on blades or impeller", "A blade or vane damaged or eroded",
                        "Loose hub, key or mounting bolts", "Lost balance weight"],
        "if_ignored": "The once-per-revolution force hammers the bearings and loosens fasteners until a bearing fails."},
    "cooling": {
        "headline": "Heat is being produced normally but removed too slowly",
        "chain": [
            ["Cooling path degrades", "Blocked fins, a clogged intake or a failing fan reduces the air or fluid that carries heat away."],
            ["Same heat in, less heat out", "Electrical and mechanical load are unchanged, so heat generation is the same."],
            ["Temperature climbs slowly", "The machine stores the difference. Temperature rises with a thermal lag and settles at a higher level."],
            ["Other signals stay flat", "Current, vibration and speed do not move, which rules out friction or overload as the heat source."]],
        "root_causes": ["Blocked cooling fins or air intake", "Cooling fan damaged or slipping",
                        "Higher ambient temperature", "Coolant or oil level low"],
        "if_ignored": "Insulation and lubricant age roughly twice as fast for every 10 °C of extra temperature."},
    "overload": {
        "headline": "The machine is being asked for more torque than normal",
        "chain": [
            ["Load torque rises", "The driven equipment resists more: a blockage, binding, or a real increase in demand."],
            ["Current follows torque", "The motor draws more current to produce the extra torque."],
            ["Speed sags", "The machine slides down its torque-speed curve and turns slightly slower."],
            ["Heat follows current squared", "Winding losses grow with the square of current, so temperature climbs after a delay."]],
        "root_causes": ["Blockage or binding in the driven equipment", "Process demand increased",
                        "Low supply voltage", "Discharge restriction or closed valve"],
        "if_ignored": "Sustained over-current overheats the windings and trips protection."},
    "reduced_drive": {
        "headline": "Less power is reaching the machine",
        "chain": [
            ["Drive input falls", "The supply, the controller or a connection is delivering less than it should."],
            ["Speed drops", "With less input the shaft slows down."],
            ["Current and vibration fall too", "A slower machine does less work and shakes less, so everything falls together."],
            ["Output suffers", "Flow, pressure or throughput drops with speed."]],
        "root_causes": ["Speed setpoint lowered", "Supply voltage sag", "Loose or corroded terminal",
                        "Controller or drive fault"],
        "if_ignored": "The process under-delivers, and a loose terminal can overheat."},
}

COMPONENT_BY_TYPE = {
    "bearing": {"default": "bearing_de", "pump": "bearing_housing"},
    "imbalance": {"dc_motor": "rotor", "motor": "rotor", "cooling_fan": "blades", "pump": "impeller",
                  "compressor": "flywheel", "gearbox": "wheel"},
    "cooling": {"dc_motor": "cooling_fan", "motor": "cooling_fan", "cooling_fan": "motor", "pump": "motor",
                "compressor": "cylinder_head", "gearbox": "housing_lower"},
    "overload": {"dc_motor": "shaft", "motor": "shaft", "pump": "impeller", "compressor": "cylinder_head",
                 "gearbox": "output_shaft", "cooling_fan": "blades"},
    "reduced_drive": {"dc_motor": "terminal_box", "motor": "terminal_box", "gearbox": "input_shaft",
                      "default": "motor"},
}


def _direction(change_pct, kind):
    tolerance = TOLERANCE_PCT.get(kind)
    if tolerance is None or change_pct is None:
        return "not assessed"
    if change_pct > tolerance:
        return "up"
    if change_pct < -tolerance:
        return "down"
    return "normal"


def diagnose(asset, channels, history, baselines, freshness, scenario=None, now=None, stopped=False):
    """Return the diagnosis payload (a plain dict that is stored and shown as-is)."""
    now = now or time.time()
    recent = [row for row in history if row["ts"] >= now - WINDOW_S]
    quality_issues = sorted({flag for row in recent for flag in (row.get("quality") or [])})
    result = {
        "asset_id": asset["asset_id"], "asset_name": asset["name"], "data_mode": asset["data_mode"],
        "method": METHOD, "rule_version": RULE_VERSION, "generated_ts": now, "window_s": WINDOW_S,
        "data_quality": {"freshness": freshness, "samples_in_window": len(recent), "issues": quality_issues},
        "source": {"from_ts": recent[0]["ts"] if recent else None, "to_ts": recent[-1]["ts"] if recent else None,
                   "samples": len(recent), "table": "telemetry"},
        "evidence": [], "unavailable": [], "hypotheses": [], "simulation": scenario,
        "confidence_note": "Evidence strength counts how many signals agree with a cause. It is not a "
                           "probability and has not been validated against real faults.",
    }
    if scenario:
        result["simulation_note"] = ("This asset is SIMULATED and a fault scenario is running. The readings and "
                                     "this diagnosis describe a software scenario, not physical equipment.")
    elif asset["data_mode"] != "REAL":
        result["simulation_note"] = f"This asset's data is {asset['data_mode']}, not live physical measurement."

    if stopped:
        result.update(status="ASSET STOPPED — NO ASSESSMENT",
                      observation="The simulated machine is stopped or still changing speed, so its readings are "
                                  "not compared with the running baseline.",
                      localisation="Not attempted.", recommended_next_steps=["Start the machine and let it reach speed."])
        return result
    if freshness not in ("LIVE", "SIMULATED", "REPLAY") or len(recent) < MIN_SAMPLES:
        result.update(status="INSUFFICIENT DATA",
                      observation=f"Only {len(recent)} usable samples in the last {WINDOW_S} s (data is {freshness}). "
                                  "No conclusion is drawn.",
                      localisation="Not attempted.", recommended_next_steps=["Restore the data connection and try again."])
        return result

    observed = {}
    for ch in channels:
        values = [row["vals"].get(ch["key"]) for row in recent]
        values = [v for v in values if v is not None]
        base = baselines.get(ch["key"])
        if len(values) < MIN_SAMPLES or not base or not base.get("median"):
            result["unavailable"].append({"channel": ch["key"], "label": ch["label"],
                                          "reason": "no valid readings in the window" if len(values) < MIN_SAMPLES
                                          else "no baseline yet"})
            continue
        level = statistics.median(values)
        change = (level - base["median"]) / abs(base["median"]) * 100
        direction = _direction(change, ch.get("kind"))
        beyond = None
        if ch.get("warn") is not None:
            high = ch.get("direction", "high") == "high"
            beyond = "critical" if ch.get("crit") is not None and (level > ch["crit"] if high else level < ch["crit"]) \
                else "warning" if (level > ch["warn"] if high else level < ch["warn"]) else None
        result["evidence"].append({
            "channel": ch["key"], "label": ch["label"], "unit": ch["unit"], "kind": ch.get("kind"),
            "baseline": round(base["median"], 4), "baseline_source": base.get("source", ""),
            "recent": round(level, 4), "change_pct": round(change, 1), "direction": direction,
            "limit_warn": ch.get("warn"), "limit_crit": ch.get("crit"), "limit_source": ch.get("limit_source"),
            "beyond_limit": beyond, "samples": len(values),
            "rule": f"deviation if change exceeds ±{TOLERANCE_PCT.get(ch.get('kind'), 0):g}% of baseline"
            if ch.get("kind") in TOLERANCE_PCT else "shown for context; not used by the rules"})
        if ch.get("kind") in TOLERANCE_PCT:
            # if two channels measure the same kind, keep the one that moved most
            if ch["kind"] not in observed or abs(change) > abs(observed[ch["kind"]][1]):
                observed[ch["kind"]] = (direction, change, ch["label"])

    deviations = {kind: obs for kind, obs in observed.items() if obs[0] in ("up", "down")}
    if not deviations:
        result.update(status="NO ANOMALY DETECTED",
                      observation="All assessed signals are within their tolerance of the baseline.",
                      localisation="Nothing to localise.", recommended_next_steps=["No action indicated by the data."])
        return result

    for h in HYPOTHESES:
        kind, need = h["requires"]
        if observed.get(kind, ("",))[0] != need:
            continue
        supporting, contradicting, missing = [], [], []
        for k, expected in h["expects"].items():
            if k not in observed:
                missing.append(k)
                continue
            got, change, label = observed[k]
            text = f"{label}: {change:+.1f}% ({got})"
            if got == expected:
                (supporting if expected != "normal" else supporting).append(text)
            else:
                contradicting.append(f"{text}, expected {expected}")
        moved = sum(1 for k, e in h["expects"].items() if e != "normal" and observed.get(k, ("",))[0] == e)
        strength = "STRONG" if moved >= 3 and not contradicting else "MODERATE" if moved >= 2 and len(contradicting) <= 1 else "LIMITED"
        component_map = COMPONENT_BY_TYPE[h["id"]]
        result["hypotheses"].append({
            "id": h["id"], "title": h["title"], "evidence_strength": strength,
            "score": len(supporting) - len(contradicting),
            "supporting": supporting, "contradicting": contradicting,
            "not_available": missing, "rationale": h["rationale"], "alternatives": h["alternatives"],
            "verification": h["verification"],
            "component_id": component_map.get(asset["type"], component_map.get("default")),
            "component_status": "SUSPECTED COMPONENT — NOT CONFIRMED"})
    result["hypotheses"].sort(key=lambda x: -x["score"])

    changed = "; ".join(f"{label} {change:+.1f}%" for _, change, label in deviations.values())
    result["observation"] = f"Deviation from baseline: {changed}."
    if not result["hypotheses"]:
        result.update(status="UNCLASSIFIED DEVIATION — REQUIRES REVIEW",
                      localisation="The pattern does not match any rule, so no component is suggested.",
                      recommended_next_steps=["Review the trends manually.", "Check sensor mounting and wiring."])
        return result

    top = result["hypotheses"][0]
    result["status"] = "SUSPECTED ANOMALY — REQUIRES VERIFICATION"
    if top["evidence_strength"] == "LIMITED" or not top["component_id"]:
        result["localisation"] = ("Evidence is too limited to point at one component. The highlighted part is the "
                                  "first place to look, nothing more.")
    else:
        result["localisation"] = ("The highlighted part is the most plausible location given the signals. "
                                  "It is suspected, not confirmed; only inspection can confirm it.")
    result["recommended_next_steps"] = top["verification"] + [
        "Follow the site's approved maintenance procedure; this output is decision support, not an instruction."]
    return result
