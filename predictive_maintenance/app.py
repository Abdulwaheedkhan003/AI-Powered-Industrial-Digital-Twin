"""Motor condition monitoring dashboard.

Run with:  python -m streamlit run app.py      (or double-click run_dashboard.bat)

Data can come from the ESP32 (live), a saved session, an imported CSV or the
synthetic demo generator. The page always states which one is on screen.
"""
import time
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import config
from config import ACCEL_COLUMNS, SENSORS, SERIAL_BAUD, SOURCE_REAL, SOURCE_SYNTHETIC
from src.detection.drift_monitor import drift_report
from src.detection.evaluation import (NO_LABELS_MESSAGE, evaluate_result,
                                      synthetic_pipeline_check)
from src.detection.isolation_forest import (ModelError, list_models, load_model,
                                            save_meta, save_model, train_model)
from src.detection.statistical_detector import fit_baselines, robust_z
from src.detection.threshold_rules import (SIGNALS, load_settings, save_settings,
                                           suggest_thresholds, validate_settings)
from src.ingestion.csv_logger import save_log
from src.ingestion.csv_reader import DataImportError, load_csv
from src.ingestion.data_generator import (SCENARIOS, generate_accel_bursts,
                                          generate_demo_data)
from src.ingestion.serial_reader import SerialReader, available_ports
from src.monitoring import notes as notes_store
from src.monitoring.alerts import (RESOLUTIONS, filter_alerts, load_alerts,
                                   sync_alerts, update_alert)
from src.monitoring.engine import get_signals, run_detectors
from src.monitoring.sensor_status import sensor_status
from src.monitoring.session_manager import (compare_sessions, describe_session,
                                            get_session_meta, list_sessions,
                                            load_session, save_demo_session,
                                            set_session_meta, training_features)
from src.processing.fft_analysis import (dominant_frequency, sampling_info,
                                         spectrum)
from src.processing.feature_pipeline import feature_columns
from src.processing.statistics import correlation_matrix, summary_statistics
from src.processing.trends import rolling_rate, trend_summary
from src.processing.validation import (mask_invalid, quality_flags,
                                       sensor_quality_summary, timestamp_quality)
from src.reporting.summary_report import LIMITATIONS, build_report_html

LIVE_REFRESH_S = 2
PAGES = ["Overview", "Live / demo monitoring", "Sensor charts", "Data quality",
         "Vibration analysis", "Anomaly detection", "Alert history",
         "Session comparison", "Model evaluation", "Reports and exports", "Settings"]

st.set_page_config(page_title="Motor condition monitoring", layout="wide")
state = st.session_state


def append_live(key, new_rows, path):
    """Log newly received rows to disk and keep them in memory for display."""
    if len(new_rows):
        save_log(new_rows, path, append=True)
        old = state.get(key)
        state[key] = new_rows if old is None else pd.concat([old, new_rows], ignore_index=True)
    return state.get(key)


def time_chart(frame, column, title, alerts=None):
    """Line chart over time with dashed red lines where alerts were raised."""
    chart = alt.Chart(frame).mark_line().encode(
        x=alt.X("timestamp:T", title="Time"),
        y=alt.Y(f"{column}:Q", title=title, scale=alt.Scale(zero=False)))
    if alerts is not None and len(alerts):
        marks = alt.Chart(alerts).mark_rule(color="red", strokeDash=[4, 3]).encode(
            x="timestamp:T", tooltip=["severity", "alert_type", "explanation"])
        chart = chart + marks
    st.altair_chart(chart.interactive(), use_container_width=True)


def sidebar_data_source():
    """Return (df, accel, report, live, mode)."""
    st.sidebar.header("Data source")
    mode = st.sidebar.radio("Source", ["ESP32 (serial)", "Saved session", "Synthetic demo",
                                       "Import CSV"], index=0 if "reader" in state else 2)
    df, accel, report, live = None, None, [], False
    if mode != "ESP32 (serial)" and "reader" in state:
        state.pop("reader").stop()          # leaving live mode closes the port

    if mode == "ESP32 (serial)":
        reader = state.get("reader")
        if reader is None:
            ports = available_ports()
            if ports:
                port = st.sidebar.selectbox("Port", [p[0] for p in ports],
                                            format_func=lambda d: f"{d} — {dict(ports)[d]}")
            else:
                port = None
                st.sidebar.warning("No serial ports found. Plug in the ESP32 and press Rescan.")
                st.sidebar.button("Rescan")
            baud = st.sidebar.number_input("Baud rate", value=SERIAL_BAUD, step=1)
            st.sidebar.caption("Keep the motor OFF while connecting: opening the port resets "
                               "the ESP32, and the firmware zeroes the current sensor at start-up.")
            if st.sidebar.button("Connect and start session", disabled=port is None, type="primary"):
                state.reader = SerialReader(port, int(baud))
                state.reader.start()
                state.live_slow = state.live_accel = None
                st.rerun()
            st.info("Not connected. Choose the ESP32's COM port in the sidebar and press "
                    "Connect. Nothing is shown here until real data arrives; use *Synthetic "
                    "demo* to explore the software without hardware.")
        else:
            live = True
            new_slow, new_accel = reader.drain()
            df = append_live("live_slow", new_slow, config.RAW_DIR / f"{reader.session_id}.csv")
            accel = append_live("live_accel", new_accel,
                                config.RAW_DIR / f"{reader.session_id}_accel.csv")
            show = {"connected": st.sidebar.success, "connecting": st.sidebar.info}
            show.get(reader.status, st.sidebar.error)(reader.message or reader.status)
            age = reader.seconds_since_last_line()
            if reader.status == "connected" and (age is None or age > 5):
                st.sidebar.warning("Port is open but no readable data in the last 5 s. Check "
                                   "the baud rate and that the sketch is running.")
            st.sidebar.checkbox("Auto-refresh", value=True, key="auto_refresh",
                                help="Untick to pause the page while you inspect a chart. "
                                     "Recording continues in the background.")
            st.sidebar.write(f"Session `{reader.session_id}`")
            st.sidebar.write(f"Lines accepted: {reader.lines_ok} · rejected: {reader.lines_bad}")
            if reader.recent_problems:
                with st.sidebar.expander("Recent line problems"):
                    for problem in list(reader.recent_problems)[-8:]:
                        st.write("- " + problem)
            if st.sidebar.button("Disconnect and end session"):
                state.pop("reader").stop()
                st.rerun()
            report = [f"Live recording, logged to data/raw/{reader.session_id}.csv as it "
                      "arrives. No cleaning applied to the log."]
    elif mode == "Saved session":
        sessions = list_sessions()
        if sessions.empty:
            st.info("No saved sessions yet. Real sessions are saved automatically while the "
                    "ESP32 is connected; demo sessions can be saved from the "
                    "*Live / demo monitoring* page.")
        else:
            labels = {r.file: f"{r.session_id}  [{r.data_source}]" for r in sessions.itertuples()}
            chosen = st.sidebar.selectbox("Session", list(labels), format_func=labels.get)
            df, accel, report = load_session(Path(chosen))
    elif mode == "Synthetic demo":
        scenario = st.sidebar.selectbox("Scenario", list(SCENARIOS))
        st.sidebar.caption(SCENARIOS[scenario])
        duration = st.sidebar.slider("Duration (s)", 60, 3600, 900, step=60)
        seed = st.sidebar.number_input("Random seed", value=42, step=1)
        df = generate_demo_data(scenario, duration_s=duration, seed=int(seed))
        accel = generate_accel_bursts(scenario, duration_s=duration, seed=int(seed))
        report = ["Generated in memory; no cleaning applied."]
    else:
        upload = st.sidebar.file_uploader("Sensor log (CSV)", type="csv")
        accel_upload = st.sidebar.file_uploader("Acceleration bursts (CSV, optional)", type="csv")
        if upload is not None:
            try:
                df, report = load_csv(upload)
            except DataImportError as err:
                st.error(f"Could not import this file: {err}")
        if accel_upload is not None:
            try:
                accel = pd.read_csv(accel_upload, parse_dates=["burst_time"])
                missing = [c for c in ["burst_id", "t_us", *ACCEL_COLUMNS] if c not in accel.columns]
                if missing:
                    raise ValueError(f"missing column(s) {missing}")
            except Exception as err:
                accel = None
                st.error(f"Could not import the acceleration file: {err}")
    return df, accel, report, live, mode


def reference_baselines(settings, source):
    """Statistical baselines from the user's reference session, if usable."""
    ref = settings["statistical"].get("reference_session")
    if not ref or not Path(ref).exists():
        return None, None
    ref_df, ref_accel, _ = load_session(Path(ref))
    if set(ref_df["data_source"]) != {source}:
        return None, ("The reference session has a different data source than this data, "
                      "so it is not used. The baseline is the start of this session instead.")
    return fit_baselines(get_signals(ref_df, ref_accel)[0]), None


# =============================================================================
st.title("Motor condition monitoring")
df, accel, report, live, mode = sidebar_data_source()
page = st.sidebar.radio("Page", PAGES, key="page")

if page == "Settings" and (df is None or df.empty):
    df = None        # Settings works without data
elif df is None or df.empty:
    if live:
        st.info("Waiting for the first complete line from the ESP32…")
        if state.get("auto_refresh", True):
            time.sleep(LIVE_REFRESH_S)
            st.rerun()
    st.stop()

settings = load_settings()

if df is not None:
    sources = sorted(df["data_source"].unique())
    source = sources[0] if len(sources) == 1 else "mixed"
    if sources == [SOURCE_SYNTHETIC]:
        st.warning("SYNTHETIC DEMO DATA — generated by software. These are not "
                   "measurements of the motor.")
    elif sources == [SOURCE_REAL]:
        st.success("REAL HARDWARE DATA" + (" — live from the ESP32." if live else " — recorded.")
                   + " Sensors are uncalibrated.")
    else:
        st.error(f"Data source: {', '.join(sources)}. Origin is unknown or mixed; "
                 "treat any conclusions with caution.")
    source_label = "synthetic" if sources == [SOURCE_SYNTHETIC] else ", ".join(sources)

    compatible = [m["model_version"] for m in list_models() if m["data_source"] == source]
    chosen_model = st.sidebar.selectbox(
        "AI model", ["None"] + compatible, index=1 if compatible else 0,
        help="Only models trained on the same kind of data (real or synthetic) are listed.")
    model = load_model(chosen_model) if chosen_model != "None" else None

    baselines, baseline_warning = reference_baselines(settings, source)
    result = run_detectors(df, accel, settings, model, stat_baselines=baselines)
    alerts = result["alerts"]
    if (live or mode == "Saved session") and len(alerts):
        sync_alerts(alerts)                       # recorded sessions keep an alert history
    session_id = result["session_id"]
    quality = sensor_quality_summary(df)
    timing = timestamp_quality(df)
    status = sensor_status(df, now=datetime.now() if live else None)
    present = [c for c in SENSORS if c in df.columns and df[c].notna().any()]
    bursts = result["burst_features"]
    active = alerts[alerts["cleared_at"].isna()] if len(alerts) else alerts

# ---- 1. Overview ------------------------------------------------------------
if page == "Overview":
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Samples", len(df))
    c2.metric("Duration (s)", f"{(df['timestamp'].max() - df['timestamp'].min()).total_seconds():.0f}")
    if "approx_sample_rate_hz" in timing:
        c3.metric("Sample rate (Hz)", f"{timing['approx_sample_rate_hz']:.2f}")
    c4.metric("Active alerts", len(active))
    st.write(f"**Session:** `{session_id}` · **Source:** {source_label} · "
             f"**AI model:** {chosen_model}")
    st.subheader("Sensor status")
    st.dataframe(status[["status", "last_valid_value", "unit", "detail"]], use_container_width=True)
    st.subheader("Session details")
    meta = get_session_meta(session_id)
    with st.form("session_meta"):
        condition = st.text_input("Operating condition (supply voltage, speed setting, load)",
                                  meta["operating_condition"],
                                  help="Free text. Speed and load are not measured by the hardware.")
        comment = st.text_area("Session notes", meta["notes"])
        if st.form_submit_button("Save session details"):
            set_session_meta(session_id, condition, comment)
            st.success("Saved.")
    st.subheader("Import / cleaning report")
    for line in report:
        st.write("- " + line)

# ---- 2. Live / demo monitoring ---------------------------------------------
elif page == "Live / demo monitoring":
    if live:
        st.write(f"**Connection:** {state.reader.status} — {state.reader.message}")
    elif mode == "Synthetic demo":
        st.write("**Connection:** none (demo mode, no hardware involved).")
        if st.button("Save this demo session"):
            path = save_demo_session(df, accel)
            st.success(f"Saved as {path.name} in data/demo.")
    else:
        st.write("**Connection:** none (recorded data).")
    st.subheader("Latest valid reading per sensor")
    cols = st.columns(4)
    for i, (label, row) in enumerate(status.iterrows()):
        value = "–" if row["last_valid_value"] is None else f"{row['last_valid_value']:.4g} {row['unit']}"
        cols[i % 4].metric(label, value, row["status"], delta_color="off")
    st.subheader("Rule-based alert status")
    if len(active):
        for a in active.itertuples():
            (st.error if a.severity == "critical" else st.warning)(
                f"**{a.severity.upper()}** ({a.alert_type}) since {a.timestamp:%H:%M:%S}: {a.explanation}")
    else:
        st.write("No active alerts." + ("" if any(r["enabled"] for r in settings["thresholds"].values())
                                        else " No thresholds are configured yet (see Settings)."))
    st.subheader("AI anomaly score")
    if result["scores"] is None:
        st.info(result["ai_message"] + " Train or select a model on the *Anomaly detection* page.")
    else:
        last = result["scores"].dropna(subset=["anomaly_score"]).tail(1)
        if len(last):
            st.metric("Latest window", f"{last['anomaly_score'].item():.3f}",
                      f"decision threshold {model[1]['threshold']:.3f}", delta_color="off")
    st.subheader("Recent measurements")
    st.dataframe(df.tail(20), use_container_width=True)

# ---- 3. Sensor charts -------------------------------------------------------
elif page == "Sensor charts":
    t_min = df["timestamp"].min().floor("s").to_pydatetime()
    t_max = df["timestamp"].max().ceil("s").to_pydatetime()
    start, end = (st.slider("Time range", t_min, t_max, (t_min, t_max), format="HH:mm:ss")
                  if t_max > t_min else (t_min, t_max))
    chosen = st.multiselect("Sensors", present, default=present,
                            format_func=lambda c: SENSORS[c]["label"])
    hide_invalid = st.checkbox("Hide out-of-range and sentinel readings", value=True)
    show_alerts = st.checkbox("Show alert markers", value=True)
    view = (mask_invalid(df) if hide_invalid else df)
    view = view[(view["timestamp"] >= start) & (view["timestamp"] <= end)]
    for col in chosen:
        st.subheader(f"{SENSORS[col]['label']} — {source_label}")
        marks = filter_alerts(alerts, start, end, signals=[col]) if show_alerts else None
        time_chart(view[["timestamp", col]], col, f"{SENSORS[col]['label']} [{SENSORS[col]['unit']}]", marks)
        st.caption(SENSORS[col]["note"])

    st.header("Trend against the starting baseline")
    if present:
        col = st.selectbox("Sensor", present, format_func=lambda c: SENSORS[c]["label"], key="trend_sensor")
        n_base = st.number_input("Baseline: first N valid readings", 10, 2000, 60)
        summary = trend_summary(df, col, baseline_samples=int(n_base))
        if not summary["valid"]:
            st.info(f"Trend analysis not available: {summary['reason']}.")
        else:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Baseline median", f"{summary['baseline_median']:.4g}")
            m2.metric("Recent median", f"{summary['recent_median']:.4g}",
                      f"{summary['change_from_baseline']:+.3g}", delta_color="off")
            m3.metric("Overall slope (per min)", f"{summary['slope_per_min']:+.3g}")
            m4.metric("Readings in persistent deviation",
                      "n/a" if summary["persistent_deviation_samples"] is None
                      else summary["persistent_deviation_samples"])
            rate = rolling_rate(df, col).rename("rate").rename_axis("timestamp").reset_index()
            time_chart(rate, "rate", f"Rate of change [{SENSORS[col]['unit']} per minute]")
        st.caption("The baseline is how this recording started, not a verified healthy reference. "
                   "No universal limit is applied.")

    st.header("Relationships between sensors")
    matrix, counts = correlation_matrix(view)
    if matrix.empty:
        st.info("Need at least two sensors that vary, with 20 or more valid readings each.")
    else:
        st.write("Spearman rank correlation (−1 to +1) in the selected time range.")
        st.dataframe(matrix.round(2), use_container_width=True)
        x_col, y_col = st.columns(2)
        xs = x_col.selectbox("X axis", list(matrix.columns), index=0)
        ys = y_col.selectbox("Y axis", list(matrix.columns), index=min(1, len(matrix.columns) - 1))
        st.scatter_chart(view, x=xs, y=ys)
        st.caption("Correlation does not prove causation. Readings can move together because both "
                   "follow something unmeasured: motor speed, load, supply voltage, room "
                   "temperature, or simply time since power-on.")

# ---- 4. Data quality --------------------------------------------------------
elif page == "Data quality":
    st.caption("These checks describe the data, not the motor. A problem here points to a "
               "sensor, wiring or communication issue.")
    st.subheader("Sensor status")
    st.dataframe(status, use_container_width=True)
    st.subheader("Counts per sensor")
    st.dataframe(quality, use_container_width=True)
    st.caption("missing = no value · out_of_range = physically impossible for the sensor · "
               "sentinel = a value the firmware or sensor emits on failure · frozen = long run "
               "of identical readings · jumps = abrupt steps.")
    st.subheader("Sampling intervals")
    st.json(timing)
    if timing.get("gaps") or timing.get("irregular_intervals"):
        st.warning("Timestamps are irregular or have gaps. This indicates a communication "
                   "interruption or a busy ESP32 loop, not a motor event.")
    st.subheader("Statistics (valid readings only)")
    st.dataframe(summary_statistics(df), use_container_width=True)
    st.caption("min/max/mean/median/std/p05/p95 are in each sensor's unit. p05 and p95 are the "
               "5th and 95th percentiles.")

# ---- 5. Vibration analysis --------------------------------------------------
elif page == "Vibration analysis":
    if bursts is None or bursts.empty:
        st.info("This dataset has no raw acceleration bursts, so windowed RMS and FFT cannot be "
                "computed. The original sketch sends one pre-processed vibration number per "
                "line; raw bursts need firmware v2 (see the firmware folder). The synthetic "
                "demo includes bursts.")
    else:
        good = bursts[bursts["valid"].astype(bool)]
        st.caption("Gravity and constant offset are removed by subtracting each axis's mean "
                   "within a burst. Values are in g. Sensor calibration and mounting are not verified.")
        if good.empty:
            st.warning("No burst is long and complete enough to analyse.")
        else:
            st.subheader(f"Vibration RMS per burst [g] — {source_label}")
            st.line_chart(good, x="burst_time", y=["rms_x_g", "rms_y_g", "rms_z_g", "rms_total_g"])
            st.subheader("Peak and variability per burst")
            st.dataframe(good.drop(columns=["valid", "reason"]).set_index("burst_id"),
                         use_container_width=True)
            st.caption("Crest factor = peak ÷ RMS. These numbers describe how strongly the sensor "
                       "shook; they do not identify a component.")
            st.subheader("Frequency spectrum of one burst")
            b1, b2 = st.columns(2)
            ids = good["burst_id"].tolist()
            burst_id = b1.selectbox("Burst", ids, index=len(ids) - 1)
            axis = b2.selectbox("Axis", ACCEL_COLUMNS, index=2)
            burst = accel[accel["burst_id"] == burst_id]
            info = sampling_info(burst["t_us"])
            if not info["valid"]:
                st.error(f"Spectrum not available: {info['reason']}.")
            else:
                if not info["regular"]:
                    st.warning(f"{info['irregular_fraction']:.0%} of the samples in this burst "
                               "are unevenly spaced. The spectrum below is unreliable.")
                freqs, amp = spectrum(burst[axis], info["sample_rate_hz"])
                peak_hz = dominant_frequency(freqs, amp)
                f1, f2, f3, f4 = st.columns(4)
                f1.metric("Sample rate (Hz)", f"{info['sample_rate_hz']:.1f}")
                f2.metric("Resolution (Hz)", f"{info['resolution_hz']:.2f}")
                f3.metric("Highest visible (Hz)", f"{info['nyquist_hz']:.0f}")
                f4.metric("Dominant (Hz)", "none clear" if peak_hz is None else f"{peak_hz:.1f}")
                st.line_chart(pd.DataFrame({"frequency_hz": freqs, "amplitude_g": amp}),
                              x="frequency_hz", y="amplitude_g")
                st.caption("Hann window, mean removed. Vibration faster than the highest visible "
                           "frequency appears at a wrong, lower frequency. A peak does not by "
                           "itself indicate a bearing or gear fault.")

# ---- 6. Anomaly detection ---------------------------------------------------
elif page == "Anomaly detection":
    st.caption("Three independent detectors, shown separately. None of them is assumed to be "
               "better than the others; see *Model evaluation*.")
    counts = alerts.groupby("alert_type").agg(alerts=("alert_id", "count"),
                                              first_alert=("timestamp", "min"))
    names = {"threshold": "1. Configured thresholds", "statistical": "2. Statistical baseline",
             "ai_model": "3. Isolation Forest", "combined": "Combined rules"}
    summary = pd.DataFrame(index=list(names)).join(counts).fillna({"alerts": 0})
    summary.index = [names[i] for i in summary.index]
    st.dataframe(summary, use_container_width=True)
    for note in result["notes"]:
        st.caption("• " + note)

    st.header("1. Configured thresholds")
    enabled = [n for n, r in settings["thresholds"].items() if r["enabled"]]
    if not enabled:
        st.info("No thresholds are enabled. There are no built-in limits; set your own on the Settings page.")
    else:
        st.write("Enabled for: " + ", ".join(SIGNALS[n]["label"] for n in enabled))
        st.dataframe(alerts[alerts["alert_type"] == "threshold"][["timestamp", "signal", "severity", "explanation"]],
                     use_container_width=True, hide_index=True)

    st.header("2. Statistical baseline")
    st.write(f"Baseline taken from **{result['baseline_kind']}**; a reading is flagged beyond "
             f"{settings['statistical']['k']:g} robust spreads.")
    if baseline_warning:
        st.warning(baseline_warning)
    if result["baseline_kind"] != "the reference session":
        st.caption("With a start-of-session baseline, normal warm-up (motor temperature, MQ-5 "
                   "settling) is flagged too. Choose a reference session in Settings for a fairer baseline.")
    signal = st.selectbox("Signal", list(result["signals"]), format_func=lambda n: SIGNALS[n]["label"])
    z = robust_z(result["signals"][signal], result["baselines"].get(signal))
    if z is None:
        st.info("No usable baseline for this signal (too few readings or it never changes).")
    else:
        time_chart(z.rename("z").rename_axis("timestamp").reset_index(), "z",
                   "Deviation from baseline [robust spreads]",
                   alerts[(alerts["alert_type"] == "statistical") & (alerts["signal"] == signal)])

    st.header("3. Isolation Forest (AI)")
    st.write(result["ai_message"])
    if result["scores"] is not None:
        meta = model[1]
        scores = result["scores"].rename(columns={"window_start": "timestamp"})
        scores["decision threshold"] = meta["threshold"]
        st.line_chart(scores, x="timestamp", y=["anomaly_score", "decision threshold"])
        st.caption(f"One score per {meta['config']['window_s']}-second window. Higher = less like "
                   "the training data. The score does not say what is different or why.")
        for w in meta["warnings"]:
            st.warning(w)
        table, message = drift_report(result["window_features"], meta)
        st.subheader("Drift from the training data")
        st.write(message)
        st.dataframe(table, use_container_width=True)
        with st.expander("Model record"):
            st.json(meta)

    st.header("Train a new model")
    st.write("Train only on data you believe shows healthy operation, covering start-up and "
             "the speeds and loads you will monitor.")
    sessions = list_sessions()
    same = sessions[sessions["data_source"] == source] if not sessions.empty else sessions
    options = ["This dataset"] + same["file"].tolist() if not same.empty else ["This dataset"]
    picked = st.multiselect("Training data", options, default=["This dataset"],
                            format_func=lambda f: f if f == "This dataset" else Path(f).stem)
    conditions = st.text_input("Operating conditions of the training data")
    confirm = st.checkbox("I confirm this data represents healthy operation, as far as I know.")
    if source == SOURCE_SYNTHETIC:
        st.caption("Synthetic training data gives a software-test model. It will be labelled as "
                   "such and cannot be used on real measurements.")
    if st.button("Train model", disabled=not (picked and confirm and conditions.strip())):
        try:
            tables, ids = [], []
            if "This dataset" in picked:
                tables.append(result["window_features"] if model is None or model[1]["config"]["window_s"] == 60
                              else run_detectors(df, accel, settings)["window_features"])
                ids.append(session_id)
            files = [Path(f) for f in picked if f != "This dataset"]
            if files:
                more, _, more_ids = training_features(files)
                tables.append(more)
                ids.append(more_ids)
            pipeline, meta = train_model(pd.concat(tables, ignore_index=True), data_source=source,
                                         training_id=", ".join(ids), operating_notes=conditions.strip())
            save_model(pipeline, meta)
            st.success(f"Saved model {meta['model_version']}. Select it in the sidebar.")
            for w in meta["warnings"]:
                st.warning(w)
        except (ModelError, ValueError) as err:
            st.error(str(err))

# ---- 7. Alert history -------------------------------------------------------
elif page == "Alert history":
    history = load_alerts()
    if mode in ("Synthetic demo", "Import CSV"):
        st.caption("Alerts from demo and imported data are not stored automatically.")
        if len(alerts) and st.button(f"Record this dataset's {len(alerts)} alert(s) in the history"):
            history = sync_alerts(alerts)
    if history.empty:
        st.info("The alert history is empty.")
    else:
        f1, f2, f3, f4 = st.columns(4)
        sev = f1.multiselect("Severity", sorted(history["severity"].unique()))
        typ = f2.multiselect("Alert type", sorted(history["alert_type"].unique()))
        sig = f3.multiselect("Sensor / signal", sorted(history["signal"].unique()))
        dates = f4.date_input("Dates", (history["timestamp"].min().date(), history["timestamp"].max().date()))
        start, end = (dates[0], dates[-1]) if isinstance(dates, (tuple, list)) and dates else (None, None)
        shown = filter_alerts(history, start, pd.Timestamp(end) + pd.Timedelta(days=1) if end else None,
                              signals=sig, severities=sev, types=typ)
        st.dataframe(shown, use_container_width=True, hide_index=True)
        st.download_button("Download shown alerts (CSV)", shown.to_csv(index=False),
                           file_name="alert_history.csv", mime="text/csv")
        if len(shown):
            st.subheader("Acknowledge or annotate an alert")
            labels = {a.alert_id: f"{a.timestamp:%Y-%m-%d %H:%M:%S} · {a.signal} · {a.severity}"
                      for a in shown.itertuples()}
            with st.form("ack"):
                alert_id = st.selectbox("Alert", list(labels), format_func=labels.get)
                row = shown.set_index("alert_id").loc[alert_id]
                ack = st.checkbox("Acknowledged", bool(row["acknowledged"]))
                resolution = st.selectbox("Resolution", RESOLUTIONS, index=RESOLUTIONS.index(row["resolution"]))
                note = st.text_input("Operator note", row["operator_note"])
                if st.form_submit_button("Save"):
                    update_alert(alert_id, acknowledged=ack, operator_note=note, resolution=resolution)
                    st.rerun()

# ---- 8. Session comparison --------------------------------------------------
elif page == "Session comparison":
    sessions = list_sessions()
    if len(sessions) < 2:
        st.info("Save at least two sessions to compare them. Real sessions are saved while the "
                "ESP32 is connected; demo sessions from the *Live / demo monitoring* page.")
    else:
        st.dataframe(sessions.drop(columns=["file"]), use_container_width=True, hide_index=True)
        labels = {r.file: f"{r.session_id}  [{r.data_source}]" for r in sessions.itertuples()}
        a_col, b_col = st.columns(2)
        file_a = a_col.selectbox("Session A", list(labels), format_func=labels.get, index=0)
        file_b = b_col.selectbox("Session B", list(labels), format_func=labels.get, index=1)
        df_a, _, _ = load_session(Path(file_a))
        df_b, _, _ = load_session(Path(file_b))
        table, warnings = compare_sessions(df_a, df_b, get_session_meta(df_a["session_id"].iloc[0]),
                                           get_session_meta(df_b["session_id"].iloc[0]))
        for w in warnings:
            st.warning(w)
        st.dataframe(table, use_container_width=True)
        st.caption("median_change = B − A. A difference is only meaningful when both sessions "
                   "ran under the same operating conditions with the sensors mounted the same way.")
        common = [c for c in present if c in df_a.columns and c in df_b.columns]
        if common:
            col = st.selectbox("Overlay sensor", common, format_func=lambda c: SENSORS[c]["label"])
            parts = []
            for name, d in (("A", mask_invalid(df_a)), ("B", mask_invalid(df_b))):
                parts.append(pd.DataFrame({"seconds": (d["timestamp"] - d["timestamp"].iloc[0]).dt.total_seconds(),
                                           "value": d[col], "session": name}))
            st.line_chart(pd.concat(parts), x="seconds", y="value", color="session")

# ---- 9. Model evaluation ----------------------------------------------------
elif page == "Model evaluation":
    st.write("**Anomaly detection is not fault classification.** The detectors only say a "
             "window is unusual compared with normal data. Naming a fault would need many "
             "recorded, verified examples of each fault, which do not exist for this motor.")
    st.header("This dataset")
    evaluation = evaluate_result(result)
    if evaluation is None:
        st.info(NO_LABELS_MESSAGE)
        st.dataframe(alerts.groupby("alert_type").size().rename("alerts raised").to_frame(),
                     use_container_width=True)
    else:
        if source == SOURCE_SYNTHETIC:
            st.warning("Labels come from the synthetic scenario generator. These numbers test "
                       "the software; they are not evidence of real fault-detection performance.")
        st.dataframe(evaluation, use_container_width=True)
        st.caption("Per 60-second window. A window counts as detected if one of that detector's "
                   "alerts was active during it. Blank = undefined (for example recall when "
                   "nothing was labelled anomalous). Detection latency is measured from the "
                   "first labelled window.")
        if model is not None and st.button("Store this evaluation in the model record"):
            meta = model[1]
            meta["evaluation"] = {"evaluated_on": session_id, "data_source": source,
                                  "date": datetime.now().isoformat(timespec="seconds"),
                                  "results": evaluation.astype(object).where(evaluation.notna(), None)
                                  .to_dict(orient="index")}
            save_meta(meta)
            st.success("Stored.")
    st.header("Software pipeline check (synthetic)")
    st.write("Trains on 12 synthetic baseline sessions and tests each detector on separately "
             "generated scenario sessions. Thresholds and the statistical baseline are set from "
             "the training sessions only.")
    if st.button("Run pipeline check"):
        with st.spinner("Generating sessions and training…"):
            state.pipeline_check = synthetic_pipeline_check()[0]
    if "pipeline_check" in state:
        st.warning("SYNTHETIC. This shows the software works end to end and how the detectors "
                   "differ on invented data. It says nothing about the real motor.")
        st.dataframe(state.pipeline_check, use_container_width=True, hide_index=True)

# ---- 10. Reports and exports ------------------------------------------------
elif page == "Reports and exports":
    prefix = "SYNTHETIC_" if source == SOURCE_SYNTHETIC else ""
    st.header("Exports")
    export_df = mask_invalid(df)
    export_df["data_quality_flags"] = quality_flags(df)
    st.caption("Cleaned data: out-of-range and sentinel readings are blanked (never replaced "
               "with estimates) and a data_quality_flags column is added.")
    e1, e2, e3, e4 = st.columns(4)
    e1.download_button("Cleaned data (CSV)", export_df.to_csv(index=False),
                       file_name=f"{prefix}{session_id}_cleaned.csv", mime="text/csv")
    if bursts is not None and len(bursts):
        e2.download_button("Vibration features (CSV)", bursts.to_csv(index=False),
                           file_name=f"{prefix}{session_id}_vibration.csv", mime="text/csv")
    windows = result["window_features"]
    if result["scores"] is not None:
        windows = windows.merge(result["scores"], on="window_start", how="left")
    e3.download_button("Window features + scores (CSV)", windows.to_csv(index=False),
                       file_name=f"{prefix}{session_id}_windows.csv", mime="text/csv")
    e4.download_button("Alerts (CSV)", alerts.to_csv(index=False),
                       file_name=f"{prefix}{session_id}_alerts.csv", mime="text/csv")

    st.header("Summary report")
    notes = notes_store.load_notes(session_id=session_id)
    stored = load_alerts()
    report_alerts = stored[stored["session_id"] == session_id] if (stored["session_id"] == session_id).any() else alerts
    html = build_report_html(session=describe_session(df), status=status, quality=quality,
                             timing=timing, statistics=summary_statistics(df), burst_features=bursts,
                             alerts=report_alerts, model_meta=model[1] if model else None,
                             ai_message=result["ai_message"], notes=notes)
    st.download_button("Download report (HTML)", html, file_name=f"{prefix}{session_id}_report.html",
                       mime="text/html")
    st.caption("Open the file in a browser and print it (Ctrl+P) to get a PDF.")

    st.header("Maintenance notes")
    with st.form("note", clear_on_submit=True):
        kind = st.selectbox("Type", notes_store.NOTE_KINDS)
        text = st.text_area("Note")
        confirmed = st.checkbox("This is a confirmed diagnosis (verified by inspection or measurement)")
        if st.form_submit_button("Add note"):
            try:
                notes_store.add_note(session_id, kind, text, confirmed)
                st.rerun()
            except ValueError as err:
                st.error(str(err))
    st.dataframe(notes[["created_at", "kind", "text", "confirmed_diagnosis"]],
                 use_container_width=True, hide_index=True)
    st.caption("Notes are observations. Only notes ticked as a confirmed diagnosis count as verified.")
    with st.expander("Important limitations"):
        for item in LIMITATIONS:
            st.write("- " + item)

# ---- 11. Settings -----------------------------------------------------------
elif page == "Settings":
    st.header("Warning and critical thresholds")
    st.caption("No limits are built in. Enter values that make sense for this motor and how "
               "the sensors are mounted. 'high' alerts above the level, 'low' below it.")
    with st.form("settings"):
        for name, rule in settings["thresholds"].items():
            info = SIGNALS[name]
            c0, c1, c2, c3 = st.columns([3, 1.2, 1.5, 1.5])
            rule["enabled"] = c0.checkbox(f"{info['label']} [{info['unit']}]", rule["enabled"], key=f"en_{name}")
            rule["direction"] = c1.selectbox("Direction", ["high", "low"],
                                             index=["high", "low"].index(rule["direction"]),
                                             key=f"dir_{name}", label_visibility="collapsed")
            rule["warning"] = c2.number_input("Warning", value=rule["warning"], key=f"w_{name}",
                                              format="%g", placeholder="warning", label_visibility="collapsed")
            rule["critical"] = c3.number_input("Critical", value=rule["critical"], key=f"c_{name}",
                                               format="%g", placeholder="critical", label_visibility="collapsed")
        st.subheader("Alert persistence")
        p = settings["persistence"]
        p1, p2, p3, p4 = st.columns(4)
        p["raise_after"] = p1.number_input("Raise after N consecutive readings", 1, 100, p["raise_after"])
        p["clear_after"] = p2.number_input("Clear after N normal readings", 1, 100, p["clear_after"])
        p["repeat_count"] = p3.number_input("Or N exceedances (0 = off)", 0, 100, p["repeat_count"])
        p["repeat_window_s"] = p4.number_input("…within this many seconds", 1, 3600, p["repeat_window_s"])
        st.subheader("Statistical baseline detector")
        s = settings["statistical"]
        s1, s2, s3 = st.columns(3)
        s["enabled"] = s1.checkbox("Enabled", s["enabled"])
        s["baseline_s"] = s2.number_input("Baseline: first N seconds", 10, 3600, int(s["baseline_s"]))
        s["k"] = s3.number_input("Flag beyond k robust spreads", 1.0, 20.0, float(s["k"]))
        saved = list_sessions()
        files = [None] + (saved["file"].tolist() if not saved.empty else [])
        current = s.get("reference_session") if s.get("reference_session") in files else None
        s["reference_session"] = st.selectbox(
            "Reference session for the baseline (comparable normal operation)", files,
            index=files.index(current), format_func=lambda f: "None — use the start of each session" if f is None else Path(f).stem)
        st.subheader("AI alert persistence")
        a1, a2 = st.columns(2)
        settings["ai"]["raise_after"] = a1.number_input("Raise after N consecutive windows", 1, 20, settings["ai"]["raise_after"])
        settings["ai"]["clear_after"] = a2.number_input("Clear after N normal windows", 1, 20, settings["ai"]["clear_after"])
        if st.form_submit_button("Save settings", type="primary"):
            errors = validate_settings(settings)
            if errors:
                for e in errors:
                    st.error(e)
            else:
                save_settings(settings)
                st.success("Settings saved.")

    if df is not None:
        with st.expander("Starting points from this dataset's baseline (for review, not applied)"):
            rows = {SIGNALS[n]["label"]: suggest_thresholds(b) for n, b in result["baselines"].items()}
            st.dataframe(pd.DataFrame({k: v for k, v in rows.items() if v}).T, use_container_width=True)
            st.caption("warning = baseline median + 4 spreads, critical = + 8 spreads. These mark "
                       "what would be unusual for this recording, not what is unsafe for the motor.")

    st.header("Combined multi-signal rules")
    st.caption("A combined rule is active while at least N of its signals have an active alert "
               "at the same time. No weights and no health score are used.")
    for i, rule in enumerate(settings["combined_rules"]):
        r1, r2 = st.columns([5, 1])
        r1.write(f"**{rule['name']}**: at least {rule['min_active']} of "
                 + ", ".join(SIGNALS[x]["label"] for x in rule["signals"]))
        if r2.button("Delete", key=f"del_{i}"):
            settings["combined_rules"].pop(i)
            save_settings(load_settings() | {"combined_rules": settings["combined_rules"]})
            st.rerun()
    with st.form("new_rule", clear_on_submit=True):
        name = st.text_input("Rule name")
        chosen = st.multiselect("Signals", list(SIGNALS), format_func=lambda n: SIGNALS[n]["label"])
        min_active = st.number_input("Minimum signals alerting together", 2, 9, 2)
        if st.form_submit_button("Add rule"):
            stored = load_settings()
            stored["combined_rules"].append({"name": name.strip() or "unnamed rule",
                                             "signals": chosen, "min_active": int(min_active)})
            errors = validate_settings(stored)
            if errors:
                for e in errors:
                    st.error(e)
            else:
                if any(SIGNALS[x]["category"] == "environment" for x in chosen):
                    st.warning("This rule includes environmental signals (gas, light, distance). "
                               "They are not motor-fault evidence.")
                save_settings(stored)
                st.rerun()

if live and state.get("auto_refresh", True):
    time.sleep(LIVE_REFRESH_S)
    st.rerun()
