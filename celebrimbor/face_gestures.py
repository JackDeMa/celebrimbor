"""Face recognition: from FaceFeatures to named events.

The face drives the cursor in one of two ways:

    head  the nose works as a joystick: turn the head and the cursor follows.
          Steady and precise, the one to start with.
    eyes  the pupils: look somewhere and the cursor goes there. Hands-free and
          head-free, but a webcam sees the pupil at a few pixels, so it aims at
          buttons rather than at characters.

With both on the head does the aiming, exactly as in head mode, and the pupils
only nudge the cursor a little way from there: their jitter stays inside a
small patch of screen instead of being spread over all of it.

Both are absolute and measured from a neutral pose taken at startup, with the
user looking at the centre of the screen. Clicks come from the eyelids: a wink
of one eye is a click, held a little longer it becomes a drag. Blinking with
both eyes, as everyone does every few seconds, never does anything.

Like the hand recogniser this never touches the mouse: it produces events in
the same format, and the engine runs the actions bound to them.
"""

from collections import deque
from dataclasses import dataclass, field

from .config import Config
from .face import EYE_SIDES, FaceFeatures, other_eye
from .filters import Deadband, PointFilter
from .gestures import ClosingDetector, GestureEvent

FACE_POINTERS = ("head", "eyes")

# Gesture -> kind of event it produces. Also used to validate the JSON.
FACE_GESTURE_KINDS: dict[str, str] = {
    "face_move": "cursor",
    "wink_left_tap": "trigger",
    "wink_left_hold": "hold",
    "wink_right_tap": "trigger",
    "wink_right_hold": "hold",
    "blink_long": "trigger",
    "mouth_open_tap": "trigger",
    "mouth_open_hold": "hold",
    "mouth_scroll_vertical": "axis",
    "mouth_scroll_horizontal": "axis",
}
MOUTH_SCROLLS = ("mouth_scroll_vertical", "mouth_scroll_horizontal")


@dataclass
class FaceRecognition:
    """Outcome of one frame: events to run plus information for the HUD."""

    events: list[GestureEvent] = field(default_factory=list)
    mode: str = "NO FACE"
    detail: str = ""
    ref_point: tuple[float, float] | None = None   # nose, in image coordinates
    eyes: dict[str, float] = field(default_factory=dict)  # openness, 1 = habitual
    calibration: float = 0.0                       # 0..1 while calibrating


class _Switch:
    """Tap or hold on one on/off signal: an eye shut, the mouth open.

    The same rule as the pinches: let go before `hold_after` and it is a tap,
    keep it past that and it becomes a hold, which ends when you let go. With
    no hold bound the tap is allowed up to `tap_max` instead, so a slow wink
    still clicks.
    """

    def __init__(
        self, name: str, active: set[str], tap_min: float, tap_max: float, hold_after: float
    ):
        self.name = name
        self.tap_bound = f"{name}_tap" in active
        self.hold_bound = f"{name}_hold" in active
        self.tap_min = tap_min
        self.tap_limit = hold_after if self.hold_bound else tap_max
        self.hold_after = hold_after
        self.down_at: float | None = None
        self.holding = False

    @property
    def bound(self) -> bool:
        return self.tap_bound or self.hold_bound

    @property
    def pending(self) -> bool:
        """Closed and not yet a hold: the cursor waits for the outcome."""
        return self.down_at is not None and not self.holding

    def update(self, on: bool, t: float) -> list[GestureEvent]:
        events: list[GestureEvent] = []
        if on:
            if self.down_at is None:
                self.down_at = t
            elif (
                self.hold_bound
                and not self.holding
                and t - self.down_at >= self.hold_after
            ):
                self.holding = True
                events.append(GestureEvent(f"{self.name}_hold", "hold", True))
        elif self.down_at is not None:
            held = t - self.down_at
            if self.holding:
                events.append(GestureEvent(f"{self.name}_hold", "hold", False))
            elif self.tap_bound and self.tap_min <= held <= self.tap_limit:
                events.append(GestureEvent(f"{self.name}_tap", "trigger"))
            self.down_at = None
            self.holding = False
        return events

    def cancel(self) -> list[GestureEvent]:
        """Drop it with no tap; a hold in progress is released."""
        events = []
        if self.holding:
            events.append(GestureEvent(f"{self.name}_hold", "hold", False))
        self.down_at = None
        self.holding = False
        return events


class FaceRecognizer:
    def __init__(self, cfg: Config, active: set[str] | None = None):
        self.cfg = cfg
        cfg.resolve_inputs()
        active = set(FACE_GESTURE_KINDS) if active is None else active
        self.moves = "face_move" in active
        # The head's cursor, and the pupils' share of it (see `_fine_update`).
        self.cursor_filter = PointFilter(cfg.head_min_cutoff, cfg.face_beta, cfg.d_cutoff)
        self.fine_filter = PointFilter(cfg.eyes_fine_cutoff, cfg.eyes_fine_beta, cfg.d_cutoff)
        self._fine_band = Deadband(cfg.eyes_fine_deadband)
        self._fine = (0.0, 0.0)                      # in screens, from the head's point
        self._fine_history: deque[tuple[float, float, float]] = deque()
        self._fine_t: float | None = None            # last frame the pupils were read
        self._fine_held = False                      # rewound for the wink under way
        self._gaze0 = (0.0, 0.0)                     # the pupils' centre, slowly following
        self.neutral: tuple[tuple[float, float], tuple[float, float]] | None = None
        self.set_mode(cfg.use_head, cfg.use_eyes)

        self._eye = {
            side: ClosingDetector(cfg.eye_window, cfg.eye_closed_ratio, cfg.eye_open_ratio)
            for side in EYE_SIDES
        }
        self._winks = {
            side: _Switch(f"wink_{side}", active, cfg.wink_min, cfg.wink_max, cfg.face_hold)
            for side in EYE_SIDES
        }
        self._mouth = _Switch("mouth_open", active, cfg.wink_min, cfg.wink_max, cfg.face_hold)
        self._mouth_is_open = False
        # With the mouth open the face's movement scrolls instead of moving
        # the cursor, which stays where it was.
        self._scrolls = [name for name in MOUTH_SCROLLS if name in active]
        self._scroll_last: tuple[float, float] | None = None
        self._blink_long = "blink_long" in active
        # Eyes that have to reopen before they can wink: after a blink with
        # both, one of them always lets go a frame before the other, and that
        # frame alone must not read as the start of a wink.
        self._spoiled: set[str] = set()
        self._both_since: float | None = None
        self._blink_long_done = False

        # cursor
        self._history: deque[tuple[float, float, float]] = deque()  # (t, x, y)
        self._frozen: tuple[float, float] | None = None
        self._settle_until = 0.0

        # calibration
        self._calib_from: float | None = None
        self._calib_samples: list[tuple[tuple[float, float], tuple[float, float]]] = []

        self._missing = 0

    # ------------------------------------------------------------------
    def set_mode(self, head: bool, eyes: bool) -> None:
        """What steers, live: the head, the pupils, or the head with the pupils refining."""
        mode = ("eyes" if eyes else "head", bool(head))
        if mode == (getattr(self, "pointer", None), getattr(self, "use_head", None)):
            return
        self.pointer, self.use_head = mode
        # The pupils mean something else with and without the head under them.
        self._fine_reset()
        if self.neutral is not None:
            self._gaze0 = self.neutral[1]

    @property
    def hybrid(self) -> bool:
        return self.pointer == "eyes" and self.use_head

    @property
    def label(self) -> str:
        return "eyes+head" if self.hybrid else self.pointer

    def tune(self, min_cutoff: float, beta: float) -> None:
        """Steady / Speed, live: the pupils' filter with the eyes on, else the head's."""
        (self.fine_filter if self.pointer == "eyes" else self.cursor_filter).tune(min_cutoff, beta)

    def recalibrate(self) -> None:
        """Measure the neutral pose again, from the next frame with a face."""
        self.neutral = None
        self._calib_from = None
        self._calib_samples = []
        self.cursor_filter.reset()
        self._fine_reset()
        self._history.clear()

    def reset(self) -> list[GestureEvent]:
        """Bring everything back to rest, returning the closing events."""
        events = self._release()
        for detector in self._eye.values():
            detector.reset()
        self.cursor_filter.reset()
        self._fine_reset()
        self._history.clear()
        self._frozen = None
        self._settle_until = 0.0
        return events

    def _fine_reset(self) -> None:
        self.fine_filter.reset()
        self._fine_band.reset()
        self._fine = (0.0, 0.0)
        self._fine_history.clear()
        self._fine_t = None
        self._fine_held = False

    def _release(self) -> list[GestureEvent]:
        events = []
        for switch in (*self._winks.values(), self._mouth):
            events += switch.cancel()
        self._mouth_is_open = False
        self._scroll_last = None
        self._spoiled.clear()
        self._both_since = None
        self._blink_long_done = False
        return events

    # ------------------------------------------------------------------
    def update(self, feats: FaceFeatures | None, t: float) -> FaceRecognition:
        if feats is None:
            self._missing += 1
            if self._missing == self.cfg.grace_frames:
                return FaceRecognition(events=self.reset())
            return FaceRecognition()
        self._missing = 0

        # --- eyelids -------------------------------------------------------
        # The habitual opening stops being measured while a wink or a mouth
        # gesture is under way: a long wink-drag would otherwise eat it, the
        # same way a long pinch-drag would eat the hand's.
        busy = any(w.down_at is not None for w in self._winks.values())
        closed = {
            side: self._eye[side].update(feats.eye_open[side], t, frozen=busy)
            for side in EYE_SIDES
        }
        openness = {
            side: feats.eye_open[side] / d.baseline if d.baseline else 1.0
            for side, d in self._eye.items()
        }

        if self.neutral is None:
            return self._calibrate(feats, closed, openness, t)

        events = self._eyelids(closed, t)
        events += self._mouth_gesture(feats, t)

        # --- cursor --------------------------------------------------------
        holding = any(s.holding for s in (*self._winks.values(), self._mouth))
        waiting = (
            any(closed.values())
            or any(s.pending for s in self._winks.values() if s.bound)
            or (self._mouth.pending and self._mouth.bound)
        )
        # Head and eyes with an eye shut, for a click or a drag: the lid drags
        # its pupil and the open eye's tends to follow. The head goes on
        # steering; the pupils' share stays as it was before the lid dropped.
        winking = self.hybrid and (
            any(closed.values()) or any(w.down_at is not None for w in self._winks.values())
        )
        if not winking:
            self._fine_held = False
        if self.scrolling and not holding:
            # Frozen like for a wink, while the head (or the eyes) scroll.
            if self._frozen is None:
                self._frozen = self._rewound(self._history, t)
            self._settle_until = t + self.cfg.face_settle
            point = self._frozen
            events += self._scroll(feats, closed, openness, t)
        elif winking:
            self._scroll_last = None
            if not self._fine_held:
                self._fine_held = True
                self._fine = self._rewound(self._fine_history, t) or self._fine
            point = self._aim(feats, closed, openness, t, read_eyes=False)
            # Held there for `face_settle` once the eye reopens.
            self._frozen = point
            self._settle_until = t + self.cfg.face_settle
        elif waiting and not holding:
            self._scroll_last = None
            # Back to where the cursor was before the eyelid started dropping,
            # and kept there until it is open again.
            if self._frozen is None:
                self._frozen = self._rewound(self._history, t)
            self._settle_until = t + self.cfg.face_settle
            point = self._frozen
        elif t < self._settle_until:
            self._scroll_last = None
            point = self._frozen
        else:
            self._scroll_last = None
            self._frozen = None
            point = self._aim(feats, closed, openness, t)
            self._history.append((t, *point))
            while self._history and t - self._history[0][0] > self.cfg.face_rewind * 2:
                self._history.popleft()

        if point is not None and self.moves:
            events.append(GestureEvent("face_move", "cursor", point))

        return FaceRecognition(
            events=events,
            mode=self._mode(closed, holding),
            detail=self._detail(),
            ref_point=feats.nose_point,
            eyes=openness,
        )

    # --- calibration ---------------------------------------------------------
    def _calibrate(
        self, feats: FaceFeatures, closed: dict[str, bool], openness, t: float
    ) -> FaceRecognition:
        """Average the pose over a short while, eyes open, to centre on it."""
        if self._calib_from is None:
            self._calib_from = t
        if not any(closed.values()) and len(feats.gaze) == len(EYE_SIDES):
            self._calib_samples.append((feats.head, self._mean_gaze(feats, EYE_SIDES)))

        elapsed = t - self._calib_from
        progress = min(elapsed / max(self.cfg.face_calibration_seconds, 1e-6), 1.0)
        if progress >= 1.0 and len(self._calib_samples) >= 5:
            n = len(self._calib_samples)
            self.neutral = (
                (
                    sum(h[0] for h, _ in self._calib_samples) / n,
                    sum(h[1] for h, _ in self._calib_samples) / n,
                ),
                (
                    sum(g[0] for _, g in self._calib_samples) / n,
                    sum(g[1] for _, g in self._calib_samples) / n,
                ),
            )
            self._gaze0 = self.neutral[1]
            self._calib_samples = []
        return FaceRecognition(
            mode="CALIBRATING",
            detail="look at the centre of the screen",
            ref_point=feats.nose_point,
            eyes=openness,
            calibration=progress,
        )

    # --- eyelids -------------------------------------------------------------
    def _eyelids(self, closed: dict[str, bool], t: float) -> list[GestureEvent]:
        events: list[GestureEvent] = []
        both = all(closed.values())
        for side in EYE_SIDES:
            if not closed[side]:
                self._spoiled.discard(side)

        if both:
            # An ordinary blink, or the other eye giving in to a squint: never
            # a click. A drag already under way is not dropped, though - the
            # other eye blinking while you hold a wink is just a blink.
            for side, wink in self._winks.items():
                if not wink.holding:
                    events += wink.cancel()
                    self._spoiled.add(side)
            if self._both_since is None:
                self._both_since = t
            elif (
                self._blink_long
                and not self._blink_long_done
                and t - self._both_since >= self.cfg.blink_long_seconds
            ):
                self._blink_long_done = True
                events.append(GestureEvent("blink_long", "trigger"))
        else:
            self._both_since = None
            self._blink_long_done = False

        for side, wink in self._winks.items():
            if not wink.bound:
                continue
            if wink.holding:
                # Held: only reopening this very eye lets go of it.
                events += wink.update(closed[side], t)
                continue
            winking = (
                closed[side]
                and not closed[other_eye(side)]
                and side not in self._spoiled
            )
            events += wink.update(winking, t)
        return events

    @property
    def scrolling(self) -> bool:
        return bool(self._scrolls) and self._mouth_is_open

    def _mouth_gesture(self, feats: FaceFeatures, t: float) -> list[GestureEvent]:
        if not (self._mouth.bound or self._scrolls):
            return []
        if self._mouth_is_open:
            self._mouth_is_open = feats.mouth_open > self.cfg.mouth_open_off
        else:
            self._mouth_is_open = feats.mouth_open > self.cfg.mouth_open_on
        return self._mouth.update(self._mouth_is_open, t)

    # --- cursor --------------------------------------------------------------
    @staticmethod
    def _mean_gaze(feats: FaceFeatures, sides) -> tuple[float, float]:
        gx = sum(feats.gaze[s][0] for s in sides) / len(sides)
        gy = sum(feats.gaze[s][1] for s in sides) / len(sides)
        return gx, gy

    def _aim(
        self,
        feats: FaceFeatures,
        closed: dict[str, bool],
        openness: dict[str, float],
        t: float,
        read_eyes: bool = True,
    ) -> tuple[float, float]:
        """Where on the screen (0..1) the face is pointing right now, filtered.

        The head places the cursor, the pupils move it from there: by a small
        patch with the head on, by the whole screen on their own.
        """
        c = self.cfg
        x = y = 0.5
        if self.pointer == "head" or self.use_head:
            (hx0, hy0), _ = self.neutral
            u = (feats.head[0] - hx0) / max(c.head_range_x, 1e-6)
            v = (feats.head[1] - hy0) / max(c.head_range_y, 1e-6)
            x, y = self.cursor_filter(
                min(max(0.5 + 0.5 * u, 0.0), 1.0), min(max(0.5 + 0.5 * v, 0.0), 1.0), t
            )
        if self.pointer == "eyes":
            if read_eyes:
                self._fine_update(feats, closed, openness, t)
            x = min(max(x + self._fine[0], 0.0), 1.0)
            y = min(max(y + self._fine[1], 0.0), 1.0)
        return x, y

    def _gaze(
        self, feats: FaceFeatures, closed: dict[str, bool], openness: dict[str, float]
    ) -> tuple[float, float] | None:
        """The pupils, on a frame where they can be trusted; else None."""
        c = self.cfg
        # With the head on both eyes are wanted: one alone reads off to a side.
        # On their own a wink-drag has to steer with the one left open.
        sides = [s for s in EYE_SIDES if s in feats.gaze and not closed[s]]
        if not sides or (self.use_head and len(sides) < len(EYE_SIDES)):
            return None
        if any(openness[s] < c.eyes_min_open for s in sides):
            return None
        if len(sides) == len(EYE_SIDES):
            (ax, ay), (bx, by) = (feats.gaze[s] for s in sides)
            limit = c.eyes_max_disagree
            if abs(ax - bx) > limit * c.eyes_range_x or abs(ay - by) > limit * c.eyes_range_y:
                return None
        return self._mean_gaze(feats, sides)

    def _fine_update(
        self,
        feats: FaceFeatures,
        closed: dict[str, bool],
        openness: dict[str, float],
        t: float,
    ) -> None:
        """Move the pupils' share of the cursor; it stays put on a bad frame."""
        c = self.cfg
        gaze = self._gaze(feats, closed, openness)
        if gaze is None:
            self._fine_t = None
            return
        (hx0, hy0), _ = self.neutral
        if self.use_head:
            comp, gain = (c.eyes_head_comp_x, c.eyes_head_comp_y), c.eyes_fine_gain
            limit = (c.eyes_fine_x, c.eyes_fine_y)
        else:
            comp, gain, limit = (0.0, 0.0), 1.0, (0.5, 0.5)
        ex = gaze[0] - self._gaze0[0] - comp[0] * (feats.head[0] - hx0)
        ey = gaze[1] - self._gaze0[1] - comp[1] * (feats.head[1] - hy0)
        # The centre follows slowly, only under the head: on their own the
        # pupils are absolute, and staring at a corner must not bring it back.
        if self.use_head and c.eyes_recentre_seconds > 0.0 and self._fine_t is not None:
            k = min(max(t - self._fine_t, 0.0) / c.eyes_recentre_seconds, 1.0)
            self._gaze0 = (self._gaze0[0] + k * ex, self._gaze0[1] + k * ey)
        self._fine_t = t
        # Clamped before the filter, so it never has to unwind from out there.
        u = min(max(gain * 0.5 * ex / max(c.eyes_range_x, 1e-6), -limit[0]), limit[0])
        v = min(max(gain * 0.5 * ey / max(c.eyes_range_y, 1e-6), -limit[1]), limit[1])
        self._fine = self._fine_band(*self.fine_filter(u, v, t))
        self._fine_history.append((t, *self._fine))
        while self._fine_history and t - self._fine_history[0][0] > c.face_rewind * 2:
            self._fine_history.popleft()

    def _scroll(
        self,
        feats: FaceFeatures,
        closed: dict[str, bool],
        openness: dict[str, float],
        t: float,
    ) -> list[GestureEvent]:
        """The pointer's movement since last frame, as scroll: up/right positive."""
        # Through the cursor's filters, which keeps them warm for when the
        # mouth closes and the cursor picks up from the face's current aim.
        x, y = self._aim(feats, closed, openness, t)
        last, self._scroll_last = self._scroll_last, (x, y)
        if last is None:
            return []
        deltas = {
            "mouth_scroll_vertical": last[1] - y,
            "mouth_scroll_horizontal": x - last[0],
        }
        return [
            GestureEvent(name, "axis", deltas[name])
            for name in self._scrolls
            if abs(deltas[name]) >= self.cfg.axis_deadzone
        ]

    def _rewound(self, history, t: float) -> tuple[float, float] | None:
        """A point as it was `face_rewind` seconds ago (or as far back as known)."""
        if not history:
            return None
        before = [h for h in history if h[0] <= t - self.cfg.face_rewind]
        _, x, y = before[-1] if before else history[0]
        return x, y

    # --- HUD -----------------------------------------------------------------
    def _mode(self, closed: dict[str, bool], holding: bool) -> str:
        if holding:
            return "DRAG"
        if self.scrolling:
            return "SCROLL"
        if all(closed.values()):
            return "BLINK"
        for side in EYE_SIDES:
            if self._winks[side].pending:
                return f"WINK {side[0].upper()}"
        if self._mouth.pending:
            return "MOUTH OPEN"
        return self.label.upper()

    def _detail(self) -> str:
        for side, wink in self._winks.items():
            if wink.holding:
                return f"wink {side}: reopen to drop"
        if self._mouth.holding:
            return "close the mouth to drop"
        if self.scrolling:
            return "close the mouth to stop scrolling"
        if any(w.pending for w in self._winks.values()):
            return "reopen to click"
        return ""
