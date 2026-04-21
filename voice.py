import time
import threading
import pyttsx3

import config

# Voice messages for each state
VOICE_MESSAGES = {
    "YAWNING":           "You appear tired. Consider taking a break.",
    "DISTRACTED":        "Please keep your eyes on the road.",
    "HEAD_DROOPING":     "Warning. Head drooping detected.",
    "EYES_CLOSING":      "Warning. Your eyes are closing.",
    "EYES_CLOSED":       "Alert. Eyes closed. Please respond.",
    "MICROSLEEP":        "Critical alert. Microsleep detected.",
    "CARDIAC_EMERGENCY": "Emergency. Cardiac event detected. Contacting hospital now.",
    "ACCIDENT":          "Emergency. Crash detected. Emergency services notified.",
    "CARDIAC_CAUSED_CRASH": "Emergency. Cardiac event followed by crash. Emergency services notified.",
    "MEDICAL_SHOCK":     "Emergency. Medical shock detected. Contacting hospital now.",
    "DRIVER_ASLEEP":     "Alert. Driver unresponsive. Calling emergency contact.",
}

# These states interrupt any current speech immediately
EMERGENCY_STATES = {
    "CARDIAC_EMERGENCY", "ACCIDENT", "CARDIAC_CAUSED_CRASH",
    "MEDICAL_SHOCK", "DRIVER_ASLEEP", "MICROSLEEP"
}

class VoiceAlert:
    def __init__(self):
        self.enabled = config.VOICE_ENABLED
        self.engine = None
        self.lock = threading.Lock()
        self.last_message = None
        self.last_speak_time = 0
        self.min_gap = 5  # minimum 5 seconds between voice alerts
        self.speaking = False
        self.speech_queue = []
        self.thread = None
        self.running = False

        if self.enabled:
            try:
                self.engine = pyttsx3.init()
                self.engine.setProperty('rate', 160)   # Slightly slower for clarity
                self.engine.setProperty('volume', 1.0)
                
                # Pick a clear voice if available
                voices = self.engine.getProperty('voices')
                if len(voices) > 1:
                    self.engine.setProperty('voice', voices[1].id)  # Often a clearer female voice
            except Exception as e:
                print(f"Voice engine init failed: {e}")
                self.engine = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)

    def speak(self, state):
        if not self.enabled or not self.engine:
            return

        message = VOICE_MESSAGES.get(state)
        if not message:
            return

        current_time = time.time()
        is_emergency = state in EMERGENCY_STATES

        with self.lock:
            # Never speak the same message twice in a row
            if message == self.last_message and not is_emergency:
                return

            # Minimum 5 second gap between voice alerts (unless emergency)
            if not is_emergency and (current_time - self.last_speak_time) < self.min_gap:
                return

            # Emergency messages go to front of queue and clear non-emergency items
            if is_emergency:
                self.speech_queue = [msg for msg in self.speech_queue if msg[1]]
                self.speech_queue.insert(0, (message, True))
            else:
                self.speech_queue.append((message, False))

            self.last_message = message
            self.last_speak_time = current_time

    def _worker(self):
        while self.running:
            msg_tuple = None
            with self.lock:
                if self.speech_queue:
                    msg_tuple = self.speech_queue.pop(0)

            if msg_tuple:
                message, is_emergency = msg_tuple
                try:
                    if is_emergency and self.speaking:
                        # Interrupt current speech for emergencies
                        try:
                            self.engine.stop()
                        except Exception:
                            pass

                    self.speaking = True
                    self.engine.say(message)
                    self.engine.runAndWait()
                    self.speaking = False
                except Exception as e:
                    self.speaking = False
                    try:
                        with open("error_log.txt", "a") as f:
                            f.write(f"VOICE ERROR: {str(e)}\n")
                    except Exception:
                        pass
            else:
                time.sleep(0.1)  # Idle wait
