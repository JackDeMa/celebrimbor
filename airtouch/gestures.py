"""Riconoscimento dei gesti: da HandFeatures a eventi con un nome.

Il riconoscitore non tocca il mouse: produce solo eventi. Chi li traduce in
azioni e' il dispatcher, guidato dal file di configurazione.

Tipi di evento (kind):
    cursor   posizione normalizzata (0..1) sullo schermo
    trigger  evento istantaneo, scatta una volta sola
    hold     stato acceso/spento
    axis     movimento continuo, porta un delta per frame
"""

from collections import deque
from dataclasses import dataclass, field

from .config import Config
from .filters import PointFilter
from .hand import PINCH_FINGERS, HandFeatures, Hysteresis

# Gesto -> tipo di evento che produce. Serve anche a validare il JSON.
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
}


class ClosingDetector:
    """Dice se le dita si stanno chiudendo, rispetto a come stanno di solito.

    Una soglia assoluta non funziona: c'e' chi tiene la mano ben aperta e chi
    la tiene raccolta, e con le dita gia' vicine a riposo una soglia fissa
    resterebbe scattata per sempre. Qui il riferimento e' il massimo osservato
    negli ultimi `window` secondi, cioe' l'apertura abituale di quel momento.
    Mentre un pinch e' in corso il riferimento si congela, altrimenti un drag
    lungo se lo mangerebbe e l'aggancio cadrebbe a meta' trascinamento.
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
        """Distanza sotto la quale scatta l'aggancio, per l'anteprima."""
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
    """Esito di un frame: eventi da eseguire piu' informazioni per l'HUD."""

    events: list[GestureEvent] = field(default_factory=list)
    mode: str = "NO HAND"
    detail: str = ""
    fist_hold_progress: float = 0.0  # 0..1, avanzamento del pugno fermo
    cursor_source: str = ""          # "indice" o "palmo"
    ref_point: tuple[float, float] | None = None  # riferimento, in coordinate immagine
    anchor_at: dict[str, float] = field(default_factory=dict)  # soglie di aggancio


class GestureRecognizer:
    def __init__(self, cfg: Config, active: set[str] | None = None):
        self.cfg = cfg
        self.cursor_filter = PointFilter(cfg.min_cutoff, cfg.beta, cfg.d_cutoff)

        # Tutti i pinch vengono misurati, anche quelli senza azione collegata:
        # servono comunque a disambiguare (toccando l'anulare col pollice anche
        # il medio finisce vicino, e senza confronto partirebbe un click sbagliato).
        # Gli eventi pero' escono solo per i pinch collegati a qualcosa.
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

        # ancoraggio del cursore
        self._closing = {
            f: ClosingDetector(cfg.anchor_window, cfg.anchor_ratio_on, cfg.anchor_ratio_off)
            for f in self.bound
        }
        self._was_anchored = False
        self._anchor_name = self._valid_anchor(cfg.anchor_point)
        self._offset = (0.0, 0.0)
        self._offset_from: float | None = None  # istante del cambio, per il riassorbimento
        self._offset_decays = False
        self._prev_ref: tuple[float, float] | None = None

        # pugno
        self._fist_track: deque[tuple[float, float, float]] = deque()  # (t, x, y)
        self._fist_still_since: float | None = None
        self._fist_hold_done = False
        self._swipe_block_until = 0.0

        # due dita
        self._two_origin: tuple[float, float] | None = None
        self._two_last: tuple[float, float] | None = None
        self._two_axis: str | None = None

        self._missing = 0

    # ------------------------------------------------------------------
    def reset(self) -> list[GestureEvent]:
        """Riporta tutto a riposo, restituendo gli eventi di chiusura."""
        events = self._release_pinches()
        self.cursor_filter.reset()
        self._fist_track.clear()
        self._fist_still_since = None
        self._fist_hold_done = False
        self._two_origin = None
        self._two_last = None
        self._two_axis = None
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

        # --- pugno: swipe direzionali e pugno fermo -----------------------
        if feats.is_fist:
            self._end_two_finger()
            self._forget_reference()
            return self._fist(feats, t)
        self._reset_fist()

        # --- due dita: assi continui ---------------------------------------
        if self._is_two_finger(feats):
            self._forget_reference()
            events = self._release_pinches()
            events += self._two_finger(feats)
            return Recognition(
                events=events,
                mode="DUE DITA",
                detail=self._two_axis or "scegli la direzione",
            )
        self._end_two_finger()

        # --- pinch: click e drag --------------------------------------------
        events = self._pinches(feats, t)

        ref, source = self._reference(feats, t)
        x, y = self.cursor_filter(ref[0], ref[1], t)
        events.append(GestureEvent("point_move", "cursor", self._to_screen(x, y)))

        holding = [f for f in self.bound if self._holding[f]]
        closed = [f for f in self.bound if self._pinch[f].state]
        if holding:
            mode, detail = "DRAG", holding[0]
        elif closed:
            mode, detail = "PINCH", f"{closed[0]}: rilascia per il click"
        else:
            mode, detail = "PUNTAMENTO", ""
        return Recognition(
            events=events,
            mode=mode,
            detail=detail,
            cursor_source=source,
            ref_point=ref,
            anchor_at={f: d.threshold for f, d in self._closing.items()},
        )

    # --- riferimento del cursore -------------------------------------------
    def _valid_anchor(self, name: str) -> str:
        valid = ("palm_outer", "palm_center", "pinky_mcp", "index_mcp", "wrist")
        if name in valid:
            return name
        print(
            f"Attenzione: anchor_point {name!r} sconosciuto "
            f"(validi: {', '.join(valid)}), uso 'palm_outer'."
        )
        return "palm_outer"

    def _reference(self, feats: HandFeatures, t: float) -> tuple[tuple[float, float], str]:
        """Punto che pilota il cursore, con passaggio indolore indice <-> palmo.

        Chiudendo le dita per un click la punta dell'indice si sposta sempre un
        po': appena il pinch inizia a stringersi si passa a un punto del palmo,
        che invece resta fermo. Per non far saltare il cursore nell'istante del
        cambio, lo scarto tra i due punti viene congelato e sommato al nuovo
        riferimento; al ritorno all'indice lo scarto si riassorbe in
        `anchor_blend` secondi.
        """
        # Basta che uno qualsiasi dei pinch in uso si stia stringendo.
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
            self._offset_decays = not anchored  # tornando all'indice lo scarto sfuma
            self._offset_from = t

        weight = 1.0
        if self._offset_decays and self._offset_from is not None:
            elapsed = t - self._offset_from
            weight = max(0.0, 1.0 - elapsed / max(self.cfg.anchor_blend, 1e-6))

        point = (raw[0] + self._offset[0] * weight, raw[1] + self._offset[1] * weight)
        self._prev_ref = point
        return point, ("palmo" if anchored else "indice")

    def _forget_reference(self) -> None:
        """Il cursore non e' in uso: la prossima volta si riparte da zero.

        L'apertura abituale delle dita non si dimentica: e' un dato sulla mano,
        non sul gesto in corso, e i campioni vecchi scadono da soli.
        """
        self._prev_ref = None
        self._offset = (0.0, 0.0)
        self._offset_from = None
        for detector in self._closing.values():
            detector.closed = False
        self._was_anchored = False

    # --- pugno ------------------------------------------------------------
    def _fist(self, feats: HandFeatures, t: float) -> Recognition:
        events = self._release_pinches()
        self.cursor_filter.reset()

        cfg = self.cfg
        x, y = feats.palm_point
        # Le distanze sono in "mani": cosi' la soglia non dipende da quanto
        # sei lontano dalla webcam.
        x, y = x / feats.scale, y / feats.scale

        self._fist_track.append((t, x, y))
        while self._fist_track and t - self._fist_track[0][0] > cfg.swipe_window:
            self._fist_track.popleft()

        travel_x = x - self._fist_track[0][1]
        travel_y = y - self._fist_track[0][2]

        # fermo o in movimento?
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
                return Recognition(events=events, mode="PUGNO", detail=name)

        # --- pugno fermo -------------------------------------------------
        progress = 0.0
        detail = "swipe o tieni fermo"
        if self._fist_still_since is not None:
            held = t - self._fist_still_since
            progress = min(held / cfg.fist_hold_seconds, 1.0)
            if not self._fist_hold_done and held >= cfg.fist_hold_seconds:
                self._fist_hold_done = True
                events.append(GestureEvent("fist_hold", "trigger"))
                detail = "fist_hold"
            elif not self._fist_hold_done:
                detail = f"fermo {held:.1f}/{cfg.fist_hold_seconds:.0f}s"
            else:
                detail = "gia' scattato"

        return Recognition(
            events=events, mode="PUGNO", detail=detail, fist_hold_progress=progress
        )

    def _reset_fist(self) -> None:
        self._fist_track.clear()
        self._fist_still_since = None
        self._fist_hold_done = False

    # --- due dita ----------------------------------------------------------
    def _is_two_finger(self, feats: HandFeatures) -> bool:
        return (
            feats.index_extended
            and feats.middle_extended
            and not feats.ring_extended
            and not feats.pinky_extended
        )

    def _two_finger(self, feats: HandFeatures) -> list[GestureEvent]:
        point = feats.palm_point
        if self._two_last is None:
            self._two_origin = point
            self._two_last = point
            self._two_axis = None
            return []

        if self._two_axis is None:
            # L'asse si sceglie una volta sola, alla prima escursione decisa:
            # senza questo blocco un movimento diagonale farebbe scattare
            # scroll e volume insieme.
            dx = point[0] - self._two_origin[0]
            dy = point[1] - self._two_origin[1]
            if max(abs(dx), abs(dy)) < self.cfg.axis_lock_travel:
                self._two_last = point
                return []
            self._two_axis = "orizzontale" if abs(dx) > abs(dy) else "verticale"

        events = []
        if self._two_axis == "verticale":
            delta = self._two_last[1] - point[1]  # mano in alto -> valore positivo
            name = "two_finger_vertical"
        else:
            delta = point[0] - self._two_last[0]  # mano a destra -> positivo
            name = "two_finger_horizontal"
        self._two_last = point

        if abs(delta) >= self.cfg.axis_deadzone:
            events.append(GestureEvent(name, "axis", delta))
        return events

    def _end_two_finger(self) -> None:
        self._two_origin = None
        self._two_last = None
        self._two_axis = None

    # --- pinch --------------------------------------------------------------
    def _pinches(self, feats: HandFeatures, t: float) -> list[GestureEvent]:
        events: list[GestureEvent] = []
        closed = {f: self._pinch[f].update(feats.pinches[f]) for f in self.fingers}

        # Con le dita ripiegate le punte finiscono vicine tra loro: se piu' di
        # un pinch risulta chiuso vince quello effettivamente piu' stretto, cosi'
        # i click non si confondono mai.
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
        """Chiude i pinch in sospeso senza generare click involontari."""
        events: list[GestureEvent] = []
        for finger in self.fingers:
            if self._holding[finger]:
                self._holding[finger] = False
                events.append(GestureEvent(f"pinch_{finger}_hold", "hold", False))
            self._down_at[finger] = None
            self._pinch[finger].reset()
        return events

    # --- mappatura sullo schermo ---------------------------------------------
    def _to_screen(self, x: float, y: float) -> tuple[float, float]:
        c = self.cfg
        u = (x - c.active_x_min) / max(c.active_x_max - c.active_x_min, 1e-6)
        v = (y - c.active_y_min) / max(c.active_y_max - c.active_y_min, 1e-6)
        return min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)
