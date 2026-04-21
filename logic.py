import time
import config

# Risk score mappings
VISION_SCORES = {
    "YAWNING":       1,
    "HEAD_DROOPING": 2,
    "DISTRACTED":    2,
    "EYES_CLOSING":  3,
    "EYES_CLOSED":   4,
    "MICROSLEEP":    6,
}

RISK_LEVELS = [
    (0,  2,  "NORMAL"),
    (3,  4,  "MILD_WARNING"),
    (5,  6,  "MODERATE_WARNING"),
    (7,  8,  "HIGH_ALERT"),
    (9,  10, "CRITICAL"),
    (11, 999, "EMERGENCY"),
]

class LogicController:
    def __init__(self):
        self.eyes_closed_since = None
        self.last_cardiac_emergency_time = 0
        self.last_lat = None
        self.last_lng = None

    def _get_risk_level(self, score):
        for lo, hi, level in RISK_LEVELS:
            if lo <= score <= hi:
                return level
        return "EMERGENCY"

    def _calculate_risk_score(self, vision_status, sensors_data):
        score = 0

        # ─── Vision signals ───
        score += VISION_SCORES.get(vision_status, 0)

        # ─── Sensor signals ───
        hr = sensors_data.get("hr", 75)
        spo2 = sensors_data.get("spo2", 98)
        hrv = sensors_data.get("hrv", 5.0)
        spo2_trend = sensors_data.get("spo2_trend", "STABLE")
        ecg_status = sensors_data.get("ecg_status", "NORMAL")

        # HR scoring
        if hr > config.HR_MAX or hr < config.HR_MIN:
            score += 2  # Critical HR
        elif (90 <= hr <= 120) or (40 <= hr <= 50):
            score += 1  # Slightly abnormal

        # SpO2 scoring
        if spo2 < config.SPO2_CRITICAL:
            score += 4  # Below 85%
        elif spo2 < config.SPO2_MIN:
            score += 3  # Below 90%

        if spo2_trend == "DROPPING":
            score += 2
        elif spo2_trend == "CRITICAL":
            score += 4

        # ECG scoring
        if ecg_status == "MISSED_BEAT":
            score += 4
        elif ecg_status == "IRREGULAR":
            score += 3

        # HRV scoring
        if hrv > config.HRV_HIGH_THRESHOLD or hrv < config.HRV_LOW_THRESHOLD:
            score += 2

        return score

    def classify(self, vision_status, sensors_data):
        hr = sensors_data.get("hr", 75)
        spo2 = sensors_data.get("spo2", 98)
        ecg = sensors_data.get("ecg", 0.0)
        hrv = sensors_data.get("hrv", 5.0)
        spo2_trend = sensors_data.get("spo2_trend", "STABLE")
        ecg_status = sensors_data.get("ecg_status", "NORMAL")
        lat = sensors_data.get("lat", 0.0)
        lng = sensors_data.get("lng", 0.0)

        current_time = time.time()

        # ─── Calculate risk score ───
        risk_score = self._calculate_risk_score(vision_status, sensors_data)
        risk_level = self._get_risk_level(risk_score)

        # ─── Track eyes closed duration ───
        if vision_status in ["EYES_CLOSED", "MICROSLEEP"]:
            if self.eyes_closed_since is None:
                self.eyes_closed_since = current_time
        else:
            self.eyes_closed_since = None

        eyes_closed_duration = 0
        if self.eyes_closed_since is not None:
            eyes_closed_duration = current_time - self.eyes_closed_since

        # ─── Default result ───
        result = {
            "state": "NORMAL",
            "confidence": 1.0,
            "vision_input": vision_status,
            "hr": hr,
            "spo2": spo2,
            "ecg": ecg,
            "hrv": hrv,
            "spo2_trend": spo2_trend,
            "ecg_status": ecg_status,
            "lat": lat,
            "lng": lng,
            "action_needed": "NONE",
            "cancel_countdown": None,
            "risk_score": risk_score,
            "risk_level": risk_level,
        }

        # ═════════════════════════════════════
        # CASE CLASSIFICATION (highest priority first)
        # ═════════════════════════════════════

        # CASE 8 — MEDICAL_SHOCK
        if spo2 < config.SPO2_CRITICAL and hr < config.HR_MIN:
            result["state"] = "MEDICAL_SHOCK"
            result["action_needed"] = "CRITICAL_SMS"
            result["confidence"] = 0.95
            return result

        # ─── GPS spike detection for ACCIDENT ───
        gps_spike = False
        if self.last_lat is not None and self.last_lng is not None:
            dist = ((lat - self.last_lat)**2 + (lng - self.last_lng)**2)**0.5
            if dist > 0.005:
                gps_spike = True

        self.last_lat = lat
        self.last_lng = lng

        # ─── Cardiac detection ───
        is_cardiac = False
        if (abs(ecg) > config.ECG_IRREGULARITY_THRESHOLD and
            spo2 < config.SPO2_MIN and
            (hr > config.HR_MAX or hr < config.HR_MIN)):
            is_cardiac = True

        # Also trigger cardiac if ECG status is bad + vitals confirm
        if ecg_status in ["IRREGULAR", "MISSED_BEAT"] and spo2 < config.SPO2_MIN:
            is_cardiac = True

        if is_cardiac:
            self.last_cardiac_emergency_time = current_time

        # CASE 6 & 7 — ACCIDENT / CARDIAC_CAUSED_CRASH
        if gps_spike and (hr > 120 or hr < 50) and vision_status in ["NO_FACE", "EYES_CLOSED", "MICROSLEEP"]:
            if (current_time - self.last_cardiac_emergency_time) < 30:
                result["state"] = "CARDIAC_CAUSED_CRASH"
                result["action_needed"] = "COMBINED_HOSPITAL_SMS"
                result["confidence"] = 0.99
            else:
                result["state"] = "ACCIDENT"
                result["action_needed"] = "HOSPITAL_SMS"
                result["confidence"] = 0.98
            return result

        # CASE 5 — CARDIAC_EMERGENCY
        if is_cardiac:
            result["state"] = "CARDIAC_EMERGENCY"
            result["action_needed"] = "HOSPITAL_SMS"
            result["confidence"] = 0.92
            return result

        # CASE 4 — DRIVER_ASLEEP
        if eyes_closed_duration >= 10:
            result["state"] = "DRIVER_ASLEEP"
            result["action_needed"] = "MAX_BUZZER + CALL_DRIVER"
            result["confidence"] = 0.99
            return result

        # ─── NEW: MICROSLEEP (PERCLOS triggered) ───
        if vision_status == "MICROSLEEP":
            result["state"] = "MICROSLEEP"
            result["action_needed"] = "LOUD_BUZZER"
            result["cancel_countdown"] = config.CANCEL_WINDOW_SECONDS
            result["confidence"] = 0.90
            return result

        # CASE 3 — EYES_CLOSED
        if vision_status == "EYES_CLOSED":
            result["state"] = "EYES_CLOSED"
            result["action_needed"] = "LOUD_BUZZER"
            result["cancel_countdown"] = config.CANCEL_WINDOW_SECONDS
            result["confidence"] = 0.85
            return result

        # CASE 2 — EYES_CLOSING
        if vision_status == "EYES_CLOSING":
            result["state"] = "EYES_CLOSING"
            result["action_needed"] = "MEDIUM_BUZZER"
            result["confidence"] = 0.80
            return result

        # ─── NEW: DISTRACTED ───
        if vision_status == "DISTRACTED":
            result["state"] = "DISTRACTED"
            result["action_needed"] = "MEDIUM_BUZZER"
            result["confidence"] = 0.78
            return result

        # CASE 1 — HEAD_DROOPING
        if vision_status == "HEAD_DROOPING":
            result["state"] = "HEAD_DROOPING"
            result["action_needed"] = "SOFT_BUZZER"
            result["confidence"] = 0.75
            return result

        # ─── NEW: YAWNING ───
        if vision_status == "YAWNING":
            result["state"] = "YAWNING"
            result["action_needed"] = "SOFT_BUZZER"
            result["confidence"] = 0.70
            return result

        # ─── Risk-score based catch-all for edge cases ───
        if risk_level == "EMERGENCY":
            result["state"] = "RISK_EMERGENCY"
            result["action_needed"] = "HOSPITAL_SMS"
            result["confidence"] = 0.85
            return result
        elif risk_level == "CRITICAL":
            result["state"] = "RISK_CRITICAL"
            result["action_needed"] = "LOUD_BUZZER"
            result["confidence"] = 0.80
            return result
        elif risk_level == "HIGH_ALERT":
            result["state"] = "RISK_HIGH"
            result["action_needed"] = "MEDIUM_BUZZER"
            result["confidence"] = 0.70
            return result
        elif risk_level in ["MODERATE_WARNING", "MILD_WARNING"]:
            result["state"] = "RISK_WARNING"
            result["action_needed"] = "SOFT_BUZZER"
            result["confidence"] = 0.60
            return result

        return result
