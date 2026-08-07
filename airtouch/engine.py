"""Collega il riconoscitore di gesti alle azioni configurate nel JSON."""

from dataclasses import dataclass, field

from .actions import Action, Backend
from .config import Config
from .controller import MouseActuator
from .gestures import GestureRecognizer, Recognition


@dataclass
class EngineState:
    """Fotografia dello stato corrente, per l'HUD e per il log su console."""

    mode: str = "NO HAND"
    detail: str = ""
    pinches: dict[str, float] = field(default_factory=dict)
    fist_hold_progress: float = 0.0
    cursor_source: str = ""
    ref_point: tuple[float, float] | None = None
    anchor_at: dict[str, float] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)


class GestureEngine:
    def __init__(
        self,
        cfg: Config,
        actuator: MouseActuator,
        bindings: dict[str, Action] | None = None,
    ):
        self.cfg = cfg
        self.mouse = actuator
        self.backend = Backend(actuator, dry_run=getattr(actuator, "dry_run", False))
        self.bindings: dict[str, Action] = bindings or {}
        self.recognizer = GestureRecognizer(cfg, active=set(self.bindings))
        self.enabled = True

    # ------------------------------------------------------------------
    def set_enabled(self, value: bool) -> None:
        self.enabled = value
        if not value:
            self.reset()

    def reset(self) -> None:
        """Riporta tutto a riposo: nessun tasto premuto, filtri azzerati."""
        for event in self.recognizer.reset():
            self._dispatch(event)
        for action in self.bindings.values():
            action.reset(self.backend)
        self.mouse.release_all()

    # ------------------------------------------------------------------
    def update(self, feats, t: float) -> EngineState:
        if not self.enabled:
            return EngineState(
                mode="PAUSA",
                detail="Ctrl+Alt+P per riattivare",
                pinches=feats.pinches if feats else {},
            )

        self.backend.now = t
        result: Recognition = self.recognizer.update(feats, t)

        labels: list[str] = []
        for event in result.events:
            label = self._dispatch(event)
            if label:
                labels.append(label)

        return EngineState(
            mode=result.mode,
            detail=result.detail,
            pinches=feats.pinches if feats else {},
            fist_hold_progress=result.fist_hold_progress,
            cursor_source=result.cursor_source,
            ref_point=result.ref_point,
            anchor_at=result.anchor_at,
            events=labels,
        )

    # ------------------------------------------------------------------
    def _dispatch(self, event) -> str | None:
        action = self.bindings.get(event.name)
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
