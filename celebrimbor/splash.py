"""A small borderless splash: spinner, status line, progress bar.

It covers the slow start (a model download on a first source run, MediaPipe
loading, the camera opening). The slow work runs in a worker thread while the
main thread pumps Tk, so the spinner keeps turning. Plain tkinter, destroyed
before the CustomTkinter window is created: one Tk root at a time.
"""

import sys
import threading
import time

from . import __version__

BG = "#070B10"
CYAN = "#00E5FF"
TEXT = "#CFE9F2"
DIM = "#1E3A44"
FONT = "Menlo" if sys.platform == "darwin" else "Consolas"
W, H = 400, 150
BAR = (24, 112, W - 24, 117)  # left, top, right, bottom


class Splash:
    """Context manager. Disabled, or with no display, every call is a no-op."""

    def __init__(self, enabled: bool = True):
        self.root = None
        self._text = "Starting..."
        self._fraction: float | None = None
        if enabled:
            try:
                self._build()
            except Exception:  # tk.TclError: no display, no Tk
                self.root = None

    def _build(self) -> None:
        import tkinter as tk

        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BG)
        x = (root.winfo_screenwidth() - W) // 2
        y = (root.winfo_screenheight() - H) // 2
        root.geometry(f"{W}x{H}+{x}+{y}")
        c = tk.Canvas(root, width=W, height=H, bg=BG, highlightthickness=1,
                      highlightbackground=CYAN)
        c.pack()
        c.create_text(24, 28, anchor="w", text="CELEBRIMBOR", fill=CYAN,
                      font=(FONT, 15, "bold"))
        c.create_text(W - 20, 28, anchor="e", text=f"v{__version__}", fill=TEXT,
                      font=(FONT, 9))
        c.create_line(24, 46, W - 24, 46, fill=DIM)
        c.create_oval(26, 62, 54, 90, outline=DIM, width=3)
        self._arc = c.create_arc(26, 62, 54, 90, start=90, extent=110, style="arc",
                                 outline=CYAN, width=3)
        self._label = c.create_text(70, 76, anchor="w", text=self._text, fill=TEXT,
                                    font=(FONT, 10))
        c.create_rectangle(*BAR, outline=DIM)
        self._bar = c.create_rectangle(*BAR, outline="", fill=CYAN)
        self._canvas = c
        self.root = root
        self._pump()

    def _pump(self) -> None:
        """Advance the animation one frame and let Tk draw it (main thread only)."""
        c, now = self._canvas, time.monotonic()
        c.itemconfigure(self._arc, start=(-now * 360) % 360)
        c.itemconfigure(self._label, text=self._text)
        left, top, right, bottom = BAR
        span = right - left
        if self._fraction is None:
            # Unknown length: a short segment sweeping across.
            head = left + span * 1.25 * ((now * 0.7) % 1.0)
            x0, x1 = max(left, head - span * 0.25), min(right, head)
        else:
            x0, x1 = left, left + span * min(1.0, max(0.0, self._fraction))
        c.coords(self._bar, x0, top, x1, bottom)
        self.root.update()

    def status(self, text: str, fraction: float | None = None) -> None:
        """Safe from any thread; from the main one it is drawn at once."""
        self._text, self._fraction = text, fraction
        if self.root is not None and threading.current_thread() is threading.main_thread():
            self._pump()

    def download(self, name: str):
        """A progress callback for detector.ensure_model."""
        def progress(done: int, total: int) -> None:
            size = f"{done / 1e6:.1f} / {total / 1e6:.1f} MB" if total else f"{done / 1e6:.1f} MB"
            self.status(f"Downloading {name} model {size}", done / total if total else None)
        return progress

    def run(self, fn, *args):
        """fn(*args) in a worker thread, the splash animating until it returns."""
        if self.root is None:
            return fn(*args)
        out = {}

        def work():
            try:
                out["value"] = fn(*args)
            except BaseException as exc:  # re-raised on the main thread below
                out["error"] = exc

        worker = threading.Thread(target=work, name="celebrimbor-startup", daemon=True)
        worker.start()
        while worker.is_alive():
            self._pump()
            worker.join(1 / 60)
        if "error" in out:
            raise out["error"]
        return out["value"]

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        if self.root is not None:
            try:
                self.root.destroy()
            except Exception:
                pass
            self.root = None


if __name__ == "__main__":
    # Visual check: python -m celebrimbor.splash
    with Splash() as s:
        s.run(lambda: [s.download("hand")(i * 400_000, 7_800_000) or time.sleep(0.05)
                       for i in range(20)])
        s.status("Loading face model...")
        s.run(time.sleep, 1.5)
        s.status("Starting camera...")
        time.sleep(1)
