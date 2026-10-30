# Celebrimbor

**Control your computer with hand gestures and a webcam.** Windows and macOS
(Apple Silicon).

Move the mouse, the volume and the windows by moving your hand in front of the
camera - or point with your **head** or your **eyes** and click with a wink.
Runs entirely on your machine: no account, no cloud, no extra hardware.

Stack: **OpenCV** (capture) + **MediaPipe HandLandmarker** (21 hand landmarks) +
**MediaPipe FaceLandmarker** (478 face landmarks, irises included, only for the
head and eye pointer) + **pynput** (real mouse and keyboard).

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

On macOS:

```
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

See [macOS](#macos) for the permissions it needs.

## Recognised gestures

| Gesture | Name in the JSON | Default action |
|---|---|---|
| Open hand moving around | `point_move` | Moves the cursor |
| Pinch **thumb + index**, short tap | `pinch_index_tap` | Left click |
| Pinch **thumb + index** held > 0.45 s | `pinch_index_hold` | Drag |
| Pinch **thumb + middle**, short tap | `pinch_middle_tap` | Right click |
| Pinch **thumb + ring** | `pinch_ring_tap` / `_hold` | *(available, not bound - a good spot for `double_click`)* |
| **Fist** flicked left | `fist_swipe_left` | `prev_window` (`Alt+Shift+Tab` / `Cmd+Shift+Tab`) |
| **Fist** flicked right | `fist_swipe_right` | `next_window` (`Alt+Tab` / `Cmd+Tab`) |
| **Fist** flicked up | `fist_swipe_up` | `show_desktop` (`Win+D` / `F11`) |
| **Fist** flicked down | `fist_swipe_down` | `show_desktop` (`Win+D` / `F11`) |
| **Fist** closed and still for 5 s | `fist_hold` | Play / Pause |
| **Index + middle** open, hand up/down | `two_finger_vertical` | Scroll, like the wheel |
| **Index + middle** open, hand right/left | `two_finger_horizontal` | Volume up / down |
| **Index + middle** open, drawing a clockwise circle | `two_finger_circle_cw` | Volume up |
| **Index + middle** open, drawing an anticlockwise circle | `two_finger_circle_ccw` | Volume down |
| **Index + middle + ring** open, hand up/down | `three_finger_vertical` | Volume up / down |
| **Index + middle + ring** open, hand right/left | `three_finger_horizontal` | *(available, not bound - virtual desktops are a good fit)* |
| **Index + middle + ring** open, drawing a clockwise circle | `three_finger_circle_cw` | Next track |
| **Index + middle + ring** open, drawing an anticlockwise circle | `three_finger_circle_ccw` | Previous track |

Practical notes:

- With a closed fist the cursor stays put: that is also how you reposition your
  hand without dragging the pointer along.
- A pinch not bound to any action produces no events, but it is measured all the
  same: it is what tells the clicks apart (touching the ring finger with the
  thumb brings the middle finger close too, and without the comparison the wrong
  click would fire). The genuinely tightest pinch always wins.
- **Two fingers and three fingers are the same gesture with a different hand
  shape**: same axes, same circle, same thresholds, each with its own set of
  actions. They never overlap - two fingers means the ring finger is down - and
  raising or lowering that finger mid-air ends one gesture and starts the other
  from scratch, so a half-drawn circle is never carried across.
- A pose with **nothing bound to it is not watched at all**: unbind all four
  `three_finger_*` names and three fingers in the air are just a hand that
  points and clicks, exactly as before.
- With the fingers open the **axis locks** on the first decisive
  movement: a diagonal movement will not fire scroll and volume together, and
  the same lock keeps sliding and circling apart. Lower the fingers to release
  it and pick another direction.
- The circle is drawn with the **hand travelling**, the fingers going on
  pointing the same way throughout - it is not the hand turning like a key. Make
  it about **half a hand wide or more** and keep going: past the first turn
  every quarter of a circle fires one event, so it works as a **volume knob**.
- The opening of a circle is indistinguishable from a slide - an arm sliding
  sideways pivots on the elbow and draws a clean arc of its own - so the slide
  is held back for 0.45 s before its first event. Nothing is lost, it comes out
  in one go, but the very start of a scroll arrives a moment late. Draw circles
  at a decent pace (**about a second per turn**): a much slower one gets a bit
  of scrolling in before it is recognised. `axis_lock_hold` buys more room if
  you would rather draw them slowly.
- Volume has two controls, on purpose: sliding sideways is quick, the circle
  does not need any particular room and never runs out of it. Bind
  `two_finger_horizontal` to something else (or to `"none"`) if you would rather
  have that axis free - and to `"none"` on both axes if you only want circles.
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

## Pointing with the head or the eyes

The cursor can follow your face instead of your hand:

```
python main.py --pointer head     # the nose works as a joystick
python main.py --pointer eyes     # the cursor goes where you look
```

or `"pointer": "head"` in `settings`. On the first run the
`face_landmarker.task` model (~4 MB) is downloaded into `models/`.

- **`head`** - turn your head and the cursor follows, about 17 degrees from the
  centre to the edge of the screen. Steady and precise: the one to start with.
- **`eyes`** - the pupils, measured against the corners of each eye, plus most
  of the head's turn (you rarely look at a corner without turning towards it a
  little). Hands-free and nearly head-free, but a webcam sees the pupil at a
  handful of pixels: it aims at buttons and icons, not at characters, and it
  wants good, even light on the face.

**Calibration.** For the first 1.5 seconds the program measures your neutral
pose: **look at the centre of the screen** while the `CALIBRATING` bar fills.
Every movement afterwards is measured from there, so it does not matter how you
sit. If you shift in your chair, or the cursor no longer sits in the centre when
you look at the centre, press **`Ctrl+Alt+C`** (or `c` with the preview
focused) and calibrate again.

**Clicks come from the eyelids**, bound in the `face` section of
[gestures.json](gestures.json):

| Face gesture | Name in the JSON | Default action |
|---|---|---|
| Head / eyes moving | `face_move` | Moves the cursor |
| **Left** eye winked | `wink_left_tap` | Left click |
| **Left** eye shut for 0.6 s | `wink_left_hold` | Drag, until the eye reopens |
| **Right** eye winked | `wink_right_tap` | Right click |
| **Right** eye shut for 0.6 s | `wink_right_hold` | *(available, not bound)* |
| Both eyes shut for 0.8 s | `blink_long` | *(available, not bound - `double_click` fits)* |
| Mouth opened and closed | `mouth_open_tap` | *(available, not bound)* |
| Mouth held open | `mouth_open_hold` | *(available, not bound)* |
| Mouth open, head moving up / down | `mouth_scroll_vertical` | Scroll: the cursor stops, the head scrolls |
| Mouth open, head moving left / right | `mouth_scroll_horizontal` | *(available, not bound - `scroll_h` fits)* |

- **Blinking never clicks.** An ordinary blink shuts both eyes, and a wink is
  one eye shut *with the other open*. A blink ends with one eye reopening a
  frame before the other, which looks like the start of a wink: an eye has to
  have reopened after a blink before it can wink again, so that frame never
  counts.
- **The click lands where you were aiming.** Shutting an eye drags the pupil
  with the eyelid and nods the head a touch. So as soon as an eye starts
  closing the cursor goes back to where it was 0.2 s earlier (`face_rewind`) and
  stays there until the eye is open again - the same idea as the palm anchoring
  of the hand pointer.
- **Eyes are not all the same.** Shut is measured against how open *your* eye
  habitually is, the maximum over the last two seconds, not against a fixed
  threshold: it works with narrow eyes, wide eyes and glasses alike. The
  `LEYE` / `REYE` bars in the preview show each eye's opening, with the red
  tick where it counts as shut.
- **With an eye shut only the nose steers**: with `--pointer eyes`, during a
  wink (click) or a wink-drag the pupils are ignored - the lid drags them - and
  the cursor moves from where it was with the nose alone, as in head mode.
- **Not everyone can wink one eye at a time.** Bind the mouth instead:
  `"mouth_open_tap": "left_click", "mouth_open_hold": "drag"` - mind that it
  then fires when you talk or yawn.

**The hands keep working.** With the face in charge they only lose the cursor:
pinching still clicks, two fingers still scroll, the fist still switches
windows. Aim with the head and pinch to click, if you prefer that to winking.
To track the face alone, and save most of the CPU of the hand model, use
`--hands 0`: the hands are then only watched for the pause fists.

The face tracker follows **one face**, the most prominent: someone walking past
behind you cannot take the cursor.

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
    "fist_swipe_up": "next_window",
    "fist_hold": "play_pause",
    "two_finger_vertical":     { "action": "scroll", "gain": 55 },
    "two_finger_horizontal":   { "action": "volume", "gain": 14 },
    "two_finger_circle_cw":    "volume_up",
    "two_finger_circle_ccw":   "volume_down",
    "three_finger_vertical":   { "action": "volume", "gain": 14 },
    "three_finger_circle_cw":  "next_track",
    "three_finger_circle_ccw": "prev_track"
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
| `next_window`, `prev_window`, `next_desktop`, `prev_desktop`, `show_desktop` | The shortcut for it on the system in use (table below) |
| any key combination | `"alt+tab"`, `"ctrl+win+left"`, `"win+d"`, `"play_pause"`, `"volume_up"`, `"mute"`, `"next_track"`, `"f5"` |

The portable names send the right combination on each system, so one
`gestures.json` works on both:

| Name | Windows | macOS |
|---|---|---|
| `next_window` | `Alt+Tab` | `Cmd+Tab` |
| `prev_window` | `Alt+Shift+Tab` | `Cmd+Shift+Tab` |
| `next_desktop` | `Ctrl+Win+Right` | `Ctrl+Right` (next Space) |
| `prev_desktop` | `Ctrl+Win+Left` | `Ctrl+Left` (previous Space) |
| `show_desktop` | `Win+D` | `F11` |

Valid modifiers: `ctrl`, `alt`, `shift`, `win` (`cmd` on a Mac - `win`, `cmd`
and `super` are the same key). Special keys: arrows, `tab`,
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
`volume_down` at the two ends. To scroll through virtual desktops by moving
three fingers horizontally (the axis left free by the defaults):

```jsonc
"three_finger_horizontal": {
  "action": "axis",
  "positive": "next_desktop",
  "negative": "prev_desktop",
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
| `Ctrl+Alt+Q` | Quit (global, works even without window focus; `Ctrl+Option` on a Mac) |
| `Ctrl+Alt+P` | Pause / resume control (global) |
| `Ctrl+Alt+Space` | Move the pointer to the other hand (global) |
| `Ctrl+Alt+C` | Calibrate the head / eye pointer again (global) |
| `q` or `Esc` | Quit (with the preview window focused) |
| `p` | Pause |
| `h` | Show/hide the hand skeleton |
| `d` | Move the pointer to the other hand |
| `o` | Show/hide the always-on-top overlay |
| `c` | Calibrate the head / eye pointer again |
| **Both fists** closed for 3 s | Pause / resume, like `Ctrl+Alt+P` |

The two global shortcuts are the escape hatch: if the cursor goes wild,
`Ctrl+Alt+P` immediately hands control back to your real hand.

**Pausing with the fists.** Close both fists and hold them for 3 seconds
(`pause_fists_seconds`); a bar fills in the preview, and the same gesture
resumes. Two fists are reserved for this: while both are closed the hands do
nothing else. It works with `--hands 0` too, where the hands are watched one
frame in three for this alone; not with `--hands 1`, which only ever sees one.

The preview window has a column on the right with the gestures currently
bound to the pointer and the keys above.

## macOS

Celebrimbor runs on Macs with Apple Silicon (M1 and later). The release zip
`celebrimbor-<version>-macos-arm64.zip` holds a `celebrimbor` folder: run
`./celebrimbor` inside it from Terminal.

**First launch.** The app is not signed, so Gatekeeper blocks it, and every
library inside the folder with it. Clear the download flag once:

```
xattr -dr com.apple.quarantine celebrimbor
```

**Permissions.** macOS asks for them on first use, or grant them in System
Settings → Privacy & Security, to **Terminal** (or whatever app you start
Celebrimbor from):

| Permission | What for |
|---|---|
| Camera | The webcam |
| Accessibility | Moving the mouse, clicking, sending keys |
| Input Monitoring | The global `Ctrl+Option` shortcuts |

Without Accessibility the program runs, sees you, and does nothing: the cursor
does not move. Restart Terminal after granting a permission.

**Differences from Windows:**
- The global shortcuts are `Ctrl+Option+Q / P / C / Space`.
- Use the portable names (`next_window`, `show_desktop`, ...) in
  `gestures.json` and the fist flicks do the Mac thing: `Cmd+Tab`, Spaces,
  `F11`. Combinations written out by hand, such as `"alt+tab"`, are sent as
  written.
- `show_desktop` sends `F11`: it works when *Show Desktop* is bound to F11 in
  System Settings → Keyboard → Keyboard Shortcuts → Mission Control (the
  default).
- No always-on-top overlay: the preview window is the only HUD.

## Command-line options

```
main.py [--config gestures.json] [--camera 0]
        [--hands 2] [--dominant right] [--pointer hand]
        [--width 640] [--height 480] [--fps 30]
        [--no-preview] [--no-overlay] [--no-mirror] [--dry-run]
        [--smoothing 1.2] [--sensitivity 1.0] [--model PATH]
        [--face-model PATH] [--list-gestures]
```

Command-line options take precedence over the `settings` section of the JSON.

- `--hands` - how many hands to track at once, 1 or 2 (default 2); 0 tracks
  none, for the head or eye pointer on its own.
- `--pointer` - what moves the cursor: `hand` (default), `head` or `eyes`. See
  "Pointing with the head or the eyes".
- `--dominant` - which hand holds the pointer when both are in frame
  (default `right`). `Ctrl+Alt+Space` swaps it while the program runs.
- `--no-overlay` - do not show the always-on-top card (see above).
- `--dry-run` - recognises gestures and shows everything, but does **not** touch
  the mouse and keyboard. Use it to tune the thresholds safely.
- `--sensitivity` - > 1 shrinks the active area (you barely have to move your
  hand), < 1 widens it (more precision, more movement). It scales the head and
  eye ranges the same way.
- `--smoothing` - the filter's cutoff frequency: low values (0.5) give a very
  smooth but slower cursor, high values (3) a more responsive but jumpier one.
  It applies to the head and eye pointer too.

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
above every other window, with the mode of each hand and the last events
(Windows only, for now):

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
| [celebrimbor/face.py](celebrimbor/face.py) | From the face landmarks to head turn, pupils, eyelids, mouth |
| [celebrimbor/face_gestures.py](celebrimbor/face_gestures.py) | Head / eye pointer, winks and calibration |
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
- `axis_lock_travel` / `axis_deadzone` - when the axis of a slide is decided and
  below which movement it is ignored. These, and everything below down to
  `two_finger_grace`, are shared by the two-finger and three-finger poses: they
  are one gesture with two hand shapes.
- `axis_lock_hold` - how long the slide waits before its first event, to give a
  circle time to show itself. Lower it for a snappier scroll and a bit of
  scrolling at the start of every circle, raise it for the opposite.
- `circle_min_radius` / `circle_max_radius` - how wide the circle has to be, in
  "hands". Outside this range it is hand tremor on one side and an arm sliding
  on its elbow on the other.
- `circle_min_span` / `circle_window` - degrees of arc before a path counts as a
  circle at all, and how much of it is kept under observation. This is what
  really separates a circle from a slide, and together they set the slowest
  circle that can be recognised (150 degrees inside 2 s, so about 5 s per turn).
- `circle_step_angle` / `circle_arm_steps` / `circle_cooldown` - one event per
  quarter turn, the first two swallowed to engage. The arc already drawn counts
  towards them, so the first event lands within a quarter turn of the circle
  being recognised.
- `circle_tolerance` / `circle_aim_drift` - how far off a true circle the path
  may sit, and how much the fingers may swing while going round. Raise the
  second one if circles drawn with the wrist rather than the arm are missed.
- `two_finger_grace` - frames of broken pose tolerated before the gesture ends,
  for both poses. A curled finger sits right on the threshold that decides
  whether it is extended, so it flickers; without this a half-drawn circle would
  be lost. It never lets a pose keep a hand that is plainly in the other one.
- `num_hands` / `dominant_hand` - how many hands to track, and which one keeps
  the pointer when both are in frame.
- `active_x_*` / `active_y_*` - the portion of the frame used as a tablet.
- `min_cutoff` / `beta` - One Euro filter: low `min_cutoff` = smoother, high
  `beta` = less latency on fast movements.

### Head and eye pointer

- `pointer` - `hand`, `head` or `eyes`.
- `face_calibration_seconds` - how long the neutral pose is measured.
- `head_range_x` / `head_range_y` - nose travel, in eye distances, from the
  centre to the edge of the screen (0.15 is about 17 degrees of head turn).
  Lower them to move your head less.
- `eyes_range_x` / `eyes_range_y` - pupil travel, in eye widths, that does the
  same. `eyes_head_gain` - how much the head's turn counts in eyes mode (0 =
  pupils only).
- `head_min_cutoff` / `eyes_min_cutoff` / `face_beta` - One Euro filter of the
  face cursor; the pupils jitter far more than the head and get a much lower
  cutoff. Lower cutoff = steadier when still; higher beta = less lag on fast
  movements.
- `pause_fists_seconds` - how long both fists must stay closed to pause.
- `eye_window` / `eye_closed_ratio` / `eye_open_ratio` - when an eye counts as
  shut, relative to its habitual opening.
- `wink_min` / `wink_max` / `face_hold` / `blink_long_seconds` - how long a wink
  has to last, when it becomes a drag, and the long blink.
- `mouth_open_on` / `mouth_open_off` - lip gap, in eye distances.
- `face_rewind` / `face_settle` - how far back the cursor goes when an eye
  starts closing, and how long it waits after it reopens.

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
   fingers held out (two of them or three, same machinery), adaptive cursor
   anchoring.
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
| `face_landmarker.task` model | Google | Apache 2.0 | Face and iris landmarks for the head / eye pointer, downloaded the same way |
| [OpenCV](https://opencv.org/) (`opencv-python`) | OpenCV team | Apache 2.0 | Webcam capture and preview window |
| [pynput](https://github.com/moses-palmer/pynput) | Moses Palmér | LGPL v3 | Mouse and keyboard control, global hotkeys |
| [NumPy](https://numpy.org/) | NumPy developers | BSD 3-Clause | Numerical foundation (dependency of OpenCV and MediaPipe) |

The smoothing algorithm is the **1€ (One Euro) Filter**, reimplemented here from
the description in the original paper:

> Géry Casiez, Nicolas Roussel, Daniel Vogel.
> *1€ Filter: A Simple Speed-based Low-pass Filter for Noisy Input in
> Interactive Systems.* CHI 2012, pp. 2527-2530.
> <https://gery.casiez.net/1euro/>

The MediaPipe models are downloaded at runtime and is **not** included in the
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
