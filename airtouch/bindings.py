"""Caricamento di gestures.json: gesto -> azione.

Il file accetta commenti su righe che iniziano con `//`, che JSON non prevede
ma che rendono la configurazione leggibile.
"""

import json
from dataclasses import fields
from pathlib import Path

from .actions import Action, Click, Drag, Hotkey, MoveCursor, NoAction, RepeatAxis, Scroll
from .config import Config
from .gestures import GESTURE_KINDS

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "gestures.json"

DEFAULT_TEXT = """{
  // Configurazione dei gesti di AirTouch.
  // Le righe che iniziano con // sono commenti (estensione locale a JSON).

  // Sovrascrive i parametri di airtouch/config.py. Lascia {} per i default.
  "settings": {
    "fist_hold_seconds": 5.0,
    "swipe_min_travel": 1.1,
    "drag_hold": 0.45
  },

  // Gesto -> azione. Metti "none" per disattivare un gesto.
  "bindings": {
    // --- mano che punta ---
    "point_move": "move_cursor",
    "pinch_index_tap": "left_click",
    "pinch_index_hold": "drag",
    "pinch_middle_tap": "right_click",

    // --- pugno chiuso che scatta in una direzione ---
    "fist_swipe_left": "ctrl+win+left",
    "fist_swipe_right": "ctrl+win+right",
    "fist_swipe_up": "alt+tab",
    "fist_swipe_down": "alt+shift+tab",

    // --- pugno chiuso e fermo per 5 secondi ---
    "fist_hold": "play_pause",

    // --- indice e medio aperti, mano che scorre ---
    "two_finger_vertical": { "action": "scroll", "gain": 55 },
    "two_finger_horizontal": { "action": "volume", "gain": 14 }
  }
}
"""

# Scorciatoie utilizzabili come stringa al posto di un oggetto.
ALIASES = {
    "none": lambda: NoAction(),
    "move_cursor": lambda: MoveCursor(),
    "left_click": lambda: Click("left"),
    "right_click": lambda: Click("right"),
    "middle_click": lambda: Click("middle"),
    "double_click": lambda: Click("left", count=2),
    "drag": lambda: Drag(),
    "scroll": lambda: Scroll(),
    "scroll_h": lambda: Scroll(horizontal=True),
    "volume": lambda: _volume_axis(),
}


class ConfigError(ValueError):
    pass


def _volume_axis(gain: float = 14.0) -> Action:
    return RepeatAxis(
        positive=Hotkey("volume_up"),
        negative=Hotkey("volume_down"),
        gain=gain,
        label="volume",
    )


def strip_comments(text: str) -> str:
    """Toglie le righe che iniziano con // (le altre restano intatte)."""
    return "\n".join(
        "" if line.lstrip().startswith("//") else line for line in text.splitlines()
    )


def build_action(spec) -> Action:
    """Da una voce del JSON all'oggetto azione."""
    if spec is None:
        return NoAction()

    if isinstance(spec, str):
        alias = ALIASES.get(spec.strip().lower())
        if alias:
            return alias()
        # Tutto il resto e' una combinazione di tasti: "alt+tab", "play_pause".
        return Hotkey(spec)

    if not isinstance(spec, dict):
        raise ConfigError(f"azione non valida: {spec!r}")

    kind = str(spec.get("action", "")).strip().lower()
    if not kind:
        raise ConfigError(f"manca il campo \"action\" in {spec!r}")

    if kind in ("hotkey", "key"):
        combo = spec.get("keys") or spec.get("key")
        if not combo:
            raise ConfigError(f"manca \"keys\" in {spec!r}")
        return Hotkey(str(combo))

    if kind == "scroll":
        return Scroll(
            gain=float(spec.get("gain", 55.0)),
            invert=bool(spec.get("invert", False)),
            horizontal=bool(spec.get("horizontal", False)),
        )

    if kind == "volume":
        return _volume_axis(gain=float(spec.get("gain", 14.0)))

    if kind == "click":
        return Click(str(spec.get("button", "left")), int(spec.get("count", 1)))

    if kind == "axis":
        return RepeatAxis(
            positive=build_action(spec.get("positive")),
            negative=build_action(spec.get("negative")),
            gain=float(spec.get("gain", 14.0)),
            max_rate=float(spec.get("max_rate", 25.0)),
            label=str(spec.get("label", "asse")),
        )

    alias = ALIASES.get(kind)
    if alias:
        return alias()
    raise ConfigError(f"azione sconosciuta: {kind!r}")


def apply_settings(cfg: Config, settings: dict) -> list[str]:
    """Applica la sezione settings su Config, segnalando le chiavi ignote."""
    valid = {f.name: f.type for f in fields(Config)}
    warnings = []
    for key, value in settings.items():
        if key not in valid:
            warnings.append(f"impostazione sconosciuta ignorata: {key!r}")
            continue
        current = getattr(cfg, key)
        try:
            if isinstance(current, bool):
                value = bool(value)
            elif isinstance(current, int):
                value = int(value)
            elif isinstance(current, float):
                value = float(value)
        except (TypeError, ValueError):
            warnings.append(f"valore non valido per {key!r}: {value!r}")
            continue
        setattr(cfg, key, value)
    return warnings


def load(path: Path | str | None, cfg: Config) -> tuple[dict[str, Action], list[str]]:
    """Carica il file (creandolo se manca) e restituisce (bindings, avvisi)."""
    path = Path(path) if path else DEFAULT_PATH
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_TEXT, encoding="utf-8")
        print(f"Creato {path} con la configurazione predefinita.")

    raw = strip_comments(path.read_text(encoding="utf-8"))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: JSON non valido alla riga {exc.lineno}: {exc.msg}") from exc

    warnings = apply_settings(cfg, data.get("settings") or {})

    bindings: dict[str, Action] = {}
    for name, spec in (data.get("bindings") or {}).items():
        kind = GESTURE_KINDS.get(name)
        if kind is None:
            warnings.append(
                f"gesto sconosciuto ignorato: {name!r} "
                f"(disponibili: {', '.join(sorted(GESTURE_KINDS))})"
            )
            continue
        try:
            action = build_action(spec)
        except (ConfigError, ValueError) as exc:
            warnings.append(f"{name}: {exc}")
            continue
        if kind not in action.kinds:
            warnings.append(
                f"{name}: il gesto e' di tipo '{kind}' ma l'azione "
                f"'{action.label}' non lo supporta, ignorata"
            )
            continue
        bindings[name] = action

    return bindings, warnings
