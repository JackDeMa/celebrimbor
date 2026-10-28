"""Connects the gesture recogniser to the actions configured in the JSON.

Each hand has its own recogniser and its own bindings, so the two never share
any state: a pinch on the left hand cannot cancel a drag on the right one. The
only thing they do share is the mouse, and there is just one cursor: which hand
is allowed to move it is decided here, frame by frame.

With the face pointer ("pointer": "head" or "eyes") the face has a recogniser
and bindings of its own, under the "face" slot, and holds the cursor for good:
the hands keep clicking, scrolling and swiping, but never move it.
"""

from dataclasses import dataclass, field

from .actions import Action, Backend
from .config import Config
from .controller import MouseActuator
from .face import FACE_SLOT, FaceFeatures
from .face_gestures import FACE_POINTERS, FaceRecognizer
from .gestures import GestureRecognizer, Recognition
from .hand import HAND_SLOTS, HandFeatures, other_hand



@dataclass
class EngineState:
    """Snapshot of one hand, for the HUD and the console log."""

    hand: str = ""
    mode: str = "NO HAND"
    detail: str = ""
    pinches: dict[str, float] = field(default_factory=dict)
    fist_hold_progress: float = 0.0
    cursor_source: str = ""
    ref_point: tuple[float, float] | None = None
    anchor_at: dict[str, float] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)
    # face only: how open each eye is (1 = habitual) and the calibration
    eyes: dict[str, float] = field(default_factory=dict)
    calibration: float = 0.0


class GestureEngine:
    def __init__(
        self,
        cfg: Config,
        actuator: MouseActuator,
        bindings: dict[str, dict[str, Action]] | None = None,
    ):
        self.cfg = cfg
        self.mouse = actuator
        self.backend = Backend(actuator, dry_run=getattr(actuator, "dry_run", False))
        bindings = bindings or {}
        # Distinct Action objects per hand, never shared: several of them carry
        # state (a drag in progress, the accumulated volume steps) and one hand
        # must not be able to unwind the other's.
        self.bindings: dict[str, dict[str, Action]] = {
            slot: dict(bindings.get(slot) or {}) for slot in (*HAND_SLOTS, FACE_SLOT)
        }
        self.recognizers = {
            slot: GestureRecognizer(cfg, active=set(self.bindings[slot]))
            for slot in HAND_SLOTS
        }
        if cfg.pointer not in ("hand", *FACE_POINTERS):
            print(
                f"Warning: unknown pointer {cfg.pointer!r} "
                f"(valid: hand, head, eyes), falling back to 'hand'."
            )
            cfg.pointer = "hand"
        self.face: FaceRecognizer | None = None
        if cfg.pointer in FACE_POINTERS:
            self.face = FaceRecognizer(cfg, active=set(self.bindings[FACE_SLOT]))
        self.enabled = True
        self.dominant = cfg.dominant_hand if cfg.dominant_hand in HAND_SLOTS else "right"
        if cfg.dominant_hand not in HAND_SLOTS:
            print(
                f"Warning: unknown dominant_hand {cfg.dominant_hand!r} "
                f"(valid: left, right), falling back to 'right'."
            )
        self._cursor_hand: str | None = None

    # ------------------------------------------------------------------
    def swap_dominant(self) -> str:
        """Hand the pointer over to the other hand, from the next frame on."""
        self.dominant = other_hand(self.dominant)
        self._cursor_hand = None
        return self.dominant

    @property
    def pointer_holder(self) -> str:
        """Who the cursor belongs to, for the HUD: the face or a hand."""
        return FACE_SLOT if self.face is not None else self.dominant

    def recalibrate(self) -> bool:
        """Take the face's neutral pose again. False without a face pointer."""
        if self.face is None:
            return False
        self.face.recalibrate()
        return True

    # ------------------------------------------------------------------
    def set_enabled(self, value: bool) -> None:
        self.enabled = value
        if not value:
            self.reset()

    def reset(self) -> None:
        """Bring everything back to rest: no key held, filters cleared."""
        for slot, recognizer in self.recognizers.items():
            for event in recognizer.reset():
                self._dispatch(slot, event)
        if self.face is not None:
            for event in self.face.reset():
                self._dispatch(FACE_SLOT, event)
        for binds in self.bindings.values():
            for action in binds.values():
                action.reset(self.backend)
        self.mouse.release_all()
        self._cursor_hand = None

    # ------------------------------------------------------------------
    def update(
        self,
        feats: dict[str, HandFeatures | None],
        t: float,
        face: FaceFeatures | None = None,
    ) -> list[EngineState]:
        """One frame: the features of every visible hand in, one state each out.

        Slots with no hand in front of the camera get `None`, which their
        recogniser needs in order to run its own grace period and let go of
        whatever it was holding. The same goes for `face`, whose state comes
        last, and only with the face pointer on.
        """
        if not self.enabled:
            states = [
                EngineState(
                    hand=slot,
                    mode="PAUSED",
                    detail="Ctrl+Alt+P to resume",
                    pinches=feats[slot].pinches if feats.get(slot) else {},
                )
                for slot in HAND_SLOTS
            ]
            if self.face is not None:
                states.append(
                    EngineState(hand=FACE_SLOT, mode="PAUSED", detail="Ctrl+Alt+P to resume")
                )
            return states

        self.backend.now = t
        results: dict[str, Recognition] = {
            slot: self.recognizers[slot].update(feats.get(slot), t)
            for slot in HAND_SLOTS
        }
        owner = self._cursor_owner(results)

        states = []
        for slot in HAND_SLOTS:
            result = results[slot]
            labels: list[str] = []
            for event in result.events:
                if event.kind == "cursor" and slot != owner:
                    continue
                label = self._dispatch(slot, event)
                if label:
                    labels.append(label)
            states.append(
                EngineState(
                    hand=slot,
                    mode=result.mode,
                    detail=result.detail,
                    pinches=feats[slot].pinches if feats.get(slot) else {},
                    fist_hold_progress=result.fist_hold_progress,
                    cursor_source=result.cursor_source,
                    ref_point=result.ref_point,
                    anchor_at=result.anchor_at,
                    events=labels,
                )
            )

        if self.face is not None:
            result = self.face.update(face, t)
            labels = [
                label
                for label in (self._dispatch(FACE_SLOT, e) for e in result.events)
                if label
            ]
            states.append(
                EngineState(
                    hand=FACE_SLOT,
                    mode=result.mode,
                    detail=result.detail,
                    ref_point=result.ref_point,
                    events=labels,
                    eyes=result.eyes,
                    calibration=result.calibration,
                )
            )
        return states

    # ------------------------------------------------------------------
    def _cursor_owner(self, results: dict[str, Recognition]) -> str | None:
        """The one hand allowed to move the cursor this frame.

        There is a single pointer: if both hands were pointing at once they
        would drag it back and forth every frame. With both in view the
        dominant hand holds it; on its own, either hand does, so you can point
        with whichever one you have free.

        The one exception is a drag in progress: the pointer is not taken away
        from the hand holding the mouse button down, or bringing the other hand
        into frame would fling whatever you are dragging across the screen.

        With the face pointer on, no hand ever gets it.
        """
        if self.face is not None:
            return None
        candidates = [
            slot
            for slot in HAND_SLOTS
            if any(
                event.kind == "cursor" and event.name in self.bindings[slot]
                for event in results[slot].events
            )
        ]
        if not candidates:
            self._cursor_hand = None
        elif self.mouse.dragging and self._cursor_hand in candidates:
            pass
        elif self.dominant in candidates:
            self._cursor_hand = self.dominant
        elif self._cursor_hand not in candidates:
            self._cursor_hand = candidates[0]
        return self._cursor_hand

    # ------------------------------------------------------------------
    def _dispatch(self, slot: str, event) -> str | None:
        action = self.bindings[slot].get(event.name)
        if action is None:
            return None
        if event.kind == "cursor":
            return action.cursor(self.backend, *event.value)
        if event.kind == "trigger":
            return action.trigger(self.backend)
        if event.kind == "hold":
            return action.hold(self.backend, bool(event.value))
        if event.kind == "axis":
            return action.axis(self.backend, float(event.value))
        return None
