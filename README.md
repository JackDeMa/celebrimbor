# Celebrimbor

**Control your Windows PC with hand gestures and a webcam.**

Move the mouse, the volume and the windows by moving your hand in front of the
camera. Runs entirely on your machine: no account, no cloud, no extra hardware.

Stack: **OpenCV** (capture) + **MediaPipe HandLandmarker** (21 hand landmarks) +
**pynput** (real mouse and keyboard).

Every gesture is bound to its action in [gestures.json](gestures.json):
recognition and action are kept separate, so you can remap everything without
touching the code. **Both hands are tracked at once**, each with its own
bindings: with both in frame the pointer stays on your dominant hand, and the
other is free for the rest.

## Quick start

```
start.bat
```

On the first run it creates `.venv`, installs the dependencies and downloads the
`hand_landmarker.task` model (~8 MB) into `models/`.

Manually:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

## Recognised gestures

| Gesture | Name in the JSON | Default action |
|---|---|---|
| Open hand moving around | `point_move` | Moves the cursor |
| Pinch **thumb + index**, short tap | `pinch_index_tap` | Left click |
| Pinch **thumb + index** held > 0.45 s | `pinch_index_hold` | Drag |
| Pinch **thumb + middle**, short tap | `pinch_middle_tap` | Right click |
| Pinch **thumb + ring** | `pinch_ring_tap` / `_hold` | *(available, not bound - a good spot for `double_click`)* |
| **Fist** flicked left | `fist_swipe_left` | `Ctrl+Win+Left` (previous desktop) |
| **Fist** flicked right | `fist_swipe_right` | `Ctrl+Win+Right` (next desktop) |
| **Fist** flicked up | `fist_swipe_up` | `Alt+Tab` |
| **Fist** flicked down | `fist_swipe_down` | `Alt+Shift+Tab` |
| **Fist** closed and still for 5 s | `fist_hold` | Play / Pause |
| **Index + middle** open, hand up/down | `two_finger_vertical` | Scroll, like the wheel |
| **Index + middle** open, hand right/left | `two_finger_horizontal` | Volume up / down |
| **Index + middle** open, hand turned clockwise | `two_finger_rotate_cw` | Volume up |
| **Index + middle** open, hand turned anticlockwise | `two_finger_rotate_ccw` | Volume down |

Practical notes:

- With a closed fist the cursor stays put: that is also how you reposition your
  hand without dragging the pointer along.
- A pinch not bound to any action produces no events, but it is measured all the
  same: it is what tells the clicks apart (touching the ring finger with the
  thumb brings the middle finger close too, and without the comparison the wrong
  click would fire). The genuinely tightest pinch always wins.
- With index and middle finger open the **axis locks** on the first decisive
  movement: a diagonal movement will not fire scroll and volume together, and
  the same lock keeps sliding and turning apart. Lower the fingers to release
  it and pick another direction.
- The rotation is measured on the direction of the two fingers, from the wrist:
  turn the hand like a key, pivoting on the wrist. Every 20 degrees fires one
  event, so it works as a **volume knob**: keep turning and the volume keeps
  going. The sliding, meanwhile, is judged on the wrist, which stays put while
  you only turn.
- Volume has two controls, on purpose: sliding sideways is quick, turning the
  hand is finer and does not need room to move. Bind `two_finger_horizontal` to
  something else (or to `"none"`) if you would rather have that axis free.
- After a swipe there is a 0.8 s pause, so `Alt+Tab` does not machine-gun.
- The `FIST STILL` bar in the preview shows the progress towards the 5 seconds.
- Every gesture is recognised on **both hands independently**: see below for how
  to give each hand its own bindings.

## Two hands at once

The two hands are tracked separately and never share any state: a pinch on one
hand cannot cancel a drag on the other, and each has its own smoothing filter,
its own axis lock and its own copies of the actions.

What they do share is the machine. There is only one cursor, so **only one hand
at a time may move it**, otherwise the two would drag the pointer back and forth
every frame. Who gets it:

- **Both hands in frame** - the **dominant** hand holds the pointer, always. The
  other one is free for everything else: scrolling, volume, swipes between
  desktops.
- **One hand in frame** - that one holds it, whichever it is. So you can point
  with the hand you have free without changing any setting.
- **Mid-drag** - the pointer is never taken away from a hand holding the mouse
  button down. Bringing the other hand into frame while you drag would otherwise
  fling whatever you are dragging across the screen.

The dominant hand is the right one by default. Change it with `dominant_hand`
in `settings`, with `--dominant left`, or **on the fly with `Ctrl+Alt+Space`**
(the `d` key does the same with the preview focused). In the preview an
asterisk next to `L` or `R` marks the hand currently holding the pointer.

Both hands are bound to the whole gesture set by default, which is what lets
either one take the pointer when it is alone. The `left` and `right` sections of
[gestures.json](gestures.json) are laid on top of the shared `bindings` section
and let you specialise one hand:

```jsonc
"bindings": { /* ... applies to both hands ... */ },

// only the right hand ever clicks; the left one keeps scroll, volume and swipes
"left":  { "pinch_index_tap": "none", "pinch_index_hold": "none" },
"right": {}
```

The hands are named from **your** point of view, not the camera's: the mirrored
image flips the model's own idea of which hand it is looking at, and the program
flips it back (with `--no-mirror` there is nothing to flip).

To go back to a single hand use `--hands 1` (or `"num_hands": 1` in `settings`):
it costs a little less CPU, and either hand still works as the pointer.

## Double click

There is no double-click gesture, and there does not need to be: a real mouse
has no such signal either. It sends two clicks and **Windows** decides, joining
them if they land within its double-click time (500 ms by default,
`GetDoubleClickTime`) and close enough together on screen. Pinching twice in a
row does exactly that, and the cursor holds still between the two because it is
anchored to the palm during a pinch (see below).

The only thing in the way is `click_cooldown`, the guard against a single pinch
firing twice: at 0.18 s it leaves the whole 180-500 ms range to pinch again,
which is a comfortable double tap. Raise it if you get accidental repeats,
lower it if the second pinch does not always register.

If you would rather have a guaranteed double click, with no timing to hit, bind
one to a gesture of its own - the ring pinch is free by default:

```jsonc
"pinch_ring_tap": "double_click"
```

That sends both clicks in one go, so Windows always reads them as a pair.

## How the cursor stays put while you click

When you close your thumb onto your index finger to click, the index fingertip
always shifts a little: if it were the one driving the cursor, every click would
land a few dozen pixels off. So the program **changes its reference point**: as
soon as the fingers start closing, the cursor switches to following the outer
edge of the palm (pinky side, between wrist and pinky base), which does not move
during a pinch.

Two details make it seamless:

- **No jump at the switch.** The offset between the two points is frozen at the
  moment of the switch and added to the new reference, so the cursor stays
  exactly where it was; on the way back to the index finger the offset is
  blended away over `anchor_blend` seconds.
- **Relative threshold, not absolute.** The program continuously measures how
  far apart the fingers are at rest (the maximum over the last `anchor_window`
  seconds) and anchors when they close below 80% of that value. With a fixed
  threshold, anyone who naturally keeps their fingers curled would stay anchored
  forever. While a click or a drag is in progress the reference freezes,
  otherwise a long drag would eat it.

The preview shows all of it: a circle highlights the point currently driving the
cursor (green = index finger, red = palm), the header says `[index]` or `[palm]`,
and on each pinch bar the grey tick is the anchoring threshold (which moves on
its own) while the red one is the click threshold.

With `anchor_point` you can pick a different reference: `palm_outer` (default),
`palm_center`, `pinky_mcp`, `index_mcp`, `wrist`.

## Configuring the gestures

Everything lives in [gestures.json](gestures.json). Lines starting with `//` are
comments (local extension: plain JSON does not have them).

```jsonc
{
  "settings": {
    "fist_hold_seconds": 5.0,
    "swipe_min_travel": 1.1,
    "drag_hold": 0.45
  },
  "bindings": {
    "point_move": "move_cursor",
    "pinch_index_tap": "left_click",
    "fist_swipe_up": "alt+tab",
    "fist_hold": "play_pause",
    "two_finger_vertical":   { "action": "scroll", "gain": 55 },
    "two_finger_horizontal": { "action": "volume", "gain": 14 },
    "two_finger_rotate_cw":  "volume_up",
    "two_finger_rotate_ccw": "volume_down"
  },
  "left":  { "point_move": "none" },
  "right": {}
}
```

`bindings` applies to both hands; `left` and `right` override it for one hand
only, with `"none"` to switch a gesture off on that side.

An always up-to-date list of gestures and actions:

```
python main.py --list-gestures
```

### Actions as a string

| String | Effect |
|---|---|
| `move_cursor` | Moves the pointer |
| `left_click`, `right_click`, `middle_click`, `double_click` | Clicks |
| `drag` | Holds the left button down |
| `scroll`, `scroll_h` | Vertical / horizontal wheel |
| `volume` | Volume up/down, proportional to the movement |
| `none` | Disables the gesture |
| any key combination | `"alt+tab"`, `"ctrl+win+left"`, `"win+d"`, `"play_pause"`, `"volume_up"`, `"mute"`, `"next_track"`, `"f5"` |

Valid modifiers: `ctrl`, `alt`, `shift`, `win`. Special keys: arrows, `tab`,
`enter`, `esc`, `space`, `home`, `end`, `pageup`, `pagedown`, `delete`,
`f1`-`f20`, plus the media keys (`play_pause`, `stop`, `next_track`,
`prev_track`, `volume_up`, `volume_down`, `mute`).

### Actions in extended form

| `action` | Parameters | Notes |
|---|---|---|
| `hotkey` | `keys` | Same as the string, but explicit |
| `scroll` | `gain`, `invert`, `horizontal` | `gain` = notches per screen width |
| `volume` | `gain` | Volume steps per unit of movement |
| `click` | `button`, `count` | `count: 2` for a double click |
| `axis` | `positive`, `negative`, `gain`, `max_rate` | Continuous movement -> repeated presses |

`axis` is the general case: `volume` is nothing but this, with `volume_up` and
`volume_down` at the two ends. To scroll through virtual desktops by moving two
fingers horizontally:

```jsonc
"two_finger_horizontal": {
  "action": "axis",
  "positive": "ctrl+win+right",
  "negative": "ctrl+win+left",
  "gain": 6,
  "max_rate": 3
}
```

If an entry is wrong the program does not fall over: it prints a precise warning
(unknown gesture, unknown key, action incompatible with the gesture kind) and
carries on with the rest of the configuration.

### Gesture kinds

Every gesture has a kind, and only accepts compatible actions:

| Kind | Meaning | Allowed actions |
|---|---|---|
| `cursor` | Position on the screen | `move_cursor` |
| `trigger` | Instantaneous event | clicks, hotkeys |
| `hold` | On/off state | `drag` |
| `axis` | Continuous movement | `scroll`, `volume`, `axis` |

## Commands

| Key | Effect |
|---|---|
| `Ctrl+Alt+Q` | Quit (global, works even without window focus) |
| `Ctrl+Alt+P` | Pause / resume control (global) |
| `Ctrl+Alt+Space` | Move the pointer to the other hand (global) |
| `q` or `Esc` | Quit (with the preview window focused) |
| `p` | Pause |
| `h` | Show/hide the hand skeleton |
| `d` | Move the pointer to the other hand |
| `o` | Show/hide the always-on-top overlay |

The two global shortcuts are the escape hatch: if the cursor goes wild,
`Ctrl+Alt+P` immediately hands control back to your real hand.

## Command-line options

```
main.py [--config gestures.json] [--camera 0]
        [--hands 2] [--dominant right]
        [--width 640] [--height 480] [--fps 30]
        [--no-preview] [--no-overlay] [--no-mirror] [--dry-run]
        [--smoothing 1.2] [--sensitivity 1.0] [--model PATH]
        [--list-gestures]
```

Command-line options take precedence over the `settings` section of the JSON.

- `--hands` - how many hands to track at once, 1 or 2 (default 2).
- `--dominant` - which hand holds the pointer when both are in frame
  (default `right`). `Ctrl+Alt+Space` swaps it while the program runs.
- `--no-overlay` - do not show the always-on-top card (see above).
- `--dry-run` - recognises gestures and shows everything, but does **not** touch
  the mouse and keyboard. Use it to tune the thresholds safely.
- `--sensitivity` - > 1 shrinks the active area (you barely have to move your
  hand), < 1 widens it (more precision, more movement).
- `--smoothing` - the filter's cutoff frequency: low values (0.5) give a very
  smooth but slower cursor, high values (3) a more responsive but jumpier one.

## Preview

The window shows the hand skeletons, the rectangle of the **active area** (the
portion of the frame mapped onto the whole screen), the current mode of each
hand with the reference point in use, the fps, the `IDX` / `MID` / `RNG` bars
with each pinch distance (red tick = click threshold, grey tick = palm anchoring
threshold) and the `FIST STILL` bar while you hold your fist closed. Only the
bars of pinches actually bound to an action are shown.

The readouts are split in two columns, `L` on the left and `R` on the right,
each on the same side of the frame as its hand; an asterisk marks the hand
holding the pointer. Recognised events are listed on the right with the initial
of the hand that fired them.

## Always-on-top overlay

The preview window ends up behind whatever you are actually working in, which is
exactly when you would want to know what the program thinks your hands are
doing. So there is also a small card in the bottom-right corner of the screen,
above every other window, with the mode of each hand and the last events:

```
L  TWO FINGERS
R* POINTING
────────────────
R LEFT CLICK
```

It never takes part in the interaction it describes: **clicks go straight
through it**, it never steals the keyboard focus and it stays out of `Alt+Tab`.
The click-through is not cosmetic - a window that swallowed clicks would swallow
the ones the program itself generates, right where the cursor is.

Turn it off with `--no-overlay` (or `"overlay": false` in `settings`), or toggle
it with the `o` key with the preview focused. It costs about 0.6 ms per frame,
so it is not what limits the frame rate.

Two limits are not ours to fix: a game in **exclusive fullscreen** covers every
always-on-top window (borderless fullscreen is fine), and an unelevated process
cannot draw over windows elevated by **UAC**, nor over the secure desktop (the
UAC prompt itself, `Ctrl+Alt+Del`). Built on `tkinter` plus a few Win32 flags
through `ctypes`, both from the standard library: no extra dependency.

## Structure

| File | Role |
|---|---|
| [main.py](main.py) | Command line |
| [gestures.json](gestures.json) | Gesture -> action mapping |
| [celebrimbor/config.py](celebrimbor/config.py) | Thresholds and parameters |
| [celebrimbor/detector.py](celebrimbor/detector.py) | MediaPipe Tasks + model download |
| [celebrimbor/hand.py](celebrimbor/hand.py) | From the 21 landmarks to extended fingers / pinches |
| [celebrimbor/gestures.py](celebrimbor/gestures.py) | Recognition: produces named events |
| [celebrimbor/bindings.py](celebrimbor/bindings.py) | Reading and validating the JSON |
| [celebrimbor/actions.py](celebrimbor/actions.py) | The runnable actions (clicks, keys, scroll) |
| [celebrimbor/engine.py](celebrimbor/engine.py) | Connects events to actions, one recogniser per hand |
| [celebrimbor/controller.py](celebrimbor/controller.py) | The real mouse |
| [celebrimbor/filters.py](celebrimbor/filters.py) | One Euro filter (anti-jitter) |
| [celebrimbor/overlay.py](celebrimbor/overlay.py) | Always-on-top click-through card |
| [celebrimbor/app.py](celebrimbor/app.py) | Webcam loop + preview |

## Tuning

The thresholds live in [celebrimbor/config.py](celebrimbor/config.py) and can be
overridden from the `settings` section of the JSON:

- `pinch_on` / `pinch_off` - how close the fingers must be for a click (double
  threshold: avoids flicker around the limit).
- `click_cooldown` - the shortest gap between two clicks (0.18 s). It is what
  makes double clicking possible, see below. Raise it if a single pinch ever
  fires twice.
- `drag_hold` - how long to hold the pinch before it becomes a drag.
- `anchor_point` / `anchor_ratio_on` / `anchor_ratio_off` / `anchor_window` /
  `anchor_blend` - the cursor anchoring to the palm during a pinch (see above).
  Set `anchor_ratio_on` to 0 to disable it and go back to following the index
  finger only.
- `swipe_min_travel` / `swipe_window` / `swipe_cooldown` - how wide and how fast
  the fist flick has to be. Distances are in "hands", i.e. multiples of the size
  of the hand in frame: the threshold does not change if you move closer to or
  farther from the webcam.
- `fist_still_travel` / `fist_hold_seconds` - how still, and for how long.
- `axis_lock_travel` / `axis_deadzone` - when the two-finger axis is decided and
  below which movement it is ignored.
- `rotate_min_angle` / `rotate_window` / `rotate_cooldown` - degrees of turn that
  fire one rotation event, over how long a window they have to be made, and the
  pause afterwards. Together they set how fast the volume climbs: 20 degrees
  every 0.15 s at most. Raise `rotate_min_angle` if a rotation slips out while
  you are scrolling, lower it for a lighter turn of the wrist.
- `num_hands` / `dominant_hand` - how many hands to track, and which one keeps
  the pointer when both are in frame.
- `active_x_*` / `active_y_*` - the portion of the frame used as a tablet.
- `min_cutoff` / `beta` - One Euro filter: low `min_cutoff` = smoother, high
  `beta` = less latency on fast movements.

## Notes

- Decent light is needed: in the dark the webcam drops its frame rate and the
  tracking gets choppy. The program explicitly asks the webcam for 30 fps, which
  on many models would otherwise stay at 10.
- Two hands at a time (`num_hands=2`), the most prominent ones in frame. Only
  one of them can move the cursor at any moment: the dominant one when both are
  up, otherwise whichever is in frame.
- The image is mirrored: move your hand right, the cursor goes right. Use
  `--no-mirror` to invert it.

## How it works

The heavy lifting - finding the hand and its 21 landmarks in every frame - is
done by MediaPipe's **HandLandmarker** model, which runs locally on the CPU.
Everything else is code from this repository: interpreting those points as open
fingers, pinches and movements, and translating them into Windows commands.

The chain, frame by frame:

1. **OpenCV** reads the frame from the webcam (DirectShow) and mirrors it.
2. **MediaPipe HandLandmarker** (Tasks API, VIDEO mode) returns the 21
   normalised landmarks of each hand, plus which hand it is. When it labels both
   of them the same way, the more confident one keeps its slot.
3. [hand.py](celebrimbor/hand.py) reduces them to quantities independent of
   distance and rotation: extended fingers (comparing distances from the wrist,
   not the vertical coordinate alone), pinch distances divided by the hand size,
   palm reference points.
4. [gestures.py](celebrimbor/gestures.py) is the state machine, one instance per
   hand: hysteresis on the pinches, time window for the swipes, axis lock for the
   two fingers, adaptive cursor anchoring.
5. The **One Euro filter** ([filters.py](celebrimbor/filters.py)) damps the jitter
   without adding latency on fast movements.
6. [actions.py](celebrimbor/actions.py) executes: **pynput** for the cursor,
   clicks, wheel and key combinations, media keys included.

The code was written with **Claude Code** (Anthropic), in several passes: first
the mouse control, then the JSON configuration, then the cursor stabilisation
during clicks. The non-obvious choices (thresholds in "hands" rather than
pixels, relative instead of absolute anchoring, pinch disambiguation in favour
of the tightest one) came out of concrete problems found while testing, and are
annotated in the comments where they matter.

## Credits and licences

The code in this repository is released under the **MIT** licence: see
[LICENSE](LICENSE).

Beyond that, the project is an assembly of third-party components, each keeping
its own licence. The credit for the hand recognition goes entirely to MediaPipe.

| Component | Author | Licence | Role |
|---|---|---|---|
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) | Google | Apache 2.0 | Hand detection and the 21 landmarks |
| `hand_landmarker.task` model | Google | Apache 2.0 | Detector weights, downloaded from `storage.googleapis.com/mediapipe-models` |
| [OpenCV](https://opencv.org/) (`opencv-python`) | OpenCV team | Apache 2.0 | Webcam capture and preview window |
| [pynput](https://github.com/moses-palmer/pynput) | Moses Palmér | LGPL v3 | Mouse and keyboard control, global hotkeys |
| [NumPy](https://numpy.org/) | NumPy developers | BSD 3-Clause | Numerical foundation (dependency of OpenCV and MediaPipe) |

The smoothing algorithm is the **1€ (One Euro) Filter**, reimplemented here from
the description in the original paper:

> Géry Casiez, Nicolas Roussel, Daniel Vogel.
> *1€ Filter: A Simple Speed-based Low-pass Filter for Noisy Input in
> Interactive Systems.* CHI 2012, pp. 2527-2530.
> <https://gery.casiez.net/1euro/>

The MediaPipe model is downloaded at runtime and is **not** included in the
repository (see [.gitignore](.gitignore)); Google's Apache 2.0 licence applies.
Mind pynput's licence: it is **LGPL v3**. Installed from pip, as it is here, it
imposes nothing on this project; but bundling it inside a frozen executable
(PyInstaller and the like) does trigger the LGPL obligations, which require
leaving the user a way to swap the library out.

Code written with the assistance of **Claude Code** (Anthropic).

## About the name

**Celebrimbor** is Sindarin for *"Silver Hand"* - more literally *celeb* (silver)
+ *paur* (fist). He was the greatest smith of the Second Age, the one who forged
the Three Rings, and who inscribed on the Doors of Durin a password that opens
them without a touch.

A hand, a fist, and a door that answers to a signal: it seemed the right name for
a program that watches your hand and hands the commands to the machine.

Tolkien's names and works belong to the Tolkien Estate; this project has no
connection to it, nor to the video games that use the same character.
