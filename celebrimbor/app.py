"""Main loop: webcam capture -> landmarks (hands, face) -> gestures -> mouse."""

import time

import cv2
from pynput import keyboard

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

WINDOW = "Celebrimbor"
_HELP_W = 320  # width of the instructions column beside the camera image

_GREEN = (120, 230, 120)
_GREY = (170, 170, 170)
_RED = (80, 80, 240)
_YELLOW = (60, 220, 240)
_WHITE = (245, 245, 245)


class CelebrimborApp:
    def __init__(self, cfg: Config, bindings: dict[str, dict] | None = None):
        self.cfg = cfg
        self.mouse = MouseActuator(
            click_cooldown=cfg.click_cooldown, dry_run=cfg.dry_run
        )
        self.engine = GestureEngine(cfg, self.mouse, bindings)
        self.running = True
        self.show_skeleton = True
        self.overlay: overlay.Overlay | None = None
        self._fps = 0.0
        self._events: list[tuple[float, str]] = []
        self._last_mode = ""
        # both fists closed: pause / resume
        self._fists_since: float | None = None
        self._fists_done = False
        self._pause_progress = 0.0
        self._frame_no = 0
        self._hands: list = []

    # --- global hotkeys ----------------------------------------------------
    def _make_hotkeys(self) -> keyboard.GlobalHotKeys:
        return keyboard.GlobalHotKeys(
            {
                "<ctrl>+<alt>+q": self.stop,
                "<ctrl>+<alt>+p": self.toggle_pause,
                "<ctrl>+<alt>+<space>": self.swap_dominant,
                "<ctrl>+<alt>+c": self.recalibrate,
            }
        )

    def stop(self) -> None:
        self.running = False

    def toggle_pause(self) -> None:
        self.engine.set_enabled(not self.engine.enabled)

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

    # --- main loop ----------------------------------------------------------
    def run(self) -> int:
        tracks_hands = self.cfg.num_hands > 0
        if not tracks_hands and self.engine.face is None:
            print("Nothing to track: --hands 0 needs --pointer head or --pointer eyes.")
            return 2

        detector = face_detector = None
        # Loaded even with --hands 0: the two fists that pause are watched anyway.
        model_path = (
            ensure_model(self.cfg.model_path) if self.cfg.model_path else ensure_model()
        )
        if self.engine.face is not None:
            face_model = ensure_model(
                self.cfg.face_model_path or DEFAULT_FACE_MODEL_PATH,
                FACE_MODEL_URL,
                "FaceLandmarker",
            )

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
        if self.engine.face is not None:
            face_detector = FaceDetector(
                face_model,
                min_detection_confidence=self.cfg.min_detection_confidence,
                min_tracking_confidence=self.cfg.min_tracking_confidence,
            )
        if self.cfg.show_preview:
            # Created up front: the loop checks the window before showing a
            # frame, and there has to be one to check.
            cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
        if self.cfg.overlay:
            self.overlay = overlay.create(face=self.engine.face is not None)
        hotkeys = self._make_hotkeys()
        hotkeys.start()
        print(
            "Celebrimbor started. Ctrl+Alt+Q to quit, Ctrl+Alt+P (or both fists "
            f"closed for {self.cfg.pause_fists_seconds:.0f} s) to pause, "
            "Ctrl+Alt+Space to move the pointer to the other hand."
        )
        if self.engine.face is not None:
            print(
                f"Pointer on the {self.engine.face.pointer}. Look at the centre of the "
                "screen while it calibrates; Ctrl+Alt+C to calibrate again."
            )
        else:
            print(f"Pointer on the {self.engine.dominant} hand.")
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
                # With --hands 0 the hands are only watched for the pause fists:
                # one frame in three is plenty for a 3-second hold.
                self._frame_no += 1
                if tracks_hands or self._frame_no % 3 == 0:
                    self._hands = detector.detect(rgb, int(now * 1000))
                hands = self._hands
                face_landmarks = (
                    face_detector.detect(rgb, int(now * 1000)) if face_detector else None
                )

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

                if not self.cfg.show_preview:
                    self._log(states)
                else:
                    # Before drawing, not after: the overlay's Tk pump drains the
                    # whole thread message queue, close button included, so the
                    # window can already be gone by the time we get here - and
                    # imshow would silently build a new one, swallowing the X.
                    if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                        break
                    self._draw(frame, hands, face_landmarks, states)
                    cv2.imshow(WINDOW, self._with_help(frame))
                    if not self._handle_keys():
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
            cv2.destroyAllWindows()
            if self.overlay is not None:
                self.overlay.close()
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

    def _handle_keys(self) -> bool:
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            return False
        if key == ord("p"):
            self.toggle_pause()
        elif key == ord("h"):
            self.show_skeleton = not self.show_skeleton
        elif key == ord("d"):
            self.swap_dominant()
        elif key == ord("o"):
            self.toggle_overlay()
        elif key == ord("c"):
            self.recalibrate()
        return True

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

    # --- HUD -----------------------------------------------------------------
    def _record(self, states: list[EngineState], now: float) -> None:
        for state in states:
            for ev in state.events:
                self._events.append((now, f"{state.hand[0].upper()} {ev}"))
        self._events = [(t, e) for t, e in self._events if now - t < 1.2]

    def _draw(self, frame, hands, face_landmarks, states: list[EngineState]) -> None:
        h, w = frame.shape[:2]
        # With --hands 0 there are no hands to report on, only the face.
        hand_states = [s for s in states if s.hand != FACE_SLOT and self.cfg.num_hands > 0]
        face_state = next((s for s in states if s.hand == FACE_SLOT), None)

        if self.show_skeleton:
            for obs in hands:
                self._draw_hand(frame, obs.landmarks, w, h)
            if face_landmarks is not None:
                self._draw_face(frame, face_landmarks, w, h)

        c = self.cfg
        if face_state is None:
            # The active area only means something while a hand holds the pointer.
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

        self._draw_header(frame, w, hand_states)
        if face_state is not None:
            self._draw_face_readouts(frame, face_state, w, h)

        # One column of readouts per hand, on its own side of the frame: the
        # left hand is the one on the left of the mirrored image.
        for state in hand_states:
            x = 10 if state.hand == "left" else w // 2
            self._draw_readouts(frame, state, x, h)

        for i, (_, ev) in enumerate(reversed(self._events[-3:])):
            cv2.putText(
                frame, ev, (w - 200, 90 + i * 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, _YELLOW, 2
            )

        if self._pause_progress > 0.0:
            label = "RESUME" if not self.engine.enabled else "PAUSE"
            self._progress(frame, label, self._pause_progress, w // 2 - 120, h // 2 + 40)
        if not self.engine.enabled:
            cv2.putText(
                frame, "PAUSED", (w // 2 - 70, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.2, _RED, 3
            )

        if self.cfg.dry_run:
            cv2.putText(
                frame, "DRY-RUN", (w - 180, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, _YELLOW, 2
            )
        cv2.putText(
            frame,
            f"{self._fps:4.1f} fps",
            (w - 85, h - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            _GREY,
            1,
        )

    def _with_help(self, frame):
        """The frame with a column of basic instructions on its right.

        The gestures are read off the bindings actually loaded, so the column
        never disagrees with gestures.json.
        """
        fh, fw = frame.shape[:2]
        out = cv2.copyMakeBorder(
            frame, 0, 0, 0, _HELP_W, cv2.BORDER_CONSTANT, value=(25, 25, 25)
        )
        holder = self.engine.pointer_holder
        face_mode = self.engine.face is not None
        mover = self.engine.face.pointer if face_mode else "index finger"
        rows: list[tuple[str, str]] = [
            (f"GESTURES ({mover.upper() if face_mode else holder.upper() + ' HAND'})", "")
        ]
        for name, action in self.engine.bindings[holder].items():
            gesture = mover if name in ("point_move", "face_move") else name.replace("_", " ")
            rows.append((gesture, action.label.upper()))
        rows += [
            ("", ""),
            ("both fists closed", f"PAUSE {self.cfg.pause_fists_seconds:.0f}s"),
            ("", ""),
            ("KEYS (THIS WINDOW)", ""),
            ("q / Esc", "quit"),
            ("p", "pause / resume"),
            ("h / o", "skeleton / overlay"),
            ("c" if face_mode else "d", "calibrate" if face_mode else "swap hand"),
            ("", ""),
            ("GLOBAL", ""),
            ("Ctrl+Alt+Q / P", "quit / pause"),
            (
                "Ctrl+Alt+C" if face_mode else "Ctrl+Alt+Space",
                "calibrate" if face_mode else "swap hand",
            ),
        ]

        x = fw + 12
        for i, (left, right) in enumerate(rows):
            y = 22 + i * 15
            if y > fh - 8:
                break
            header = not right and left.isupper()
            cv2.putText(
                out, left, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                _YELLOW if header else _GREY, 1,
            )
            if right:
                cv2.putText(
                    out, right[:18], (x + 165, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, _WHITE, 1
                )
        return out

    def _draw_header(self, frame, w: int, states: list[EngineState]) -> None:
        """Two rows: the mode of each hand, and underneath its detail."""
        color = _GREEN if self.engine.enabled else _RED
        cv2.rectangle(frame, (0, 0), (w, 56), (25, 25, 25), -1)
        for state in states:
            x = 10 if state.hand == "left" else w // 2
            # The dot marks the hand holding the pointer when both are in view.
            initial = state.hand[0].upper()
            marker = "*" if state.hand == self.engine.pointer_holder else " "
            faded = state.mode == "NO HAND"
            cv2.putText(
                frame,
                f"{initial}{marker} {state.mode}",
                (x, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                _GREY if faded else color,
                2,
            )
            detail = state.detail
            if state.cursor_source:
                detail = f"[{state.cursor_source}] {detail}".rstrip()
            if detail:
                cv2.putText(
                    frame, detail, (x, 47), cv2.FONT_HERSHEY_SIMPLEX, 0.42, _GREY, 1
                )

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
        """A strip under the header: what the face is doing, how open each eye is."""
        color = _GREEN if self.engine.enabled else _RED
        cv2.rectangle(frame, (0, 56), (w, 80), (40, 40, 40), -1)
        text = f"F* {state.mode}"
        if state.detail:
            text = f"{text}  {state.detail}"
        cv2.putText(
            frame,
            text,
            (10, 74),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            _GREY if state.mode == "NO FACE" else color,
            1,
        )

        if state.ref_point is not None:
            rp = (int(state.ref_point[0] * w), int(state.ref_point[1] * h))
            cv2.circle(frame, rp, 9, _GREEN, 2)

        if state.calibration > 0.0:
            self._progress(frame, "CALIBRATING", state.calibration, w // 2 - 120, h // 2)

        # Openness of each eye, 1 = habitual: the tick is where it counts as shut.
        for i, side in enumerate(face.EYE_SIDES):
            if side in state.eyes:
                self._eye_bar(frame, f"{side[0].upper()}EYE", state.eyes[side], 10, 90 + i * 20)

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
