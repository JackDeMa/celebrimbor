"""Main loop: webcam capture -> landmarks (hands, face) -> gestures -> mouse."""

import sys
import time

import cv2
from pynput import keyboard

from . import bindings as bindings_mod
from . import face, hand, overlay
from .config import Config
from .controller import MouseActuator
from .detector import (
    DEFAULT_FACE_MODEL_PATH,
    FACE_CONNECTIONS,
    FACE_MODEL_URL,
    HAND_CONNECTIONS,
    FaceDetector,
    HandDetector,
    ensure_model,
)
from .engine import FACE_SLOT, EngineState, GestureEngine

# The values Steady (1 / cutoff) and Speed (beta) step through: round numbers,
# finer where a small change is felt, coarser where it is not.
_STEADY_STEPS = (0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12, 15, 20)
_SPEED_STEPS = (0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 15, 20)


def _ladder_step(ladder: tuple, value: float, direction: int) -> float:
    """The next round value above (+1) or below (-1) `value`; off the ladder,
    the nearest one in that direction. Stays put at either end."""
    if direction > 0:
        return next((v for v in ladder if v > value + 1e-9), ladder[-1])
    if direction < 0:
        return next((v for v in reversed(ladder) if v < value - 1e-9), ladder[0])
    return value


# The Config fields of each pointer's filter: (cutoff, beta).
_FILTER_KEYS = {
    "hand": ("min_cutoff", "beta"),
    "head": ("head_min_cutoff", "face_beta"),
    "eyes": ("eyes_min_cutoff", "face_beta"),
}

_GREEN = (120, 230, 120)
_GREY = (170, 170, 170)
_RED = (80, 80, 240)
_YELLOW = (60, 220, 240)
_WHITE = (245, 245, 245)


# macOS virtual key codes. Option turns letters into other characters (Option+Q
# types "œ"), so pynput's GlobalHotKeys, which matches characters, never fires
# there: the shortcuts are matched on the physical key instead.
_MAC_VK = {"q": 12, "p": 35, "c": 8, "<space>": 49}
_MAC_MODS = {
    keyboard.Key.ctrl: "ctrl",
    keyboard.Key.ctrl_l: "ctrl",
    keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.alt: "alt",
    keyboard.Key.alt_l: "alt",
    keyboard.Key.alt_r: "alt",
}


def _mac_hotkeys(actions: dict) -> keyboard.Listener:
    by_vk = {_MAC_VK[key]: action for key, action in actions.items()}
    held: set[str] = set()

    def vk(key) -> int | None:
        # A letter arrives as a KeyCode, Space as a Key whose value holds it.
        return getattr(key, "vk", None) or getattr(getattr(key, "value", None), "vk", None)

    def press(key) -> None:
        if key in _MAC_MODS:
            held.add(_MAC_MODS[key])
        elif held == {"ctrl", "alt"} and vk(key) in by_vk:
            by_vk[vk(key)]()

    def release(key) -> None:
        held.discard(_MAC_MODS.get(key))

    return keyboard.Listener(on_press=press, on_release=release)


class CelebrimborApp:
    def __init__(
        self,
        cfg: Config,
        bindings: dict[str, dict] | None = None,
        config_path: str | None = None,
    ):
        self.cfg = cfg
        self.config_path = config_path
        self.mouse = MouseActuator(
            click_cooldown=cfg.click_cooldown, dry_run=cfg.dry_run
        )
        self.engine = GestureEngine(cfg, self.mouse, bindings)
        self.running = True
        self.show_skeleton = True
        self.overlay: overlay.Overlay | None = None
        self.window = None
        self._fps = 0.0
        self._events: list[tuple[float, str]] = []
        self._last_mode = ""
        # both fists closed: pause / resume
        self._fists_since: float | None = None
        self._fists_done = False
        self._pause_progress = 0.0
        # the real mouse moving: an automatic pause, lifted when it rests
        self._mouse_paused = False
        self._mouse_moved_at = 0.0
        self._frame_no = 0
        self._hands: list = []
        # what Reset goes back to: every filter's values at launch
        self._tuning_start = {
            k: getattr(cfg, k)
            for keys in (*_FILTER_KEYS.values(), ("cursor_deadband",))
            for k in keys
        }

    # --- global hotkeys ----------------------------------------------------
    def _make_hotkeys(self) -> keyboard.Listener:
        """Ctrl+Alt+key, global: Ctrl+Option+key on a Mac."""
        actions = {
            "q": self.stop,
            "p": self.toggle_pause,
            "<space>": self.swap_dominant,
            "c": self.recalibrate,
        }
        if sys.platform == "darwin":
            return _mac_hotkeys(actions)
        return keyboard.GlobalHotKeys({f"<ctrl>+<alt>+{k}": f for k, f in actions.items()})

    def stop(self) -> None:
        self.running = False

    def toggle_pause(self) -> None:
        if self._mouse_paused:
            # Paused by the real mouse: pausing by hand makes it stick.
            self._mouse_paused = False
            self.engine.set_enabled(False)
            return
        self.engine.set_enabled(not self.engine.enabled)

    def _watch_mouse(self, now: float) -> None:
        """The real mouse moving pauses; it resumes once the mouse rests."""
        if self.cfg.mouse_pause_seconds <= 0:
            return
        if self.mouse.user_moved():
            self._mouse_moved_at = now
            if self.engine.enabled:
                self._mouse_paused = True
                self.engine.set_enabled(False, "mouse in use, resumes when still")
                self._events.append((now, "PAUSED (mouse)"))
        elif self._mouse_paused and now - self._mouse_moved_at >= self.cfg.mouse_pause_seconds:
            self._mouse_paused = False
            self.engine.set_enabled(True)
            self._events.append((now, "RESUMED"))

    def toggle_skeleton(self) -> None:
        self.show_skeleton = not self.show_skeleton

    def swap_dominant(self) -> None:
        hand = self.engine.swap_dominant()
        # The hotkey fires on the listener thread, so the notice is timestamped
        # here rather than by the loop.
        self._events.append((time.monotonic(), f"{hand[0].upper()} POINTER"))
        print(f"Pointer to the {hand} hand.", flush=True)

    def recalibrate(self) -> None:
        if self.engine.recalibrate():
            self._events.append((time.monotonic(), "F RECALIBRATE"))
            print("Recalibrating: look at the centre of the screen.", flush=True)

    def _fists_pause(self, feats: dict, now: float) -> bool:
        """Both fists closed for `pause_fists_seconds`: pause or resume.

        Returns whether both fists are closed right now.
        """
        if len(feats) < 2 or not all(f.is_fist for f in feats.values()):
            self._fists_since = None
            self._fists_done = False
            self._pause_progress = 0.0
            return False
        if self._fists_since is None:
            self._fists_since = now
        held = now - self._fists_since
        if not self._fists_done and held >= self.cfg.pause_fists_seconds:
            # Once per hold: keep the fists closed and it does not flip back.
            self._fists_done = True
            self.toggle_pause()
            label = "RESUMED" if self.engine.enabled else "PAUSED"
            self._events.append((now, label))
            print(f"{label} (both fists).", flush=True)
        self._pause_progress = (
            0.0 if self._fists_done else held / max(self.cfg.pause_fists_seconds, 1e-6)
        )
        return True

    def toggle_overlay(self) -> None:
        if self.overlay is not None:
            self.overlay.set_visible(not self.overlay.visible)

    # --- inputs and tuning (the window's switches and buttons) ----------------
    def filter_keys(self) -> tuple[str, str]:
        """The Config fields of the filter being tuned: (cutoff, beta).

        The face's when it is on (it is the one that jitters), else the hand's.
        """
        pointer = self.engine.face.pointer if self.engine.face is not None else "hand"
        return _FILTER_KEYS[pointer]

    def toggle_input(self, part: str) -> None:
        """Hand / Head / Eyes switch: that input on or off, at once."""
        inputs = {"hand": self.cfg.use_hand, "head": self.cfg.use_head, "eyes": self.cfg.use_eyes}
        inputs[part] = not inputs[part]
        self.engine.set_inputs(**inputs)
        state = "ON" if inputs[part] else "OFF"
        self._events.append((time.monotonic(), f"{part.upper()} {state}"))
        print(f"{part} {state}.", flush=True)

    def steadiness(self) -> float:
        """What Steady shows: 1 / cutoff, so it grows as the cursor calms down."""
        return round(1.0 / getattr(self.cfg, self.filter_keys()[0]), 2)

    def tune(self, steady: int = 0, speed: int = 0) -> None:
        """One step up (+1) or down (-1) the Steady or Speed ladder, applied at once."""
        _, bk = self.filter_keys()
        cutoff = 1.0 / _ladder_step(_STEADY_STEPS, self.steadiness(), steady)
        beta = _ladder_step(_SPEED_STEPS, getattr(self.cfg, bk), speed)
        self._set_filter(cutoff, beta)

    def set_filter(self, steady: float | None = None, speed: float | None = None) -> None:
        """Steady and / or Speed typed in by hand: any value, not only the ladder's."""
        ck, bk = self.filter_keys()
        cutoff = getattr(self.cfg, ck) if steady is None else 1.0 / min(max(steady, 0.1), 100.0)
        beta = getattr(self.cfg, bk) if speed is None else min(max(speed, 0.0), 100.0)
        self._set_filter(cutoff, beta)

    def _set_filter(self, cutoff: float, beta: float) -> None:
        ck, bk = self.filter_keys()
        # Enough digits for 1 / cutoff to come back as the round number it was.
        setattr(self.cfg, ck, round(cutoff, 5))
        setattr(self.cfg, bk, round(beta, 3))
        self.engine.tune_filter(getattr(self.cfg, ck), getattr(self.cfg, bk))

    def set_deadband(self, pixels: float) -> None:
        self.cfg.cursor_deadband = min(max(pixels, 0.0), 300.0)
        self.engine.deadband.radius = self.cfg.cursor_deadband

    def reset_tuning(self) -> None:
        ck, bk = self.filter_keys()
        self._set_filter(self._tuning_start[ck], self._tuning_start[bk])
        self.set_deadband(self._tuning_start["cursor_deadband"])

    def save_tuning(self) -> None:
        keys = (*self.filter_keys(), "cursor_deadband", "use_hand", "use_head", "use_eyes")
        values = {k: getattr(self.cfg, k) for k in keys}
        try:
            path = bindings_mod.save_settings(self.config_path, values)
        except (OSError, bindings_mod.ConfigError) as exc:
            print(f"Settings not saved: {exc}", flush=True)
            self._events.append((time.monotonic(), "SAVE FAILED"))
            return
        print(f"Saved to {path}: {values}", flush=True)
        self._events.append((time.monotonic(), "SAVED"))

    def gesture_sections(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """(title, [(gesture, action)]) for every input switched on.

        Read off the bindings actually loaded, so the list never disagrees
        with gestures.json.
        """
        sections = []
        if self.engine.face is not None:
            sections.append((self.engine.face.label, FACE_SLOT))
        if self.cfg.use_hand:
            sections.append(("index finger", self.engine.dominant))
        out = []
        for mover, slot in sections:
            title = mover.capitalize() if slot == FACE_SLOT else f"{slot.capitalize()} hand"
            pairs = [
                (
                    mover if name in ("point_move", "face_move") else name.replace("_", " "),
                    action.label.lower(),
                )
                for name, action in self.engine.bindings[slot].items()
            ]
            out.append((title, pairs))
        out.append(
            ("Always", [("both fists closed", f"pause {self.cfg.pause_fists_seconds:g} s")])
        )
        return out

    # --- main loop ----------------------------------------------------------
    def _face_detector(self) -> FaceDetector:
        """Built the first time the head or the eyes are switched on."""
        model = ensure_model(
            self.cfg.face_model_path or DEFAULT_FACE_MODEL_PATH, FACE_MODEL_URL, "FaceLandmarker"
        )
        return FaceDetector(
            model,
            min_detection_confidence=self.cfg.min_detection_confidence,
            min_tracking_confidence=self.cfg.min_tracking_confidence,
        )

    def run(self) -> int:
        face_detector = None
        # Loaded with the hands off too: the two fists that pause are watched anyway.
        model_path = (
            ensure_model(self.cfg.model_path) if self.cfg.model_path else ensure_model()
        )
        if self.engine.face is not None:
            face_detector = self._face_detector()

        cap = self._open_camera()
        if cap is None:
            return 1

        detector = HandDetector(
            model_path,
            num_hands=min(2, self.cfg.num_hands) or 2,
            min_detection_confidence=self.cfg.min_detection_confidence,
            min_tracking_confidence=self.cfg.min_tracking_confidence,
            mirrored=self.cfg.mirror,
        )
        if self.cfg.show_preview:
            from .window import ControlWindow  # Tk only when there is a window

            self.window = ControlWindow(self)
        if self.cfg.overlay:
            # With the face row always: the head can be switched on at any time.
            # A child of the window when there is one: one Tk per process.
            master = self.window.root if self.window is not None else None
            self.overlay = overlay.create(face=True, master=master)
        hotkeys = self._make_hotkeys()
        hotkeys.start()
        print(
            "Celebrimbor started. Ctrl+Alt+Q to quit, Ctrl+Alt+P (or both fists "
            f"closed for {self.cfg.pause_fists_seconds:.0f} s) to pause, "
            "Ctrl+Alt+Space to move the pointer to the other hand."
        )
        on = [n for n, v in (("hand", self.cfg.use_hand), ("head", self.cfg.use_head),
                             ("eyes", self.cfg.use_eyes)) if v]
        print(f"Driving the mouse: {', '.join(on) or 'nothing (switch something on)'}.")
        if self.engine.face is not None:
            print(
                "Look at the centre of the screen while it calibrates; "
                "Ctrl+Alt+C to calibrate again."
            )
        if self.cfg.dry_run:
            print("DRY-RUN: gestures are recognised but the mouse does not move.")

        last_t = time.monotonic()
        try:
            while self.running:
                ok, frame = cap.read()
                if not ok:
                    print("No frame read from the webcam, exiting.")
                    break

                if self.cfg.mirror:
                    frame = cv2.flip(frame, 1)

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                now = time.monotonic()
                # With the hands off they are only watched for the pause fists:
                # one frame in three is plenty for a 3-second hold.
                tracks_hands = self.cfg.use_hand
                self._frame_no += 1
                if tracks_hands or self._frame_no % 3 == 0:
                    self._hands = detector.detect(rgb, int(now * 1000))
                hands = self._hands
                face_landmarks = None
                if self.engine.face is not None:
                    if face_detector is None:
                        face_detector = self._face_detector()
                    face_landmarks = face_detector.detect(rgb, int(now * 1000))

                dt = now - last_t
                last_t = now
                if dt > 0:
                    self._fps = 0.9 * self._fps + 0.1 * (1.0 / dt)

                feats = {obs.slot: hand.extract(obs.landmarks) for obs in hands}
                # Two fists are the pause gesture and nothing else: the hand
                # recognisers never see them, or a still fist would fire its own
                # fist_hold on the way there.
                if self._fists_pause(feats, now) or not tracks_hands:
                    feats = {}
                self._watch_mouse(now)
                face_feats = None
                if face_landmarks is not None:
                    fh, fw = frame.shape[:2]
                    face_feats = face.extract(face_landmarks, fw / fh, self.cfg.mirror)
                states = self.engine.update(feats, now, face_feats)
                self._record(states, now)

                if self.overlay is not None:
                    self.overlay.update(
                        states,
                        [e for _, e in self._events],
                        self.engine.pointer_holder,
                        self.engine.enabled,
                    )

                if self.window is None:
                    self._log(states)
                else:
                    self._draw(frame, hands, face_landmarks, states)
                    self.window.show(frame, states, [e for _, e in self._events], self._fps)
                    if not self.window.alive:
                        break
        except KeyboardInterrupt:
            pass
        finally:
            self.engine.reset()
            hotkeys.stop()
            detector.close()
            if face_detector is not None:
                face_detector.close()
            cap.release()
            if self.overlay is not None:
                self.overlay.close()
            if self.window is not None:
                self.window.close()
        return 0

    # --- webcam -------------------------------------------------------------
    def _open_camera(self):
        # DirectShow on Windows opens much faster than the MSMF backend.
        backend = getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY)
        cap = cv2.VideoCapture(self.cfg.camera_index, backend)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.cfg.camera_index)
        if not cap.isOpened():
            print(f"Cannot open webcam {self.cfg.camera_index}.")
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.frame_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.frame_height)
        # Without this explicit request the webcam may stay at 10 fps.
        cap.set(cv2.CAP_PROP_FPS, self.cfg.target_fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    # --- console (when the preview is disabled) -----------------------------
    def _log(self, states: list[EngineState]) -> None:
        modes = "  ".join(f"{s.hand[0].upper()}:{s.mode}" for s in states)
        if modes != self._last_mode:
            self._last_mode = modes
            details = "  ".join(s.detail for s in states if s.detail)
            print(f"[{self._fps:4.1f} fps] {modes}  {details}".rstrip(), flush=True)
        for state in states:
            for ev in state.events:
                print(f"  -> [{state.hand[0].upper()}] {ev}", flush=True)

    # --- drawing on the camera image ------------------------------------------
    def _record(self, states: list[EngineState], now: float) -> None:
        for state in states:
            for ev in state.events:
                self._events.append((now, f"{state.hand[0].upper()} {ev}"))
        self._events = [(t, e) for t, e in self._events if now - t < 1.2]

    def _draw(self, frame, hands, face_landmarks, states: list[EngineState]) -> None:
        """What belongs on the picture itself: skeletons, aim points, bars.

        Modes, events and fps live in the window around it.
        """
        h, w = frame.shape[:2]
        # With the hands off there are no hands to report on, only the face.
        hand_states = [s for s in states if s.hand != FACE_SLOT and self.cfg.use_hand]
        face_state = next((s for s in states if s.hand == FACE_SLOT), None)

        if self.show_skeleton:
            for obs in hands:
                self._draw_hand(frame, obs.landmarks, w, h)
            if face_landmarks is not None:
                self._draw_face(frame, face_landmarks, w, h)

        c = self.cfg
        if c.use_hand:
            # The active area only means something while a hand can point.
            cv2.rectangle(
                frame,
                (int(c.active_x_min * w), int(c.active_y_min * h)),
                (int(c.active_x_max * w), int(c.active_y_max * h)),
                _GREY,
                1,
            )

        # The point actually driving the cursor: during a pinch it moves from
        # the index finger to the palm, and you can see it here.
        for state in hand_states:
            if state.ref_point is None:
                continue
            rp = (int(state.ref_point[0] * w), int(state.ref_point[1] * h))
            anchored = state.cursor_source == "palm"
            cv2.circle(frame, rp, 11, _RED if anchored else _GREEN, 2)
            cv2.circle(frame, rp, 2, _RED if anchored else _GREEN, -1)

        if face_state is not None:
            self._draw_face_readouts(frame, face_state, w, h)

        # One column of readouts per hand, on its own side of the frame: the
        # left hand is the one on the left of the mirrored image.
        for state in hand_states:
            x = 10 if state.hand == "left" else w // 2
            self._draw_readouts(frame, state, x, h)

        if self._pause_progress > 0.0:
            label = "RESUME" if not self.engine.enabled else "PAUSE"
            self._progress(frame, label, self._pause_progress, w // 2 - 120, h // 2 + 40)

    def _draw_readouts(self, frame, state: EngineState, x: int, h: int) -> None:
        """Pinch bars and still-fist progress of one hand, in its own column."""
        labels = {"index": "IDX", "middle": "MID", "ring": "RNG"}
        shown = [f for f in labels if f in state.pinches]
        for i, finger in enumerate(shown):
            y = h - 40 - 22 * (len(shown) - 1 - i)
            self._bar(
                frame,
                labels[finger],
                state.pinches[finger],
                x,
                y,
                anchor_at=state.anchor_at.get(finger),
            )

        if state.fist_hold_progress > 0.0:
            self._progress(
                frame, "FIST STILL", state.fist_hold_progress, x, h - 40 - 22 * len(shown)
            )

    def _draw_hand(self, frame, landmarks, w: int, h: int) -> None:
        pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
        for a, b in HAND_CONNECTIONS:
            cv2.line(frame, pts[a], pts[b], _WHITE, 2)
        for i, p in enumerate(pts):
            # highlight thumb, index, middle and ring: the fingers in charge
            highlight = i in (hand.THUMB_TIP, hand.INDEX_TIP, hand.MIDDLE_TIP, hand.RING_TIP)
            cv2.circle(frame, p, 6 if highlight else 3, _YELLOW if highlight else _GREEN, -1)

    def _draw_face_readouts(self, frame, state: EngineState, w: int, h: int) -> None:
        """Where the nose points, the calibration, how open each eye is."""
        if state.ref_point is not None:
            rp = (int(state.ref_point[0] * w), int(state.ref_point[1] * h))
            cv2.circle(frame, rp, 9, _GREEN, 2)

        if state.calibration > 0.0:
            self._progress(frame, "CALIBRATING", state.calibration, w // 2 - 120, h // 2)

        # Openness of each eye, 1 = habitual: the tick is where it counts as shut.
        for i, side in enumerate(face.EYE_SIDES):
            if side in state.eyes:
                self._eye_bar(frame, f"{side[0].upper()}EYE", state.eyes[side], 10, 12 + i * 20)

    def _eye_bar(self, frame, label: str, value: float, x: int, y: int) -> None:
        ratio = min(max(value, 0.0), 1.0)
        x0, width = x + 50, 110
        shut = value < self.cfg.eye_closed_ratio
        cv2.putText(frame, label, (x, y + 11), cv2.FONT_HERSHEY_SIMPLEX, 0.5, _GREY, 1)
        cv2.rectangle(frame, (x0, y), (x0 + width, y + 12), _GREY, 1)
        cv2.rectangle(
            frame, (x0, y), (x0 + int(width * ratio), y + 12), _YELLOW if shut else _GREEN, -1
        )
        thr = x0 + int(width * self.cfg.eye_closed_ratio)
        cv2.line(frame, (thr, y - 2), (thr, y + 14), _RED, 1)

    def _draw_face(self, frame, landmarks, w: int, h: int) -> None:
        pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
        for a, b in FACE_CONNECTIONS:
            if a < len(pts) and b < len(pts):
                cv2.line(frame, pts[a], pts[b], _GREY, 1)
        for i in face.IRIS_CENTRES:
            if i < len(pts):
                cv2.circle(frame, pts[i], 2, _YELLOW, -1)

    def _progress(self, frame, label: str, ratio: float, x: int, y: int) -> None:
        x0, width = x + 40, 160
        cv2.putText(frame, label, (x0, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, _GREY, 1)
        cv2.rectangle(frame, (x0, y), (x0 + width, y + 10), _GREY, 1)
        cv2.rectangle(
            frame,
            (x0, y),
            (x0 + int(width * min(max(ratio, 0.0), 1.0)), y + 10),
            _YELLOW if ratio < 1.0 else _GREEN,
            -1,
        )

    def _bar(
        self,
        frame,
        label: str,
        value: float,
        x: int,
        y: int,
        anchor_at: float | None = None,
    ) -> None:
        full = 1.2  # full-scale value of the bar
        ratio = min(max(value / full, 0.0), 1.0)
        x0, width = x + 40, 110
        active = value < self.cfg.pinch_on
        cv2.putText(frame, label, (x, y + 11), cv2.FONT_HERSHEY_SIMPLEX, 0.5, _GREY, 1)
        cv2.rectangle(frame, (x0, y), (x0 + width, y + 12), _GREY, 1)
        cv2.rectangle(
            frame,
            (x0, y),
            (x0 + int(width * ratio), y + 12),
            _YELLOW if active else _GREEN,
            -1,
        )
        thr = x0 + int(width * (self.cfg.pinch_on / full))
        cv2.line(frame, (thr, y - 2), (thr, y + 14), _RED, 1)
        if anchor_at:
            a = x0 + int(width * min(anchor_at / full, 1.0))
            cv2.line(frame, (a, y - 2), (a, y + 14), _GREY, 1)
