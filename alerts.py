import time
import math
import datetime
import requests
from twilio.rest import Client

import config
from voice import VoiceAlert


class AlertSystem:
    def __init__(self, serial_interface=None):
        self.serial = serial_interface

        # Twilio setup (reads from config.py)
        self.twilio = None
        try:
            if (hasattr(config, 'TWILIO_SID') and config.TWILIO_SID != "your_sid" and
                hasattr(config, 'TWILIO_TOKEN') and config.TWILIO_TOKEN != "your_token"):
                self.twilio = Client(config.TWILIO_SID, config.TWILIO_TOKEN)
        except Exception as e:
            self._log(f"Twilio Initialization Error: {e}")
            self.twilio = None

        # Voice engine
        self.voice = VoiceAlert()
        self.voice.start()

        # SMS throttle
        self.last_sms_time = 0.0
        self.last_warning_sms_time = 0.0

        # Cancel window
        self.cancel_countdown_start = 0.0
        self.cancel_active = False
        self.cancel_remaining = 0

        # Escalating alert pattern
        self.offence_count = 0
        self.offence_window_start = 0.0
        self.offence_window = 300  # 5 minutes

        # Buzzer command throttle (prevents spam per command)
        self.last_commands = {}
        self.command_cooldown = 15.0  # seconds between identical commands

        # State handling throttle
        self.last_handled_state = ""
        self.last_handled_action = ""
        self.last_handle_time = 0.0
        self.handle_cooldown = 15.0  # limit TTS and repeating events
        
        # State locks
        self.driver_is_escalated = False

        # Alert history (for dashboard)
        self.alert_history = []  # list of (timestamp_str, message)
        self.max_history = 20

    def _log(self, message):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] {message}\n"
        print(log_line.strip())
        try:
            with open("alerts_log.txt", "a") as f:
                f.write(log_line)
        except Exception:
            pass

    def _add_history(self, message, result=None):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        entry = {"timestamp": timestamp, "message": message}
        if result:
            entry["state"] = result.get("state", "")
            entry["hr"] = result.get("hr", 0)
            entry["spo2"] = result.get("spo2", 0)
            entry["action"] = result.get("action_needed", "")
        self.alert_history.append(entry)
        if len(self.alert_history) > self.max_history:
            self.alert_history = self.alert_history[-self.max_history:]

    def get_history(self):
        return list(self.alert_history[-10:])  # last 10 for dashboard table

    def _send_command_to_esp32(self, cmd):
        now = time.time()

        # Throttle: skip if this specific command was sent within cooldown
        if cmd != "RESET":
            if cmd in self.last_commands and (now - self.last_commands[cmd]) < self.command_cooldown:
                return
            self.last_commands[cmd] = now

        if self.serial and hasattr(self.serial, 'send_command'):
            try:
                self.serial.send_command(cmd)
            except Exception as e:
                self._log(f"Failed to send to ESP32 via sensor monitor: {e}")
        elif self.serial and hasattr(self.serial, 'write'):
            try:
                self.serial.write(f"{cmd}\n".encode('utf-8'))
            except Exception as e:
                self._log(f"Failed to send to ESP32: {e}")
        self._log(f"COMMAND to ESP32: {cmd}")

    # ═══════════════════════════════════════
    # Overpass API Hospital Finder (from comp.py)
    # ═══════════════════════════════════════

    @staticmethod
    def _haversine(lat1, lon1, lat2, lon2):
        R = 6371  # Earth radius in km
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dlon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return round(R * c, 2)

    def _find_hospitals(self, lat, lng, count=3):
        """Uses Overpass API to find nearest hospitals — defaults to mock data if no GPS or API fails."""
        # Validate GPS mock
        if abs(lat) < 0.1 and abs(lng) < 0.1:
            lat = 12.9716
            lng = 77.5946

        hospitals = []
        try:
            overpass_url = "https://overpass-api.de/api/interpreter"
            query = f"""
            [out:json];
            (
              node["amenity"="hospital"](around:20000,{lat},{lng});
              way["amenity"="hospital"](around:20000,{lat},{lng});
              relation["amenity"="hospital"](around:20000,{lat},{lng});
            );
            out center;
            """
            response = requests.get(overpass_url, params={"data": query}, timeout=5)

            if response.status_code == 200:
                data = response.json()
                elements = data.get("elements", [])

                for el in elements:
                    h_lat = float(el.get("lat") or el.get("center", {}).get("lat") or 0.0)
                    h_lon = float(el.get("lon") or el.get("center", {}).get("lon") or 0.0)
                    name = el.get("tags", {}).get("name", "Unknown Hospital")

                    if h_lat and h_lon:
                        dist = self._haversine(lat, lng, h_lat, h_lon)
                        if dist < 100.0: # Ignore absurdly far outliers
                            hospitals.append((name, dist, h_lat, h_lon))

                hospitals.sort(key=lambda x: x[1])
                hospitals = hospitals[:count]

        except Exception as e:
            self._log(f"Overpass API Error / Timeout: {e}")

        # Fallback if API fails or returns no real hospitals nearby
        if not hospitals:
            self._log("Using regional fallback logic for hospitals.")
            hospitals = [
                ("Apollo Hospital Jayanagar", 1.2, lat + 0.01, lng + 0.01),
                ("Manipal Hospital Old Airport Road", 3.4, lat - 0.02, lng + 0.02),
                ("Fortis Hospital Bannerghatta", 5.1, lat + 0.03, lng - 0.01)
            ]

        return hospitals[:count]

    # ═══════════════════════════════════════
    # SMS Sender
    # ═══════════════════════════════════════

    def _send_sms(self, to_number, body):
        self._log(f"Sending SMS to {to_number}:\n{body}")
        if self.twilio:
            try:
                message = self.twilio.messages.create(
                    body=body,
                    from_=config.TWILIO_FROM,
                    to=to_number
                )
                self._log(f"SMS Sent! SID: {message.sid}")
            except Exception as e:
                self._log(f"Twilio Error: {e}")
        else:
            self._log("(SIMULATED SMS - Twilio not configured)")

    # ─── Escalation Logic ───

    def _track_offence(self):
        now = time.time()
        if now - self.offence_window_start > self.offence_window:
            self.offence_count = 0
            self.offence_window_start = now
        self.offence_count += 1
        return self.offence_count

    def _get_escalation_level(self, base_action):
        count = self._track_offence()
        if count >= 3:
            if "SOFT" in base_action: return "BUZZ_LOUD"
            elif "MEDIUM" in base_action: return "BUZZ_MAX"
            return base_action
        elif count >= 2:
            if "SOFT" in base_action: return "BUZZ_MEDIUM"
            elif "MEDIUM" in base_action: return "BUZZ_LOUD"
            return base_action
        return base_action

    # ═══════════════════════════════════════
    # Main Handler
    # ═══════════════════════════════════════

    def handle(self, classification_result, button_pressed=False):
        state = classification_result.get("state", "NORMAL")
        action = classification_result.get("action_needed", "NONE")
        risk_score = classification_result.get("risk_score", 0)
        risk_level = classification_result.get("risk_level", "NORMAL")

        # ─── Cancel window logic ───
        if button_pressed and self.cancel_active:
            self._log("ALERT CANCELLED BY DRIVER (Button Pressed)")
            self._add_history("ALERT CANCELLED", classification_result)
            self._send_command_to_esp32("RESET")
            self.cancel_active = False
            self.cancel_remaining = 0
            return

        if self.cancel_active:
            elapsed = time.time() - self.cancel_countdown_start
            self.cancel_remaining = max(0, config.CANCEL_WINDOW_SECONDS - int(elapsed))

            if elapsed >= config.CANCEL_WINDOW_SECONDS:
                self._log("CANCEL WINDOW EXPIRED - ESCALATING")
                self._add_history("CANCEL EXPIRED - ESCALATING", classification_result)
                self.cancel_active = False
                self.cancel_remaining = 0

                if state in ["EYES_CLOSED", "MICROSLEEP"]:
                    state = "DRIVER_ASLEEP"
                    action = "MAX_BUZZER + CALL_DRIVER"
                    self.driver_is_escalated = True
            else:
                return

        # Skip non-alert states and reset locks
        if state in ["NORMAL", "ALERT"] and action == "NONE":
            self.driver_is_escalated = False
            return

        # Lock onto escalation if driver has not recovered
        if self.driver_is_escalated and state in ["EYES_CLOSED", "MICROSLEEP"]:
            state = "DRIVER_ASLEEP"
            action = "MAX_BUZZER + CALL_DRIVER"

        # Unified execution rate limiting for TTS / Logs: 
        # Only process the same state-action pair once every X seconds
        now = time.time()
        if not self.cancel_active and state == self.last_handled_state and action == self.last_handled_action:
            if (now - self.last_handle_time) < self.handle_cooldown:
                return
                
        self.last_handled_state = state
        self.last_handled_action = action
        self.last_handle_time = now

        # ─── Start countdown for EYES_CLOSED / MICROSLEEP ───
        if state in ["EYES_CLOSED", "MICROSLEEP"] and "LOUD" in action and not self.cancel_active:
            self.cancel_active = True
            self.cancel_countdown_start = time.time()
            self.cancel_remaining = config.CANCEL_WINDOW_SECONDS
            self._log(f"STARTING {config.CANCEL_WINDOW_SECONDS}s CANCELLATION COUNTDOWN")
            self._add_history(f"{state} - {config.CANCEL_WINDOW_SECONDS}s COUNTDOWN", classification_result)
            self._send_command_to_esp32("BUZZ_LOUD")
            self.voice.speak(state)
            return

        # ─── Hardware buzzer commands with escalation ───
        if action == "SOFT_BUZZER":
            escalated = self._get_escalation_level("BUZZ_SOFT")
            self._send_command_to_esp32(escalated)
            self._add_history(f"{state} detected", classification_result)
            self.voice.speak(state)

        elif action == "MEDIUM_BUZZER":
            escalated = self._get_escalation_level("BUZZ_MEDIUM")
            self._send_command_to_esp32(escalated)
            self._add_history(f"{state} detected", classification_result)
            self.voice.speak(state)

        elif "MAX_BUZZER" in action:
            self._send_command_to_esp32("BUZZ_MAX")
            self._send_command_to_esp32("VIBRATE_MAX")
            self._add_history(f"{state} - MAX ALERT", classification_result)
            self.voice.speak(state)

        # ─── Pre-Emergency Warning SMS (risk score 7-8) ───
        current_time = time.time()
        if risk_score >= config.PRE_EMERGENCY_SCORE and risk_level == "HIGH_ALERT":
            if (current_time - self.last_warning_sms_time) > 300:
                lat = classification_result.get("lat", 0.0)
                lng = classification_result.get("lng", 0.0)
                body = (
                    f"VitalDrive Warning: Driver showing signs of fatigue.\n"
                    f"Risk Score: {risk_score}/15\n"
                    f"Monitoring closely.\n"
                    f"Location: https://maps.google.com/?q={lat},{lng}"
                )
                self._send_sms(config.EMERGENCY_CONTACT, body)
                self._add_history("WARNING SMS sent to contact", classification_result)
                self.last_warning_sms_time = current_time

        # ─── Emergency SMS (throttled: 5 min cooldown) ───
        if "SMS" in action or "CALL" in action:
            if (current_time - self.last_sms_time) < 300:
                return

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lat = classification_result.get("lat", 0.0)
        lng = classification_result.get("lng", 0.0)
        hr = classification_result.get("hr", 0)
        spo2 = classification_result.get("spo2", 0)

        if state == "CARDIAC_EMERGENCY":
            hospitals = self._find_hospitals(lat, lng, count=3)
            hospital_lines = "\n".join(
                [f"  {i+1}. {h[0]} ({h[1]}km)" for i, h in enumerate(hospitals)]
            )
            body = (
                f"CARDIAC EMERGENCY - VitalDrive Alert\n"
                f"Patient: Driver\n"
                f"HR: {hr} BPM | SpO2: {spo2}%\n"
                f"ECG: Irregular pattern detected\n"
                f"Location: https://maps.google.com/?q={lat},{lng}\n"
                f"Nearest Hospitals:\n{hospital_lines}\n"
                f"Time: {timestamp}"
            )
            self._send_sms(config.HOSPITAL_CONTACT, body)
            self._send_sms(config.EMERGENCY_CONTACT, body)
            self._add_history("CARDIAC EMERGENCY - SMS sent", classification_result)
            self.voice.speak(state)
            self.last_sms_time = current_time

        elif state in ["ACCIDENT", "CARDIAC_CAUSED_CRASH"]:
            hospitals = self._find_hospitals(lat, lng, count=3)
            hospital_lines = "\n".join(
                [f"  {i+1}. {h[0]} ({h[1]}km)" for i, h in enumerate(hospitals)]
            )
            body = (
                f"CRASH EMERGENCY - VitalDrive Alert\n"
                f"High impact detected\n"
                f"HR: {hr} BPM | SpO2: {spo2}%\n"
                f"Location: https://maps.google.com/?q={lat},{lng}\n"
                f"Nearest Hospitals:\n{hospital_lines}\n"
                f"Time: {timestamp}"
            )
            self._send_sms(config.EMERGENCY_CONTACT, body)
            self._add_history("CRASH EMERGENCY - SMS sent", classification_result)
            self.voice.speak(state)
            self.last_sms_time = current_time

        elif state == "MEDICAL_SHOCK":
            hospitals = self._find_hospitals(lat, lng, count=3)
            hospital_lines = "\n".join(
                [f"  {i+1}. {h[0]} ({h[1]}km)" for i, h in enumerate(hospitals)]
            )
            body = (
                f"CRITICAL MEDICAL SHOCK - VitalDrive Alert\n"
                f"Driver vitals crashed below critical limits.\n"
                f"HR: {hr} BPM | SpO2: {spo2}%\n"
                f"Location: https://maps.google.com/?q={lat},{lng}\n"
                f"Nearest Hospitals:\n{hospital_lines}\n"
                f"Time: {timestamp}"
            )
            self._send_sms(config.HOSPITAL_CONTACT, body)
            self._send_sms(config.EMERGENCY_CONTACT, body)
            self._add_history("MEDICAL SHOCK - SMS sent", classification_result)
            self.voice.speak(state)
            self.last_sms_time = current_time

        elif state == "DRIVER_ASLEEP":
            self._log("Calling Emergency Contact... (Simulated Phone Call)")
            self._add_history("DRIVER ASLEEP - Calling contact", classification_result)
            self.voice.speak(state)
            self.last_sms_time = current_time

        elif state == "RISK_EMERGENCY":
            hospitals = self._find_hospitals(lat, lng, count=3)
            hospital_lines = "\n".join(
                [f"  {i+1}. {h[0]} ({h[1]}km)" for i, h in enumerate(hospitals)]
            )
            body = (
                f"RISK EMERGENCY - VitalDrive Alert\n"
                f"Multiple risk factors detected simultaneously.\n"
                f"Risk Score: {classification_result.get('risk_score', 0)}\n"
                f"HR: {hr} BPM | SpO2: {spo2}%\n"
                f"Location: https://maps.google.com/?q={lat},{lng}\n"
                f"Nearest Hospitals:\n{hospital_lines}\n"
                f"Time: {timestamp}"
            )
            self._send_sms(config.HOSPITAL_CONTACT, body)
            self._send_sms(config.EMERGENCY_CONTACT, body)
            self._add_history("RISK EMERGENCY - SMS sent", classification_result)
            self.voice.speak(state)
            self.last_sms_time = current_time

    def stop(self):
        self.voice.stop()
