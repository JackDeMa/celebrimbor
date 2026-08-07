"""Azioni collegabili ai gesti: click, scroll, tasti, combinazioni, volume.

Ogni azione dichiara i "kind" di gesto che sa gestire:

    trigger  evento istantaneo (uno swipe, un tap)
    axis     movimento continuo, riceve un delta a ogni frame
    hold     stato acceso/spento (il pinch tenuto -> drag)
    cursor   posizione normalizzata sullo schermo
"""

from pynput.keyboard import Controller as KeyboardController
from pynput.keyboard import Key, KeyCode

from .controller import MouseActuator

# ---------------------------------------------------------------------------
# Tastiera
# ---------------------------------------------------------------------------

_MODIFIERS = {
    "ctrl": Key.ctrl,
    "control": Key.ctrl,
    "alt": Key.alt,
    "shift": Key.shift,
    "win": Key.cmd,
    "windows": Key.cmd,
    "cmd": Key.cmd,
    "super": Key.cmd,
}

_NAMED_KEYS = {
    "up": Key.up,
    "down": Key.down,
    "left": Key.left,
    "right": Key.right,
    "tab": Key.tab,
    "enter": Key.enter,
    "esc": Key.esc,
    "escape": Key.esc,
    "space": Key.space,
    "backspace": Key.backspace,
    "delete": Key.delete,
    "home": Key.home,
    "end": Key.end,
    "pageup": Key.page_up,
    "pagedown": Key.page_down,
    "insert": Key.insert,
    "play_pause": Key.media_play_pause,
    "stop": Key.media_stop,
    "next_track": Key.media_next,
    "prev_track": Key.media_previous,
    "volume_up": Key.media_volume_up,
    "volume_down": Key.media_volume_down,
    "mute": Key.media_volume_mute,
}
_NAMED_KEYS.update({f"f{i}": getattr(Key, f"f{i}") for i in range(1, 21)})


class UnknownKeyError(ValueError):
    pass


def parse_combo(combo: str) -> tuple[list, object]:
    """"ctrl+win+left" -> ([Key.ctrl, Key.cmd], Key.left).

    L'ultimo elemento e' il tasto da battere, gli altri restano premuti attorno.
    """
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise UnknownKeyError(f"combinazione vuota: {combo!r}")

    mods = []
    for p in parts[:-1]:
        if p not in _MODIFIERS:
            raise UnknownKeyError(f"modificatore sconosciuto: {p!r} in {combo!r}")
        mods.append(_MODIFIERS[p])

    last = parts[-1]
    if last in _NAMED_KEYS:
        key = _NAMED_KEYS[last]
    elif last in _MODIFIERS:
        key = _MODIFIERS[last]
    elif len(last) == 1:
        key = KeyCode.from_char(last)
    else:
        raise UnknownKeyError(f"tasto sconosciuto: {last!r} in {combo!r}")
    return mods, key


class KeyPresser:
    def __init__(self, dry_run: bool = False):
        self.kb = KeyboardController()
        self.dry_run = dry_run

    def tap(self, mods: list, key) -> None:
        if self.dry_run:
            return
        for m in mods:
            self.kb.press(m)
        try:
            self.kb.press(key)
            self.kb.release(key)
        finally:
            for m in reversed(mods):
                self.kb.release(m)


class Backend:
    """Cio' su cui le azioni agiscono davvero."""

    def __init__(self, mouse: MouseActuator, dry_run: bool = False):
        self.mouse = mouse
        self.keys = KeyPresser(dry_run)
        # Tempo logico del frame corrente, aggiornato dall'engine: le azioni a
        # ripetizione lo usano per limitare la cadenza senza leggere l'orologio.
        self.now = 0.0


# ---------------------------------------------------------------------------
# Azioni
# ---------------------------------------------------------------------------


class Action:
    kinds: tuple[str, ...] = ()
    label: str = "?"

    def trigger(self, backend: Backend) -> str | None:
        return None

    def axis(self, backend: Backend, delta: float) -> str | None:
        return None

    def hold(self, backend: Backend, active: bool) -> str | None:
        return None

    def cursor(self, backend: Backend, x: float, y: float) -> str | None:
        return None

    def reset(self, backend: Backend) -> None:
        pass


class NoAction(Action):
    kinds = ("trigger", "axis", "hold", "cursor")
    label = "-"


class MoveCursor(Action):
    kinds = ("cursor",)
    label = "cursore"

    def cursor(self, backend: Backend, x: float, y: float) -> None:
        backend.mouse.move_to(x, y)


class Click(Action):
    kinds = ("trigger",)

    def __init__(self, button: str = "left", count: int = 1):
        from pynput.mouse import Button

        self.button = {"left": Button.left, "right": Button.right, "middle": Button.middle}[button]
        self.count = count
        self.label = f"click {button}" + (f" x{count}" if count > 1 else "")

    def trigger(self, backend: Backend) -> str | None:
        fired = False
        for _ in range(self.count):
            fired = backend.mouse.click(self.button) or fired
        return self.label.upper() if fired else None


class Drag(Action):
    kinds = ("hold",)
    label = "drag"

    def hold(self, backend: Backend, active: bool) -> str:
        if active:
            backend.mouse.start_drag()
            return "DRAG START"
        backend.mouse.end_drag()
        return "DRAG END"

    def reset(self, backend: Backend) -> None:
        backend.mouse.end_drag()


class Scroll(Action):
    """Rotella vera: accumula il delta e manda tacche intere."""

    kinds = ("axis",)

    def __init__(self, gain: float = 55.0, invert: bool = False, horizontal: bool = False):
        self.gain = gain
        self.sign = -1.0 if invert else 1.0
        self.horizontal = horizontal
        self.label = f"scroll {'orizzontale' if horizontal else 'verticale'}"

    def axis(self, backend: Backend, delta: float) -> None:
        amount = delta * self.gain * self.sign
        if self.horizontal:
            backend.mouse.scroll_h(amount)
        else:
            backend.mouse.scroll(amount)

    def reset(self, backend: Backend) -> None:
        backend.mouse.reset_scroll()


class Hotkey(Action):
    kinds = ("trigger",)

    def __init__(self, combo: str):
        self.combo = combo
        self.mods, self.key = parse_combo(combo)
        self.label = combo.upper()

    def trigger(self, backend: Backend) -> str:
        backend.keys.tap(self.mods, self.key)
        return self.label


class RepeatAxis(Action):
    """Trasforma un movimento continuo in pressioni ripetute di tasti.

    Serve per il volume: la manopola non esiste, esiste solo "un gradino su".
    """

    kinds = ("axis",)

    def __init__(
        self,
        positive: Action,
        negative: Action,
        gain: float = 14.0,
        max_rate: float = 25.0,
        label: str = "asse",
    ):
        self.positive = positive
        self.negative = negative
        self.gain = gain
        self.min_interval = 1.0 / max_rate if max_rate > 0 else 0.0
        self.label = label
        self._accum = 0.0
        self._last = 0.0

    def axis(self, backend: Backend, delta: float) -> str | None:
        self._accum += delta * self.gain
        steps = int(self._accum)
        if not steps:
            return None
        if backend.now - self._last < self.min_interval:
            return None
        self._last = backend.now

        # Un gradino per frame: evita raffiche ingestibili su movimenti bruschi.
        step = 1 if steps > 0 else -1
        self._accum -= step
        return (self.positive if step > 0 else self.negative).trigger(backend)

    def reset(self, backend: Backend) -> None:
        self._accum = 0.0
