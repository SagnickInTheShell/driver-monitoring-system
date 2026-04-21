# ═══════════════════════════════════════
# VitalDrive AI — Configuration
# ═══════════════════════════════════════

# Camera
ESP32_CAM_IP = "192.168.1.100"  # enter IP address here

# Serial
SERIAL_PORT = "AUTO"  # auto detect COM port, or enter COMx
BAUD_RATE = 115200
SIMULATION_MODE = True  # True allows demo mode with fake sensor values if hardware missing

# Vision thresholds
EAR_THRESHOLD = 0.25
DROWSY_SECONDS = 2.0
HEAD_ANGLE_THRESHOLD = 30

# Health thresholds
HR_MIN = 40
HR_MAX = 120
SPO2_MIN = 90
SPO2_CRITICAL = 85
ECG_IRREGULARITY_THRESHOLD = 0.7

# Alert timings
CANCEL_WINDOW_SECONDS = 10
ESCALATION_DELAY = 5

# Twilio (placeholders)
TWILIO_SID = "your_sid"
TWILIO_TOKEN = "your_token"
TWILIO_FROM = "+1xxxxxxxxxx"
EMERGENCY_CONTACT = "+91xxxxxxxxxx"
HOSPITAL_CONTACT = "+91xxxxxxxxxx"

# ═══════════════════════════════════════
# UPGRADED FEATURES
# ═══════════════════════════════════════

# Voice
VOICE_ENABLED = True

# PERCLOS (Percentage of Eye Closure)
PERCLOS_WINDOW = 60        # seconds to track PERCLOS
PERCLOS_THRESHOLD = 0.15   # 15% of time eyes closed = drowsy

# Blink rate
BLINK_WINDOW = 60          # seconds to track blink rate
BLINK_LOW = 8              # below = hyperfocused / micro-sleep risk
BLINK_HIGH = 30            # above = extreme fatigue

# Yawn detection
MAR_THRESHOLD = 0.6        # mouth aspect ratio threshold
YAWN_SECONDS = 2.0         # how long mouth open = yawn

# Distraction detection
DISTRACTION_ANGLE = 30     # degrees left/right for distraction
DISTRACTION_SECONDS = 2.0  # seconds looking away = distracted

# HRV (Heart Rate Variability)
HRV_HIGH_THRESHOLD = 15    # std dev > 15 = irregular heartbeat
HRV_LOW_THRESHOLD = 2      # std dev < 2 = stress/shock

# SpO2 Trend
SPO2_DROP_THRESHOLD = 3    # % drop in 30s = trending down

# Risk scoring
PRE_EMERGENCY_SCORE = 7    # risk score for warning SMS
