"""The preview window: webcam image, state, switches and tuning in one place.

CustomTkinter on top of Tk, themed as a HUD: near-black panels, hairline
borders, cyan lines, monospace labels; the same on Windows and on macOS. The
main loop drives it with one `show()` per camera frame; there is no mainloop of
its own, the same way the overlay works.

The window only displays and forwards clicks: every decision stays in the app.
"""

import sys
import tkinter as tk

import customtkinter as ctk
import cv2
from PIL import Image

from .face import FACE_SLOT
from .hand import HAND_SLOTS

# The palette, shared with the drawing on the picture (app.py) and the overlay.
_BG = "#070B10"
_PANEL = "#0D141C"
_RAISED = "#121C26"
_LINE = "#1C2E3B"
_CYAN = "#00E5FF"
_CYAN_DIM = "#0B5563"
_AMBER = "#FFB020"
_RED = "#FF3B5C"
_GREEN = "#3DFFA8"
_TEXT = "#CFE9F2"
_MUTED = "#5E7787"

_MONO = {"win32": "Consolas", "darwin": "Menlo"}.get(sys.platform, "DejaVu Sans Mono")
_SIDEBAR_W = 320
_SLOT_NAMES = {"left": "LEFT", "right": "RIGHT", FACE_SLOT: "FACE"}

_KEYS_HELP = (
    "q quit · p pause · h skeleton · o overlay\n"
    "c calibrate · d swap hand\n"
    "Ctrl+Alt+Q / P / C / Space from anywhere"
)

# Laid over CustomTkinter's stock theme: only what the window uses.
_THEME = {
    "CTk": {"fg_color": _BG},
    "CTkToplevel": {"fg_color": _BG},
    "CTkFrame": {
        "corner_radius": 3, "border_width": 1, "fg_color": _PANEL,
        "top_fg_color": _RAISED, "border_color": _LINE,
    },
    "CTkButton": {
        "corner_radius": 2, "border_width": 1, "fg_color": "transparent",
        "hover_color": _CYAN_DIM, "border_color": _CYAN, "text_color": _CYAN,
        "text_color_disabled": _MUTED,
    },
    "CTkLabel": {"corner_radius": 0, "text_color": _TEXT},
    "CTkEntry": {
        "corner_radius": 2, "border_width": 1, "fg_color": _BG,
        "border_color": _LINE, "text_color": _TEXT,
    },
    "CTkSwitch": {
        "corner_radius": 3, "fg_color": _LINE, "progress_color": _CYAN,
        "button_color": _TEXT, "button_hover_color": "#FFFFFF", "text_color": _TEXT,
    },
    "CTkScrollbar": {
        "corner_radius": 2, "fg_color": "transparent",
        "button_color": _LINE, "button_hover_color": _CYAN_DIM,
    },
}


def _mono(size: int = 11, bold: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family=_MONO, size=size, weight="bold" if bold else "normal")


class ControlWindow:
    def __init__(self, app):
        self.app = app
        self.alive = True
        self._shown: dict[int, object] = {}   # widget -> last value set, to skip no-op redraws
        self._gesture_key = None

        ctk.set_appearance_mode("dark")
        for widget, options in _THEME.items():
            ctk.ThemeManager.theme[widget].update(options)
        if sys.platform == "win32":  # macOS keeps the stock theme's SF
            ctk.ThemeManager.theme["CTkFont"]["family"] = "Segoe UI"
        self.root = ctk.CTk()
        self.root.title("Celebrimbor")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.bind("<KeyPress>", self._on_key)
        # A click anywhere else takes the focus out of a value field, applying it.
        self.root.bind(
            "<Button-1>",
            lambda e: None if isinstance(e.widget, tk.Entry) else self.root.focus_set(),
            add="+",
        )

        small = ctk.CTkFont(size=11)
        self._build_main(small)
        self._build_sidebar(small)
        self._center()

    def _center(self) -> None:
        """Open in the middle of the screen, not wherever the system drops it."""
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - self.root.winfo_reqwidth()) // 2
        y = (self.root.winfo_screenheight() - self.root.winfo_reqheight()) // 2
        self.root.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    # --- layout ---------------------------------------------------------------
    def _build_main(self, small) -> None:
        main = ctk.CTkFrame(self.root, fg_color="transparent", border_width=0)
        main.grid(row=0, column=0, padx=(14, 7), pady=14, sticky="n")

        status = ctk.CTkFrame(main)
        status.pack(fill="x")
        self._chips: dict[str, tuple[ctk.CTkLabel, ctk.CTkLabel]] = {}
        for slot in (*HAND_SLOTS, FACE_SLOT):
            chip = ctk.CTkFrame(status, fg_color="transparent", border_width=0)
            chip.pack(side="left", padx=(12, 4), pady=6)
            mode = ctk.CTkLabel(
                chip, text="", width=150, height=18, anchor="w", font=_mono(12, bold=True)
            )
            mode.pack(anchor="w")
            detail = ctk.CTkLabel(
                chip, text="", width=150, height=14, anchor="w", font=small, text_color=_MUTED
            )
            detail.pack(anchor="w")
            self._chips[slot] = (mode, detail)
        self._fps = ctk.CTkLabel(status, text="", font=_mono(10), text_color=_MUTED)
        self._fps.pack(side="right", padx=12)
        # Outlined, not filled; with nothing to say its border melts into the panel.
        self._badge_box = ctk.CTkFrame(status, fg_color="transparent", border_color=_PANEL)
        self._badge_box.pack(side="right")
        self._badge = ctk.CTkLabel(self._badge_box, text="", height=18, font=_mono(10, bold=True))
        self._badge.pack(padx=2, pady=2)

        video = ctk.CTkFrame(main, fg_color=_BG)
        video.pack(pady=(10, 0))
        self._video = ctk.CTkLabel(video, text="")
        self._video.pack(padx=1, pady=1)
        self._video_image = None

        self._events = ctk.CTkLabel(
            main, text="", height=22, anchor="w", font=_mono(11), text_color=_AMBER
        )
        self._events.pack(fill="x", pady=(6, 0), padx=4)

    def _build_sidebar(self, small) -> None:
        app = self.app
        side = ctk.CTkFrame(self.root, width=_SIDEBAR_W)
        side.grid(row=0, column=1, padx=(7, 14), pady=14, sticky="ns")
        side.pack_propagate(False)  # fixed width, the height of the camera column

        def section(title: str) -> None:
            ctk.CTkLabel(
                side, text=f"// {title.upper()}", anchor="w", height=16,
                text_color=_CYAN, font=_mono(11, bold=True),
            ).pack(fill="x", padx=18, pady=(14, 3))
            ctk.CTkFrame(side, height=1, fg_color=_LINE, border_width=0).pack(
                fill="x", padx=18, pady=(0, 6)
            )

        section("Inputs")
        self._switches: dict[str, ctk.CTkSwitch] = {}
        for part, label in (("hand", "Hand"), ("head", "Head / nose"), ("eyes", "Eyes / pupils")):
            switch = ctk.CTkSwitch(
                side, text=label, command=lambda part=part: app.toggle_input(part)
            )
            switch.pack(anchor="w", padx=18, pady=3)
            self._switches[part] = switch

        section("Cursor")
        self._tuning_for = ctk.CTkLabel(
            side, text="", anchor="w", font=_mono(10), text_color=_MUTED, height=14
        )
        self._tuning_for.pack(fill="x", padx=18)
        # The values are fields too: type a number, Enter (or click away) applies it.
        self._values: dict[str, ctk.CTkEntry] = {}
        self._editing: set[str] = set()
        for key, label, hint, minus, plus, typed in (
            ("steady", "Steady", "calmer when still",
             lambda: app.tune(steady=-1), lambda: app.tune(steady=+1),
             lambda v: app.set_filter(steady=v)),
            ("speed", "Speed", "quicker on fast moves",
             lambda: app.tune(speed=-1), lambda: app.tune(speed=+1),
             lambda v: app.set_filter(speed=v)),
            ("dead", "Deadzone", "tremor ignored, in pixels",
             lambda: app.set_deadband(app.cfg.cursor_deadband - 1),
             lambda: app.set_deadband(app.cfg.cursor_deadband + 1),
             app.set_deadband),
        ):
            row = ctk.CTkFrame(side, fg_color="transparent", border_width=0)
            row.pack(fill="x", padx=18, pady=5)
            text = ctk.CTkFrame(row, fg_color="transparent", border_width=0)
            text.pack(side="left")
            ctk.CTkLabel(text, text=label, anchor="w", height=18).pack(anchor="w")
            ctk.CTkLabel(
                text, text=hint, anchor="w", height=14, font=small, text_color=_MUTED
            ).pack(anchor="w")
            # packed right to left: - value +
            ctk.CTkButton(
                row, text="+", width=32, height=30, font=_mono(13), command=plus
            ).pack(side="right")
            value = ctk.CTkEntry(row, width=56, height=30, justify="center", font=_mono(12))
            value.pack(side="right", padx=4)
            value.bind("<FocusIn>", lambda _e, key=key: self._editing.add(key))
            value.bind("<FocusOut>", lambda _e, key=key, f=typed: self._commit(key, f))
            value.bind("<Return>", lambda _e: self.root.focus_set())  # -> FocusOut
            value.bind("<Escape>", lambda _e, key=key: self._cancel(key))
            ctk.CTkButton(
                row, text="−", width=32, height=30, font=_mono(13), command=minus
            ).pack(side="right")
            self._values[key] = value

        buttons = ctk.CTkFrame(side, fg_color="transparent", border_width=0)
        buttons.pack(fill="x", padx=18, pady=(10, 0))
        ctk.CTkButton(
            buttons, text="RESET", width=100, font=_mono(11), border_color=_LINE,
            text_color=_MUTED, command=app.reset_tuning,
        ).pack(side="left", expand=True, fill="x", padx=(0, 5))
        ctk.CTkButton(
            buttons, text="SAVE", width=100, font=_mono(11, bold=True), command=app.save_tuning
        ).pack(side="left", expand=True, fill="x", padx=(5, 0))

        section("Gestures")
        self._gestures = ctk.CTkScrollableFrame(
            side, fg_color="transparent", border_width=0, height=120
        )
        self._gestures.pack(fill="both", expand=True, padx=8)
        self._gestures.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            side, text=_KEYS_HELP, justify="left", anchor="w", font=_mono(10), text_color=_MUTED
        ).pack(fill="x", padx=18, pady=(4, 12))

    # --- per frame --------------------------------------------------------------
    def show(self, frame_bgr, states, events: list[str], fps: float) -> None:
        """One camera frame, already drawn on, plus this frame's state."""
        if not self.alive:
            return
        h, w = frame_bgr.shape[:2]
        image = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        self._video_image = ctk.CTkImage(light_image=image, size=(w, h))
        self._video.configure(image=self._video_image)

        self._update_status(states, events, fps)
        self._update_controls()
        try:
            self.root.update()
        except tk.TclError:  # closed in the middle of it
            self.alive = False

    def _set(self, widget, **options) -> None:
        """configure() only when something changed: Tk redraws are not free."""
        if self._shown.get(id(widget)) != options:
            self._shown[id(widget)] = options
            widget.configure(**options)

    def _update_status(self, states, events: list[str], fps: float) -> None:
        app, engine = self.app, self.app.engine
        by_slot = {s.hand: s for s in states}
        for slot, (mode, detail) in self._chips.items():
            state = by_slot.get(slot)
            on = engine.face is not None if slot == FACE_SLOT else app.cfg.use_hand
            name = _SLOT_NAMES[slot]
            if not on or state is None:
                self._set(mode, text=f"{name}   OFF", text_color=_MUTED)
                self._set(detail, text="")
                continue
            # The arrow marks whoever holds the cursor right now.
            mark = "▸" if slot == engine.pointer_holder else " "
            idle = state.mode in ("NO HAND", "NO FACE")
            color = _MUTED if idle else (_GREEN if engine.enabled else _RED)
            self._set(mode, text=f"{name} {mark} {state.mode.upper()}", text_color=color)
            text = state.detail
            if state.cursor_source:
                text = f"[{state.cursor_source}] {text}".rstrip()
            self._set(detail, text=text)

        if not engine.enabled:
            self._badge_text(" PAUSED · MOUSE " if app._mouse_paused else " PAUSED ", _RED)
        elif app.cfg.dry_run:
            self._badge_text(" DRY-RUN ", _AMBER)
        else:
            self._badge_text("", _PANEL)
        self._set(self._fps, text=f"{fps:.0f} FPS")
        self._set(self._events, text="   ·   ".join(events[-3:]))

    def _badge_text(self, text: str, color: str) -> None:
        self._set(self._badge, text=text, text_color=color)
        self._set(self._badge_box, border_color=color)

    def _update_controls(self) -> None:
        app, cfg, engine = self.app, self.app.cfg, self.app.engine
        for part, switch in self._switches.items():
            on = bool(getattr(cfg, f"use_{part}"))
            if bool(switch.get()) != on:
                switch.select() if on else switch.deselect()
        _, beta_key = app.filter_keys()
        self._set_value("steady", f"{app.steadiness():g}")
        self._set_value("speed", f"{getattr(cfg, beta_key):g}")
        self._set_value("dead", f"{cfg.cursor_deadband:g}")
        target = engine.face.label if engine.face is not None else "hand"
        self._set(self._tuning_for, text=f"TUNING ▸ {target.upper()} POINTER")

        key = (cfg.use_hand, engine.face.label if engine.face else None, engine.dominant)
        if key != self._gesture_key:
            self._gesture_key = key
            self._fill_gestures()

    def _set_value(self, key: str, text: str) -> None:
        """A field shows the live value, except while it is being typed into."""
        entry = self._values[key]
        if key in self._editing or self._shown.get(id(entry)) == text:
            return
        self._shown[id(entry)] = text
        entry.delete(0, "end")
        entry.insert(0, text)

    def _commit(self, key: str, apply) -> None:
        self._editing.discard(key)
        try:
            apply(float(self._values[key].get().replace(",", ".")))
        except ValueError:
            pass  # not a number: the field goes back to the value in use
        self._shown.pop(id(self._values[key]), None)  # rewrite it, clamped and tidy

    def _cancel(self, key: str) -> None:
        # Not a number any more: FocusOut then finds nothing to apply.
        self._values[key].delete(0, "end")
        self.root.focus_set()

    def _fill_gestures(self) -> None:
        for child in self._gestures.winfo_children():
            child.destroy()
        row = 0
        for title, pairs in self.app.gesture_sections():
            ctk.CTkLabel(
                self._gestures, text=title.upper(), anchor="w", text_color=_MUTED,
                font=_mono(10, bold=True),
            ).grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2))
            row += 1
            for gesture, action in pairs:
                ctk.CTkLabel(
                    self._gestures, text=gesture, anchor="w", height=18,
                    font=ctk.CTkFont(size=12), text_color=_MUTED,
                ).grid(row=row, column=0, sticky="w", padx=(8, 6))
                ctk.CTkLabel(
                    self._gestures, text=action, anchor="e", height=18,
                    font=ctk.CTkFont(size=12),
                ).grid(row=row, column=1, sticky="e", padx=(6, 8))
                row += 1

    # --- input ------------------------------------------------------------------
    def _on_key(self, event) -> None:
        if isinstance(event.widget, tk.Entry):
            return  # typing a value, not a shortcut
        app = self.app
        action = {
            "q": app.stop,
            "escape": app.stop,
            "p": app.toggle_pause,
            "h": app.toggle_skeleton,
            "d": app.swap_dominant,
            "o": app.toggle_overlay,
            "c": app.recalibrate,
        }.get(event.keysym.lower())
        if action is not None:
            action()

    def _close(self) -> None:
        self.app.stop()

    def close(self) -> None:
        self.alive = False
        try:
            self.root.destroy()
        except tk.TclError:
            pass
