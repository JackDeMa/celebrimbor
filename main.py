"""Entry point: python main.py [options]."""

import argparse
import sys

from celebrimbor import bindings as bindings_mod
from celebrimbor.app import CelebrimborApp
from celebrimbor.config import Config
from celebrimbor.gestures import GESTURE_KINDS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="celebrimbor",
        description="Control the mouse by moving your hand in front of the webcam.",
    )
    p.add_argument(
        "--config",
        default=None,
        help=f"gesture file (default {bindings_mod.DEFAULT_PATH.name}, created if missing)",
    )
    p.add_argument("--camera", type=int, default=None, help="webcam index (default 0)")
    p.add_argument(
        "--hands",
        type=int,
        choices=(1, 2),
        default=None,
        help="how many hands to track at once (default 2)",
    )
    p.add_argument(
        "--dominant",
        choices=("left", "right"),
        default=None,
        help="hand holding the pointer when both are in frame (default right)",
    )
    p.add_argument("--width", type=int, default=None, help="capture width")
    p.add_argument("--height", type=int, default=None, help="capture height")
    p.add_argument("--fps", type=int, default=None, help="fps requested from the webcam")
    p.add_argument(
        "--no-preview", action="store_true", help="do not show the preview window"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="recognise gestures without touching mouse and keyboard (useful for tuning)",
    )
    p.add_argument(
        "--no-mirror", action="store_true", help="disable the mirrored image"
    )
    p.add_argument(
        "--model", default=None, help="path to an alternative hand_landmarker.task"
    )
    p.add_argument(
        "--smoothing",
        type=float,
        default=None,
        help="filter cutoff frequency: lower = smoother (default 1.2)",
    )
    p.add_argument(
        "--sensitivity",
        type=float,
        default=None,
        help="size of the active area: >1 move your hand less, <1 more precision",
    )
    p.add_argument(
        "--list-gestures",
        action="store_true",
        help="list the recognised gestures and the available actions, then exit",
    )
    return p


def _scale_active_area(cfg: Config, sensitivity: float) -> None:
    """Shrinks (high sensitivity) or widens (low) the active area of the frame."""
    factor = max(0.2, min(3.0, 1.0 / sensitivity))
    for lo, hi in (("active_x_min", "active_x_max"), ("active_y_min", "active_y_max")):
        a, b = getattr(cfg, lo), getattr(cfg, hi)
        center = (a + b) / 2.0
        half = (b - a) / 2.0 * factor
        setattr(cfg, lo, max(0.0, center - half))
        setattr(cfg, hi, min(1.0, center + half))


def apply_cli(cfg: Config, a: argparse.Namespace) -> None:
    """Command-line options take precedence over the JSON."""
    for arg, field in (
        ("camera", "camera_index"),
        ("hands", "num_hands"),
        ("dominant", "dominant_hand"),
        ("width", "frame_width"),
        ("height", "frame_height"),
        ("fps", "target_fps"),
        ("model", "model_path"),
        ("smoothing", "min_cutoff"),
    ):
        value = getattr(a, arg)
        if value is not None:
            setattr(cfg, field, value)

    if a.no_preview:
        cfg.show_preview = False
    if a.no_mirror:
        cfg.mirror = False
    if a.dry_run:
        cfg.dry_run = True
    if a.sensitivity is not None:
        _scale_active_area(cfg, a.sensitivity)


def print_reference() -> None:
    print("Recognised gestures (name to use in gestures.json):\n")
    for name, kind in GESTURE_KINDS.items():
        print(f"  {name:<24} kind: {kind}")
    print("\nActions usable as a string:\n")
    for alias in sorted(bindings_mod.ALIASES):
        print(f"  {alias}")
    print("  <key combination>   e.g. \"alt+tab\", \"ctrl+win+left\", \"play_pause\"")
    print("\nActions in extended form: hotkey, scroll, volume, click, axis.")
    print("See the README for the parameters of each one.")
    print(
        "\nBoth hands are tracked: \"bindings\" applies to both, the \"left\" and\n"
        "\"right\" sections override it for one hand only (\"none\" to switch a\n"
        "gesture off on that side)."
    )


def main() -> int:
    a = build_parser().parse_args()
    if a.list_gestures:
        print_reference()
        return 0

    cfg = Config()
    try:
        binds, warnings = bindings_mod.load(a.config, cfg)
    except bindings_mod.ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    apply_cli(cfg, a)

    for w in warnings:
        print(f"Warning: {w}")
    if not any(binds.values()):
        print("No gesture bound: check the \"bindings\" section.")

    return CelebrimborApp(cfg, binds).run()


if __name__ == "__main__":
    sys.exit(main())
