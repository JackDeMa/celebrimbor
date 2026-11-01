"""Loading gestures.json: gesture -> action, one set per hand.

The file accepts comments on lines starting with `//`, which JSON does not
support but which keep the configuration readable.

The "bindings" section applies to both hands; the "left" and "right" sections
add to it or override it for one hand only, with "none" to switch a gesture off
on that side.

The "face" section binds the face gestures (winks, mouth), used when the
cursor follows the head or the eyes ("pointer" in settings).
"""

import json
import re
import sys
from dataclasses import fields
from pathlib import Path

from .actions import Action, Click, Drag, Hotkey, MoveCursor, NoAction, RepeatAxis, Scroll
from .config import Config
from .face import FACE_SLOT
from .face_gestures import FACE_GESTURE_KINDS
from .gestures import GESTURE_KINDS
from .hand import HAND_SLOTS

# Next to celebrimbor.exe when frozen, where the user can find and edit it.
DEFAULT_PATH = (
    Path(sys.executable).parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
) / "gestures.json"

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
    // next_window, prev_window, next_desktop, prev_desktop, show_desktop send
    // the right shortcut on Windows and on macOS alike.
    "fist_swipe_left": "prev_desktop",
    "fist_swipe_right": "next_desktop",
    "fist_swipe_up": "next_window",
    "fist_swipe_down": "prev_window",

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
    // "three_finger_horizontal": { "action": "axis", "positive": "next_desktop", "negative": "prev_desktop", "gain": 3, "max_rate": 2 },
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

  "right": {},

  // Face gestures, used when the cursor follows the head or the eyes: set
  // "pointer" to "head" or "eyes" in settings, or start with --pointer head.
  // The hands keep every gesture above except moving the cursor.
  "face": {
    "face_move": "move_cursor",
    // One eye shut and reopened: a click. Both eyes together (a blink) never click.
    "wink_left_tap": "left_click",
    // The same wink held for "face_hold" seconds: drag until the eye reopens.
    "wink_left_hold": "drag",
    "wink_right_tap": "right_click",
    // Mouth open: the cursor stops and moving the head scrolls instead.
    // Add "mouth_scroll_horizontal" for sideways scrolling too.
    "mouth_scroll_vertical": { "action": "scroll", "gain": 40 }
    // For whoever cannot wink one eye at a time:
    // "mouth_open_tap": "left_click", "mouth_open_hold": "drag",
    // Both eyes shut for "blink_long_seconds":
    // "blink_long": "double_click"
  }
}
"""

# The same intent on either system: these names send the Windows shortcut on
# Windows and the macOS one on a Mac, so one gestures.json fits both.
_MAC = sys.platform == "darwin"
PORTABLE_KEYS = {
    "next_window": "cmd+tab" if _MAC else "alt+tab",
    "prev_window": "cmd+shift+tab" if _MAC else "alt+shift+tab",
    "next_desktop": "ctrl+right" if _MAC else "ctrl+win+right",
    "prev_desktop": "ctrl+left" if _MAC else "ctrl+win+left",
    "show_desktop": "f11" if _MAC else "win+d",
}

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


# What the face slot gets when the file has no "face" section at all.
DEFAULT_FACE_BINDINGS = {
    "face_move": "move_cursor",
    "wink_left_tap": "left_click",
    "wink_left_hold": "drag",
    "wink_right_tap": "right_click",
    "mouth_scroll_vertical": {"action": "scroll", "gain": 40},
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
        return Hotkey(PORTABLE_KEYS.get(spec.strip().lower(), spec))

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


def save_settings(path: Path | str | None, values: dict[str, float | bool]) -> Path:
    """Write `values` into the "settings" section, comments and layout kept.

    A key already set is changed in place, a new one goes at the top of the
    section. The result is parsed before it is written: a file this cannot
    edit safely is left alone.
    """
    path = Path(path) if path else DEFAULT_PATH
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        literal = json.dumps(value) if isinstance(value, bool) else f"{value:g}"
        # Only a line that starts with the key: a commented-out one stays as it is.
        pattern = rf'(?m)^(\s*"{re.escape(key)}"\s*:\s*)(true|false|null|[-+0-9.eE]+)'
        text, found = re.subn(pattern, lambda m: m.group(1) + literal, text, count=1)
        if found:
            continue
        section = re.search(r'"settings"\s*:\s*\{', text)
        if section is None:
            raise ConfigError(f"{path}: no \"settings\" section to write into")
        empty = text[section.end():].lstrip().startswith("}")
        entry = f'\n    "{key}": {literal}' + ("" if empty else ",")
        text = text[: section.end()] + entry + text[section.end():]
    try:
        json.loads(strip_comments(text))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: could not update it safely ({exc.msg})") from exc
    path.write_text(text, encoding="utf-8")
    return path


def build_bindings(
    specs: dict, kinds: dict[str, str] = GESTURE_KINDS
) -> tuple[dict[str, Action], list[str]]:
    """Turn one slot's gesture -> spec mapping into gesture -> action."""
    bindings: dict[str, Action] = {}
    warnings: list[str] = []
    for name, spec in specs.items():
        kind = kinds.get(name)
        if kind is None:
            warnings.append(
                f"unknown gesture ignored: {name!r} "
                f"(available: {', '.join(sorted(kinds))})"
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

    face = data.get(FACE_SLOT)
    if face is None:
        # Configurations written before the face pointer have no section for
        # it: the defaults make --pointer head work on them out of the box.
        face = DEFAULT_FACE_BINDINGS
    bindings[FACE_SLOT], face_warnings = build_bindings(face, FACE_GESTURE_KINDS)
    warnings += [f"face: {w}" for w in face_warnings]

    return bindings, warnings
