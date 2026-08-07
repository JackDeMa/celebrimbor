"""Attuazione sul sistema operativo: spostamento cursore, click, drag, scroll."""

import sys
import time

from pynput.mouse import Button, Controller


def get_screen_size() -> tuple[int, int]:
    """Dimensione dello schermo in pixel reali (DPI-aware su Windows)."""
    if sys.platform == "win32":
        import ctypes

        try:
            # PROCESS_PER_MONITOR_DPI_AWARE: evita coordinate scalate con display
            # al 125%/150%, altrimenti il cursore non raggiunge i bordi.
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
    """Wrapper sopra pynput con protezioni: cooldown sui click e drag coerente."""

    def __init__(self, click_cooldown: float = 0.3, dry_run: bool = False):
        self.mouse = Controller()
        self.screen_w, self.screen_h = get_screen_size()
        self.click_cooldown = click_cooldown
        self.dry_run = dry_run  # riconosce tutto ma non attua nulla
        self._last_click = 0.0
        self.dragging = False
        self._scroll_accum = 0.0
        self._scroll_accum_h = 0.0

    # --- movimento -------------------------------------------------------
    def move_to(self, x: float, y: float) -> None:
        px = int(min(max(x, 0.0), 1.0) * (self.screen_w - 1))
        py = int(min(max(y, 0.0), 1.0) * (self.screen_h - 1))
        if not self.dry_run:
            self.mouse.position = (px, py)

    # --- click -----------------------------------------------------------
    def click(self, button: Button = Button.left) -> bool:
        now = time.monotonic()
        if now - self._last_click < self.click_cooldown:
            return False
        if not self.dry_run:
            self.mouse.click(button)
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
        """Accumula lo scroll frazionario ed emette tacche intere."""
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

    # --- pulizia ---------------------------------------------------------
    def release_all(self) -> None:
        self.end_drag()
        self.reset_scroll()
