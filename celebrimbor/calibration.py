"""Redrawing the active area: the rectangle of frame that maps onto the screen.

The default active area is a guess about where you sit and how far you reach.
It is wrong for anyone whose webcam is off to one side, or who leans on one
elbow. Rather than tuning four numbers in a file, you draw the rectangle in the
air with one hand and the corners land where your hand actually goes.

The whole thing is a small state machine driven from the main loop, one frame at
a time. While it runs the gesture engine is paused: the hand tracing the
rectangle would otherwise be pointing, clicking and sliding all the way round.

    ARMED    -> hand seen, waiting for it to hold still to start
    DRAWING  -> recording the path, until the hand holds still again
    DONE     -> rectangle accepted (or rejected), the caller reads and drops us

Both stop conditions are "hold still", which is the one signal that needs no
extra pose: you stop moving and it takes. No pinch to hold across the whole
tracing, no second gesture to remember.
"""

import time
from dataclasses import dataclass

# The path is the palm point, not the fingertip: the same reference the cursor
# uses when anchored, and it does not wander when the fingers move.
STILL_TRAVEL = 0.035     # frame fractions: movement below this counts as still
STILL_SECONDS = 0.8      # how long to hold still to start, and to finish
MIN_SIDE = 0.12          # a rectangle smaller than this is a slip, not a gesture
MARGIN = 0.02            # keep-out band at the frame edge (see _clean)
TIMEOUT = 45.0           # abandoned mid-way: give up rather than stay stuck
TRIM = 0.05              # fraction of the path trimmed off each end (see _clean)

# Both fists held still for this long triggers a calibration. Long on purpose:
# it is not a gesture you want to walk into by resting your hands.
BOTH_FISTS_SECONDS = 15.0


@dataclass
class Progress:
    """What the HUD needs to draw: a line of text and a 0..1 bar."""

    text: str
    ratio: float = 0.0
    rect: tuple[float, float, float, float] | None = None  # live bounding box


class Calibration:
    """One calibration run, fed the tracking point one frame at a time."""

    def __init__(self) -> None:
        self.state = "ARMED"
        self.result: tuple[float, float, float, float] | None = None
        self.message = ""
        self._path: list[tuple[float, float]] = []
        self._still_since: float | None = None
        self._anchor: tuple[float, float] | None = None
        self._started = time.monotonic()

    @property
    def done(self) -> bool:
        return self.state == "DONE"

    @property
    def path(self) -> list[tuple[float, float]]:
        """The path traced so far, for the HUD to draw."""
        return self._path

    # ------------------------------------------------------------------
    def update(self, point: tuple[float, float] | None, t: float) -> Progress:
        """One frame. `point` is the tracked hand, or None if none is in view."""
        if self.state == "DONE":
            return Progress(self.message, 1.0)

        if t - self._started > TIMEOUT:
            return self._fail("calibration timed out")

        if point is None:
            # Losing the hand does not throw the path away: it is easy to leave
            # the frame for a moment at a corner, which is exactly where the
            # camera's field of view runs out. Only the still timer resets, or
            # a hand gone missing would read as a hand holding perfectly still
            # and end the drawing on its own.
            self._still_since = None
            self._anchor = None
            waiting = "show your hand" if self.state == "ARMED" else "hand lost, keep going"
            return Progress(waiting)

        still_for = self._still(point, t)

        if self.state == "ARMED":
            if still_for >= STILL_SECONDS:
                self.state = "DRAWING"
                self._path = [point]
                self._still_since = None
                self._anchor = None
                return Progress("draw the rectangle", 0.0)
            return Progress(
                "hold still to start", min(still_for / STILL_SECONDS, 1.0)
            )

        # DRAWING
        self._path.append(point)
        if still_for >= STILL_SECONDS:
            return self._finish()
        return Progress(
            "drawing - hold still to finish",
            min(still_for / STILL_SECONDS, 1.0),
            rect=_bounds(self._path),
        )

    # ------------------------------------------------------------------
    def _still(self, point: tuple[float, float], t: float) -> float:
        """Seconds the hand has been within STILL_TRAVEL of where it stopped.

        Measured against the point where it first settled, not against the
        previous frame: a hand creeping along slowly enough moves less than the
        threshold every single frame, and would otherwise read as still forever.
        """
        if self._anchor is None or _far(point, self._anchor, STILL_TRAVEL):
            self._anchor = point
            self._still_since = t
            return 0.0
        return t - self._still_since

    # ------------------------------------------------------------------
    def _finish(self) -> Progress:
        rect = _clean(self._path)
        if rect is None:
            return self._fail("rectangle too small or off-frame, nothing changed")
        self.result = rect
        return self._ok("calibrated")

    def _fail(self, message: str) -> Progress:
        self.state = "DONE"
        self.result = None
        self.message = message
        return Progress(message, 1.0)

    def _ok(self, message: str) -> Progress:
        self.state = "DONE"
        self.message = message
        return Progress(message, 1.0, rect=self.result)


# ----------------------------------------------------------------------
def _far(a: tuple[float, float], b: tuple[float, float], limit: float) -> bool:
    return abs(a[0] - b[0]) > limit or abs(a[1] - b[1]) > limit


def _bounds(path: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in path]
    ys = [p[1] for p in path]
    return min(xs), min(ys), max(xs), max(ys)


def _clean(path: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    """Bounding box of the traced path, or None if it is not usable.

    The "minimal cleanup": both ends of the path are the hand arriving at and
    leaving the starting corner, plus the settling wobble of holding still, and
    neither belongs to the rectangle. Trimming a fraction off each end costs a
    little of a corner and removes all of it.

    The rectangle is then required to sit inside the frame with a margin. A box
    touching the edge means the hand ran out of camera before it ran out of
    rectangle, so the corner is wherever the field of view happened to stop -
    not where you meant to put it, and not somewhere you can reach again.
    """
    if len(path) < 12:
        return None

    cut = int(len(path) * TRIM)
    core = path[cut : len(path) - cut] if cut else path
    if len(core) < 8:
        return None

    x0, y0, x1, y1 = _bounds(core)
    if x1 - x0 < MIN_SIDE or y1 - y0 < MIN_SIDE:
        return None
    if x0 < MARGIN or y0 < MARGIN or x1 > 1.0 - MARGIN or y1 > 1.0 - MARGIN:
        return None
    return x0, y0, x1, y1


# ----------------------------------------------------------------------
class BothFistsWatch:
    """Watches for both fists held still together, the hands-free trigger.

    The per-hand recogniser already measures how long each fist has been still
    and reports it as a 0..1 progress; what it cannot see is the other hand. So
    the two are joined here: the run is only as old as the more recent of the
    two hands to settle.
    """

    def __init__(self, seconds: float = BOTH_FISTS_SECONDS):
        self.seconds = seconds
        self._since: float | None = None

    def update(self, states, t: float) -> float:
        """Returns the progress 0..1; 1.0 means fire."""
        fists = [s for s in states if s.mode == "FIST"]
        both_still = len(fists) == 2 and all(s.fist_hold_progress > 0.0 for s in fists)
        if not both_still:
            self._since = None
            return 0.0
        if self._since is None:
            self._since = t
        return min((t - self._since) / self.seconds, 1.0)

    def reset(self) -> None:
        self._since = None
