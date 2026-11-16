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
import math
import sys
import time
import tkinter as tk

if sys.platform == "win32":
    from ctypes import wintypes

from .face import FACE_SLOT
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
_CYAN = "#00E5FF"
_BG = "#070B10"
_LINE = "#1C2E3B"
_GREEN = "#3DFFA8"
_GREY = "#5E7787"
_RED = "#FF3B5C"
_YELLOW = "#FFB020"


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


def _click_through(root) -> int:
    """Make a Tk toplevel topmost, click-through, unfocusable; return its hwnd."""
    user32 = ctypes.windll.user32
    # Tk wraps the toplevel in a frame window: the styles belong on the
    # outer one, which is where GetParent lands.
    hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()

    get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    put = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get.restype = ctypes.c_ssize_t
    get.argtypes = [wintypes.HWND, ctypes.c_int]
    put.restype = ctypes.c_ssize_t
    put.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]

    style = get(hwnd, _GWL_EXSTYLE)
    put(
        hwnd,
        _GWL_EXSTYLE,
        style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW,
    )
    # Style changes only take hold once the window is told to reapply them.
    user32.SetWindowPos(
        hwnd,
        _HWND_TOPMOST,
        0,
        0,
        0,
        0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
    )
    return hwnd


class CalibrationMark:
    """A reticle in the middle of the screen, shown while the face calibrates.

    The camera preview already has a progress bar, but the calibration asks you
    to look at the centre of the *screen*: this is the thing to look at. Same
    click-through window as the card, with a colour key for the background so
    only the drawing is visible.
    """

    SIZE = 160
    _KEY = "#010203"

    def __init__(self, master=None):
        self.root = tk.Toplevel(master) if master is not None else tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", self._KEY)
        self.root.configure(bg=self._KEY)
        self.canvas = tk.Canvas(
            self.root, width=self.SIZE, height=self.SIZE, bg=self._KEY, highlightthickness=0
        )
        self.canvas.pack()
        user32 = ctypes.windll.user32
        x = (user32.GetSystemMetrics(0) - self.SIZE) // 2
        y = (user32.GetSystemMetrics(1) - self.SIZE) // 2
        self.root.geometry(f"{self.SIZE}x{self.SIZE}+{x}+{y}")
        self.root.update_idletasks()
        self.hwnd = _click_through(self.root)
        self.root.withdraw()
        self.visible = False

    def update(self, progress: float) -> None:
        """progress: 0 = not calibrating (hidden), 0..1 = how far along it is."""
        if progress <= 0.0:
            if self.visible:
                self.visible = False
                self.root.withdraw()
                self._pump()
            return
        if not self.visible:
            self.visible = True
            self.root.deiconify()
            _click_through(self.root)

        c, s = self.canvas, self.SIZE
        m = s // 2
        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 5.0)
        r = 34 + 8 * pulse
        c.delete("all")
        c.create_oval(m - r, m - r, m + r, m + r, outline=_CYAN, width=2)
        c.create_arc(
            m - 62, m - 62, m + 62, m + 62, start=90, extent=-360 * min(progress, 1.0),
            style="arc", outline=_YELLOW, width=3,
        )
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            c.create_line(m + dx * 44, m + dy * 44, m + dx * 62, m + dy * 62, fill=_CYAN, width=2)
        c.create_oval(m - 5, m - 5, m + 5, m + 5, fill=_CYAN, outline="")
        self.root.attributes("-topmost", True)
        self._pump()

    def _pump(self) -> None:
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


def create_mark(master=None) -> "CalibrationMark | None":
    if sys.platform != "win32":
        return None
    try:
        return CalibrationMark(master=master)
    except Exception as exc:
        print(f"Calibration mark unavailable ({exc}), carrying on without it.")
        return None


class Overlay:
    """A small always-on-top card with the state of both hands (and the face)."""

    WIDTH = 250
    MARGIN = 24
    MAX_CHARS = 26  # what fits on one line at this width

    def __init__(self, opacity: float = 0.82, face: bool = False, master=None):
        # A child of the preview window when there is one: one Tk per process.
        self.root = tk.Toplevel(master) if master is not None else tk.Tk()
        self.root.overrideredirect(True)  # no title bar, no border
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", opacity)
        self.root.configure(bg=_BG)

        frame = tk.Frame(
            self.root, bg=_BG, padx=12, pady=8, highlightthickness=1, highlightbackground="#0B5563"
        )
        frame.pack(fill="both", expand=True)

        self._hands: dict[str, tk.Label] = {}
        for slot in (*HAND_SLOTS, FACE_SLOT) if face else HAND_SLOTS:
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
        self.hwnd = _click_through(self.root)

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
            if state.mode in ("NO HAND", "NO FACE"):
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


def create(opacity: float = 0.82, face: bool = False, master=None) -> Overlay | None:
    """Build the overlay, or return None with a reason if it cannot be had."""
    if sys.platform != "win32":
        print("Overlay: Windows only, carrying on without it.")
        return None
    try:
        return Overlay(opacity=opacity, face=face, master=master)
    except Exception as exc:  # a missing Tk, a headless session, a locked desktop
        print(f"Overlay unavailable ({exc}), carrying on without it.")
        return None
