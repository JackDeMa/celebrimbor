"""Hand and face detection with MediaPipe Tasks (HandLandmarker, FaceLandmarker).

The old `mediapipe.solutions` have been dropped from recent MediaPipe releases:
this uses the Tasks API instead, which needs the model files downloaded once
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

FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)
DEFAULT_FACE_MODEL_PATH = DEFAULT_MODEL_PATH.with_name("face_landmarker.task")

HAND_CONNECTIONS = [
    (c.start, c.end) for c in vision.HandLandmarksConnections.HAND_CONNECTIONS
]


# The parts of the face mesh the preview draws: eyes, irises and lips are what
# the face pointer reads, the oval shows where the model thinks the face is.
FACE_CONNECTIONS = [
    (c.start, c.end)
    for group in (
        vision.FaceLandmarksConnections.FACE_LANDMARKS_FACE_OVAL,
        vision.FaceLandmarksConnections.FACE_LANDMARKS_LEFT_EYE,
        vision.FaceLandmarksConnections.FACE_LANDMARKS_RIGHT_EYE,
        vision.FaceLandmarksConnections.FACE_LANDMARKS_LEFT_IRIS,
        vision.FaceLandmarksConnections.FACE_LANDMARKS_RIGHT_IRIS,
        vision.FaceLandmarksConnections.FACE_LANDMARKS_LIPS,
    )
    for c in group
]


class ModelError(RuntimeError):
    """A model file is missing and could not be downloaded."""


def have_model(path: Path) -> bool:
    path = Path(path)
    return path.is_file() and path.stat().st_size > 0


def ensure_model(
    path: Path = DEFAULT_MODEL_PATH,
    url: str = MODEL_URL,
    name: str = "hand",
    progress=None,
    timeout: float = 20.0,
) -> Path:
    """Return the model path, downloading the file if it is missing.

    Frozen builds carry both models, so this only downloads in a source
    checkout. `progress(done, total)` is called as bytes arrive (total is 0
    when the server does not say). The file is written to a .part next to it
    and renamed once complete: a broken download never leaves a bad model.
    """
    path = Path(path)
    if have_model(path):
        return path

    import http.client
    import urllib.request

    print(f"Downloading the {name} model to {path} ...", flush=True)
    tmp = path.with_name(path.name + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=timeout) as resp, open(tmp, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(64 * 1024):
                out.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        if done == 0 or (total and done != total):
            raise OSError(f"incomplete download, {done} of {total or '?'} bytes")
        tmp.replace(path)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        reason = getattr(exc, "reason", None) or exc  # URLError hides the cause there
        raise ModelError(
            f"Cannot download the {name} model: {reason}\n"
            f"Check the internet connection, or download\n  {url}\nand save it as\n  {path}"
        ) from exc
    print("Model ready.", flush=True)
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


class FaceDetector:
    """Synchronous wrapper in VIDEO mode: a frame in, one face's landmarks out.

    Only one face is tracked: the cursor belongs to whoever sits in front of
    the screen, and a second face walking by must not be able to take it.
    """

    def __init__(
        self,
        model_path: Path,
        min_detection_confidence: float = 0.6,
        min_tracking_confidence: float = 0.5,
    ):
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=min_detection_confidence,
            min_face_presence_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._last_ts = -1

    def detect(self, rgb_frame, timestamp_ms: int) -> list | None:
        """Return the landmarks of the face in frame, or None."""
        if timestamp_ms <= self._last_ts:
            timestamp_ms = self._last_ts + 1
        self._last_ts = timestamp_ms

        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        if not result.face_landmarks:
            return None
        return result.face_landmarks[0]

    def close(self) -> None:
        self._landmarker.close()
