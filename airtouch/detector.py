"""Hand detection with MediaPipe Tasks (HandLandmarker).

The old `mediapipe.solutions` have been dropped from recent MediaPipe releases:
this uses the Tasks API instead, which needs the model file downloaded once
(see `ensure_model`).
"""

from pathlib import Path

import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
DEFAULT_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "hand_landmarker.task"

HAND_CONNECTIONS = [
    (c.start, c.end) for c in vision.HandLandmarksConnections.HAND_CONNECTIONS
]


def ensure_model(path: Path = DEFAULT_MODEL_PATH) -> Path:
    """Return the model path, downloading the file if it is missing."""
    path = Path(path)
    if path.exists() and path.stat().st_size > 0:
        return path

    import urllib.request

    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading the HandLandmarker model to {path} ...")
    tmp = path.with_suffix(".part")
    urllib.request.urlretrieve(MODEL_URL, tmp)
    tmp.replace(path)
    print("Model ready.")
    return path


class HandDetector:
    """Synchronous wrapper in VIDEO mode: a frame in, the landmarks out."""

    def __init__(
        self,
        model_path: Path,
        num_hands: int = 1,
        min_detection_confidence: float = 0.6,
        min_tracking_confidence: float = 0.5,
    ):
        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=num_hands,
            min_hand_detection_confidence=min_detection_confidence,
            min_hand_presence_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(options)
        self._last_ts = -1

    def detect(self, rgb_frame, timestamp_ms: int):
        """Return the 21 landmarks of the first hand, or None."""
        # The Tasks API demands strictly increasing timestamps.
        if timestamp_ms <= self._last_ts:
            timestamp_ms = self._last_ts + 1
        self._last_ts = timestamp_ms

        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        if not result.hand_landmarks:
            return None
        return result.hand_landmarks[0]

    def close(self) -> None:
        self._landmarker.close()
