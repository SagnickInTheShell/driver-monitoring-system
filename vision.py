import cv2
import time
import math
import numpy as np
import threading
import collections
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

import config

# Eye landmarks
LEFT_EYE  = [362, 385, 387, 263, 373, 380]
RIGHT_EYE = [33,  160, 158, 133, 153, 144]

# Mouth landmarks for yawn detection
MOUTH = [13, 14, 78, 308, 82, 87, 312, 317]

class VisionMonitor:
    def __init__(self):
        self.status = "NO_FACE"
        self.running = False
        self.thread = None
        self.current_frame = None

        # EAR / Eyes closed tracking
        self.eyes_closed_start_time = None

        # PERCLOS tracking
        self.perclos_history = collections.deque(maxlen=600)  # 60s at ~10 samples/s
        self.perclos_score = 0.0

        # Blink tracking
        self.blink_timestamps = collections.deque()
        self.blink_rate = 0
        self.eye_was_closed = False  # for detecting blink transitions

        # Yawn tracking
        self.yawn_start_time = None
        self.is_yawning = False

        # Distraction tracking
        self.distracted_start_time = None

        # Exposed metrics for dashboard
        self.avg_ear = 0.0
        self.head_angle = 0.0
        self.mar = 0.0
        self.yaw_angle = 0.0

        # Load FaceLandmarker (mp.tasks API)
        try:
            base_options = python.BaseOptions(model_asset_path='face_landmarker.task')
            options = vision.FaceLandmarkerOptions(
                base_options=base_options,
                output_face_blendshapes=False,
                output_facial_transformation_matrixes=True,
                num_faces=1
            )
            self.detector = vision.FaceLandmarker.create_from_options(options)
        except Exception as e:
            print(f"Failed to load FaceLandmarker. Is 'face_landmarker.task' in the same folder? Error: {e}")
            self.detector = None

    def get_status(self):
        return self.status

    def get_perclos(self):
        return self.perclos_score

    def get_blink_rate(self):
        return self.blink_rate

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join()

    # ─── Calculation helpers ───

    def _calculate_ear(self, landmarks, eye_indices):
        p = [landmarks[i] for i in eye_indices]

        def dist(p1, p2):
            return math.hypot(p1.x - p2.x, p1.y - p2.y)

        v1 = dist(p[1], p[5])
        v2 = dist(p[2], p[4])
        h = dist(p[0], p[3])

        if h == 0:
            return 0.0
        return (v1 + v2) / (2.0 * h)

    def _calculate_mar(self, landmarks):
        # Mouth Aspect Ratio using MOUTH landmarks
        # Indices: [13, 14, 78, 308, 82, 87, 312, 317]
        p = [landmarks[i] for i in MOUTH]

        def dist(p1, p2):
            return math.hypot(p1.x - p2.x, p1.y - p2.y)

        # Vertical distances (top-bottom pairs)
        v1 = dist(p[4], p[5])  # 82 - 87
        v2 = dist(p[6], p[7])  # 312 - 317
        v3 = dist(p[0], p[1])  # 13 - 14 (center vertical)

        # Horizontal distance
        h = dist(p[2], p[3])   # 78 - 308

        if h == 0:
            return 0.0
        return (v1 + v2 + v3) / (3.0 * h)

    def _calculate_head_angles(self, transformation_matrix):
        """Returns (pitch_degrees, yaw_degrees)"""
        rmat = transformation_matrix[:3, :3]
        sy = math.sqrt(rmat[0, 0] ** 2 + rmat[1, 0] ** 2)
        singular = sy < 1e-6

        if not singular:
            x = math.atan2(rmat[2, 1], rmat[2, 2])   # pitch
            y = math.atan2(-rmat[2, 0], sy)           # yaw
        else:
            x = math.atan2(-rmat[1, 2], rmat[1, 1])
            y = math.atan2(-rmat[2, 0], sy)

        pitch_deg = abs(math.degrees(x))
        yaw_deg = abs(math.degrees(y))
        return pitch_deg, yaw_deg

    # ─── PERCLOS ───

    def _update_perclos(self, eyes_closed):
        now = time.time()
        self.perclos_history.append((now, eyes_closed))

        # Remove entries older than PERCLOS_WINDOW
        cutoff = now - config.PERCLOS_WINDOW
        while self.perclos_history and self.perclos_history[0][0] < cutoff:
            self.perclos_history.popleft()

        if len(self.perclos_history) > 0:
            closed_count = sum(1 for _, c in self.perclos_history if c)
            self.perclos_score = closed_count / len(self.perclos_history)
        else:
            self.perclos_score = 0.0

    # ─── Blink rate ───

    def _update_blink_rate(self, eyes_closed):
        now = time.time()

        # Detect blink: transition from closed -> open
        if self.eye_was_closed and not eyes_closed:
            self.blink_timestamps.append(now)

        self.eye_was_closed = eyes_closed

        # Remove blinks older than BLINK_WINDOW
        cutoff = now - config.BLINK_WINDOW
        while self.blink_timestamps and self.blink_timestamps[0] < cutoff:
            self.blink_timestamps.popleft()

        self.blink_rate = len(self.blink_timestamps)

    # ─── Camera ───

    def _open_camera(self):
        cam = None
        stream_url = f"http://{config.ESP32_CAM_IP}:81/stream"
        try:
            cam = cv2.VideoCapture(stream_url)
            if not cam.isOpened():
                raise Exception("ESP32 stream failed to open.")
        except Exception as e:
            print(f"Failed to open ESP32_CAM stream at {stream_url}: {e}. Retrying connection...")
            return None

        if cam is not None and cam.isOpened():
            cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            for _ in range(30):
                cam.read()

        return cam

    # ─── Main loop ───

    def _run(self):
        if not self.detector:
            return

        cap = self._open_camera()

        while self.running:
            if cap is None or not cap.isOpened():
                self.status = "NO_FACE"
                time.sleep(1)
                cap = self._open_camera()
                continue

            try:
                ret, frame = cap.read()
                if not ret:
                    self.status = "NO_FACE"
                    time.sleep(1)
                    cap.release()
                    cap = self._open_camera()
                    continue

                display_frame = frame.copy()

                # Convert to RGB for MediaPipe
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

                result = self.detector.detect(mp_image)

                self.avg_ear = 0.0
                self.head_angle = 0.0
                self.mar = 0.0
                self.yaw_angle = 0.0

                if not result.face_landmarks:
                    self.status = "NO_FACE"
                    self.eyes_closed_start_time = None
                    self.yawn_start_time = None
                    self.distracted_start_time = None
                    self._update_perclos(False)
                    self._update_blink_rate(False)
                else:
                    landmarks = result.face_landmarks[0]
                    transform_matrix = result.facial_transformation_matrixes[0]

                    # Calculate all metrics
                    left_ear = self._calculate_ear(landmarks, LEFT_EYE)
                    right_ear = self._calculate_ear(landmarks, RIGHT_EYE)
                    self.avg_ear = (left_ear + right_ear) / 2.0
                    self.head_angle, self.yaw_angle = self._calculate_head_angles(transform_matrix)
                    self.mar = self._calculate_mar(landmarks)

                    eyes_closed = self.avg_ear < config.EAR_THRESHOLD

                    # Update PERCLOS and blink rate
                    self._update_perclos(eyes_closed)
                    self._update_blink_rate(eyes_closed)

                    # ─── Status classification (priority order) ───

                    # 1. MICROSLEEP (PERCLOS triggered)
                    if self.perclos_score > config.PERCLOS_THRESHOLD and eyes_closed:
                        self.status = "MICROSLEEP"
                        if self.eyes_closed_start_time is None:
                            self.eyes_closed_start_time = time.time()

                    # 2. EYES_CLOSED (EAR below threshold for 2+ seconds)
                    elif eyes_closed:
                        if self.eyes_closed_start_time is None:
                            self.eyes_closed_start_time = time.time()

                        closed_duration = time.time() - self.eyes_closed_start_time
                        if closed_duration >= config.DROWSY_SECONDS:
                            self.status = "EYES_CLOSED"
                        else:
                            self.status = "EYES_CLOSING"

                    # 3. YAWNING (MAR above threshold for 2+ seconds)
                    elif self.mar > config.MAR_THRESHOLD:
                        if self.yawn_start_time is None:
                            self.yawn_start_time = time.time()

                        yawn_duration = time.time() - self.yawn_start_time
                        if yawn_duration >= config.YAWN_SECONDS:
                            self.status = "YAWNING"
                            self.is_yawning = True
                        else:
                            # Still opening mouth, keep last non-yawn status or ALERT
                            if self.status not in ["YAWNING"]:
                                self.status = "ALERT"
                    else:
                        self.yawn_start_time = None
                        self.is_yawning = False

                        # 4. HEAD_DROOPING (pitch angle > threshold)
                        if self.head_angle > config.HEAD_ANGLE_THRESHOLD:
                            self.status = "HEAD_DROOPING"
                            self.eyes_closed_start_time = None
                            self.distracted_start_time = None

                        # 5. DISTRACTED (yaw angle > threshold for 2+ seconds)
                        elif self.yaw_angle > config.DISTRACTION_ANGLE:
                            if self.distracted_start_time is None:
                                self.distracted_start_time = time.time()

                            distracted_duration = time.time() - self.distracted_start_time
                            if distracted_duration >= config.DISTRACTION_SECONDS:
                                self.status = "DISTRACTED"
                            else:
                                self.status = "ALERT"
                        else:
                            # All clear
                            self.status = "ALERT"
                            self.eyes_closed_start_time = None
                            self.distracted_start_time = None

                # ─── Draw on frame ───
                if self.status == "ALERT":
                    color = (0, 255, 0)       # Green
                elif self.status in ["YAWNING", "HEAD_DROOPING", "EYES_CLOSING", "DISTRACTED"]:
                    color = (0, 165, 255)     # Orange
                elif self.status in ["EYES_CLOSED", "MICROSLEEP", "NO_FACE"]:
                    color = (0, 0, 255)       # Red
                else:
                    color = (255, 255, 255)

                cv2.putText(display_frame, f"EAR: {self.avg_ear:.2f}", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                cv2.putText(display_frame, f"Pitch: {self.head_angle:.1f} | Yaw: {self.yaw_angle:.1f}", (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                cv2.putText(display_frame, f"MAR: {self.mar:.2f}", (10, 90),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                cv2.putText(display_frame, f"PERCLOS: {self.perclos_score*100:.1f}%", (10, 120),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                cv2.putText(display_frame, f"Blinks/min: {self.blink_rate}", (10, 150),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                cv2.putText(display_frame, f"Status: {self.status}", (10, 180),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

                self.current_frame = display_frame

            except Exception as e:
                with open("error_log.txt", "a") as f:
                    f.write(f"VISION ERROR: {str(e)}\n")
                self.status = "NO_FACE"

            # ~20 FPS
            time.sleep(0.05)

        if cap:
            cap.release()
