"""Acting on the operating system: cursor movement, clicks, drag, scroll."""

import sys
import time

from pynput.mouse import Button, Controller


def get_screen_size() -> tuple[int, int]:
    """Screen size in real pixels (DPI-aware on Windows)."""
    if sys.platform == "win32":
        import ctypes

        try:
            # PROCESS_PER_MONITOR_DPI_AWARE: avoids scaled coordinates on
            # displays at 125%/150%, otherwise the cursor cannot reach the edges.
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))

    import tkinter

    root = tkinter.Tk()
    root.withdraw()
    size = (root.winfo_screenwidth(), root.winfo_screenheight())
    root.destroy()
    return size


class MouseActuator:
    """Wrapper over pynput with safeguards: click cooldown and coherent drag."""

    def __init__(self, click_cooldown: float = 0.3, dry_run: bool = False):
        self.mouse = Controller()
        self.screen_w, self.screen_h = get_screen_size()
        self.click_cooldown = click_cooldown
        self.dry_run = dry_run  # recognises everything but actuates nothing
        self._last_click = 0.0
        self.dragging = False
        self._scroll_accum = 0.0
        self._scroll_accum_h = 0.0

    # --- movement --------------------------------------------------------
    def move_to(self, x: float, y: float) -> None:
        px = int(min(max(x, 0.0), 1.0) * (self.screen_w - 1))
        py = int(min(max(y, 0.0), 1.0) * (self.screen_h - 1))
        if not self.dry_run:
            self.mouse.position = (px, py)

    # --- click -----------------------------------------------------------
    def click(self, button: Button = Button.left, count: int = 1) -> bool:
        """One gesture, one call: `count` clicks sent back to back.

        The cooldown guards against two separate gestures firing in a row, not
        against the clicks that make up a single deliberate one. Sending them
        through pynput in one go is also what gets them close enough together
        for Windows to read them as a double click.
        """
        now = time.monotonic()
        if now - self._last_click < self.click_cooldown:
            return False
        if not self.dry_run:
            self.mouse.click(button, count)
        self._last_click = now
        return True

    # --- drag ------------------------------------------------------------
    def start_drag(self) -> None:
        if not self.dragging:
            if not self.dry_run:
                self.mouse.press(Button.left)
            self.dragging = True

    def end_drag(self) -> None:
        if self.dragging:
            if not self.dry_run:
                self.mouse.release(Button.left)
            self.dragging = False

    # --- scroll ----------------------------------------------------------
    def scroll(self, amount: float) -> None:
        """Accumulates fractional scroll and emits whole notches."""
        self._scroll_accum += amount
        steps = int(self._scroll_accum)
        if steps:
            if not self.dry_run:
                self.mouse.scroll(0, steps)
            self._scroll_accum -= steps

    def scroll_h(self, amount: float) -> None:
        self._scroll_accum_h += amount
        steps = int(self._scroll_accum_h)
        if steps:
            if not self.dry_run:
                self.mouse.scroll(steps, 0)
            self._scroll_accum_h -= steps

    def reset_scroll(self) -> None:
        self._scroll_accum = 0.0
        self._scroll_accum_h = 0.0

    # --- cleanup ---------------------------------------------------------
    def release_all(self) -> None:
        self.end_drag()
        self.reset_scroll()
