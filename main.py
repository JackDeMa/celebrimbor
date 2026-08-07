"""Punto di ingresso: python main.py [opzioni]."""

import argparse
import sys

from airtouch import bindings as bindings_mod
from airtouch.app import AirTouchApp
from airtouch.config import Config
from airtouch.gestures import GESTURE_KINDS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="airtouch",
        description="Controlla il mouse muovendo la mano davanti alla webcam.",
    )
    p.add_argument(
        "--config",
        default=None,
        help=f"file dei gesti (default {bindings_mod.DEFAULT_PATH.name}, creato se manca)",
    )
    p.add_argument("--camera", type=int, default=None, help="indice webcam (default 0)")
    p.add_argument("--width", type=int, default=None, help="larghezza di cattura")
    p.add_argument("--height", type=int, default=None, help="altezza di cattura")
    p.add_argument("--fps", type=int, default=None, help="fps richiesti alla webcam")
    p.add_argument(
        "--no-preview", action="store_true", help="non mostrare la finestra di anteprima"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="riconosce i gesti senza toccare mouse e tastiera (utile per tarare)",
    )
    p.add_argument(
        "--no-mirror", action="store_true", help="disattiva l'immagine a specchio"
    )
    p.add_argument(
        "--model", default=None, help="percorso di un hand_landmarker.task alternativo"
    )
    p.add_argument(
        "--smoothing",
        type=float,
        default=None,
        help="frequenza di taglio del filtro: piu' bassa = piu' fluido (default 1.2)",
    )
    p.add_argument(
        "--sensitivity",
        type=float,
        default=None,
        help="ampiezza dell'area attiva: >1 muovi meno la mano, <1 piu' precisione",
    )
    p.add_argument(
        "--list-gestures",
        action="store_true",
        help="elenca i gesti riconosciuti e le azioni disponibili, poi esce",
    )
    return p


def _scale_active_area(cfg: Config, sensitivity: float) -> None:
    """Restringe (sensibilita' alta) o allarga (bassa) la zona attiva del frame."""
    factor = max(0.2, min(3.0, 1.0 / sensitivity))
    for lo, hi in (("active_x_min", "active_x_max"), ("active_y_min", "active_y_max")):
        a, b = getattr(cfg, lo), getattr(cfg, hi)
        center = (a + b) / 2.0
        half = (b - a) / 2.0 * factor
        setattr(cfg, lo, max(0.0, center - half))
        setattr(cfg, hi, min(1.0, center + half))


def apply_cli(cfg: Config, a: argparse.Namespace) -> None:
    """Le opzioni da riga di comando hanno la precedenza sul JSON."""
    for arg, field in (
        ("camera", "camera_index"),
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
    print("Gesti riconosciuti (nome da usare in gestures.json):\n")
    for name, kind in GESTURE_KINDS.items():
        print(f"  {name:<24} tipo: {kind}")
    print("\nAzioni indicabili come stringa:\n")
    for alias in sorted(bindings_mod.ALIASES):
        print(f"  {alias}")
    print("  <combinazione di tasti>   es. \"alt+tab\", \"ctrl+win+left\", \"play_pause\"")
    print("\nAzioni in forma estesa: hotkey, scroll, volume, click, axis.")
    print("Vedi il README per i parametri di ciascuna.")


def main() -> int:
    a = build_parser().parse_args()
    if a.list_gestures:
        print_reference()
        return 0

    cfg = Config()
    try:
        binds, warnings = bindings_mod.load(a.config, cfg)
    except bindings_mod.ConfigError as exc:
        print(f"Errore nella configurazione: {exc}")
        return 2

    apply_cli(cfg, a)

    for w in warnings:
        print(f"Attenzione: {w}")
    if not binds:
        print("Nessun gesto collegato: controlla la sezione \"bindings\".")

    return AirTouchApp(cfg, binds).run()


if __name__ == "__main__":
    sys.exit(main())
