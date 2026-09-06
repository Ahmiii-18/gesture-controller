"""
Real-Time Gesture Controller
-----------------------------
Streamlit + WebRTC + MediaPipe Hands app that maps hand gestures to
system actions (volume, play/pause, scroll).

Environment-aware by design: system audio (PyCAW) and OS input
simulation (PyAutoGUI) only work on a local desktop session, so this
module detects what's actually available at runtime and degrades
gracefully to a visualization-only demo on headless/cloud hosts
(e.g. Streamlit Community Cloud).
"""

from __future__ import annotations

import math
import platform
import threading
import time
from dataclasses import dataclass

import av
import cv2
import mediapipe as mp
import numpy as np
import streamlit as st
from streamlit_webrtc import VideoProcessorBase, WebRtcMode, webrtc_streamer

mp_hands = mp.solutions.hands
mp_draw = mp.solutions.drawing_utils
mp_styles = mp.solutions.drawing_styles


# --------------------------------------------------------------------------
# Environment capability detection
# --------------------------------------------------------------------------
@dataclass
class Capabilities:
    audio: bool = False
    automation: bool = False
    audio_note: str = ""
    automation_note: str = ""


def detect_capabilities() -> Capabilities:
    """Probe for local-desktop-only features without crashing on cloud hosts."""
    caps = Capabilities()

    # System volume control requires the Windows Core Audio API (PyCAW).
    if platform.system() == "Windows":
        try:
            import comtypes
            from comtypes import CLSCTX_ALL
            from ctypes import POINTER, cast
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            comtypes.CoInitialize()
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            st.session_state.setdefault("_volume_control", cast(interface, POINTER(IAudioEndpointVolume)))
            vol_range = st.session_state["_volume_control"].GetVolumeRange()
            st.session_state["_vol_db_range"] = (vol_range[0], vol_range[1])
            caps.audio = True
        except Exception as e:  # noqa: BLE001 - deliberately broad, this is a capability probe
            caps.audio_note = str(e)
    else:
        caps.audio_note = "System audio control requires Windows (PyCAW)."

    # Keyboard/mouse simulation requires a real display session. PyAutoGUI
    # (via mouseinfo) touches the X11 DISPLAY at import time, which doesn't
    # exist on headless containers, so the import itself must be guarded.
    try:
        import pyautogui  # noqa: F401

        caps.automation = True
    except Exception as e:  # noqa: BLE001
        caps.automation_note = str(e)

    return caps


CAPS = detect_capabilities()


# --------------------------------------------------------------------------
# Video processor: hand tracking + gesture -> action mapping
# --------------------------------------------------------------------------
class GestureControllerProcessor(VideoProcessorBase):
    def __init__(self) -> None:
        self.lock = threading.Lock()

        # Tunable parameters (updated live from the sidebar)
        self.alpha_min = 0.05
        self.alpha_max = 0.85
        self.deadzone = 1.5
        self.cutoff_speed = 15.0
        self.min_detection_conf = 0.7
        self.min_tracking_conf = 0.7
        self.show_hud = True
        self.show_skeleton = True

        # Smoothing / gesture state
        self.smooth_vol = 0.0
        self.prev_raw_vol = 0.0
        self.last_fist_time = 0.0
        self.prev_middle_y: float | None = None

        self.hands = self._make_detector()

    def _make_detector(self) -> mp_hands.Hands:
        return mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            model_complexity=1,
            min_detection_confidence=self.min_detection_conf,
            min_tracking_confidence=self.min_tracking_conf,
        )

    def update_params(
        self,
        alpha_min: float,
        alpha_max: float,
        deadzone: float,
        det_conf: float,
        track_conf: float,
        show_hud: bool,
        show_skeleton: bool,
    ) -> None:
        with self.lock:
            self.alpha_min = alpha_min
            self.alpha_max = alpha_max
            self.deadzone = deadzone
            self.show_hud = show_hud
            self.show_skeleton = show_skeleton

            if self.min_detection_conf != det_conf or self.min_tracking_conf != track_conf:
                self.min_detection_conf = det_conf
                self.min_tracking_conf = track_conf
                self.hands = self._make_detector()

    @staticmethod
    def is_fist(hand_landmarks) -> bool:
        tips = [8, 12, 16, 20]
        mcps = [5, 9, 13, 17]
        return all(
            hand_landmarks.landmark[tip].y >= hand_landmarks.landmark[mcp].y
            for tip, mcp in zip(tips, mcps)
        )

    def _apply_volume(self, smooth_vol: float) -> None:
        """Push the smoothed 0-100 value to the OS if audio control is available."""
        if not CAPS.audio:
            return
        try:
            volume_control = st.session_state.get("_volume_control")
            min_vol, max_vol = st.session_state.get("_vol_db_range", (0, 0))
            target_db = np.interp(smooth_vol, [0, 100], [min_vol, max_vol])
            volume_control.SetMasterVolumeLevel(target_db, None)
        except Exception:
            pass

    @staticmethod
    def _press_space() -> None:
        if not CAPS.automation:
            return
        try:
            import pyautogui

            pyautogui.press("space")
        except Exception:
            pass

    @staticmethod
    def _scroll(amount: int) -> None:
        if not CAPS.automation:
            return
        try:
            import pyautogui

            pyautogui.scroll(amount)
        except Exception:
            pass

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
            a_min, a_max, dz = self.alpha_min, self.alpha_max, self.deadzone
            do_hud, do_skel = self.show_hud, self.show_skeleton

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

                # --- 1. PLAY / PAUSE (fist) ---
                if self.is_fist(hand_landmarks):
                    if curr_time - self.last_fist_time > 1.2:
                        self._press_space()
                        self.last_fist_time = curr_time

                    cv2.putText(
                        img, "ACTION: PLAY / PAUSE", (int(180 * scale), int(60 * scale)),
                        cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 255), thickness, lineType=cv2.LINE_AA,
                    )
                else:
                    thumb, index, middle = (
                        hand_landmarks.landmark[4],
                        hand_landmarks.landmark[8],
                        hand_landmarks.landmark[12],
                    )
                    x1, y1 = int(thumb.x * w), int(thumb.y * h)
                    x2, y2 = int(index.x * w), int(index.y * h)
                    x3, y3 = int(middle.x * w), int(middle.y * h)

                    # --- 2. VOLUME (pinch distance) ---
                    circle_radius = int(8 * scale)
                    cv2.circle(img, (x1, y1), circle_radius, (255, 128, 0), cv2.FILLED, lineType=cv2.LINE_AA)
                    cv2.circle(img, (x2, y2), circle_radius, (255, 128, 0), cv2.FILLED, lineType=cv2.LINE_AA)
                    cv2.line(img, (x1, y1), (x2, y2), (255, 0, 255), thickness, lineType=cv2.LINE_AA)

                    distance = math.hypot(x2 - x1, y2 - y1)
                    min_dist, max_dist = int(25 * scale), int(220 * scale)
                    raw_vol = np.interp(distance, [min_dist, max_dist], [0, 100])

                    velocity = abs(raw_vol - self.prev_raw_vol)
                    self.prev_raw_vol = raw_vol

                    current_alpha = 0.0 if velocity < dz else np.interp(velocity, [0, self.cutoff_speed], [a_min, a_max])
                    self.smooth_vol = (current_alpha * raw_vol) + ((1 - current_alpha) * self.smooth_vol)
                    self._apply_volume(self.smooth_vol)

                    # --- 3. SCROLL (middle finger vertical motion) ---
                    if hand_landmarks.landmark[12].y < hand_landmarks.landmark[9].y:
                        if self.prev_middle_y is not None:
                            dy = y3 - self.prev_middle_y
                            deadzone_px = int(12 * scale)
                            if abs(dy) > deadzone_px:
                                scroll_amount = -int(dy * 4)
                                self._scroll(scroll_amount)
                                label = "SCROLL UP" if scroll_amount > 0 else "SCROLL DOWN"
                                cv2.putText(
                                    img, label, (int(180 * scale), int(100 * scale)),
                                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 0), thickness, lineType=cv2.LINE_AA,
                                )
                        self.prev_middle_y = y3
                    else:
                        self.prev_middle_y = None

        if do_hud:
            self._draw_volume_gauge(img, scale, thickness, font_scale)

        return av.VideoFrame.from_ndarray(img, format="bgr24")

    def _draw_volume_gauge(self, img, scale: float, thickness: int, font_scale: float) -> None:
        left, top = int(50 * scale), int(150 * scale)
        right, bottom = int(85 * scale), int(400 * scale)
        bar_y = int(np.interp(self.smooth_vol, [0, 100], [bottom, top]))

        cv2.rectangle(img, (left, top), (right, bottom), (0, 255, 0), thickness, lineType=cv2.LINE_AA)
        cv2.rectangle(img, (left, bar_y), (right, bottom), (0, 255, 0), cv2.FILLED, lineType=cv2.LINE_AA)
        cv2.putText(
            img, f"{int(self.smooth_vol)}%", (left - int(10 * scale), bottom + int(40 * scale)),
            cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 255, 0), thickness, lineType=cv2.LINE_AA,
        )


# --------------------------------------------------------------------------
# Streamlit UI
# --------------------------------------------------------------------------
def render_environment_banner() -> None:
    if CAPS.audio and CAPS.automation:
        return  # everything works locally, no banner needed

    with st.container(border=True):
        st.markdown("**⚠️ Running in a limited environment**")
        st.caption(
            "Hand tracking and the on-screen HUD work everywhere. The items below "
            "need a real local desktop session and are disabled here."
        )
        col1, col2 = st.columns(2)
        with col1:
            status = "✅ Active" if CAPS.audio else "🚫 Disabled"
            st.markdown(f"**System volume control:** {status}")
        with col2:
            status = "✅ Active" if CAPS.automation else "🚫 Disabled"
            st.markdown(f"**Play/pause & scroll:** {status}")


def render_sidebar() -> dict:
    st.sidebar.header("📷 Camera & Resolution")
    resolution_preset = st.sidebar.selectbox(
        "Target Quality Preset",
        ["Full HD (1080p)", "HD (720p)", "Standard (480p)"],
        index=1,
        help="720p is a good balance of tracking accuracy and browser performance.",
    )
    resolutions = {
        "Full HD (1080p)": (1920, 1080),
        "HD (720p)": (1280, 720),
        "Standard (480p)": (854, 480),
    }
    cam_width, cam_height = resolutions[resolution_preset]

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

    return {
        "cam_width": cam_width,
        "cam_height": cam_height,
        "alpha_min": alpha_min,
        "alpha_max": alpha_max,
        "deadzone": deadzone,
        "det_conf": det_conf,
        "track_conf": track_conf,
        "show_hud": show_hud,
        "show_skeleton": show_skeleton,
    }


def main() -> None:
    st.set_page_config(page_title="Gesture Controller", layout="wide")
    st.title("🎮 Multi-Modal Dynamic Gesture Controller")

    render_environment_banner()
    params = render_sidebar()

    ctx = webrtc_streamer(
        key="gesture-control-pro",
        mode=WebRtcMode.SENDRECV,
        video_processor_factory=GestureControllerProcessor,
        media_stream_constraints={
            "video": {
                "width": {"ideal": params["cam_width"]},
                "height": {"ideal": params["cam_height"]},
                "frameRate": {"ideal": 30},
            },
            "audio": False,
        },
        async_processing=True,
    )

    if ctx.video_processor:
        ctx.video_processor.update_params(
            params["alpha_min"],
            params["alpha_max"],
            params["deadzone"],
            params["det_conf"],
            params["track_conf"],
            params["show_hud"],
            params["show_skeleton"],
        )

    with st.expander("🖐️ Gesture reference"):
        st.markdown(
            "| Gesture | Action |\n"
            "|---|---|\n"
            "| Pinch (thumb + index) | Volume, scaled to finger separation |\n"
            "| Closed fist | Play / pause (spacebar) |\n"
            "| Middle finger raised, move up/down | Scroll |\n"
        )


if __name__ == "__main__":
    main()