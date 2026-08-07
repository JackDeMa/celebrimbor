"""Ciclo principale: cattura webcam -> landmark -> gesti -> mouse."""

import time

import cv2
from pynput import keyboard

from . import hand
from .config import Config
from .controller import MouseActuator
from .detector import HAND_CONNECTIONS, HandDetector, ensure_model
from .engine import EngineState, GestureEngine

WINDOW = "AirTouch"

_GREEN = (120, 230, 120)
_GREY = (170, 170, 170)
_RED = (80, 80, 240)
_YELLOW = (60, 220, 240)
_WHITE = (245, 245, 245)


class AirTouchApp:
    def __init__(self, cfg: Config, bindings: dict | None = None):
        self.cfg = cfg
        self.mouse = MouseActuator(
            click_cooldown=cfg.click_cooldown, dry_run=cfg.dry_run
        )
        self.engine = GestureEngine(cfg, self.mouse, bindings)
        self.running = True
        self.show_overlay = True
        self._fps = 0.0
        self._events: list[tuple[float, str]] = []
        self._last_mode = ""

    # --- hotkey globali ----------------------------------------------------
    def _make_hotkeys(self) -> keyboard.GlobalHotKeys:
        return keyboard.GlobalHotKeys(
            {
                "<ctrl>+<alt>+q": self.stop,
                "<ctrl>+<alt>+p": self.toggle_pause,
            }
        )

    def stop(self) -> None:
        self.running = False

    def toggle_pause(self) -> None:
        self.engine.set_enabled(not self.engine.enabled)

    # --- ciclo principale ---------------------------------------------------
    def run(self) -> int:
        model_path = ensure_model(self.cfg.model_path) if self.cfg.model_path else ensure_model()

        cap = self._open_camera()
        if cap is None:
            return 1

        detector = HandDetector(
            model_path,
            num_hands=1,
            min_detection_confidence=self.cfg.min_detection_confidence,
            min_tracking_confidence=self.cfg.min_tracking_confidence,
        )
        hotkeys = self._make_hotkeys()
        hotkeys.start()
        print("AirTouch avviato. Ctrl+Alt+Q per uscire, Ctrl+Alt+P per la pausa.")
        if self.cfg.dry_run:
            print("DRY-RUN: i gesti vengono riconosciuti ma il mouse non si muove.")

        last_t = time.monotonic()
        try:
            while self.running:
                ok, frame = cap.read()
                if not ok:
                    print("Frame non letto dalla webcam, esco.")
                    break

                if self.cfg.mirror:
                    frame = cv2.flip(frame, 1)

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                now = time.monotonic()
                landmarks = detector.detect(rgb, int(now * 1000))

                dt = now - last_t
                last_t = now
                if dt > 0:
                    self._fps = 0.9 * self._fps + 0.1 * (1.0 / dt)

                feats = hand.extract(landmarks) if landmarks else None
                state = self.engine.update(feats, now)
                self._record(state, now)

                if not self.cfg.show_preview:
                    self._log(state)
                else:
                    self._draw(frame, landmarks, state)
                    cv2.imshow(WINDOW, frame)
                    if not self._handle_keys():
                        break
                    if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                        break
        except KeyboardInterrupt:
            pass
        finally:
            self.engine.reset()
            hotkeys.stop()
            detector.close()
            cap.release()
            cv2.destroyAllWindows()
        return 0

    # --- webcam -------------------------------------------------------------
    def _open_camera(self):
        # DirectShow su Windows apre molto piu' in fretta del backend MSMF.
        backend = getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY)
        cap = cv2.VideoCapture(self.cfg.camera_index, backend)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.cfg.camera_index)
        if not cap.isOpened():
            print(f"Impossibile aprire la webcam {self.cfg.camera_index}.")
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.frame_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.frame_height)
        # Senza questa richiesta esplicita la webcam puo' restare a 10 fps.
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
            self.show_overlay = not self.show_overlay
        return True

    # --- console (quando l'anteprima e' disattivata) -------------------------
    def _log(self, state: EngineState) -> None:
        if state.mode != self._last_mode:
            self._last_mode = state.mode
            print(f"[{self._fps:4.1f} fps] {state.mode} {state.detail}".rstrip(), flush=True)
        for ev in state.events:
            print(f"  -> {ev}", flush=True)

    # --- HUD -----------------------------------------------------------------
    def _record(self, state: EngineState, now: float) -> None:
        for ev in state.events:
            self._events.append((now, ev))
        self._events = [(t, e) for t, e in self._events if now - t < 1.2]

    def _draw(self, frame, landmarks, state: EngineState) -> None:
        h, w = frame.shape[:2]

        if landmarks is not None and self.show_overlay:
            self._draw_hand(frame, landmarks, w, h)

        c = self.cfg
        cv2.rectangle(
            frame,
            (int(c.active_x_min * w), int(c.active_y_min * h)),
            (int(c.active_x_max * w), int(c.active_y_max * h)),
            _GREY,
            1,
        )

        # Il punto che sta davvero pilotando il cursore: durante il pinch passa
        # dall'indice al palmo, e qui si vede.
        if state.ref_point is not None:
            rp = (int(state.ref_point[0] * w), int(state.ref_point[1] * h))
            anchored = state.cursor_source == "palmo"
            cv2.circle(frame, rp, 11, _RED if anchored else _GREEN, 2)
            cv2.circle(frame, rp, 2, _RED if anchored else _GREEN, -1)

        color = _RED if not self.engine.enabled else _GREEN
        cv2.rectangle(frame, (0, 0), (w, 34), (25, 25, 25), -1)
        cv2.putText(frame, state.mode, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        detail = state.detail
        if state.cursor_source:
            detail = f"[{state.cursor_source}] {detail}".rstrip()
        if detail:
            cv2.putText(
                frame, detail, (200, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.5, _GREY, 1
            )
        if self.cfg.dry_run:
            cv2.putText(
                frame, "DRY-RUN", (w - 200, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6, _YELLOW, 2
            )
        cv2.putText(
            frame,
            f"{self._fps:4.1f} fps",
            (w - 95, 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            _GREY,
            1,
        )

        # barre dei pinch: la tacca rossa e' la soglia di scatto, quella grigia
        # il punto in cui il cursore si ancora al palmo
        labels = {"index": "IND", "middle": "MED", "ring": "ANU"}
        shown = [f for f in labels if f in state.pinches]
        for i, finger in enumerate(shown):
            y = h - 40 - 22 * (len(shown) - 1 - i)
            self._bar(
                frame,
                labels[finger],
                state.pinches[finger],
                y,
                anchor_at=state.anchor_at.get(finger),
            )

        if state.fist_hold_progress > 0.0:
            self._progress(frame, "PUGNO FERMO", state.fist_hold_progress, h - 40 - 22 * len(shown))

        for i, (_, ev) in enumerate(reversed(self._events[-3:])):
            cv2.putText(
                frame, ev, (w - 200, 62 + i * 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, _YELLOW, 2
            )

        cv2.putText(
            frame,
            "q=esci  p=pausa  h=overlay  |  Ctrl+Alt+Q, Ctrl+Alt+P globali",
            (10, h - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            _GREY,
            1,
        )

    def _draw_hand(self, frame, landmarks, w: int, h: int) -> None:
        pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
        for a, b in HAND_CONNECTIONS:
            cv2.line(frame, pts[a], pts[b], _WHITE, 2)
        for i, p in enumerate(pts):
            # evidenzia pollice, indice, medio e anulare: le dita che comandano
            highlight = i in (hand.THUMB_TIP, hand.INDEX_TIP, hand.MIDDLE_TIP, hand.RING_TIP)
            cv2.circle(frame, p, 6 if highlight else 3, _YELLOW if highlight else _GREEN, -1)

    def _progress(self, frame, label: str, ratio: float, y: int) -> None:
        x0, width = 50, 160
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
        self, frame, label: str, value: float, y: int, anchor_at: float | None = None
    ) -> None:
        full = 1.2  # valore di fondo scala della barra
        ratio = min(max(value / full, 0.0), 1.0)
        x0, width = 50, 110
        active = value < self.cfg.pinch_on
        cv2.putText(frame, label, (10, y + 11), cv2.FONT_HERSHEY_SIMPLEX, 0.5, _GREY, 1)
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
