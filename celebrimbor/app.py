"""Main loop: webcam capture -> landmarks -> gestures -> mouse."""

import time

import cv2
from pynput import keyboard

from . import bindings as bindings_mod
from . import hand, overlay
from .calibration import BothFistsWatch, Calibration
from .config import Config
from .controller import MouseActuator
from .detector import HAND_CONNECTIONS, HandDetector, ensure_model
from .engine import EngineState, GestureEngine

WINDOW = "Celebrimbor"

_GREEN = (120, 230, 120)
_GREY = (170, 170, 170)
_RED = (80, 80, 240)
_YELLOW = (60, 220, 240)
_WHITE = (245, 245, 245)


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
        self._fps = 0.0
        self._events: list[tuple[float, str]] = []
        self._last_mode = ""
        self.calibration: Calibration | None = None
        self._calib_progress = None
        self._calib_hold_until = 0.0
        self._arming = 0.0
        self._both_fists = BothFistsWatch()

    # --- global hotkeys ----------------------------------------------------
    def _make_hotkeys(self) -> keyboard.GlobalHotKeys:
        return keyboard.GlobalHotKeys(
            {
                "<ctrl>+<alt>+q": self.stop,
                "<ctrl>+<alt>+p": self.toggle_pause,
                "<ctrl>+<alt>+<space>": self.swap_dominant,
                "<ctrl>+<alt>+c": self.start_calibration,
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

    # --- calibration --------------------------------------------------------
    def start_calibration(self) -> None:
        """Begin redrawing the active area. Safe to call from the hotkey thread.

        Nothing is done here beyond flipping the state: the run itself is fed
        by the main loop, which is the only place a frame exists.
        """
        if self.calibration is not None:
            # The same hotkey both starts and calls it off: while one is running
            # it is the only key you will be reaching for.
            self.cancel_calibration()
            return
        self.engine.reset()  # drop a drag or a held key before we stop dispatching
        self._both_fists.reset()
        self.calibration = Calibration()
        self._events.append((time.monotonic(), "CALIBRATION"))
        print(
            "Calibration: hold your hand still to start, trace a rectangle in "
            "the air, then hold still again to finish. Ctrl+Alt+C to cancel.",
            flush=True,
        )

    def cancel_calibration(self) -> None:
        self.calibration = None
        self._calib_progress = None
        self._both_fists.reset()
        print("Calibration cancelled, the active area is unchanged.", flush=True)

    def _idle_states(self, feats) -> list[EngineState]:
        """Placeholder states while calibrating: the HUD still wants a row."""
        return [
            EngineState(
                hand=slot,
                mode="CALIBRATING" if feats.get(slot) else "NO HAND",
                detail=self._calib_progress.text if self._calib_progress else "",
            )
            for slot in hand.HAND_SLOTS
        ]

    def _calibration_point(self, feats) -> tuple[float, float] | None:
        """The hand the rectangle is traced with: the dominant one if in view.

        The palm, not the fingertip - the same point the cursor rides on when
        it is anchored, so the corners you draw are the corners you will reach.
        """
        for slot in (self.engine.dominant, hand.other_hand(self.engine.dominant)):
            f = feats.get(slot)
            if f is not None:
                return f.palm_point
        return None

    def _run_calibration(self, feats, now: float) -> None:
        """One frame of the calibration, and the applying of its result."""
        calib = self.calibration
        self._calib_progress = calib.update(self._calibration_point(feats), now)
        if not calib.done:
            return

        if calib.result is not None:
            self._apply_area(calib.result)
        else:
            print(f"Calibration: {calib.message}.", flush=True)
        self._events.append((now, calib.message.upper()[:18]))
        self.calibration = None
        self._both_fists.reset()
        # The result is left on screen for a moment rather than vanishing on the
        # very frame it is decided, which nobody would see.
        self._calib_hold_until = now + 2.0

    def _apply_area(self, rect: tuple[float, float, float, float]) -> None:
        """The new area onto the running Config, and into the file if we can."""
        c = self.cfg
        c.active_x_min, c.active_y_min, c.active_x_max, c.active_y_max = rect
        print(
            f"Calibrated active area: x {c.active_x_min:.3f}-{c.active_x_max:.3f}  "
            f"y {c.active_y_min:.3f}-{c.active_y_max:.3f}",
            flush=True,
        )
        try:
            path = bindings_mod.save_settings(
                self.config_path,
                {
                    "active_x_min": round(c.active_x_min, 4),
                    "active_x_max": round(c.active_x_max, 4),
                    "active_y_min": round(c.active_y_min, 4),
                    "active_y_max": round(c.active_y_max, 4),
                },
            )
            print(f"Saved to {path}.", flush=True)
        except (OSError, ValueError) as exc:
            # A read-only folder is no reason to lose the calibration: it is
            # live either way, it just will not outlast this run.
            print(f"Could not save the calibration ({exc}), it holds until you quit.", flush=True)

    def toggle_overlay(self) -> None:
        if self.overlay is not None:
            self.overlay.set_visible(not self.overlay.visible)

    # --- main loop ----------------------------------------------------------
    def run(self) -> int:
        model_path = ensure_model(self.cfg.model_path) if self.cfg.model_path else ensure_model()

        cap = self._open_camera()
        if cap is None:
            return 1

        detector = HandDetector(
            model_path,
            num_hands=max(1, min(2, self.cfg.num_hands)),
            min_detection_confidence=self.cfg.min_detection_confidence,
            min_tracking_confidence=self.cfg.min_tracking_confidence,
            mirrored=self.cfg.mirror,
        )
        if self.cfg.show_preview:
            # Created up front: the loop checks the window before showing a
            # frame, and there has to be one to check.
            cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
        if self.cfg.overlay:
            self.overlay = overlay.create()
        hotkeys = self._make_hotkeys()
        hotkeys.start()
        print(
            "Celebrimbor started. Ctrl+Alt+Q to quit, Ctrl+Alt+P to pause, "
            "Ctrl+Alt+Space to move the pointer to the other hand."
        )
        print(
            "Ctrl+Alt+C (or both fists held still for "
            f"{int(self._both_fists.seconds)}s) to redraw the active area."
        )
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
                hands = detector.detect(rgb, int(now * 1000))

                dt = now - last_t
                last_t = now
                if dt > 0:
                    self._fps = 0.9 * self._fps + 0.1 * (1.0 / dt)

                feats = {obs.slot: hand.extract(obs.landmarks) for obs in hands}

                if self.calibration is not None:
                    # The engine does not run at all while calibrating: the hand
                    # drawing the rectangle would be clicking and sliding its
                    # way round the frame otherwise.
                    states = self._idle_states(feats)
                    self._arming = 0.0
                    self._run_calibration(feats, now)
                else:
                    states = self.engine.update(feats, now)
                    self._record(states, now)
                    self._arming = self._both_fists.update(states, now)
                    if self._arming >= 1.0:
                        self.start_calibration()
                    if now > self._calib_hold_until:
                        self._calib_progress = None

                if self.overlay is not None:
                    # The calibration line goes where the events go: the card is
                    # two hands and two lines, and during a calibration the
                    # instruction is the only thing worth either of them.
                    notes = [e for _, e in self._events]
                    if self._calib_progress is not None:
                        notes = [f"CAL {self._calib_progress.text}"]
                    self.overlay.update(
                        states,
                        notes,
                        self.engine.dominant,
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
                    self._draw(frame, hands, states)
                    cv2.imshow(WINDOW, frame)
                    if not self._handle_keys():
                        break
        except KeyboardInterrupt:
            pass
        finally:
            self.engine.reset()
            hotkeys.stop()
            detector.close()
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
            if self.calibration is None:
                self.start_calibration()
            else:
                self.cancel_calibration()
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

    def _draw(self, frame, hands, states: list[EngineState]) -> None:
        h, w = frame.shape[:2]

        if self.show_skeleton:
            for obs in hands:
                self._draw_hand(frame, obs.landmarks, w, h)

        c = self.cfg
        cv2.rectangle(
            frame,
            (int(c.active_x_min * w), int(c.active_y_min * h)),
            (int(c.active_x_max * w), int(c.active_y_max * h)),
            _GREY,
            1,
        )

        if self._calib_progress is not None:
            self._draw_calibration(frame, w, h)

        # The point actually driving the cursor: during a pinch it moves from
        # the index finger to the palm, and you can see it here.
        for state in states:
            if state.ref_point is None:
                continue
            rp = (int(state.ref_point[0] * w), int(state.ref_point[1] * h))
            anchored = state.cursor_source == "palm"
            cv2.circle(frame, rp, 11, _RED if anchored else _GREEN, 2)
            cv2.circle(frame, rp, 2, _RED if anchored else _GREEN, -1)

        self._draw_header(frame, w, states)

        # The 15-second hold has to be visible from the first second, or holding
        # two fists still would just look like nothing happening.
        if self._arming > 0.0:
            self._progress(frame, "CALIBRATE IN", 1.0 - self._arming, w // 2 - 120, 80)

        # One column of readouts per hand, on its own side of the frame: the
        # left hand is the one on the left of the mirrored image.
        for state in states:
            x = 10 if state.hand == "left" else w // 2
            self._draw_readouts(frame, state, x, h)

        for i, (_, ev) in enumerate(reversed(self._events[-3:])):
            cv2.putText(
                frame, ev, (w - 200, 90 + i * 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, _YELLOW, 2
            )

        cv2.putText(
            frame,
            "q=quit p=pause h=skeleton d=pointer o=overlay c=calibrate | Ctrl+Alt+Q/P/Space/C",
            (10, h - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            _GREY,
            1,
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

    def _draw_calibration(self, frame, w: int, h: int) -> None:
        """The calibration takeover: dimmed frame, the box drawn so far, a bar.

        The frame is dimmed on purpose. It is the one unmistakable sign that the
        gestures are not being listened to right now, which matters more here
        than anywhere else: you are about to wave your hand around in poses that
        would normally be clicking things.
        """
        prog = self._calib_progress
        overlay_frame = frame.copy()
        cv2.rectangle(overlay_frame, (0, 0), (w, h), (0, 0, 0), -1)
        cv2.addWeighted(overlay_frame, 0.35, frame, 0.65, 0, frame)

        # The path traced so far, and the box it currently spans.
        calib = self.calibration
        trace = calib.path if calib is not None else ()
        if len(trace) > 1:
            pts = [(int(x * w), int(y * h)) for x, y in trace]
            for a, b in zip(pts, pts[1:]):
                cv2.line(frame, a, b, _YELLOW, 2)
        if prog.rect is not None:
            x0, y0, x1, y1 = prog.rect
            done = calib is None  # the accepted rectangle, once it is settled
            cv2.rectangle(
                frame,
                (int(x0 * w), int(y0 * h)),
                (int(x1 * w), int(y1 * h)),
                _GREEN if done else _YELLOW,
                2 if done else 1,
            )

        cv2.rectangle(frame, (0, 0), (w, 56), (25, 25, 25), -1)
        cv2.putText(
            frame,
            f"CALIBRATION: {prog.text}",
            (10, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            _YELLOW,
            2,
        )
        cv2.putText(
            frame,
            "trace the rectangle you want to reach  |  Ctrl+Alt+C to cancel",
            (10, 47),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            _GREY,
            1,
        )
        if prog.ratio > 0.0:
            self._progress(frame, "HOLD", prog.ratio, 10, h - 60)

    def _draw_header(self, frame, w: int, states: list[EngineState]) -> None:
        """Two rows: the mode of each hand, and underneath its detail."""
        color = _GREEN if self.engine.enabled else _RED
        cv2.rectangle(frame, (0, 0), (w, 56), (25, 25, 25), -1)
        for state in states:
            x = 10 if state.hand == "left" else w // 2
            # The dot marks the hand holding the pointer when both are in view.
            initial = state.hand[0].upper()
            marker = "*" if state.hand == self.engine.dominant else " "
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
