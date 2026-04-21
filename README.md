# SafeDrive AI 🚗💓

**SafeDrive AI** is an in-vehicle driver monitoring and cardiac emergency detection system built for our college hackathon.

It seamlessly combines real-time computer vision (tracking drowsiness and head drooping) with hardware sensor data (heart rate, SpO2, and ECG) to evaluate a driver's state and instantly escalate alerts when accidents or medical emergencies occur.

---

## 📸 Project Demo

### 👁️ Vision Detection
| Driver Safe | Driver Drowsy |
|---|---|
| ![Safe](media/vision_safe_detection.jpg) | ![Drowsy](media/vision_drowsy_detection.jpg) |

### 💻 Software Dashboard
| Main Dashboard | SpO2 Gauge |
|---|---|
| ![Dashboard](media/dashboard_main_screen.jpg) | ![SpO2](media/dashboard_spo2_gauge.jpg) |

### 🔧 Hardware Setup
| ECG Normal | ECG Emergency |
|---|---|
| ![Normal](media/hardware_ecg_normal.jpg) | ![Emergency](media/hardware_ecg_emergency.jpg) |

![Full Setup](media/hardware_full_setup.jpg)
![Breadboard](media/hardware_breadboard_green.jpg)

---

## Features ✨

- **Computer Vision Pipeline**: Uses the MediaPipe FaceLandmarker API and OpenCV to accurately track eye aspect ratio (EAR), PERCLOS score, blink rate, yawn detection, and head pitch angles for real-time drowsiness detection.
- **Hardware Integration**: Reads and processes serial data from an ESP32 connected to MAX30102 (SpO2/HR), AD8232 (ECG), and a Neo-6M GPS.
- **Smart Logic Engine**: Contextually evaluates 8 different emergency states (e.g., Driver Asleep, Medical Shock, Cardiac Emergency + Accident) using a risk scoring system.
- **Communication Alerts**: Harnesses the Twilio API to automatically dispatch formatted SMS alerts containing the driver's vitals, GPS coordinates, and routing to the nearest hospital via OpenStreetMap mapping.
- **Voice Alerts**: Offline text-to-speech warnings using pyttsx3 for real-time audio feedback.
- **Live GUI Dashboard**: A responsive Streamlit dashboard displaying live vitals, ECG trace, SpO2 gauge, driver location map, and alert history.

---

## 🚨 8 Emergency Cases

| Case | Condition | Response |
|---|---|---|
| 1 | Head Drooping | Soft buzzer |
| 2 | Eyes Closing | Medium buzzer + mild vibration |
| 3 | Eyes Closed | Loud buzzer + 10s cancel window |
| 4 | Driver Asleep | Max buzzer + call driver |
| 5 | Cardiac Emergency | Hospital SMS with vitals + GPS |
| 6 | Accident Detected | Crash SMS with vitals + GPS |
| 7 | Cardiac Caused Crash | Combined emergency SMS |
| 8 | Medical Shock | Critical vitals SMS |

---

## 🔧 Hardware Components

| Component | Purpose | Cost |
|---|---|---|
| ESP32-CAM | Camera + Microcontroller | ₹350 |
| AD8232 ECG | Heart electrical activity | ₹250 |
| MAX30102 | SpO2 + Heart rate | ₹150 |
| Neo-6M GPS | Location tracking | ₹180 |
| Buzzer | Audio alert | ₹20 |
| Vibration Motor | Physical alert | ₹20 |
| Push Button | Cancel false alarm | ₹5 |
| **Total** | | **₹975** |

---

## 📁 Project Structure

- `main.py` — Coordinates application threads, dashboard, and demo loops
- `vision.py` — Detects driver focus using MediaPipe computer vision
- `sensors.py` — Connects and parses real-time serial vitals from ESP32
- `logic.py` — Evaluates vision and sensor state against 8 emergency cases
- `alerts.py` — Operates Twilio hooks, OSM hospital finder, buzzer commands
- `dashboard.py` — Renders live Streamlit dashboard
- `voice.py` — Handles offline text-to-speech warnings
- `config.py` — All constants, thresholds, API keys, and mode switches

---

## 🛠️ Setup & Installation

1. Install Python 3.10+
2. Install dependencies:
```bash
   pip install -r requirements.txt
```
3. Download the MediaPipe `face_landmarker.task` model and place it in the project root.
4. Open `config.py` and add your credentials:
```python
   TWILIO_SID = "your_sid"
   TWILIO_TOKEN = "your_token"
   EMERGENCY_CONTACT = "+91xxxxxxxxxx"
```
5. Set simulation mode:
```python
   SIMULATION_MODE = True   # no hardware needed
   SIMULATION_MODE = False  # live ESP32 connected
```
6. Run the system:
```bash
   python main.py
```

---

## 🔌 Hardware Note

Ensure the ESP32 serial COM port is accessible, then set in `config.py`:
```python
SIMULATION_MODE = False
USE_ESP32_CAM = True
ESP32_CAM_URL = "http://<your-esp32-ip>/stream"
```

---

## 🚀 Future Scope

- GSM module for highway connectivity without WiFi
- Integration with vehicle braking system
- Fleet management for truck companies
- Passenger vitals monitoring
- Alcohol detection add-on