import cv2
import time
import numpy as np
import streamlit as st
import plotly.graph_objects as go
from PIL import Image

import config


def render_dashboard(shared_state):
    """Renders the full Streamlit dashboard. Called by main.py on each refresh cycle."""

    st.set_page_config(
        page_title="VitalDrive AI — Driver Monitoring System",
        page_icon="🚗",
        layout="wide",
        initial_sidebar_state="collapsed"
    )

    # ─── Settings Sidebar ───
    with st.sidebar:
        st.header("⚙️ Settings")
        new_sim = st.checkbox("Simulation Mode", value=config.SIMULATION_MODE,
                              help="Fake sensor values if hardware missing")
        if new_sim != config.SIMULATION_MODE:
            config.SIMULATION_MODE = new_sim
            st.rerun()
        
        new_ip = st.text_input("ESP32-CAM IP", value=config.ESP32_CAM_IP)
        if new_ip != config.ESP32_CAM_IP:
            config.ESP32_CAM_IP = new_ip
            st.rerun()
            
        new_port = st.text_input("COM Port", value=config.SERIAL_PORT)
        if new_port != config.SERIAL_PORT:
            config.SERIAL_PORT = new_port
            st.rerun()

    # ─── Custom CSS ───
    st.markdown("""
    <style>
    .stApp { background-color: #0e1117; }
    .metric-card {
        background: #1a1d23; border-radius: 12px; padding: 16px;
        border: 1px solid #2d3139; text-align: center;
    }
    .metric-value { font-size: 2.2rem; font-weight: 700; }
    .metric-label { font-size: 0.85rem; color: #888; text-transform: uppercase; }
    .status-normal { background: #0d3320; border: 1px solid #1a6b3f; border-radius: 8px; padding: 12px; text-align: center; }
    .status-warning { background: #3d2e00; border: 1px solid #7a5c00; border-radius: 8px; padding: 12px; text-align: center; }
    .status-emergency { background: #3d0a0a; border: 1px solid #8b1a1a; border-radius: 8px; padding: 12px; text-align: center; animation: flash 1s infinite; }
    @keyframes flash { 50% { opacity: 0.6; } }
    .history-table { font-size: 0.85rem; }
    </style>
    """, unsafe_allow_html=True)

    # ─── Read shared state ───
    with shared_state["lock"]:
        sensor_data = shared_state.get("sensor_data", {}).copy()
        logic_result = shared_state.get("logic_result", {}).copy()
        vision_status = shared_state.get("vision_status", "NO_FACE")
        cancel_remaining = shared_state.get("cancel_remaining", 0)
        current_frame = shared_state.get("current_frame", None)
        alert_history = list(shared_state.get("alert_history", []))
        demo_stage = shared_state.get("demo_stage", "")
        perclos = shared_state.get("perclos", 0.0)
        blink_rate = shared_state.get("blink_rate", 0)
        safety_score = shared_state.get("safety_score", 100)

    # Extract values
    hr = sensor_data.get("hr", 0)
    spo2 = sensor_data.get("spo2", 0)
    ecg = sensor_data.get("ecg", 0.0)
    hrv = sensor_data.get("hrv", 0.0)
    spo2_trend = sensor_data.get("spo2_trend", "STABLE")
    ecg_status = sensor_data.get("ecg_status", "NORMAL")
    lat = sensor_data.get("lat", 0.0)
    lng = sensor_data.get("lng", 0.0)
    risk_score = logic_result.get("risk_score", 0)
    risk_level = logic_result.get("risk_level", "NORMAL")
    state = logic_result.get("state", "NORMAL")
    confidence = logic_result.get("confidence", 1.0)

    # Track ECG history in session state
    if "ecg_history" not in st.session_state:
        st.session_state.ecg_history = [0.0] * 100
    st.session_state.ecg_history.append(ecg)
    st.session_state.ecg_history = st.session_state.ecg_history[-100:]

    # Track previous values for deltas
    prev_hr = st.session_state.get("prev_hr", hr)
    prev_spo2 = st.session_state.get("prev_spo2", spo2)
    st.session_state.prev_hr = hr
    st.session_state.prev_spo2 = spo2

    # ═══════════════════════════════════════
    # TITLE + DEMO STAGE
    # ═══════════════════════════════════════
    col_title, col_demo = st.columns([3, 1])
    with col_title:
        st.markdown("## 🚗 VitalDrive AI — Driver Monitoring System")
    with col_demo:
        if demo_stage:
            st.info(f"🎬 DEMO: {demo_stage}")

    # ═══════════════════════════════════════
    # ROW 1 — 6 Metric Cards
    # ═══════════════════════════════════════
    m1, m2, m3, m4, m5, m6 = st.columns(6)

    hr_delta = round(hr - prev_hr, 1)
    spo2_delta = round(spo2 - prev_spo2, 1)

    with m1:
        st.metric("❤️ Heart Rate", f"{hr:.0f} BPM", f"{hr_delta:+.1f}")
    with m2:
        st.metric("🫁 SpO2", f"{spo2:.0f}%", f"{spo2_delta:+.1f}")
    with m3:
        st.metric("⚠️ Risk Score", f"{risk_score}/15", risk_level)
    with m4:
        st.metric("👁️ PERCLOS", f"{perclos*100:.1f}%")
    with m5:
        st.metric("👀 Blink Rate", f"{blink_rate}/min")
    with m6:
        score_emoji = "🟢" if safety_score >= 80 else ("🟡" if safety_score >= 50 else "🔴")
        st.metric(f"{score_emoji} Safety Score", f"{safety_score}/100")

    # ═══════════════════════════════════════
    # ROW 2 — Status Banner
    # ═══════════════════════════════════════
    emergency_states = {"CARDIAC_EMERGENCY", "ACCIDENT", "CARDIAC_CAUSED_CRASH",
                        "MEDICAL_SHOCK", "DRIVER_ASLEEP", "MICROSLEEP",
                        "EYES_CLOSED", "RISK_EMERGENCY", "RISK_CRITICAL"}
    warning_states = {"YAWNING", "HEAD_DROOPING", "EYES_CLOSING",
                      "DISTRACTED", "RISK_HIGH", "RISK_WARNING"}

    if state in emergency_states:
        st.markdown(
            f'<div class="status-emergency">'
            f'<span style="color:#ff4444;font-size:1.4rem;font-weight:700;">'
            f'🚨 EMERGENCY: {state.replace("_", " ")} — Confidence: {confidence:.0%}</span>'
            f'</div>', unsafe_allow_html=True)
        if cancel_remaining > 0:
            st.warning(f"⏱️ Cancel countdown: **{cancel_remaining}s** remaining")
    elif state in warning_states:
        st.markdown(
            f'<div class="status-warning">'
            f'<span style="color:#ffaa00;font-size:1.4rem;font-weight:700;">'
            f'⚠️ WARNING: {state.replace("_", " ")} — Confidence: {confidence:.0%}</span>'
            f'</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            f'<div class="status-normal">'
            f'<span style="color:#00ff00;font-size:1.4rem;font-weight:700;">'
            f'✅ NORMAL — All Systems Clear</span>'
            f'</div>', unsafe_allow_html=True)

    st.markdown("")  # spacer

    # ═══════════════════════════════════════
    # ROW 3 — Camera + Charts
    # ═══════════════════════════════════════
    col_cam, col_charts = st.columns([1, 1])

    with col_cam:
        st.markdown("#### 📷 Live Camera Feed")
        if current_frame is not None:
            try:
                rgb_frame = cv2.cvtColor(current_frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb_frame)
                st.image(pil_img, use_container_width=True)
            except Exception:
                st.info("📷 Camera initializing...")
        else:
            st.info("📷 Waiting for camera feed...")

        # Vision metrics below camera
        vc1, vc2, vc3 = st.columns(3)
        with vc1:
            st.caption(f"**EAR:** {shared_state.get('avg_ear', 0.0):.2f}")
        with vc2:
            st.caption(f"**Head Angle:** {shared_state.get('head_angle', 0.0):.1f}°")
        with vc3:
            st.caption(f"**Vision:** {vision_status}")

    with col_charts:
        # ECG Trace (Plotly)
        st.markdown("#### 📈 ECG Trace")
        fig_ecg = go.Figure()
        fig_ecg.add_trace(go.Scatter(
            y=st.session_state.ecg_history,
            mode='lines',
            line=dict(color='#00ff00', width=2),
            fill='none'
        ))
        fig_ecg.update_layout(
            height=200, margin=dict(l=0, r=0, t=10, b=10),
            paper_bgcolor='#0e1117', plot_bgcolor='#1a1d23',
            xaxis=dict(showticklabels=False, showgrid=False),
            yaxis=dict(showticklabels=False, showgrid=False, range=[-1.5, 1.5]),
            showlegend=False
        )
        st.plotly_chart(fig_ecg, use_container_width=True)

        # SpO2 Gauge
        ch1, ch2, ch3 = st.columns(3)
        with ch1:
            fig_spo2 = go.Figure(go.Indicator(
                mode="gauge+number",
                value=spo2,
                title={'text': "SpO2 %", 'font': {'size': 14, 'color': '#888'}},
                gauge={
                    'axis': {'range': [70, 100], 'tickcolor': '#444'},
                    'bar': {'color': '#00ff00' if spo2 >= 90 else ('#ffaa00' if spo2 >= 85 else '#ff0000')},
                    'bgcolor': '#1a1d23',
                    'steps': [
                        {'range': [70, 85], 'color': '#3d0a0a'},
                        {'range': [85, 90], 'color': '#3d2e00'},
                        {'range': [90, 100], 'color': '#0d3320'}
                    ],
                }
            ))
            fig_spo2.update_layout(
                height=180, margin=dict(l=20, r=20, t=30, b=10),
                paper_bgcolor='#0e1117', font={'color': '#fff'}
            )
            st.plotly_chart(fig_spo2, use_container_width=True)

        with ch2:
            st.markdown("**HRV**")
            hrv_color = "🟢" if config.HRV_LOW_THRESHOLD <= hrv <= config.HRV_HIGH_THRESHOLD else "🔴"
            st.markdown(f"### {hrv_color} {hrv:.1f}")
            st.caption(f"Trend: {spo2_trend}")

        with ch3:
            st.markdown("**ECG Status**")
            ecg_dot = "🟢" if ecg_status == "NORMAL" else "🔴"
            st.markdown(f"### {ecg_dot} {ecg_status}")

    # ═══════════════════════════════════════
    # ROW 4 — Map
    # ═══════════════════════════════════════
    st.markdown("#### 📍 Driver Location")
    col_map, col_mapinfo = st.columns([3, 1])
    with col_map:
        import pandas as pd
        map_data = pd.DataFrame({"lat": [lat], "lon": [lng]})
        st.map(map_data, zoom=13)
    with col_mapinfo:
        st.markdown(f"**Latitude:** {lat:.6f}")
        st.markdown(f"**Longitude:** {lng:.6f}")
        maps_link = f"https://maps.google.com/?q={lat},{lng}"
        st.markdown(f"[🗺️ Open in Google Maps]({maps_link})")

    # ═══════════════════════════════════════
    # ROW 5 — Alert History Table
    # ═══════════════════════════════════════
    st.markdown("#### 📋 Alert History")
    if alert_history:
        import pandas as pd
        rows = []
        for entry in alert_history[-10:]:
            if isinstance(entry, dict):
                rows.append({
                    "Time": entry.get("timestamp", ""),
                    "State": entry.get("state", ""),
                    "HR": entry.get("hr", ""),
                    "SpO2": entry.get("spo2", ""),
                    "Action": entry.get("action", ""),
                    "Message": entry.get("message", ""),
                })
            else:
                # Legacy tuple format fallback
                rows.append({"Time": entry[0] if len(entry) > 0 else "",
                             "Message": entry[1] if len(entry) > 1 else ""})
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("No alerts recorded yet.")

    # ═══════════════════════════════════════
    # ROW 6 — System Status
    # ═══════════════════════════════════════
    st.markdown("#### 🖥️ System Status")
    s1, s2, s3, s4 = st.columns(4)
    with s1:
        mode = "🟡 SIMULATION" if config.SIMULATION_MODE else "🟢 LIVE"
        st.markdown(f"**Mode:** {mode}")
    with s2:
        cam = f"ESP32-CAM ({config.ESP32_CAM_IP})"
        st.markdown(f"**Camera:** {cam}")
    with s3:
        serial_status = sensor_data.get("status", "unknown")
        st.markdown(f"**Serial:** {serial_status}")
    with s4:
        st.markdown(f"**Updated:** {time.strftime('%H:%M:%S')}")

    # ═══════════════════════════════════════
    # BOTTOM — Demo Controls
    # ═══════════════════════════════════════
    st.markdown("---")
    st.markdown("#### 🎮 Controls")
    b1, b2, b3, b4 = st.columns(4)

    with b1:
        if st.button("🎬 Start Demo Mode", use_container_width=True):
            with shared_state["lock"]:
                shared_state["trigger_demo"] = True

    with b2:
        if st.button("💔 Inject Cardiac Emergency", use_container_width=True):
            with shared_state["lock"]:
                shared_state["trigger_cardiac"] = True

    with b3:
        if st.button("💥 Inject Accident", use_container_width=True):
            with shared_state["lock"]:
                shared_state["trigger_accident"] = True

    with b4:
        if st.button("🔄 Reset All Alerts", use_container_width=True):
            with shared_state["lock"]:
                shared_state["trigger_reset"] = True
