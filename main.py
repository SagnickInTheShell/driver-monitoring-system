import time
import sys
import datetime
import threading

import streamlit as st
from streamlit_autorefresh import st_autorefresh

import config
from vision import VisionMonitor
from sensors import SensorMonitor
from logic import LogicController
from alerts import AlertSystem
from dashboard import render_dashboard

# ═══════════════════════════════════════
# DEMO STAGES
# ═══════════════════════════════════════

DEMO_STAGES = [
    (0,   8,  "Stage 1/8 — Normal Driving",              None,            None),
    (8,  12,  "Stage 2/8 — Yawning Detected",            "YAWNING",       None),
    (12, 16,  "Stage 3/8 — Eyes Closing",                 "EYES_CLOSING",  None),
    (16, 20,  "Stage 4/8 — Eyes Closed + Alarm",          "EYES_CLOSED",   None),
    (20, 25,  "Stage 5/8 — Driver Unresponsive",          "EYES_CLOSED",   None),
    (25, 30,  "Stage 6/8 — ECG Irregular, SpO2 Dropping", None,           "PRE_CARDIAC"),
    (30, 35,  "Stage 7/8 — CARDIAC EMERGENCY",            None,           "CARDIAC"),
    (35, 40,  "Stage 8/8 — SMS Sent, Hospital Notified",  None,           None),
]


def get_demo_state(elapsed):
    for start, end, label, vis_override, sensor_action in DEMO_STAGES:
        if start <= elapsed < end:
            return label, vis_override, sensor_action
    return "DEMO COMPLETE", None, None


# ═══════════════════════════════════════
# SAFETY SCORE CALCULATOR
# ═══════════════════════════════════════

SAFETY_PENALTIES = {
    "YAWNING": 1, "DISTRACTED": 2, "HEAD_DROOPING": 2,
    "EYES_CLOSING": 3, "EYES_CLOSED": 5, "MICROSLEEP": 8,
    "DRIVER_ASLEEP": 10, "CARDIAC_EMERGENCY": 15,
    "ACCIDENT": 20, "MEDICAL_SHOCK": 20,
}


def update_safety_score(shared_state, state):
    penalty = SAFETY_PENALTIES.get(state, 0)
    if penalty > 0:
        shared_state["safety_score"] = max(0, shared_state["safety_score"] - penalty)
        shared_state["last_clean_time"] = time.time()
    else:
        if (time.time() - shared_state.get("last_clean_time", time.time())) > 300:
            shared_state["safety_score"] = min(100, shared_state["safety_score"] + 1)


# ═══════════════════════════════════════
# BACKGROUND LOGIC LOOP
# ═══════════════════════════════════════

def logic_loop(shared_state, vision_monitor, sensor_monitor, logic_controller, alert_system):
    """Runs at 10Hz in a background thread. Updates shared_state for the dashboard."""

    demo_cardiac_injected = False
    demo_pre_cardiac_injected = False

    while shared_state.get("running", True):
        try:
            # ─── Read button triggers from dashboard ───
            with shared_state["lock"]:
                if shared_state.get("trigger_demo"):
                    shared_state["demo_active"] = not shared_state.get("demo_active", False)
                    shared_state["demo_start_time"] = time.time()
                    shared_state["trigger_demo"] = False
                    demo_cardiac_injected = False
                    demo_pre_cardiac_injected = False

                if shared_state.get("trigger_cardiac"):
                    sensor_monitor.inject_cardiac_emergency()
                    shared_state["trigger_cardiac"] = False

                if shared_state.get("trigger_accident"):
                    sensor_monitor.inject_accident()
                    shared_state["trigger_accident"] = False

                if shared_state.get("trigger_reset"):
                    alert_system._send_command_to_esp32("RESET")
                    alert_system.cancel_active = False
                    alert_system.cancel_remaining = 0
                    alert_system.offence_count = 0
                    shared_state["safety_score"] = 100
                    shared_state["demo_active"] = False
                    shared_state["demo_stage"] = ""
                    shared_state["trigger_reset"] = False

            # ─── Vision status ───
            vision_status = vision_monitor.get_status()

            # ─── Demo mode override ───
            demo_active = shared_state.get("demo_active", False)
            if demo_active:
                elapsed = time.time() - shared_state.get("demo_start_time", time.time())
                label, vis_override, sensor_action = get_demo_state(elapsed)

                with shared_state["lock"]:
                    shared_state["demo_stage"] = label

                if vis_override:
                    vision_status = vis_override

                if sensor_action == "PRE_CARDIAC" and not demo_pre_cardiac_injected:
                    sensor_monitor.inject_cardiac_emergency()
                    demo_pre_cardiac_injected = True

                if sensor_action == "CARDIAC" and not demo_cardiac_injected:
                    sensor_monitor.inject_cardiac_emergency()
                    demo_cardiac_injected = True
            else:
                with shared_state["lock"]:
                    shared_state["demo_stage"] = ""

            # ─── Sensor data ───
            sensor_data = sensor_monitor.get_data()

            # ─── Logic classification ───
            result = logic_controller.classify(vision_status, sensor_data)

            # ─── Alerts ───
            alert_system.handle(result, button_pressed=False)

            # ─── Update shared state for dashboard ───
            with shared_state["lock"]:
                shared_state["vision_status"] = vision_status
                shared_state["sensor_data"] = sensor_data
                shared_state["logic_result"] = result
                shared_state["cancel_remaining"] = alert_system.cancel_remaining
                shared_state["alert_history"] = alert_system.get_history()

                # Camera frame
                frame = vision_monitor.current_frame
                if frame is not None:
                    shared_state["current_frame"] = frame.copy()

                # Vision metrics
                shared_state["perclos"] = vision_monitor.get_perclos()
                shared_state["blink_rate"] = vision_monitor.get_blink_rate()
                shared_state["avg_ear"] = getattr(vision_monitor, 'avg_ear', 0.0)
                shared_state["head_angle"] = getattr(vision_monitor, 'head_angle', 0.0)

                # Safety score
                update_safety_score(shared_state, result.get("state", "NORMAL"))

        except Exception as e:
            print(f"LOGIC LOOP ERROR: {e}")
            try:
                with open("error_log.txt", "a") as f:
                    f.write(f"LOGIC LOOP ERROR: {str(e)}\n")
            except Exception:
                pass

        time.sleep(0.1)  # 10Hz


# ═══════════════════════════════════════
# INITIALIZATION (runs once per session)
# ═══════════════════════════════════════

def initialize_system():
    """Initialize all subsystems. Called once via st.session_state."""

    print("═" * 50)
    print("   VITALDRIVE AI — INITIALIZING")
    print("═" * 50)
    print(f"  Simulation Mode : {config.SIMULATION_MODE}")
    print(f"  Camera Mode     : ESP32-CAM (IP: {config.ESP32_CAM_IP})")
    print(f"  Voice Enabled   : {config.VOICE_ENABLED}")
    print("═" * 50)

    # Shared state dictionary (thread-safe)
    shared_state = {
        "lock": threading.Lock(),
        "running": True,
        "vision_status": "NO_FACE",
        "sensor_data": {},
        "logic_result": {},
        "cancel_remaining": 0,
        "current_frame": None,
        "alert_history": [],
        "demo_stage": "",
        "demo_active": False,
        "demo_start_time": 0,
        "perclos": 0.0,
        "blink_rate": 0,
        "avg_ear": 0.0,
        "head_angle": 0.0,
        "safety_score": 100,
        "last_clean_time": time.time(),
        # Dashboard button triggers
        "trigger_demo": False,
        "trigger_cardiac": False,
        "trigger_accident": False,
        "trigger_reset": False,
    }

    # Start sensors
    print("  [1/4] Starting sensor thread...")
    sensor_monitor = SensorMonitor()
    sensor_monitor.start()
    time.sleep(2)

    # Start alerts (includes voice engine)
    print("  [2/4] Starting alert system...")
    alert_system = AlertSystem(serial_interface=sensor_monitor)

    # Start vision
    print("  [3/4] Starting camera/vision thread...")
    vision_monitor = VisionMonitor()
    vision_monitor.start()
    time.sleep(1)

    # Start logic loop
    print("  [4/4] Starting logic loop...")
    logic_controller = LogicController()
    logic_thread = threading.Thread(
        target=logic_loop,
        args=(shared_state, vision_monitor, sensor_monitor, logic_controller, alert_system),
        daemon=True
    )
    logic_thread.start()

    print("═" * 50)
    print("  SYSTEM READY — All modules online")
    print("═" * 50)

    return shared_state, vision_monitor, sensor_monitor, alert_system


# ═══════════════════════════════════════
# STREAMLIT ENTRY POINT
# ═══════════════════════════════════════

# Initialize once per session
if "initialized" not in st.session_state:
    shared_state, vision_monitor, sensor_monitor, alert_system = initialize_system()
    st.session_state.initialized = True
    st.session_state.shared_state = shared_state
    st.session_state.vision_monitor = vision_monitor
    st.session_state.sensor_monitor = sensor_monitor
    st.session_state.alert_system = alert_system

# Auto-refresh every 500 ms for smoother dashboard
st_autorefresh(interval=500, key="vitaldrive_refresh")

# Render the dashboard
render_dashboard(st.session_state.shared_state)
