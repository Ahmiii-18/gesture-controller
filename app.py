import platform
import math
import threading
import time

import av
import cv2
import numpy as np
import pyautogui
import streamlit as st
from streamlit_webrtc import WebRtcMode, VideoProcessorBase, webrtc_streamer

# --- Cross-Platform System Audio Setup (PyCAW) ---
AUDIO_AVAILABLE = False
if platform.system() == "Windows":
    try:
        import comtypes
        comtypes.CoInitialize()
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume_control = cast(interface, POINTER(IAudioEndpointVolume))
        vol_range = volume_control.GetVolumeRange()
        min_vol, max_vol = vol_range[0], vol_range[1]
        AUDIO_AVAILABLE = True
    except Exception as e:
        print(f"PyCAW Audio Setup Note: {e}")

# --- MediaPipe Solutions Setup ---
import mediapipe as mp

try:
    mp_hands = mp.solutions.hands
    mp_draw = mp.solutions.drawing_utils
    mp_styles = mp.solutions.drawing_styles
except AttributeError:
    import mediapipe.python.solutions.hands as mp_hands
    import mediapipe.python.solutions.drawing_utils as mp_draw
    import mediapipe.python.solutions.drawing_styles as mp_styles


class GestureControllerProcessor(VideoProcessorBase):
    def __init__(self):
        self.lock = threading.Lock()

        # Default Parameters
        self.alpha_min = 0.05
        self.alpha_max = 0.85
        self.deadzone = 1.5
        self.cutoff_speed = 15.0
        self.min_detection_conf = 0.7
        self.min_tracking_conf = 0.7
        self.show_hud = True
        self.show_skeleton = True

        # State Variables
        self.smooth_vol = 0.0
        self.prev_raw_vol = 0.0
        self.last_fist_time = 0.0
        self.prev_middle_y = None

        # Initialize Hand Detection Engine
        self.hands = mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            model_complexity=1,
            min_detection_confidence=self.min_detection_conf,
            min_tracking_confidence=self.min_tracking_conf,
        )

    def update_params(self, alpha_min, alpha_max, deadzone, det_conf, track_conf, show_hud, show_skeleton):
        with self.lock:
            self.alpha_min = alpha_min
            self.alpha_max = alpha_max
            self.deadzone = deadzone
            self.show_hud = show_hud
            self.show_skeleton = show_skeleton

            # Re-initialize hands if confidence thresholds change significantly
            if self.min_detection_conf != det_conf or self.min_tracking_conf != track_conf:
                self.min_detection_conf = det_conf
                self.min_tracking_conf = track_conf
                self.hands = mp_hands.Hands(
                    static_image_mode=False,
                    max_num_hands=1,
                    model_complexity=1,
                    min_detection_confidence=self.min_detection_conf,
                    min_tracking_confidence=self.min_tracking_conf,
                )

    def is_fist(self, hand_landmarks):
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

        scale = max(1.0, h / 720.0)
        thickness = max(2, int(2 * scale))
        font_scale = 0.7 * scale

        rgb_frame = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        results = self.hands.process(rgb_frame)

        with self.lock:
            a_min = self.alpha_min
            a_max = self.alpha_max
            dz = self.deadzone
            do_hud = self.show_hud
            do_skel = self.show_skeleton

        if results.multi_hand_landmarks:
            for hand_landmarks in results.multi_hand_landmarks:
                if do_skel:
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
                    if curr_time - self.last_fist_time > 1.2:
                        try:
                            pyautogui.press("space")
                        except Exception:
                            pass
                        self.last_fist_time = curr_time

                    status_text = "ACTION: PLAY / PAUSE"
                    cv2.putText(
                        img, status_text, (int(180 * scale), int(60 * scale)),
                        cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 255), thickness, lineType=cv2.LINE_AA
                    )
                else:
                    thumb = hand_landmarks.landmark[4]
                    index = hand_landmarks.landmark[8]
                    middle = hand_landmarks.landmark[12]

                    x1, y1 = int(thumb.x * w), int(thumb.y * h)
                    x2, y2 = int(index.x * w), int(index.y * h)
                    x3, y3 = int(middle.x * w), int(middle.y * h)

                    # --- 2. VOLUME CONTROL ---
                    circle_radius = int(8 * scale)
                    cv2.circle(img, (x1, y1), circle_radius, (255, 128, 0), cv2.FILLED, lineType=cv2.LINE_AA)
                    cv2.circle(img, (x2, y2), circle_radius, (255, 128, 0), cv2.FILLED, lineType=cv2.LINE_AA)
                    cv2.line(img, (x1, y1), (x2, y2), (255, 0, 255), thickness, lineType=cv2.LINE_AA)

                    distance = math.hypot(x2 - x1, y2 - y1)
                    min_dist = int(25 * scale)
                    max_dist = int(220 * scale)
                    raw_vol = np.interp(distance, [min_dist, max_dist], [0, 100])

                    velocity = abs(raw_vol - self.prev_raw_vol)
                    self.prev_raw_vol = raw_vol

                    if velocity < dz:
                        current_alpha = 0.0
                    else:
                        current_alpha = np.interp(velocity, [0, self.cutoff_speed], [a_min, a_max])

                    self.smooth_vol = (current_alpha * raw_vol) + ((1 - current_alpha) * self.smooth_vol)

                    if AUDIO_AVAILABLE:
                        try:
                            target_vol_db = np.interp(self.smooth_vol, [0, 100], [min_vol, max_vol])
                            volume_control.SetMasterVolumeLevel(target_vol_db, None)
                        except Exception:
                            pass

                    # --- 3. SCROLLING CONTROL ---
                    if hand_landmarks.landmark[12].y < hand_landmarks.landmark[9].y:
                        if self.prev_middle_y is not None:
                            dy = y3 - self.prev_middle_y
                            deadzone_px = int(12 * scale)
                            if abs(dy) > deadzone_px:
                                scroll_amount = -int(dy * 4)
                                try:
                                    pyautogui.scroll(scroll_amount)
                                except Exception:
                                    pass
                                scroll_action = "SCROLL UP" if scroll_amount > 0 else "SCROLL DOWN"
                                cv2.putText(
                                    img, scroll_action, (int(180 * scale), int(100 * scale)),
                                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 0), thickness, lineType=cv2.LINE_AA
                                )
                        self.prev_middle_y = y3
                    else:
                        self.prev_middle_y = None

        # --- High-Resolution HUD Volume Gauge ---
        if do_hud:
            gauge_left = int(50 * scale)
            gauge_top = int(150 * scale)
            gauge_right = int(85 * scale)
            gauge_bottom = int(400 * scale)

            bar_y = int(np.interp(self.smooth_vol, [0, 100], [gauge_bottom, gauge_top]))
            cv2.rectangle(img, (gauge_left, gauge_top), (gauge_right, gauge_bottom), (0, 255, 0), thickness, lineType=cv2.LINE_AA)
            cv2.rectangle(img, (gauge_left, bar_y), (gauge_right, gauge_bottom), (0, 255, 0), cv2.FILLED, lineType=cv2.LINE_AA)
            cv2.putText(
                img, f"{int(self.smooth_vol)}%", (gauge_left - int(10 * scale), gauge_bottom + int(40 * scale)),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 255, 0), thickness, lineType=cv2.LINE_AA
            )

        return av.VideoFrame.from_ndarray(img, format="bgr24")


# --- Streamlit Frontend ---
st.set_page_config(page_title="High-Precision Gesture Controller", layout="wide")
st.title("🎮 Multi-Modal Dynamic Gesture Controller")

if not AUDIO_AVAILABLE:
    st.info("ℹ️ Running in cloud/simulation mode. System audio controls are active on local Windows machines.")

# --- Interactive Sidebar Setup ---
st.sidebar.header("📷 Camera & Resolution")
resolution_preset = st.sidebar.selectbox(
    "Target Quality Preset",
    ["Full HD (1080p)", "HD (720p)", "Standard (480p)"],
    index=0,
    help="Selects ideal resolution. 1080p provides crisp tracking without browser lagging."
)

if "1080p" in resolution_preset:
    cam_width, cam_height = 1920, 1080
elif "720p" in resolution_preset:
    cam_width, cam_height = 1280, 720
else:
    cam_width, cam_height = 854, 480

st.sidebar.markdown("---")
st.sidebar.header("🎛️ Tracking & Smoothing")

alpha_min = st.sidebar.slider("Min Alpha (Jitter Reduction)", 0.01, 0.20, 0.05, 0.01)
alpha_max = st.sidebar.slider("Max Alpha (Dynamic Response)", 0.30, 1.00, 0.85, 0.05)
deadzone = st.sidebar.slider("Deadzone Threshold (%)", 0.0, 5.0, 1.5, 0.1)

st.sidebar.markdown("---")
st.sidebar.header("🎯 Detection Confidence")
det_conf = st.sidebar.slider("Min Detection Confidence", 0.5, 0.95, 0.7, 0.05)
track_conf = st.sidebar.slider("Min Tracking Confidence", 0.5, 0.95, 0.7, 0.05)

st.sidebar.markdown("---")
st.sidebar.header("👁️ Visual Overlays")
show_hud = st.sidebar.checkbox("Show Volume HUD Gauge", value=True)
show_skeleton = st.sidebar.checkbox("Show Hand Skeleton Mesh", value=True)

# Configure Streamlit WebRTC Streamer with dynamic resolution
ctx = webrtc_streamer(
    key="gesture-control-pro",
    mode=WebRtcMode.SENDRECV,
    video_processor_factory=GestureControllerProcessor,
    media_stream_constraints={
        "video": {
            "width": {"ideal": cam_width},
            "height": {"ideal": cam_height},
            "frameRate": {"ideal": 30},
        },
        "audio": False,
    },
    async_processing=True,
)

if ctx.video_processor:
    ctx.video_processor.update_params(
        alpha_min, alpha_max, deadzone, det_conf, track_conf, show_hud, show_skeleton
    )