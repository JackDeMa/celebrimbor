"""Hand detection with MediaPipe Tasks (HandLandmarker).

The old `mediapipe.solutions` have been dropped from recent MediaPipe releases:
this uses the Tasks API instead, which needs the model file downloaded once
(see `ensure_model`).
"""

from dataclasses import dataclass, replace
from pathlib import Path

import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from .hand import HAND_SLOTS, other_hand

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


@dataclass
class HandObservation:
    """One detected hand: which one it is, how sure we are, its 21 landmarks."""

    slot: str          # "left" or "right", from the user's point of view
    score: float       # confidence of the handedness classification
    landmarks: list


def _assign_slots(hands: list[HandObservation]) -> list[HandObservation]:
    """One hand per slot at most.

    MediaPipe classifies each hand on its own, so now and then it labels both
    of them the same way. The more confident one keeps its slot and the other
    is moved to the free one, otherwise a single hand would drive both sets of
    bindings at once.
    """
    taken: dict[str, HandObservation] = {}
    for obs in sorted(hands, key=lambda h: h.score, reverse=True):
        slot = obs.slot if obs.slot not in taken else other_hand(obs.slot)
        if slot in taken:
            continue
        taken[slot] = replace(obs, slot=slot)
    return [taken[slot] for slot in HAND_SLOTS if slot in taken]


class HandDetector:
    """Synchronous wrapper in VIDEO mode: a frame in, the landmarks out."""

    def __init__(
        self,
        model_path: Path,
        num_hands: int = 2,
        min_detection_confidence: float = 0.6,
        min_tracking_confidence: float = 0.5,
        mirrored: bool = True,
    ):
        self.mirrored = mirrored
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

    def detect(self, rgb_frame, timestamp_ms: int) -> list[HandObservation]:
        """Return the detected hands, at most one per slot."""
        # The Tasks API demands strictly increasing timestamps.
        if timestamp_ms <= self._last_ts:
            timestamp_ms = self._last_ts + 1
        self._last_ts = timestamp_ms

        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        if not result.hand_landmarks:
            return []

        hands = [
            HandObservation(self._slot(categories[0].category_name),
                            categories[0].score, landmarks)
            for landmarks, categories in zip(result.hand_landmarks, result.handedness)
        ]
        return _assign_slots(hands)

    def _slot(self, category_name: str) -> str:
        # MediaPipe reads the hand off the image it is given, and the image we
        # give it is mirrored: a right hand arrives looking like a left one, so
        # the label has to be flipped back. With --no-mirror the frame reaches
        # the model untouched and the label is already the right one.
        slot = category_name.strip().lower()
        if slot not in HAND_SLOTS:
            return HAND_SLOTS[0]
        return other_hand(slot) if self.mirrored else slot

    def close(self) -> None:
        self._landmarker.close()
