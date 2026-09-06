# 🎮 Real-Time Windows Gesture Controller

A high-resolution, multi-modal human-computer interface (HCI) built using Streamlit, MediaPipe Hands, OpenCV, and WebRTC. Control system audio volume, media playback, and document scrolling in real time using hand gestures.

---

## ⚡ Core Features

* **Real-Time WebRTC Streaming**: Sub-second video processing pipeline optimized for high-resolution cameras.
* **Proportional Volume Pinch Control**: Precise volume adjustment via dynamic distance calculations with adaptive exponential smoothing filter.
* **System Media Actions**: Play / Pause toggling using hand fist detection via `pyautogui`.
* **Vertical Scroll Control**: Smooth page navigation tracking middle finger position.
* **PyCAW System Integration**: Direct access to Windows Master Endpoint Volume.

---

## 🛠 Hand Gesture Mechanics

| Action | Hand Gesture | Description |
| :--- | :--- | :--- |
| **Volume Control** | Pinch (Index + Thumb) | Controls system volume scaled to index-thumb separation distance. |
| **Play / Pause** | Closed Fist | Triggers Spacebar keypress with dynamic debounce protection. |
| **Scroll Up / Down** | Middle Finger Motion | Tracks vertical finger displacement relative to hand position. |

---

## ⚙️ Installation & Setup

### Prerequisites
* **Python**: 3.10 or higher
* **OS**: Windows (required for PyCAW audio control; non-Windows systems will default to simulation mode)

### Installation Steps

```bash
# 1. Clone repository
git clone [https://github.com/](https://github.com/)<your-username>/gesture-controller.git
cd gesture-controller

# 2. Set up virtual environment
python -m venv venv
venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run application
streamlit run app.py