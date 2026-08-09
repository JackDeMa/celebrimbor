"""Geometric feature extraction from the 21 MediaPipe Hands landmarks.

Landmark indices:
    0  wrist
    4  thumb tip         3  thumb IP
    8  index tip         5  index MCP     6  index PIP
    12 middle tip        9  middle MCP   10  middle PIP
    16 ring tip         13  ring MCP     14  ring PIP
    20 pinky tip        17  pinky MCP    18  pinky PIP
"""

from dataclasses import dataclass
from math import atan2, hypot

WRIST = 0
THUMB_IP, THUMB_TIP = 3, 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_PIP, RING_TIP = 14, 16
PINKY_MCP, PINKY_PIP, PINKY_TIP = 17, 18, 20

# Fingers that can pinch against the thumb, in the order they appear in the
# preview window.
PINCH_FINGERS = {"index": INDEX_TIP, "middle": MIDDLE_TIP, "ring": RING_TIP}

# The two hands are tracked independently: each one has its own recogniser and
# its own bindings. Names are from the user's point of view, not the camera's.
HAND_SLOTS = ("left", "right")


def other_hand(slot: str) -> str:
    return "right" if slot == "left" else "left"

Point = tuple[float, float]


def _dist(a: Point, b: Point) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


@dataclass
class HandFeatures:
    """Description of the hand, independent of distance and rotation."""

    points: list[Point]
    scale: float              # hand size (wrist -> middle MCP)
    index_extended: bool
    middle_extended: bool
    ring_extended: bool
    pinky_extended: bool
    thumb_extended: bool
    pinches: dict[str, float]  # "index"/"middle"/"ring" -> distance from the thumb
    index_point: Point        # index fingertip: precise but shaky during pinches
    palm_point: Point         # palm centre (used for swipes and axes)
    palm_outer: Point         # outer palm edge, pinky side

    def anchor(self, name: str) -> Point:
        """Alternative reference point to the index finger, chosen by config."""
        pts = self.points
        if name == "palm_outer":
            return self.palm_outer
        if name == "palm_center":
            return self.palm_point
        if name == "pinky_mcp":
            return pts[PINKY_MCP]
        if name == "index_mcp":
            return pts[INDEX_MCP]
        if name == "wrist":
            return pts[WRIST]
        raise KeyError(name)

    @property
    def pointing_angle(self) -> float:
        """Direction the index and middle fingers point at, in radians.

        Measured from the wrist to the midpoint of the two fingertips: a long
        lever, so the landmark jitter barely moves it. y grows downwards, so a
        growing angle is a clockwise rotation as seen in the preview window.
        """
        pts = self.points
        tip_x = (pts[INDEX_TIP][0] + pts[MIDDLE_TIP][0]) / 2.0
        tip_y = (pts[INDEX_TIP][1] + pts[MIDDLE_TIP][1]) / 2.0
        wrist = pts[WRIST]
        return atan2(tip_y - wrist[1], tip_x - wrist[0])

    @property
    def extended_count(self) -> int:
        return sum(
            (
                self.index_extended,
                self.middle_extended,
                self.ring_extended,
                self.pinky_extended,
                self.thumb_extended,
            )
        )

    @property
    def is_fist(self) -> bool:
        return not (
            self.index_extended
            or self.middle_extended
            or self.ring_extended
            or self.pinky_extended
        )


def _finger_extended(pts: list[Point], tip: int, pip: int) -> bool:
    """A finger is extended if its tip is farther from the wrist than the PIP.

    The comparison is based on distances (not on the y coordinate alone), so it
    works with the hand rotated or tilted too.
    """
    wrist = pts[WRIST]
    return _dist(pts[tip], wrist) > _dist(pts[pip], wrist) * 1.06


def extract(landmarks) -> HandFeatures:
    """Convert MediaPipe's normalised landmarks into usable features."""
    pts: list[Point] = [(lm.x, lm.y) for lm in landmarks]

    # Hand scale: wrist -> base of the middle finger. It does not change when
    # the fingers close, which makes it a good normalisation reference.
    scale = _dist(pts[WRIST], pts[MIDDLE_MCP])
    if scale < 1e-6:
        scale = 1e-6

    index_ext = _finger_extended(pts, INDEX_TIP, INDEX_PIP)
    middle_ext = _finger_extended(pts, MIDDLE_TIP, MIDDLE_PIP)
    ring_ext = _finger_extended(pts, RING_TIP, RING_PIP)
    pinky_ext = _finger_extended(pts, PINKY_TIP, PINKY_PIP)

    # The thumb opens sideways: it is evaluated against the base of the pinky.
    pinky_mcp = pts[PINKY_MCP]
    thumb_ext = _dist(pts[THUMB_TIP], pinky_mcp) > _dist(pts[THUMB_IP], pinky_mcp) * 1.10

    palm = (
        (pts[WRIST][0] + pts[INDEX_MCP][0] + pts[PINKY_MCP][0]) / 3.0,
        (pts[WRIST][1] + pts[INDEX_MCP][1] + pts[PINKY_MCP][1]) / 3.0,
    )
    # Outer palm edge (pinky side, the "karate chop" edge): wrist and pinky base
    # do not move when thumb, middle and ring fingers close for a pinch, unlike
    # the index fingertip.
    palm_outer = (
        (pts[WRIST][0] + pts[PINKY_MCP][0]) / 2.0,
        (pts[WRIST][1] + pts[PINKY_MCP][1]) / 2.0,
    )

    return HandFeatures(
        points=pts,
        scale=scale,
        index_extended=index_ext,
        middle_extended=middle_ext,
        ring_extended=ring_ext,
        pinky_extended=pinky_ext,
        thumb_extended=thumb_ext,
        pinches={
            name: _dist(pts[THUMB_TIP], pts[tip]) / scale
            for name, tip in PINCH_FINGERS.items()
        },
        index_point=pts[INDEX_TIP],
        palm_point=palm,
        palm_outer=palm_outer,
    )


class Hysteresis:
    """Two-threshold switch: avoids flicker around the threshold."""

    def __init__(self, on_below: float, off_above: float):
        self.on_below = on_below
        self.off_above = off_above
        self.state = False

    def update(self, value: float) -> bool:
        if self.state:
            if value > self.off_above:
                self.state = False
        else:
            if value < self.on_below:
                self.state = True
        return self.state

    def reset(self) -> None:
        self.state = False
