"""Geometric features from the 478 MediaPipe FaceLandmarker landmarks.

Only a handful of them are used:
    1                  nose tip
    33, 133            corners of one eye     160, 144, 158, 153  its eyelids
    263, 362           corners of the other   385, 380, 387, 373  its eyelids
    468, 473           centres of the two irises
    13, 14             inner lips

Everything is measured in units of the face itself (the distance between the
eyes, the width of one eye) and with the head's roll taken out, so neither
sitting closer to the webcam nor tilting the head sideways moves the cursor.
"""

from dataclasses import dataclass
from math import atan2, cos, hypot, sin

NOSE_TIP = 1
UPPER_LIP, LOWER_LIP = 13, 14
IRIS_CENTRES = (468, 473)

# corners, then the two eyelid pairs (upper, lower) used for the aspect ratio
_EYES = (
    ((33, 133), ((160, 144), (158, 153))),
    ((263, 362), ((385, 380), (387, 373))),
)

# Named from the user's point of view, like the hands.
EYE_SIDES = ("left", "right")

# The face's slot next to the two hands': its bindings live under this name.
FACE_SLOT = "face"

Point = tuple[float, float]


def other_eye(side: str) -> str:
    return "right" if side == "left" else "left"


def _dist(a: Point, b: Point) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


def _mid(a: Point, b: Point) -> Point:
    return (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0


@dataclass
class FaceFeatures:
    """Description of the face, independent of distance and head roll.

    x grows towards the user's right and y downwards, whether or not the image
    is mirrored.
    """

    points: list[Point]          # image coordinates, for the preview
    head: Point                  # nose tip relative to the eyes, in eye distances
    gaze: dict[str, Point]       # per eye: iris relative to its corners, in eye widths
    eye_open: dict[str, float]   # per eye: eye aspect ratio (height over width)
    mouth_open: float            # inner lip gap, in eye distances
    nose_point: Point            # image coordinates, for the preview


def extract(landmarks, aspect: float = 4.0 / 3.0, mirrored: bool = True) -> FaceFeatures:
    """Convert MediaPipe's normalised landmarks into usable features.

    `aspect` is the frame's width over its height: the landmarks are
    normalised on each axis separately, and distances only mean something once
    x and y are back on the same scale.
    """
    pts: list[Point] = [(lm.x, lm.y) for lm in landmarks]
    sq: list[Point] = [(x * aspect, y) for x, y in pts]

    # Which eye is which, by where it sits in the frame: a mirrored image is a
    # mirror, so the eye on the left of it is the user's left one; an
    # unmirrored one is the camera's view and the other way round.
    eyes = sorted(_EYES, key=lambda e: sq[e[0][0]][0] + sq[e[0][1]][0])
    sides = EYE_SIDES if mirrored else tuple(reversed(EYE_SIDES))
    flip = 1.0 if mirrored else -1.0

    centres = {side: _mid(sq[e[0][0]], sq[e[0][1]]) for side, e in zip(sides, eyes)}
    image_left, image_right = (centres[s] for s in sides)
    iod = max(_dist(image_left, image_right), 1e-6)
    roll = atan2(image_right[1] - image_left[1], image_right[0] - image_left[0])
    c, s = cos(roll), sin(roll)

    def upright(dx: float, dy: float) -> Point:
        """Undo the head's roll: a tilted head is not a turned one."""
        return dx * c + dy * s, -dx * s + dy * c

    mid = _mid(image_left, image_right)
    hx, hy = upright(sq[NOSE_TIP][0] - mid[0], sq[NOSE_TIP][1] - mid[1])
    head = (flip * hx / iod, hy / iod)

    irises = [sq[i] for i in IRIS_CENTRES if i < len(sq)]
    gaze: dict[str, Point] = {}
    eye_open: dict[str, float] = {}
    for side, ((a, b), lids) in zip(sides, eyes):
        width = max(_dist(sq[a], sq[b]), 1e-6)
        centre = centres[side]
        eye_open[side] = sum(_dist(sq[u], sq[d]) for u, d in lids) / (len(lids) * width)
        if irises:
            # Paired by distance rather than by index: which iris the model
            # numbers first is its business, which eye it sits in is not.
            iris = min(irises, key=lambda p: _dist(p, centre))
            gx, gy = upright(iris[0] - centre[0], iris[1] - centre[1])
            gaze[side] = (flip * gx / width, gy / width)

    return FaceFeatures(
        points=pts,
        head=head,
        gaze=gaze,
        eye_open=eye_open,
        mouth_open=_dist(sq[UPPER_LIP], sq[LOWER_LIP]) / iod,
        nose_point=pts[NOSE_TIP],
    )
