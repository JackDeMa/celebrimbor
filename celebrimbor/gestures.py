"""Gesture recognition: from HandFeatures to named events.

The recogniser never touches the mouse: it only produces events. Translating
them into actions is the dispatcher's job, driven by the configuration file.

Event kinds:
    cursor   normalised position (0..1) on the screen
    trigger  instantaneous event, fires exactly once
    hold     on/off state
    axis     continuous movement, carries a per-frame delta
"""

from collections import deque
from dataclasses import dataclass, field
from math import pi, radians

from .config import Config
from .filters import PointFilter
from .hand import PINCH_FINGERS, WRIST, HandFeatures, Hysteresis

# Gesture -> kind of event it produces. Also used to validate the JSON.
GESTURE_KINDS: dict[str, str] = {
    "point_move": "cursor",
    "pinch_index_tap": "trigger",
    "pinch_index_hold": "hold",
    "pinch_middle_tap": "trigger",
    "pinch_middle_hold": "hold",
    "pinch_ring_tap": "trigger",
    "pinch_ring_hold": "hold",
    "fist_swipe_up": "trigger",
    "fist_swipe_down": "trigger",
    "fist_swipe_left": "trigger",
    "fist_swipe_right": "trigger",
    "fist_hold": "trigger",
    "two_finger_vertical": "axis",
    "two_finger_horizontal": "axis",
    "two_finger_rotate_cw": "trigger",
    "two_finger_rotate_ccw": "trigger",
}


class ClosingDetector:
    """Tells whether the fingers are closing in, relative to their usual rest.

    An absolute threshold does not work: some people hold their hand wide open,
    others keep it curled, and with fingers already close at rest a fixed
    threshold would stay latched forever. Here the reference is the maximum
    observed over the last `window` seconds, i.e. the habitual opening at that
    moment. While a pinch is in progress the reference freezes, otherwise a long
    drag would eat it and the anchor would drop halfway through.
    """

    def __init__(self, window: float, ratio_on: float, ratio_off: float):
        self.window = window
        self.ratio_on = ratio_on
        self.ratio_off = ratio_off
        self.samples: deque[tuple[float, float]] = deque()
        self.baseline = 0.0
        self.closed = False

    def update(self, value: float, t: float, frozen: bool) -> bool:
        if not frozen:
            self.samples.append((t, value))
            while self.samples and t - self.samples[0][0] > self.window:
                self.samples.popleft()
            self.baseline = max(v for _, v in self.samples)

        ratio = value / max(self.baseline, 1e-6) if self.baseline else 1.0
        if self.closed:
            if ratio > self.ratio_off:
                self.closed = False
        elif ratio < self.ratio_on:
            self.closed = True
        return self.closed

    @property
    def threshold(self) -> float:
        """Distance below which anchoring kicks in, for the preview window."""
        return self.baseline * self.ratio_on

    def reset(self) -> None:
        self.samples.clear()
        self.baseline = 0.0
        self.closed = False


@dataclass
class GestureEvent:
    name: str
    kind: str
    value: object = None


@dataclass
class Recognition:
    """Outcome of one frame: events to run plus information for the HUD."""

    events: list[GestureEvent] = field(default_factory=list)
    mode: str = "NO HAND"
    detail: str = ""
    fist_hold_progress: float = 0.0  # 0..1, progress of the still fist
    cursor_source: str = ""          # "index" or "palm"
    ref_point: tuple[float, float] | None = None  # reference, in image coordinates
    anchor_at: dict[str, float] = field(default_factory=dict)  # anchoring thresholds


class GestureRecognizer:
    def __init__(self, cfg: Config, active: set[str] | None = None):
        self.cfg = cfg
        self.cursor_filter = PointFilter(cfg.min_cutoff, cfg.beta, cfg.d_cutoff)

        # Every pinch is measured, even the ones without a bound action: they
        # are still needed to disambiguate (touching the ring finger with the
        # thumb brings the middle finger close too, and without the comparison
        # the wrong click would fire). Events, however, are emitted only for
        # pinches that are actually bound to something.
        self.fingers = list(PINCH_FINGERS)
        self.bound = [
            f
            for f in self.fingers
            if active is None
            or f"pinch_{f}_tap" in active
            or f"pinch_{f}_hold" in active
        ]
        self._pinch = {f: Hysteresis(cfg.pinch_on, cfg.pinch_off) for f in self.fingers}
        self._down_at: dict[str, float | None] = {f: None for f in self.fingers}
        self._holding: dict[str, bool] = {f: False for f in self.fingers}

        # cursor anchoring
        self._closing = {
            f: ClosingDetector(cfg.anchor_window, cfg.anchor_ratio_on, cfg.anchor_ratio_off)
            for f in self.bound
        }
        self._was_anchored = False
        self._anchor_name = self._valid_anchor(cfg.anchor_point)
        self._offset = (0.0, 0.0)
        self._offset_from: float | None = None  # moment of the switch, for the blend
        self._offset_decays = False
        self._prev_ref: tuple[float, float] | None = None

        # fist
        self._fist_track: deque[tuple[float, float, float]] = deque()  # (t, x, y)
        self._fist_still_since: float | None = None
        self._fist_hold_done = False
        self._swipe_block_until = 0.0

        # two fingers
        self._two_origin: tuple[float, float] | None = None
        self._two_last: tuple[float, float] | None = None
        self._two_axis: str | None = None
        self._rot_track: deque[tuple[float, float]] = deque()  # (t, turn so far)
        self._rot_total = 0.0
        self._rot_prev: float | None = None
        self._rot_block_until = 0.0

        self._missing = 0

    # ------------------------------------------------------------------
    def reset(self) -> list[GestureEvent]:
        """Bring everything back to rest, returning the closing events."""
        events = self._release_pinches()
        self.cursor_filter.reset()
        self._fist_track.clear()
        self._fist_still_since = None
        self._fist_hold_done = False
        self._end_two_finger()
        for detector in self._closing.values():
            detector.reset()
        self._was_anchored = False
        self._offset = (0.0, 0.0)
        self._offset_from = None
        self._prev_ref = None
        return events

    # ------------------------------------------------------------------
    def update(self, feats: HandFeatures | None, t: float) -> Recognition:
        if feats is None:
            self._missing += 1
            if self._missing == self.cfg.grace_frames:
                return Recognition(events=self.reset(), mode="NO HAND")
            return Recognition(mode="NO HAND")
        self._missing = 0

        # --- fist: directional swipes and still fist ----------------------
        if feats.is_fist:
            self._end_two_finger()
            self._forget_reference()
            return self._fist(feats, t)
        self._reset_fist()

        # --- two fingers: continuous axes -----------------------------------
        if self._is_two_finger(feats):
            self._forget_reference()
            events = self._release_pinches()
            events += self._two_finger(feats, t)
            return Recognition(
                events=events,
                mode="TWO FINGERS",
                detail=self._two_axis or "slide or turn the hand",
            )
        self._end_two_finger()

        # --- pinch: clicks and drag -----------------------------------------
        events = self._pinches(feats, t)

        ref, source = self._reference(feats, t)
        x, y = self.cursor_filter(ref[0], ref[1], t)
        events.append(GestureEvent("point_move", "cursor", self._to_screen(x, y)))

        holding = [f for f in self.bound if self._holding[f]]
        closed = [f for f in self.bound if self._pinch[f].state]
        if holding:
            mode, detail = "DRAG", holding[0]
        elif closed:
            mode, detail = "PINCH", f"{closed[0]}: release to click"
        else:
            mode, detail = "POINTING", ""
        return Recognition(
            events=events,
            mode=mode,
            detail=detail,
            cursor_source=source,
            ref_point=ref,
            anchor_at={f: d.threshold for f, d in self._closing.items()},
        )

    # --- cursor reference ---------------------------------------------------
    def _valid_anchor(self, name: str) -> str:
        valid = ("palm_outer", "palm_center", "pinky_mcp", "index_mcp", "wrist")
        if name in valid:
            return name
        print(
            f"Warning: unknown anchor_point {name!r} "
            f"(valid: {', '.join(valid)}), falling back to 'palm_outer'."
        )
        return "palm_outer"

    def _reference(self, feats: HandFeatures, t: float) -> tuple[tuple[float, float], str]:
        """Point driving the cursor, with a seamless index <-> palm switch.

        Closing the fingers for a click always shifts the index fingertip a
        little: as soon as the pinch starts tightening we switch to a point on
        the palm, which instead stays put. So the cursor does not jump at the
        moment of the switch, the offset between the two points is frozen and
        added to the new reference; on the way back to the index finger the
        offset is blended away over `anchor_blend` seconds.
        """
        # It is enough for any of the pinches in use to be tightening.
        anchored = False
        for finger, detector in self._closing.items():
            if detector.update(feats.pinches[finger], t, frozen=self._pinch[finger].state):
                anchored = True
        was_anchored = self._was_anchored
        self._was_anchored = anchored
        raw = feats.anchor(self._anchor_name) if anchored else feats.index_point

        if self._prev_ref is None:
            self._offset = (0.0, 0.0)
            self._offset_from = None
        elif anchored != was_anchored:
            self._offset = (
                self._prev_ref[0] - raw[0],
                self._prev_ref[1] - raw[1],
            )
            self._offset_decays = not anchored  # back on the index, the offset fades
            self._offset_from = t

        weight = 1.0
        if self._offset_decays and self._offset_from is not None:
            elapsed = t - self._offset_from
            weight = max(0.0, 1.0 - elapsed / max(self.cfg.anchor_blend, 1e-6))

        point = (raw[0] + self._offset[0] * weight, raw[1] + self._offset[1] * weight)
        self._prev_ref = point
        return point, ("palm" if anchored else "index")

    def _forget_reference(self) -> None:
        """The cursor is not in use: next time we start from scratch.

        The habitual finger opening is not forgotten: it is a fact about the
        hand, not about the gesture in progress, and old samples expire on their
        own.
        """
        self._prev_ref = None
        self._offset = (0.0, 0.0)
        self._offset_from = None
        for detector in self._closing.values():
            detector.closed = False
        self._was_anchored = False

    # --- fist ---------------------------------------------------------------
    def _fist(self, feats: HandFeatures, t: float) -> Recognition:
        events = self._release_pinches()
        self.cursor_filter.reset()

        cfg = self.cfg
        x, y = feats.palm_point
        # Distances are in "hands": that way the threshold does not depend on
        # how far you are from the webcam.
        x, y = x / feats.scale, y / feats.scale

        self._fist_track.append((t, x, y))
        while self._fist_track and t - self._fist_track[0][0] > cfg.swipe_window:
            self._fist_track.popleft()

        travel_x = x - self._fist_track[0][1]
        travel_y = y - self._fist_track[0][2]

        # still or moving?
        moving = max(abs(travel_x), abs(travel_y)) > cfg.fist_still_travel
        if moving:
            self._fist_still_since = None
            self._fist_hold_done = False
        elif self._fist_still_since is None:
            self._fist_still_since = t

        # --- swipe -------------------------------------------------------
        if t >= self._swipe_block_until and len(self._fist_track) >= 3:
            name = None
            if abs(travel_x) > abs(travel_y) and abs(travel_x) > cfg.swipe_min_travel:
                name = "fist_swipe_right" if travel_x > 0 else "fist_swipe_left"
            elif abs(travel_y) > cfg.swipe_min_travel:
                name = "fist_swipe_down" if travel_y > 0 else "fist_swipe_up"
            if name:
                events.append(GestureEvent(name, "trigger"))
                self._fist_track.clear()
                self._swipe_block_until = t + cfg.swipe_cooldown
                self._fist_still_since = None
                return Recognition(events=events, mode="FIST", detail=name)

        # --- still fist ----------------------------------------------------
        progress = 0.0
        detail = "swipe or hold still"
        if self._fist_still_since is not None:
            held = t - self._fist_still_since
            progress = min(held / cfg.fist_hold_seconds, 1.0)
            if not self._fist_hold_done and held >= cfg.fist_hold_seconds:
                self._fist_hold_done = True
                events.append(GestureEvent("fist_hold", "trigger"))
                detail = "fist_hold"
            elif not self._fist_hold_done:
                detail = f"still {held:.1f}/{cfg.fist_hold_seconds:.0f}s"
            else:
                detail = "already fired"

        return Recognition(
            events=events, mode="FIST", detail=detail, fist_hold_progress=progress
        )

    def _reset_fist(self) -> None:
        self._fist_track.clear()
        self._fist_still_since = None
        self._fist_hold_done = False

    # --- two fingers --------------------------------------------------------
    def _is_two_finger(self, feats: HandFeatures) -> bool:
        return (
            feats.index_extended
            and feats.middle_extended
            and not feats.ring_extended
            and not feats.pinky_extended
        )

    def _two_finger(self, feats: HandFeatures, t: float) -> list[GestureEvent]:
        point = feats.palm_point
        wrist = feats.points[WRIST]
        turn = self._track_rotation(feats.pointing_angle, t)
        if self._two_last is None:
            self._two_origin = wrist
            self._two_last = point
            self._two_axis = None
            return []

        if self._two_axis is None:
            # The axis is chosen once only, on the first decisive movement:
            # without this lock a diagonal movement would fire scroll and
            # volume at the same time, and turning the hand would drag them
            # along as well.
            #
            # The travel is measured at the wrist and not at the palm: the wrist
            # stays put while the hand only turns, so a rotation does not eat
            # the lock before it has been recognised.
            dx = wrist[0] - self._two_origin[0]
            dy = wrist[1] - self._two_origin[1]
            if abs(turn) >= radians(self.cfg.rotate_min_angle):
                self._two_axis = "rotation"
            elif max(abs(dx), abs(dy)) >= self.cfg.axis_lock_travel:
                self._two_axis = "horizontal" if abs(dx) > abs(dy) else "vertical"
            else:
                self._two_last = point
                return []

        if self._two_axis == "rotation":
            return self._rotation(turn, t)

        events = []
        if self._two_axis == "vertical":
            delta = self._two_last[1] - point[1]  # hand up -> positive value
            name = "two_finger_vertical"
        else:
            delta = point[0] - self._two_last[0]  # hand right -> positive
            name = "two_finger_horizontal"
        self._two_last = point

        if abs(delta) >= self.cfg.axis_deadzone:
            events.append(GestureEvent(name, "axis", delta))
        return events

    def _track_rotation(self, angle: float, t: float) -> float:
        """Signed turn accumulated over the last `rotate_window` seconds.

        atan2 jumps by a whole turn when the fingers cross the far side, so the
        per-frame differences are unwrapped before being summed. Only the window
        is kept: over a longer span the slow drift of a hand held up would add
        up to a rotation nobody made.
        """
        if self._rot_prev is not None:
            step = angle - self._rot_prev
            self._rot_total += (step + pi) % (2 * pi) - pi  # shortest way round
        self._rot_prev = angle

        self._rot_track.append((t, self._rot_total))
        while (
            len(self._rot_track) > 1
            and t - self._rot_track[0][0] > self.cfg.rotate_window
        ):
            self._rot_track.popleft()
        return self._rot_total - self._rot_track[0][1]

    def _rotation(self, turn: float, t: float) -> list[GestureEvent]:
        """One event per `rotate_min_angle` of turn, keeping on while turning."""
        if t < self._rot_block_until or abs(turn) < radians(self.cfg.rotate_min_angle):
            return []
        # y grows downwards and the image is mirrored, so a growing angle is the
        # clockwise turn the user sees in front of them.
        name = "two_finger_rotate_cw" if turn > 0 else "two_finger_rotate_ccw"
        self._rot_track.clear()
        self._rot_track.append((t, self._rot_total))
        self._rot_block_until = t + self.cfg.rotate_cooldown
        return [GestureEvent(name, "trigger")]

    def _end_two_finger(self) -> None:
        self._two_origin = None
        self._two_last = None
        self._two_axis = None
        self._rot_track.clear()
        self._rot_total = 0.0
        self._rot_prev = None

    # --- pinch --------------------------------------------------------------
    def _pinches(self, feats: HandFeatures, t: float) -> list[GestureEvent]:
        events: list[GestureEvent] = []
        closed = {f: self._pinch[f].update(feats.pinches[f]) for f in self.fingers}

        # With the fingers curled the tips end up close to each other: if more
        # than one pinch reads as closed, the actually tightest one wins, so
        # clicks never get mixed up.
        active = [f for f, c in closed.items() if c]
        if len(active) > 1:
            winner = min(active, key=lambda f: feats.pinches[f])
            closed = {f: (f == winner) for f in closed}

        for finger, is_closed in closed.items():
            if finger not in self.bound:
                continue
            if is_closed:
                if self._down_at[finger] is None:
                    self._down_at[finger] = t
                elif (
                    t - self._down_at[finger] >= self.cfg.drag_hold
                    and not self._holding[finger]
                ):
                    self._holding[finger] = True
                    events.append(GestureEvent(f"pinch_{finger}_hold", "hold", True))
            elif self._down_at[finger] is not None:
                held = t - self._down_at[finger]
                if self._holding[finger]:
                    self._holding[finger] = False
                    events.append(GestureEvent(f"pinch_{finger}_hold", "hold", False))
                elif held < self.cfg.drag_hold:
                    events.append(GestureEvent(f"pinch_{finger}_tap", "trigger"))
                self._down_at[finger] = None

        return events

    def _release_pinches(self) -> list[GestureEvent]:
        """Close the pending pinches without generating accidental clicks."""
        events: list[GestureEvent] = []
        for finger in self.fingers:
            if self._holding[finger]:
                self._holding[finger] = False
                events.append(GestureEvent(f"pinch_{finger}_hold", "hold", False))
            self._down_at[finger] = None
            self._pinch[finger].reset()
        return events

    # --- mapping onto the screen ---------------------------------------------
    def _to_screen(self, x: float, y: float) -> tuple[float, float]:
        c = self.cfg
        u = (x - c.active_x_min) / max(c.active_x_max - c.active_x_min, 1e-6)
        v = (y - c.active_y_min) / max(c.active_y_max - c.active_y_min, 1e-6)
        return min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)
