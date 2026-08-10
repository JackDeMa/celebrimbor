"""Always-on-top overlay: the gesture state, visible over any other window.

The preview window is fine while you are tuning, but it ends up behind whatever
you are actually working in - which is precisely when knowing what the program
thinks your hands are doing would be useful. This is a small card that floats
above everything else and never takes part in the interaction it describes:

    WS_EX_TRANSPARENT   the mouse goes straight through it. Not cosmetic: a
                        window that swallowed clicks would swallow the ones the
                        program itself generates, right where the cursor is.
    WS_EX_NOACTIVATE    it never takes the keyboard focus away from your work.
    WS_EX_TOOLWINDOW    it stays out of the Alt+Tab list.

tkinter and ctypes only, both from the standard library.

Two limits are not ours to fix: a game in exclusive fullscreen covers every
topmost window, and a process running unelevated cannot draw over windows that
were elevated by UAC (nor over the secure desktop).
"""

import ctypes
import sys
import tkinter as tk
from ctypes import wintypes

from .hand import HAND_SLOTS

# --- Win32 ------------------------------------------------------------------
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080

_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020

_SPI_GETWORKAREA = 0x0030

# Same palette as the preview HUD, written the way Tk wants it.
_BG = "#141414"
_LINE = "#333333"
_GREEN = "#78e678"
_GREY = "#aaaaaa"
_RED = "#f05050"
_YELLOW = "#f0dc3c"


def _work_area() -> tuple[int, int, int, int]:
    """Desktop area minus the taskbar, so the card does not sit under it."""
    rect = wintypes.RECT()
    ok = ctypes.windll.user32.SystemParametersInfoW(
        _SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
    )
    if not ok:
        user32 = ctypes.windll.user32
        return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return rect.left, rect.top, rect.right, rect.bottom


class Overlay:
    """A small always-on-top card with the state of both hands."""

    WIDTH = 250
    MARGIN = 24
    MAX_CHARS = 26  # what fits on one line at this width

    def __init__(self, opacity: float = 0.82):
        self.root = tk.Tk()
        self.root.overrideredirect(True)  # no title bar, no border
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", opacity)
        self.root.configure(bg=_BG)

        frame = tk.Frame(self.root, bg=_BG, padx=12, pady=8)
        frame.pack(fill="both", expand=True)

        self._hands: dict[str, tk.Label] = {}
        for slot in HAND_SLOTS:
            label = tk.Label(
                frame,
                text=f"{slot[0].upper()}  -",
                font=("Consolas", 11),
                bg=_BG,
                fg=_GREY,
                anchor="w",
            )
            label.pack(fill="x")
            self._hands[slot] = label

        tk.Frame(frame, bg=_LINE, height=1).pack(fill="x", pady=(6, 4))
        # Fixed height: the card must not resize as events come and go, or it
        # would jitter in the corner.
        self._events = tk.Label(
            frame,
            text="",
            font=("Consolas", 10),
            bg=_BG,
            fg=_YELLOW,
            anchor="nw",
            justify="left",
            height=2,
        )
        self._events.pack(fill="x")

        self._place()
        self._apply_styles()
        self.visible = True
        self._since_raise = 0
        self._pump()

    # ------------------------------------------------------------------
    def _place(self) -> None:
        self.root.update_idletasks()
        height = self.root.winfo_reqheight()
        _, _, right, bottom = _work_area()
        x = right - self.WIDTH - self.MARGIN
        y = bottom - height - self.MARGIN
        self.root.geometry(f"{self.WIDTH}x{height}+{x}+{y}")

    def _apply_styles(self) -> None:
        user32 = ctypes.windll.user32
        # Tk wraps the toplevel in a frame window: the styles belong on the
        # outer one, which is where GetParent lands.
        self.hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()

        get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        put = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get.restype = ctypes.c_ssize_t
        get.argtypes = [wintypes.HWND, ctypes.c_int]
        put.restype = ctypes.c_ssize_t
        put.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]

        style = get(self.hwnd, _GWL_EXSTYLE)
        put(
            self.hwnd,
            _GWL_EXSTYLE,
            style
            | _WS_EX_LAYERED
            | _WS_EX_TRANSPARENT
            | _WS_EX_NOACTIVATE
            | _WS_EX_TOOLWINDOW,
        )
        # Style changes only take hold once the window is told to reapply them.
        user32.SetWindowPos(
            self.hwnd,
            _HWND_TOPMOST,
            0,
            0,
            0,
            0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
        )

    def raise_above(self) -> None:
        """Back on top of the other topmost windows."""
        ctypes.windll.user32.SetWindowPos(
            self.hwnd,
            _HWND_TOPMOST,
            0,
            0,
            0,
            0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
        )

    # ------------------------------------------------------------------
    def set_visible(self, visible: bool) -> None:
        self.visible = visible
        if visible:
            self.root.deiconify()
            self.raise_above()
        else:
            self.root.withdraw()
        self._pump()

    def update(
        self, states, events: list[str], dominant: str, enabled: bool
    ) -> None:
        """Redraw the card with this frame's state."""
        if not self.visible:
            return

        for state in states:
            label = self._hands.get(state.hand)
            if label is None:
                continue
            marker = "*" if state.hand == dominant else " "
            text = f"{state.hand[0].upper()}{marker} {state.mode}"
            if state.detail:
                text = f"{text}  {state.detail}"
            if state.mode == "NO HAND":
                color = _GREY
            else:
                color = _GREEN if enabled else _RED
            label.configure(text=text[: self.MAX_CHARS], fg=color)

        self._events.configure(text="\n".join(e[: self.MAX_CHARS] for e in events[-2:]))

        # Another window going topmost would slip over us: reassert once a
        # second or so rather than on every frame.
        self._since_raise += 1
        if self._since_raise >= 30:
            self._since_raise = 0
            self.raise_above()

        self._pump()

    def _pump(self) -> None:
        """Let Tk redraw, without a mainloop of its own."""
        try:
            self.root.update_idletasks()
            self.root.update()
        except tk.TclError:
            pass

    def close(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def create(opacity: float = 0.82) -> Overlay | None:
    """Build the overlay, or return None with a reason if it cannot be had."""
    if sys.platform != "win32":
        print("Overlay: Windows only, carrying on without it.")
        return None
    try:
        return Overlay(opacity=opacity)
    except Exception as exc:  # a missing Tk, a headless session, a locked desktop
        print(f"Overlay unavailable ({exc}), carrying on without it.")
        return None
