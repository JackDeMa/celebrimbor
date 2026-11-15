"""Main loop: webcam capture -> landmarks (hands, face) -> gestures -> mouse."""

import functools
import sys
import time

import cv2
import numpy as np
from pynput import keyboard

from . import bindings as bindings_mod
from . import face, hand, overlay
from .config import Config
from .controller import MouseActuator
from .detector import (
    DEFAULT_FACE_MODEL_PATH,
    DEFAULT_MODEL_PATH,
    FACE_CONNECTIONS,
    FACE_MODEL_URL,
    HAND_CONNECTIONS,
    FaceDetector,
    HandDetector,
    ensure_model,
    have_model,
)
from .engine import FACE_SLOT, EngineState, GestureEngine
from .splash import Splash

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
    "eyes": ("eyes_fine_cutoff", "eyes_fine_beta"),
}

# The HUD palette, in BGR: the same colours as the window around the picture.
_CYAN = (255, 229, 0)      # the main line colour
_DIM = (99, 85, 11)        # empty cells, hairlines
_MESH = (150, 128, 16)     # face mesh: between the two
_AMBER = (32, 176, 255)    # armed, palm anchor, the fingers in charge
_ALERT = (92, 59, 255)     # thresholds
_OK = (168, 255, 61)
_TEXT = (242, 233, 207)
_MUTED = (135, 119, 94)
_PANEL = (28, 20, 13)
_FONT = cv2.FONT_HERSHEY_DUPLEX
_AA = cv2.LINE_AA
_CELLS, _CELL_W = 12, 9    # a bar: this many cells, this many pixels apart


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
        # HUD layers, built for the first frame's size: scanlines, glow
        self._scan = None
        self._glow = None
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
            self.cfg.face_model_path or DEFAULT_FACE_MODEL_PATH, FACE_MODEL_URL, "face"
        )
        return FaceDetector(
            model,
            min_detection_confidence=self.cfg.min_detection_confidence,
            min_tracking_confidence=self.cfg.min_tracking_confidence,
        )

    def _load_detectors(self, splash: Splash):
        """The slow part of starting: models (downloaded if missing), MediaPipe."""
        # Loaded with the hands off too: the two fists that pause are watched anyway.
        model_path = ensure_model(
            self.cfg.model_path or DEFAULT_MODEL_PATH, progress=splash.download("hand")
        )
        # The face model even with the head off: it can be switched on at any
        # time, and a download then would freeze the window.
        ensure_model(self.cfg.face_model_path or DEFAULT_FACE_MODEL_PATH, FACE_MODEL_URL,
                     "face", splash.download("face"))
        splash.status("Loading hand model...")
        detector = HandDetector(
            model_path,
            num_hands=min(2, self.cfg.num_hands) or 2,
            min_detection_confidence=self.cfg.min_detection_confidence,
            min_tracking_confidence=self.cfg.min_tracking_confidence,
            mirrored=self.cfg.mirror,
        )
        face_detector = None
        if self.engine.face is not None:
            splash.status("Loading face model...")
            face_detector = self._face_detector()
        return detector, face_detector

    def run(self) -> int:
        # The splash shows with a window to wait for, and for a download always.
        missing = not (have_model(self.cfg.model_path or DEFAULT_MODEL_PATH)
                       and have_model(self.cfg.face_model_path or DEFAULT_FACE_MODEL_PATH))
        with Splash(self.cfg.show_preview or missing) as splash:
            detector, face_detector = splash.run(self._load_detectors, splash)
            # On the main thread: macOS only asks for camera access from there.
            splash.status("Starting camera...")
            cap = self._open_camera()
        if cap is None:
            detector.close()
            if face_detector is not None:
                face_detector.close()
            return 1

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

        # The picture steps back, dimmed and scanlined, so the lines stand out.
        if self._scan is None or self._scan.shape != frame.shape:
            self._scan = np.zeros_like(frame)
            self._scan[::3] = 12
            self._glow = np.zeros((h // 2, w // 2, 3), np.uint8)
        cv2.addWeighted(frame, 0.7, self._scan, -1.0, 0, dst=frame)

        if self.show_skeleton and (hands or face_landmarks is not None):
            # Twice: into a black layer at half size, blurred and added (the
            # glow), then crisp on top.
            self._glow[:] = 0
            if face_landmarks is not None:
                face_pts = np.array([(lm.x * w, lm.y * h) for lm in face_landmarks], np.int32)
            for layer, div in ((self._glow, 2), (frame, 1)):
                fw, fh = w / div, h / div
                if face_landmarks is not None:
                    self._draw_face(layer, face_pts // div, div == 1)
                for obs in hands:
                    pts = [(int(lm.x * fw), int(lm.y * fh)) for lm in obs.landmarks]
                    self._draw_hand(layer, pts, obs.slot, div == 1)
                if div == 2:
                    glow = cv2.GaussianBlur(layer, (7, 7), 0)
                    cv2.add(frame, cv2.resize(glow, (w, h)), dst=frame)

        c = self.cfg
        if c.use_hand:
            # The active area only means something while a hand can point.
            x0, y0 = int(c.active_x_min * w), int(c.active_y_min * h)
            _brackets(frame, x0, y0, int(c.active_x_max * w), int(c.active_y_max * h), _MUTED, 16)
            _text(frame, "ACTIVE AREA", (x0 + 6, y0 + 14), _MUTED, 0.35)

        # The point actually driving the cursor: during a pinch it moves from
        # the index finger to the palm, and you can see it here.
        for state in hand_states:
            if state.ref_point is None:
                continue
            rp = (int(state.ref_point[0] * w), int(state.ref_point[1] * h))
            _reticle(frame, rp, 11, _AMBER if state.cursor_source == "palm" else _CYAN)

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

    def _draw_hand(self, img, pts: list, slot: str, crisp: bool) -> None:
        if not crisp:  # the glow layer: only the bones shine
            for a, b in HAND_CONNECTIONS:
                cv2.line(img, pts[a], pts[b], _CYAN, 1)
            return
        for a, b in HAND_CONNECTIONS:
            cv2.line(img, pts[a], pts[b], _CYAN, 1, _AA)
        for i, p in enumerate(pts):
            cv2.circle(img, p, 2, _PANEL, -1)
            cv2.circle(img, p, 3, _CYAN, 1, _AA)
            # ringed: thumb, index, middle and ring, the fingers in charge
            if i in (hand.THUMB_TIP, hand.INDEX_TIP, hand.MIDDLE_TIP, hand.RING_TIP):
                cv2.circle(img, p, 7, _AMBER, 1, _AA)
        xs, ys = zip(*pts)
        x0, y0 = min(xs) - 14, min(ys) - 14
        _brackets(img, x0, y0, max(xs) + 14, max(ys) + 14, _CYAN)
        _text(img, f"{slot.upper()} HAND", (x0, y0 - 6), _CYAN)

    def _draw_face_readouts(self, frame, state: EngineState, w: int, h: int) -> None:
        """Where the nose points, the calibration, how open each eye is."""
        if state.ref_point is not None:
            rp = (int(state.ref_point[0] * w), int(state.ref_point[1] * h))
            _reticle(frame, rp, 9, _CYAN)

        if state.calibration > 0.0:
            self._progress(frame, "CALIBRATING", state.calibration, w // 2 - 120, h // 2)

        # Openness of each eye, 1 = habitual: the triangle is where it counts as shut.
        for i, side in enumerate(face.EYE_SIDES):
            if side in state.eyes:
                self._eye_bar(frame, f"{side[0].upper()}EYE", state.eyes[side], 10, 14 + i * 22)

    def _eye_bar(self, frame, label: str, value: float, x: int, y: int) -> None:
        shut = value < self.cfg.eye_closed_ratio
        _meter(frame, label, value, value, x, y, _AMBER if shut else _CYAN,
               self.cfg.eye_closed_ratio)

    def _draw_face(self, img, pts, crisp: bool) -> None:
        """The mesh in one call; on the crisp pass, a reticle on each iris."""
        cv2.polylines(img, pts[_face_segments(len(pts))], False, _MESH, 1,
                      _AA if crisp else cv2.LINE_8)
        if crisp:
            for ring in face.IRISES:  # the centre first, then its edge
                if ring[-1] < len(pts):
                    _reticle(img, tuple(pts[ring[0]].tolist()), 7, _CYAN, time.monotonic())

    def _progress(self, frame, label: str, ratio: float, x: int, y: int) -> None:
        ratio = min(max(ratio, 0.0), 1.0)
        x0 = x + 40
        _panel(frame, x0 - 6, y - 18, x0 + 16 * _CELL_W + 46, y + 16)
        _text(frame, label, (x0, y - 5), _TEXT)
        _cells(frame, x0, y, ratio, _AMBER if ratio < 1.0 else _OK, 16)
        _text(frame, f"{ratio * 100:3.0f}%", (x0 + 16 * _CELL_W + 6, y + 9), _TEXT)

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
        armed = value < self.cfg.pinch_on
        _meter(frame, label, value, value / full, x, y, _AMBER if armed else _CYAN,
               self.cfg.pinch_on / full, anchor_at / full if anchor_at else None)


# --- HUD primitives -----------------------------------------------------------
def _text(img, text: str, org, color=_TEXT, scale: float = 0.4) -> None:
    cv2.putText(img, text, org, _FONT, scale, color, 1, _AA)


def _brackets(img, x0: int, y0: int, x1: int, y1: int, color, size: int = 10) -> None:
    """The four corners of a box, and nothing in between."""
    corners = [
        [(x, y + dy * size), (x, y), (x + dx * size, y)]
        for x, dx in ((x0, 1), (x1, -1))
        for y, dy in ((y0, 1), (y1, -1))
    ]
    cv2.polylines(img, np.array(corners, np.int32), False, color, 1, _AA)


def _reticle(img, p, r: int, color, spin: float | None = None) -> None:
    """A crosshair with a gap in the middle. With `spin` (a time, in seconds)
    it is an iris lock instead: a ring, four ticks and an arc going round."""
    x, y = p
    inner = 4 if spin is None else r - 2
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        cv2.line(img, (x + dx * inner, y + dy * inner),
                 (x + dx * (r + 3), y + dy * (r + 3)), color, 1, _AA)
    if spin is None:
        cv2.circle(img, p, 1, color, -1, _AA)
    else:
        cv2.circle(img, p, r, color, 1, _AA)
        cv2.ellipse(img, p, (r + 6, r + 6), (spin * 200) % 360, 0, 80, color, 1, _AA)


def _panel(img, x0: int, y0: int, x1: int, y1: int) -> None:
    """A translucent dark plate with a hairline border: blends its region only."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w - 1), min(y1, h - 1)
    if x1 <= x0 or y1 <= y0:
        return
    # 30% picture + 70% _PANEL
    dark = cv2.convertScaleAbs(img[y0:y1, x0:x1], alpha=0.3)
    img[y0:y1, x0:x1] = cv2.add(dark, (20, 14, 9, 0))
    cv2.rectangle(img, (x0, y0), (x1, y1), _DIM, 1)


@functools.lru_cache(maxsize=256)
def _cell_strip(lit: int, color: tuple, count: int) -> np.ndarray:
    strip = np.full((10, count * _CELL_W, 3), _PANEL, np.uint8)
    for i in range(count):
        strip[:, i * _CELL_W:(i + 1) * _CELL_W - 2] = color if i < lit else _DIM
    return strip


def _cells(img, x: int, y: int, ratio: float, color, count: int = _CELLS) -> None:
    """A segmented bar: pasted from a cache, a hundred rectangles a frame add up."""
    strip = _cell_strip(round(min(max(ratio, 0.0), 1.0) * count), color, count)
    roi = img[y:y + 10, x:x + strip.shape[1]]
    roi[:] = strip[:roi.shape[0], :roi.shape[1]]


def _meter(img, label: str, value: float, ratio: float, x: int, y: int, color,
           threshold: float | None = None, mark: float | None = None) -> None:
    """One readout row: label, segmented bar, number. `threshold` and `mark`
    are positions along the bar (0..1): a triangle above it, a tick below."""
    x0, width = x + 46, _CELLS * _CELL_W
    _panel(img, x - 6, y - 6, x0 + width + 46, y + 16)
    _text(img, label, (x, y + 9), _MUTED)
    _cells(img, x0, y, ratio, color)
    _text(img, f"{value:.2f}", (x0 + width + 6, y + 9), _TEXT)
    if threshold is not None:
        t = x0 + int(width * min(threshold, 1.0))
        cv2.fillConvexPoly(img, np.array([(t - 3, y - 5), (t + 3, y - 5), (t, y - 1)], np.int32),
                           _ALERT, _AA)
    if mark is not None:
        m = x0 + int(width * min(mark, 1.0))
        cv2.line(img, (m, y + 10), (m, y + 14), _TEXT, 1)


_SEGMENTS: dict[int, np.ndarray] = {}


def _face_segments(count: int) -> np.ndarray:
    """FACE_CONNECTIONS as an index array, for a face of `count` landmarks."""
    if count not in _SEGMENTS:
        _SEGMENTS[count] = np.array(
            [(a, b) for a, b in FACE_CONNECTIONS if a < count and b < count], np.intp
        )
    return _SEGMENTS[count]
