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
from math import atan2, hypot, pi, radians, sqrt

from .config import Config
from .filters import PointFilter
from .hand import (
    FINGER_TIPS,
    PINCH_FINGERS,
    WRIST,
    HandFeatures,
    Hysteresis,
)

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
    "two_finger_circle_cw": "trigger",
    "two_finger_circle_ccw": "trigger",
    "three_finger_vertical": "axis",
    "three_finger_horizontal": "axis",
    "three_finger_circle_cw": "trigger",
    "three_finger_circle_ccw": "trigger",
}

# The hand shapes that draw slides and circles: index and middle, or those two
# with the ring finger added. One more finger held out is a different gesture
# with the same shape to it, so the two are the same machinery with a different
# pose and a different set of names.
#   (prefix of the events, label for the HUD, fingers out, fingers in)
POSES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("two_finger", "TWO FINGERS", ("index", "middle"), ("ring", "pinky")),
    ("three_finger", "THREE FINGERS", ("index", "middle", "ring"), ("pinky",)),
)

# What each pose can produce, appended to its prefix.
POSE_EVENTS = ("vertical", "horizontal", "circle_cw", "circle_ccw")


Point = tuple[float, float]


def _dist(a: Point, b: Point) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


def _fit_circle(pts: list[Point]) -> tuple[float, float, float] | None:
    """Circle closest to the given points: (centre x, centre y, radius).

    Kasa's algebraic fit: rewriting the circle as x^2+y^2 = Ax+By+C makes it
    linear in the unknowns, so the answer comes out of a single 3x3 system with
    no iteration - cheap enough to run on every frame.

    On a straight run the system is degenerate, a line being a circle of
    infinite radius, and there is nothing sensible to return: that is the
    `None`, and it is precisely the case we want to reject.
    """
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n

    # Centred on the average point: the system is far better conditioned there.
    suu = svv = suv = suuu = svvv = suvv = svuu = 0.0
    for px, py in pts:
        u = px - mx
        v = py - my
        suu += u * u
        svv += v * v
        suv += u * v
        suuu += u * u * u
        svvv += v * v * v
        suvv += u * v * v
        svuu += v * u * u

    det = 2.0 * (suu * svv - suv * suv)
    if abs(det) < 1e-12:
        return None
    uc = (svv * (suuu + suvv) - suv * (svvv + svuu)) / det
    vc = (suu * (svvv + svuu) - suv * (suuu + suvv)) / det
    return mx + uc, my + vc, sqrt(uc * uc + vc * vc + (suu + svv) / n)


def _span(pts: list[Point], centre: Point) -> float:
    """Arc swept around `centre` along the path, signed, in radians.

    The steps are unwrapped before being summed, so crossing the far side is
    worth nothing, and going back the way you came takes the arc back down.
    """
    total, prev = 0.0, None
    for p in pts:
        phase = atan2(p[1] - centre[1], p[0] - centre[0])
        if prev is not None:
            total += (phase - prev + pi) % (2 * pi) - pi
        prev = phase
    return total


class CircleDetector:
    """Fingertips travelling around a circle, the fingers keeping their aim.

    Not to be confused with turning the hand like a key: there the fingers
    change direction and swing around a wrist that stays put. Here the hand
    travels and the two fingers go on pointing the same way - it is the *path*
    that closes into a circle, which is why the aim is watched and required to
    stay still.

    The centre is fitted to the recent path on every frame instead of being
    taken as its average: while the circle is still being drawn the samples all
    sit on one arc, and their average lies on the arc itself, nowhere near the
    centre. The fit recovers the real centre from a fraction of a turn.

    What actually decides is not how neatly the path fits a circle - a hand
    sliding sideways pivots on the elbow and draws a very clean arc - but *how
    far round it has come*. Half a turn of a circle small enough to be one is a
    hand's worth of travel; half a turn of the elbow's arc would take the hand
    out of the frame.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.samples: deque[tuple[float, float, float, float]] = deque()  # t, x, y, aim
        self.ok = False           # the path under observation is a circle
        self.radius = 0.0
        self.steps = 0            # steps of `circle_step_angle` since it started
        self._arc = 0.0           # arc since the last step, signed
        self._dir = 0
        self._phase: float | None = None
        self._aim_raw: float | None = None
        self._aim = 0.0           # unwrapped, so the +-pi crossing does not count
        self._block_until = 0.0

    def reset(self) -> None:
        self.samples.clear()
        self._aim_raw = None
        self._aim = 0.0
        self._break()

    def _break(self) -> None:
        """The path stopped being a circle: whatever was drawn does not count."""
        self.ok = False
        self.radius = 0.0
        self.steps = 0
        self._arc = 0.0
        self._dir = 0
        self._phase = None

    # ------------------------------------------------------------------
    def update(self, point: Point, aim: float, t: float) -> int:
        """One frame in: +1 for a clockwise step, -1 anticlockwise, 0 for none.

        Zero is also what comes back during the arming steps: they are drawn
        like any other, they simply do not fire.
        """
        if self._aim_raw is not None:
            self._aim += (aim - self._aim_raw + pi) % (2 * pi) - pi
        self._aim_raw = aim
        self.samples.append((t, point[0], point[1], self._aim))
        while len(self.samples) > 2 and t - self.samples[0][0] > self.cfg.circle_window:
            self.samples.popleft()

        cfg = self.cfg
        if len(self.samples) < cfg.circle_min_samples:
            return 0

        pts = [(x, y) for _, x, y, _ in self.samples]
        fit = _fit_circle(pts)
        if fit is None:
            self._break()
            return 0
        cx, cy, self.radius = fit
        span = _span(pts, (cx, cy))
        if not self._circular(pts, (cx, cy), span):
            self._break()
            return 0

        step = radians(cfg.circle_step_angle)
        phase = atan2(point[1] - cy, point[0] - cx)
        if self._phase is None:
            # Just recognised. The arc already drawn counts towards the arming,
            # capped so that engaging can never fire an event by itself: one
            # more step is always to be drawn, and it lands within a quarter
            # turn of here.
            self._dir = 1 if span > 0 else -1
            self.steps = min(int(abs(span) / step), cfg.circle_arm_steps)
        else:
            # Shortest way round: a whole turn of jump is the far side being
            # crossed, not the hand teleporting.
            self._arc += (phase - self._phase + pi) % (2 * pi) - pi
        self._phase = phase

        if abs(self._arc) < step or t < self._block_until:
            return 0

        # y grows downwards and the image is mirrored, so a growing angle is the
        # clockwise circle the user sees themselves drawing.
        direction = 1 if self._arc > 0 else -1
        self._arc -= direction * step
        if direction != self._dir:
            self.steps = 0  # going round the other way restarts the arming
        self._dir = direction
        self.steps += 1
        self._block_until = t + cfg.circle_cooldown
        return direction if self.steps > cfg.circle_arm_steps else 0

    def _circular(self, pts: list[Point], centre: Point, span: float) -> bool:
        cfg = self.cfg
        r = self.radius
        error = sqrt(sum((_dist(p, centre) - r) ** 2 for p in pts) / len(pts))
        aims = [a for _, _, _, a in self.samples]
        self.ok = (
            cfg.circle_min_radius <= r <= cfg.circle_max_radius
            and error <= r * cfg.circle_tolerance
            and abs(span) >= radians(cfg.circle_min_span)
            and max(aims) - min(aims) <= radians(cfg.circle_aim_drift)
        )
        return self.ok

    @property
    def to_engage(self) -> int:
        """Steps still to be drawn before the first event, for the HUD."""
        return max(self.cfg.circle_arm_steps + 1 - self.steps, 0)


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


class FingerPose:
    """Slides along one axis and circles in the air, drawn with fingers held out.

    Index and middle is one pose, the same two plus the ring finger another:
    the gesture is the same, only the shape of the hand and the names of the
    events change, so both are this one class. What it does keep apart is the
    state: putting a third finger out mid-air ends one gesture and begins
    another, and a half-drawn circle must not carry over into it.
    """

    def __init__(
        self,
        cfg: Config,
        prefix: str,
        label: str,
        extended: tuple[str, ...],
        curled: tuple[str, ...],
    ):
        self.cfg = cfg
        self.prefix = prefix
        self.mode = label
        self.extended = extended
        self.curled = curled
        # The path is drawn with the tips of the fingers that are out, so a
        # third finger joining in does not shift the circle off centre.
        self.tips = tuple(FINGER_TIPS[f] for f in extended)

        self._origin: tuple[float, float] | None = None
        self._last: tuple[float, float] | None = None
        self._axis: str | None = None
        self._missing = 0
        self._held = 0.0
        self._until = 0.0
        self._circle = CircleDetector(cfg)

    # ------------------------------------------------------------------
    def matches(self, feats: HandFeatures) -> bool:
        out = feats.extended
        return all(out[f] for f in self.extended) and not any(
            out[f] for f in self.curled
        )

    def in_grace(self) -> bool:
        """Is a broken pose still worth waiting on?

        A curled finger sits right on the threshold that says whether it is
        extended, so it flickers; without this the gesture would end on a single
        bad frame and take the half-drawn circle with it.
        """
        return self._last is not None and self._missing < self.cfg.two_finger_grace

    def end(self) -> None:
        self._origin = None
        self._last = None
        self._axis = None
        self._missing = 0
        self._held = 0.0
        self._until = 0.0
        self._circle.reset()

    @property
    def detail(self) -> str:
        if self._axis != "circle":
            return self._axis or "slide or draw a circle"
        left = self._circle.to_engage
        return f"circle: {left} more to engage" if left else "circle"

    # ------------------------------------------------------------------
    def update(self, feats: HandFeatures, t: float, posed: bool) -> list[GestureEvent]:
        """One frame of the pose. Nothing is measured during the grace frames:
        the pose is in doubt there, and a gap in the path costs far less than a
        stray sample would."""
        if not posed:
            self._missing += 1
            return []
        self._missing = 0

        point = feats.palm_point
        wrist = feats.points[WRIST]
        # The circle is watched on the fingertips, in "hands": that way it does
        # not have to be drawn any wider when you sit further from the webcam.
        cx, cy = feats.tips_center(self.tips)
        turn = self._circle.update(
            (cx / feats.scale, cy / feats.scale), feats.pointing_angle(self.tips), t
        )

        if self._last is None:
            self._origin = wrist
            self._last = point
            self._axis = None
            return []

        # A circle only shows itself once the path has come round far enough,
        # and by then the slide has usually taken the lock: so it is taken back
        # off it, and everything the slide was holding never happened.
        if self._circle.ok and self._axis != "circle":
            self._axis = "circle"
            self._held = 0.0

        if self._axis is None:
            # The axis is chosen once only, on the first decisive movement:
            # without this lock a diagonal movement would fire scroll and
            # volume at the same time. The travel is measured at the wrist,
            # which is steadier than the fingertips.
            dx = wrist[0] - self._origin[0]
            dy = wrist[1] - self._origin[1]
            if max(abs(dx), abs(dy)) < self.cfg.axis_lock_travel:
                self._last = point
                return []
            self._axis = "horizontal" if abs(dx) > abs(dy) else "vertical"
            self._until = t + self.cfg.axis_lock_hold

        if self._axis == "circle":
            if not turn:
                return []
            suffix = "circle_cw" if turn > 0 else "circle_ccw"
            return [GestureEvent(f"{self.prefix}_{suffix}", "trigger")]

        if self._axis == "vertical":
            delta = self._last[1] - point[1]  # hand up -> positive value
        else:
            delta = point[0] - self._last[0]  # hand right -> positive
        self._last = point

        # Held back until the circle has had its chance to speak up, then
        # released in one go: the movement is delayed, never thrown away.
        self._held += delta
        if t < self._until:
            return []
        delta, self._held = self._held, 0.0

        if abs(delta) < self.cfg.axis_deadzone:
            return []
        return [GestureEvent(f"{self.prefix}_{self._axis}", "axis", delta)]


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

        # fingers held out: slides and circles. A pose nobody has bound anything
        # to is not built at all - it would otherwise swallow the frames in
        # which the hand happens to be in it, and with them the cursor.
        self._poses = [
            FingerPose(cfg, prefix, label, extended, curled)
            for prefix, label, extended, curled in POSES
            if active is None
            or any(f"{prefix}_{event}" in active for event in POSE_EVENTS)
        ]

        self._missing = 0

    # ------------------------------------------------------------------
    def reset(self) -> list[GestureEvent]:
        """Bring everything back to rest, returning the closing events."""
        events = self._release_pinches()
        self.cursor_filter.reset()
        self._fist_track.clear()
        self._fist_still_since = None
        self._fist_hold_done = False
        self._end_poses()
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
            self._end_poses()
            self._forget_reference()
            return self._fist(feats, t)
        self._reset_fist()

        # --- fingers held out: sliding axes and circles ---------------------
        pose, posed = self._pose_in_use(feats)
        if pose is not None:
            self._forget_reference()
            events = self._release_pinches()
            events += pose.update(feats, t, posed)
            return Recognition(events=events, mode=pose.mode, detail=pose.detail)

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
        # It is enough for any of the pinches in use to be tightening, unless
        # the palm holds the cursor at all times and there is nothing to detect.
        anchored = self.cfg.anchor_always
        if not anchored:
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

    # --- fingers held out ---------------------------------------------------
    def _pose_in_use(self, feats: HandFeatures) -> tuple["FingerPose | None", bool]:
        """The pose the hand is in, and whether it is being held right now.

        The shape actually on show wins: a pose still inside its grace frames
        only gets the hand back if no other pose claims it, or lifting the ring
        finger to go from two fingers to three would spend the whole gesture
        waiting for a pose that has already been left.

        Every other pose is ended here, so a gesture never resumes where it was
        interrupted: it starts again from the first frame of its own shape.
        """
        posed = True
        claimed = next((p for p in self._poses if p.matches(feats)), None)
        if claimed is None:
            posed = False
            claimed = next((p for p in self._poses if p.in_grace()), None)

        for pose in self._poses:
            if pose is not claimed:
                pose.end()
        return claimed, posed

    def _end_poses(self) -> None:
        for pose in self._poses:
            pose.end()

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
