import math
import threading
import time
from ctypes import POINTER, cast

import av
import cv2
import numpy as np
import pyautogui
import streamlit as st
from streamlit_webrtc import WebRtcMode, VideoProcessorBase, webrtc_streamer

# --- Windows System Audio Setup (PyCAW) ---
try:
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    volume_control = cast(interface, POINTER(IAudioEndpointVolume))
    vol_range = volume_control.GetVolumeRange()
    min_vol, max_vol = vol_range[0], vol_range[1]
    AUDIO_AVAILABLE = True
except Exception as e:
    AUDIO_AVAILABLE = False
    print(f"PyCAW Audio Setup Note: {e}")

# --- MediaPipe Solutions Setup ---
import mediapipe as mp

try:
    import mediapipe.solutions.hands as mp_hands
    import mediapipe.solutions.drawing_utils as mp_draw
    import mediapipe.solutions.drawing_styles as mp_styles
except (AttributeError, ModuleNotFoundError):
    mp_hands = mp.solutions.hands
    mp_draw = mp.solutions.drawing_utils
    mp_styles = mp.solutions.drawing_styles

class GestureControllerProcessor(VideoProcessorBase):
    def __init__(self):
        self.lock = threading.Lock()

        # Filter Parameters
        self.alpha_min = 0.05
        self.alpha_max = 0.85
        self.deadzone = 1.5
        self.cutoff_speed = 15.0

        # State Variables
        self.smooth_vol = 0.0
        self.prev_raw_vol = 0.0
        self.last_fist_time = 0.0
        self.prev_middle_y = None

        # High-Precision Hand Detection Engine
        self.hands = mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            model_complexity=1,
            min_detection_confidence=0.7,
            min_tracking_confidence=0.7,
        )

    def update_params(self, alpha_min, alpha_max, deadzone):
        with self.lock:
            self.alpha_min = alpha_min
            self.alpha_max = alpha_max
            self.deadzone = deadzone

    def is_fist(self, hand_landmarks):
        """Returns True if finger tips are below their respective MCP joints."""
        tips = [8, 12, 16, 20]
        mcps = [5, 9, 13, 17]
        for tip, mcp in zip(tips, mcps):
            if hand_landmarks.landmark[tip].y < hand_landmarks.landmark[mcp].y:
                return False
        return True

    def recv(self, frame: av.VideoFrame) -> av.VideoFrame:
        img = frame.to_ndarray(format="bgr24")
        img = cv2.flip(img, 1)
        h, w, _ = img.shape

        # Scale factor for UI drawing based on resolution
        scale = max(1.0, h / 720.0)
        thickness = max(2, int(2 * scale))
        font_scale = 0.7 * scale

        rgb_frame = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        results = self.hands.process(rgb_frame)

        with self.lock:
            a_min = self.alpha_min
            a_max = self.alpha_max
            dz = self.deadzone

        status_text = ""

        if results.multi_hand_landmarks:
            for hand_landmarks in results.multi_hand_landmarks:
                # Render Antialiased Skeletal Overlay
                mp_draw.draw_landmarks(
                    img,
                    hand_landmarks,
                    mp_hands.HAND_CONNECTIONS,
                    mp_styles.get_default_hand_landmarks_style(),
                    mp_styles.get_default_hand_connections_style(),
                )

                curr_time = time.time()

                # --- 1. PLAY / PAUSE (Fist Gesture) ---
                if self.is_fist(hand_landmarks):
                    if curr_time - self.last_fist_time > 1.2:  # 1.2 second debounce
                        pyautogui.press("space")
                        self.last_fist_time = curr_time

                    status_text = "ACTION: PLAY / PAUSE"
                    cv2.putText(
                        img,
                        status_text,
                        (int(180 * scale), int(60 * scale)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        font_scale,
                        (0, 0, 255),
                        thickness,
                        lineType=cv2.LINE_AA,
                    )

                else:
                    thumb = hand_landmarks.landmark[4]
                    index = hand_landmarks.landmark[8]
                    middle = hand_landmarks.landmark[12]

                    x1, y1 = int(thumb.x * w), int(thumb.y * h)
                    x2, y2 = int(index.x * w), int(index.y * h)
                    x3, y3 = int(middle.x * w), int(middle.y * h)

                    # --- 2. VOLUME CONTROL (Pinch Distance Calibration) ---
                    circle_radius = int(8 * scale)
                    cv2.circle(img, (x1, y1), circle_radius, (255, 128, 0), cv2.FILLED, lineType=cv2.LINE_AA)
                    cv2.circle(img, (x2, y2), circle_radius, (255, 128, 0), cv2.FILLED, lineType=cv2.LINE_AA)
                    cv2.line(img, (x1, y1), (x2, y2), (255, 0, 255), thickness, lineType=cv2.LINE_AA)

                    distance = math.hypot(x2 - x1, y2 - y1)
                    
                    # Dynamically scale min/max target range based on frame width
                    min_dist = int(25 * scale)
                    max_dist = int(220 * scale)
                    raw_vol = np.interp(distance, [min_dist, max_dist], [0, 100])

                    # Dynamic Exponential Filter
                    velocity = abs(raw_vol - self.prev_raw_vol)
                    self.prev_raw_vol = raw_vol

                    if velocity < dz:
                        current_alpha = 0.0
                    else:
                        current_alpha = np.interp(velocity, [0, self.cutoff_speed], [a_min, a_max])

                    self.smooth_vol = (current_alpha * raw_vol) + ((1 - current_alpha) * self.smooth_vol)

                    # Update Master System Audio
                    if AUDIO_AVAILABLE:
                        try:
                            target_vol_db = np.interp(self.smooth_vol, [0, 100], [min_vol, max_vol])
                            volume_control.SetMasterVolumeLevel(target_vol_db, None)
                        except Exception:
                            pass

                    # --- 3. SCROLLING CONTROL (Middle Finger Tracking) ---
                    if hand_landmarks.landmark[12].y < hand_landmarks.landmark[9].y:
                        if self.prev_middle_y is not None:
                            dy = y3 - self.prev_middle_y
                            deadzone_px = int(12 * scale)
                            if abs(dy) > deadzone_px:
                                scroll_amount = -int(dy * 4)
                                pyautogui.scroll(scroll_amount)
                                scroll_action = "SCROLL UP" if scroll_amount > 0 else "SCROLL DOWN"
                                cv2.putText(
                                    img,
                                    scroll_action,
                                    (int(180 * scale), int(100 * scale)),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    font_scale,
                                    (255, 255, 0),
                                    thickness,
                                    lineType=cv2.LINE_AA,
                                )
                        self.prev_middle_y = y3
                    else:
                        self.prev_middle_y = None

        # --- High-Resolution HUD Volume Gauge ---
        gauge_left = int(50 * scale)
        gauge_top = int(150 * scale)
        gauge_right = int(85 * scale)
        gauge_bottom = int(400 * scale)

        bar_y = int(np.interp(self.smooth_vol, [0, 100], [gauge_bottom, gauge_top]))
        
        # Border box
        cv2.rectangle(img, (gauge_left, gauge_top), (gauge_right, gauge_bottom), (0, 255, 0), thickness, lineType=cv2.LINE_AA)
        # Filled volume Level
        cv2.rectangle(img, (gauge_left, bar_y), (gauge_right, gauge_bottom), (0, 255, 0), cv2.FILLED, lineType=cv2.LINE_AA)
        # Text label
        cv2.putText(
            img,
            f"{int(self.smooth_vol)}%",
            (gauge_left - int(10 * scale), gauge_bottom + int(40 * scale)),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 255, 0),
            thickness,
            lineType=cv2.LINE_AA,
        )

        return av.VideoFrame.from_ndarray(img, format="bgr24")


# --- Streamlit Frontend ---
st.set_page_config(page_title="High-Precision Gesture Controller", layout="wide")
st.title("🎮 Multi-Modal Dynamic Gesture Controller")

if not AUDIO_AVAILABLE:
    st.warning("⚠️ PyCAW Master Audio control is offline. Volume HUD running in simulation mode.")

st.sidebar.header("🎛️ Dynamic Filter & Sensitivity")

alpha_min = st.sidebar.slider(
    "Min Alpha (Jitter Reduction)",
    min_value=0.01,
    max_value=0.20,
    value=0.05,
    step=0.01,
    help="Lower values suppress minor hand tremors when static.",
)

alpha_max = st.sidebar.slider(
    "Max Alpha (Dynamic Response)",
    min_value=0.30,
    max_value=1.00,
    value=0.85,
    step=0.05,
    help="Higher values optimize response time during rapid movements.",
)

deadzone = st.sidebar.slider(
    "Deadzone Threshold (%)",
    min_value=0.0,
    max_value=5.0,
    value=1.5,
    step=0.1,
    help="Ignores micro movements below this speed threshold.",
)

# Configure Streamlit WebRTC Streamer
ctx = webrtc_streamer(
    key="gesture-control-pro",
    mode=WebRtcMode.SENDRECV,
    video_processor_factory=GestureControllerProcessor,
    media_stream_constraints={
        "video": {
            "width": {"ideal": 3840, "min": 1280},
            "height": {"ideal": 2160, "min": 720},
            "frameRate": {"ideal": 60, "min": 30},
        },
        "audio": False,
    },
    async_processing=True,
)

if ctx.video_processor:
    ctx.video_processor.update_params(alpha_min, alpha_max, deadzone)