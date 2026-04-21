import time
import math
import random
import json
import threading
import collections
import serial
import serial.tools.list_ports

import config

class SensorMonitor:
    def __init__(self):
        self.data_lock = threading.Lock()

        self.data = {
            "hr": 75,
            "spo2": 98,
            "ecg": 0.0,
            "hrv": 5.0,
            "spo2_trend": "STABLE",
            "ecg_status": "NORMAL",
            "lat": 12.9716,
            "lng": 77.5946,
            "gps_available": False,
            "status": "initializing"
        }

        self.running = False
        self.thread = None
        self.sim_emergency = None
        self.emergency_start_time = 0
        self.sim_time = 0.0

        # HRV tracking (last 20 HR readings)
        self.hr_history = collections.deque(maxlen=20)

        # SpO2 trend tracking (last 10 readings with timestamps)
        self.spo2_history = collections.deque(maxlen=10)

        # ECG peak detection
        self.ecg_history = collections.deque(maxlen=200)  # ~20 seconds at 10Hz
        self.ecg_peak_times = collections.deque(maxlen=30)
        self.last_ecg_was_rising = False
        self.last_ecg_val = 0.0

    def get_data(self):
        with self.data_lock:
            return self.data.copy()

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join()

    def inject_cardiac_emergency(self):
        self.sim_emergency = "CARDIAC"
        self.emergency_start_time = time.time()

    def inject_accident(self):
        self.sim_emergency = "ACCIDENT"
        self.emergency_start_time = time.time()

    # ─── HRV Analysis ───

    def _calculate_hrv(self, hr):
        self.hr_history.append(hr)
        if len(self.hr_history) < 5:
            return 5.0  # default neutral HRV

        values = list(self.hr_history)
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std_dev = math.sqrt(variance)
        return round(std_dev, 2)

    # ─── SpO2 Trend Analysis ───

    def _calculate_spo2_trend(self, spo2):
        now = time.time()
        self.spo2_history.append((now, spo2))

        if len(self.spo2_history) < 3:
            return "STABLE"

        # Check drop over the last 30 seconds
        oldest_in_window = None
        for t, val in self.spo2_history:
            if now - t <= 30:
                oldest_in_window = val
                break

        if oldest_in_window is None:
            oldest_in_window = self.spo2_history[0][1]

        drop = oldest_in_window - spo2

        if spo2 < config.SPO2_CRITICAL:
            return "CRITICAL"
        elif drop >= config.SPO2_DROP_THRESHOLD:
            return "DROPPING"
        else:
            return "STABLE"

    # ─── ECG Peak Detection ───

    def _analyze_ecg(self, ecg_val):
        now = time.time()
        self.ecg_history.append((now, ecg_val))

        # Simple R-peak detection: detect local maxima above threshold
        is_rising = ecg_val > self.last_ecg_val
        # Peak = was rising, now falling, and value was high enough to be R wave
        if self.last_ecg_was_rising and not is_rising and self.last_ecg_val > 0.5:
            self.ecg_peak_times.append(now)

        self.last_ecg_was_rising = is_rising
        self.last_ecg_val = ecg_val

        # Need at least 3 peaks to analyze
        if len(self.ecg_peak_times) < 3:
            return "NORMAL"

        # Calculate intervals between recent peaks
        peaks = list(self.ecg_peak_times)
        intervals = [peaks[i+1] - peaks[i] for i in range(len(peaks)-1)]

        if not intervals:
            return "NORMAL"

        avg_interval = sum(intervals) / len(intervals)

        # Check for missed beats (gap > 1.5x normal interval)
        for interval in intervals[-5:]:  # check last 5 intervals
            if avg_interval > 0 and interval > avg_interval * 1.5:
                return "MISSED_BEAT"

        # Check for irregular rhythm (high variation in intervals)
        if len(intervals) >= 3:
            mean_int = sum(intervals) / len(intervals)
            variance = sum((i - mean_int) ** 2 for i in intervals) / len(intervals)
            std_int = math.sqrt(variance)
            if mean_int > 0 and (std_int / mean_int) > 0.3:  # coefficient of variation > 30%
                return "IRREGULAR"

        return "NORMAL"

    # ─── Serial Port Detection ───

    def _auto_detect_port(self):
        ports = list(serial.tools.list_ports.comports())
        for port in ports:
            if any(tag in port.description for tag in ["Serial", "UART", "CH340", "CP210"]):
                return port.device

        if ports:
            return ports[0].device

        return None

    # ─── Serial Parsing ───

    def _parse_serial_line(self, line):
        line = line.strip()
        if not line:
            return None

        updates = {}

        # Try JSON
        if line.startswith('{') and line.endswith('}'):
            try:
                data = json.loads(line)
                updates["hr"] = data.get("hr", self.data["hr"])
                updates["spo2"] = data.get("spo2", self.data["spo2"])
                updates["ecg"] = data.get("ecg", self.data["ecg"])
                if "lat" in data and "lng" in data:
                    updates["lat"] = data["lat"]
                    updates["lng"] = data["lng"]
                    updates["gps_available"] = True
                return updates
            except json.JSONDecodeError:
                pass

        # Try Key-Value
        if "HR:" in line or "SPO2:" in line:
            parts = line.split(',')
            for p in parts:
                kv = p.split(':')
                if len(kv) == 2:
                    k = kv[0].strip().upper()
                    try:
                        v = float(kv[1].strip())
                        if k == "HR": updates["hr"] = v
                        elif k == "SPO2": updates["spo2"] = v
                        elif k == "ECG": updates["ecg"] = v
                        elif k == "LAT": updates["lat"] = v
                        elif k == "LNG": updates["lng"] = v
                    except ValueError:
                        pass

            if "lat" in updates and "lng" in updates:
                updates["gps_available"] = True
            return updates

        # Try CSV
        parts = line.split(',')
        if len(parts) >= 3:
            try:
                updates["hr"] = float(parts[0])
                updates["spo2"] = float(parts[1])
                updates["ecg"] = float(parts[2])
                if len(parts) >= 5:
                    updates["lat"] = float(parts[3])
                    updates["lng"] = float(parts[4])
                    updates["gps_available"] = True
                return updates
            except ValueError:
                pass

        return None

    # ─── Simulation ───

    def _simulate_data(self):
        self.sim_time += 0.1

        # Baseline values
        hr = 75 + math.sin(self.sim_time * 0.5) * 5 + random.uniform(-2, 2)
        spo2 = 98 - random.uniform(0, 1.5)

        # ECG waveform (P-QRS-T)
        t_mod = self.sim_time % 1.0
        ecg = 0.0
        if 0.1 < t_mod < 0.2:    ecg = 0.2    # P wave
        elif 0.3 < t_mod < 0.35: ecg = -0.3    # Q wave
        elif 0.35 <= t_mod < 0.4: ecg = 1.0    # R wave
        elif 0.4 <= t_mod < 0.45: ecg = -0.4   # S wave
        elif 0.6 < t_mod < 0.75:  ecg = 0.3    # T wave
        else:                     ecg = random.uniform(-0.05, 0.05)

        lat = 12.9716 + random.uniform(-0.0001, 0.0001)
        lng = 77.5946 + random.uniform(-0.0001, 0.0001)

        # Apply injected emergencies
        if self.sim_emergency == "CARDIAC":
            elapsed = time.time() - self.emergency_start_time
            if elapsed < 30:
                hr = 130 + math.sin(self.sim_time * 2) * 20
                spo2 = max(80, 95 - elapsed)
                ecg += random.uniform(-0.5, 0.5)

        elif self.sim_emergency == "ACCIDENT":
            hr = 140 + random.uniform(-5, 5)
            spo2 = 98 - random.uniform(0, 5)
            lat += 0.01
            lng += 0.01

        # Compute derived analytics
        hrv = self._calculate_hrv(hr)
        spo2_trend = self._calculate_spo2_trend(spo2)
        ecg_status = self._analyze_ecg(ecg)

        with self.data_lock:
            self.data["hr"] = round(hr, 1)
            self.data["spo2"] = round(spo2, 1)
            self.data["ecg"] = round(ecg, 3)
            self.data["hrv"] = hrv
            self.data["spo2_trend"] = spo2_trend
            self.data["ecg_status"] = ecg_status
            self.data["lat"] = round(lat, 6)
            self.data["lng"] = round(lng, 6)
            self.data["gps_available"] = True
            self.data["status"] = "simulating"

    # ─── Comms ───
    def send_command(self, cmd):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(f"{cmd}\n".encode('utf-8'))
            except Exception as e:
                with open("error_log.txt", "a") as f:
                    f.write(f"SEND_COMMAND ERROR: {e}\n")

    # ─── Main loop ───

    def _run(self):
        self.ser = None

        while self.running:
            if config.SIMULATION_MODE:
                self._simulate_data()
                time.sleep(0.1)
                continue

            # Hardware mode
            try:
                if self.ser is None or not self.ser.is_open:
                    port = config.SERIAL_PORT
                    if port == "AUTO":
                        port = self._auto_detect_port()

                    if port:
                        self.ser = serial.Serial(port, config.BAUD_RATE, timeout=1)
                        with self.data_lock:
                            self.data["status"] = "ok"
                    else:
                        with self.data_lock:
                            self.data["status"] = "no_port_simulating"
                        # Hardware missing -> allow demo mode via simulation
                        self._simulate_data()
                        time.sleep(1)
                        continue

                if self.ser.in_waiting:
                    raw_line = self.ser.readline().decode('utf-8', errors='ignore')
                    updates = self._parse_serial_line(raw_line)

                    if updates:
                        hr = updates.get("hr", self.data["hr"])
                        spo2 = updates.get("spo2", self.data["spo2"])
                        ecg = updates.get("ecg", self.data["ecg"])

                        # Compute derived analytics on real data
                        hrv = self._calculate_hrv(hr)
                        spo2_trend = self._calculate_spo2_trend(spo2)
                        ecg_status = self._analyze_ecg(ecg)

                        with self.data_lock:
                            for k, v in updates.items():
                                self.data[k] = v
                            self.data["hrv"] = hrv
                            self.data["spo2_trend"] = spo2_trend
                            self.data["ecg_status"] = ecg_status
                            self.data["status"] = "ok"

            except Exception as e:
                if self.ser:
                    self.ser.close()
                self.ser = None
                with self.data_lock:
                    self.data["status"] = "disconnected_simulating"
                self._simulate_data()

                with open("error_log.txt", "a") as f:
                    f.write(f"SENSORS ERROR: {str(e)}\n")

                time.sleep(1)

            time.sleep(0.01)

        if self.ser:
            self.ser.close()
