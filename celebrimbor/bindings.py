"""Loading gestures.json: gesture -> action, one set per hand.

The file accepts comments on lines starting with `//`, which JSON does not
support but which keep the configuration readable.

The "bindings" section applies to both hands; the "left" and "right" sections
add to it or override it for one hand only, with "none" to switch a gesture off
on that side.
"""

import json
from dataclasses import fields
from pathlib import Path

from .actions import Action, Click, Drag, Hotkey, MoveCursor, NoAction, RepeatAxis, Scroll
from .config import Config
from .gestures import GESTURE_KINDS
from .hand import HAND_SLOTS

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "gestures.json"

DEFAULT_TEXT = """{
  // Celebrimbor gesture configuration.
  // Lines starting with // are comments (local extension to JSON).

  // Overrides the parameters in celebrimbor/config.py. Leave {} for the defaults.
  "settings": {
    "fist_hold_seconds": 1.0,
    "swipe_min_travel": 1.1,
    "drag_hold": 0.45,
    "dominant_hand": "right"
  },

  // Gesture -> action, for both hands. Use "none" to disable a gesture.
  "bindings": {
    // --- pointing hand ---
    "point_move": "move_cursor",
    "pinch_index_tap": "left_click",
    "pinch_index_hold": "drag",
    "pinch_middle_tap": "right_click",
    // Two quick index pinches already work as a double click: Windows joins
    // them itself. Uncomment for a guaranteed one, with no timing to hit.
    // "pinch_ring_tap": "double_click",

    // --- closed fist flicked in a direction ---
    "fist_swipe_left": "ctrl+win+left",
    "fist_swipe_right": "ctrl+win+right",
    "fist_swipe_up": "alt+tab",
    "fist_swipe_down": "alt+shift+tab",

    // --- fist closed and still for 5 seconds ---
    "fist_hold": "play_pause",

    // --- index and middle finger open, hand sliding ---
    "two_finger_vertical": { "action": "scroll", "gain": 55 },
    "two_finger_horizontal": { "action": "volume", "gain": 14 },

    // --- index and middle finger open, drawing a circle in the air ---
    "two_finger_circle_cw": "volume_up",
    "two_finger_circle_ccw": "volume_down",

    // --- index, middle and ring finger open ---
    // Same gesture as above with one more finger out, and its own actions.
    // Leave a name out (or set it to "none") and that slide does nothing;
    // unbind all four and the pose goes back to being just a hand in front of
    // the camera, free to point and click.
    "three_finger_vertical": { "action": "volume", "gain": 14 },
    // "three_finger_horizontal": { "action": "axis", "positive": "ctrl+win+right", "negative": "ctrl+win+left", "gain": 3, "max_rate": 2 },
    "three_finger_circle_cw": "next_track",
    "three_finger_circle_ccw": "prev_track"
  },

  // Per-hand overrides, laid on top of "bindings". Both hands get the whole set
  // above, so either one can drive the mouse when it is alone in frame; with
  // both in view the pointer goes to the dominant hand ("dominant_hand" in
  // settings, Ctrl+Alt+Space to swap it on the fly).
  // Use these two sections to specialise one hand, e.g. "pinch_index_tap":
  // "none" on the left to make sure only the right one ever clicks.
  "left": {},

  "right": {}
}
"""

# Shorthands usable as a string in place of an object.
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
    """Drops the lines starting with // (the others are left untouched)."""
    return "\n".join(
        "" if line.lstrip().startswith("//") else line for line in text.splitlines()
    )


def build_action(spec) -> Action:
    """From a JSON entry to the action object."""
    if spec is None:
        return NoAction()

    if isinstance(spec, str):
        alias = ALIASES.get(spec.strip().lower())
        if alias:
            return alias()
        # Everything else is a key combination: "alt+tab", "play_pause".
        return Hotkey(spec)

    if not isinstance(spec, dict):
        raise ConfigError(f"invalid action: {spec!r}")

    kind = str(spec.get("action", "")).strip().lower()
    if not kind:
        raise ConfigError(f"missing \"action\" field in {spec!r}")

    if kind in ("hotkey", "key"):
        combo = spec.get("keys") or spec.get("key")
        if not combo:
            raise ConfigError(f"missing \"keys\" in {spec!r}")
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
            label=str(spec.get("label", "axis")),
        )

    alias = ALIASES.get(kind)
    if alias:
        return alias()
    raise ConfigError(f"unknown action: {kind!r}")


def apply_settings(cfg: Config, settings: dict) -> list[str]:
    """Applies the settings section onto Config, reporting unknown keys."""
    valid = {f.name: f.type for f in fields(Config)}
    warnings = []
    for key, value in settings.items():
        if key not in valid:
            warnings.append(f"unknown setting ignored: {key!r}")
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
            warnings.append(f"invalid value for {key!r}: {value!r}")
            continue
        setattr(cfg, key, value)
    return warnings


def build_bindings(specs: dict) -> tuple[dict[str, Action], list[str]]:
    """Turn one hand's gesture -> spec mapping into gesture -> action."""
    bindings: dict[str, Action] = {}
    warnings: list[str] = []
    for name, spec in specs.items():
        kind = GESTURE_KINDS.get(name)
        if kind is None:
            warnings.append(
                f"unknown gesture ignored: {name!r} "
                f"(available: {', '.join(sorted(GESTURE_KINDS))})"
            )
            continue
        try:
            action = build_action(spec)
        except (ConfigError, ValueError) as exc:
            warnings.append(f"{name}: {exc}")
            continue
        if kind not in action.kinds:
            warnings.append(
                f"{name}: the gesture is of kind '{kind}' but the action "
                f"'{action.label}' does not support it, ignored"
            )
            continue
        # "none" leaves no entry behind: a gesture nobody listens to is not
        # even evaluated by the recogniser, and cannot claim the cursor.
        if isinstance(action, NoAction):
            continue
        bindings[name] = action
    return bindings, warnings


def load(
    path: Path | str | None, cfg: Config
) -> tuple[dict[str, dict[str, Action]], list[str]]:
    """Load the file (creating it if missing) and return (bindings, warnings).

    The bindings come back one set per hand, with fresh Action objects on each
    side: several actions hold state and the two hands must not share it.
    """
    path = Path(path) if path else DEFAULT_PATH
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_TEXT, encoding="utf-8")
        print(f"Created {path} with the default configuration.")

    raw = strip_comments(path.read_text(encoding="utf-8"))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON at line {exc.lineno}: {exc.msg}") from exc

    warnings = apply_settings(cfg, data.get("settings") or {})

    shared = data.get("bindings") or {}
    bindings: dict[str, dict[str, Action]] = {}
    seen = set(warnings)
    for slot in HAND_SLOTS:
        specs = {**shared, **(data.get(slot) or {})}
        bindings[slot], hand_warnings = build_bindings(specs)
        # The shared section is built once per hand, so its complaints would
        # otherwise be printed twice.
        for w in hand_warnings:
            if w not in seen:
                seen.add(w)
                warnings.append(w)

    return bindings, warnings


def save_settings(path: Path | str | None, values: dict) -> Path:
    """Write `values` into the "settings" section, leaving the rest alone.

    Rewritten through json, so the comments outside the section do not survive
    - which is why they are put back: the default file is mostly comments, and
    calibrating should not cost you the documentation you are reading.
    """
    path = Path(path) if path else DEFAULT_PATH
    original = path.read_text(encoding="utf-8") if path.exists() else DEFAULT_TEXT
    data = json.loads(strip_comments(original))
    data["settings"] = {**(data.get("settings") or {}), **values}

    body = json.dumps(data, indent=2)
    # Reattach the comment lines that led the file, the header explaining what
    # it is. The ones inside the sections are gone; keeping them would mean
    # writing a JSON-with-comments editor, and this is a file you rarely write.
    header = []
    for line in original.splitlines():
        if line.lstrip().startswith("//"):
            header.append(line)
        elif header:
            break
    if header:
        body = "\n".join(header) + "\n" + body
    path.write_text(body + "\n", encoding="utf-8")
    return path
