"""Configuration parameters, all gathered in one place."""

from dataclasses import dataclass


@dataclass
class Config:
    # --- webcam ---
    camera_index: int = 0
    frame_width: int = 640
    frame_height: int = 480
    # Without an explicit request many webcams stay at 10 fps: asking for it
    # unlocks 30 fps and makes the cursor far more responsive.
    target_fps: int = 30
    mirror: bool = True  # mirrored image: move right -> cursor goes right

    # --- hand detection ---
    model_path: str | None = None  # None = default model under models/
    # Two hands tracked at once, each with its own bindings (sections "left"
    # and "right" in gestures.json). Set to 1 to track a single hand: it costs
    # a little less CPU, and the hand still lands in its own slot.
    num_hands: int = 2
    # Hand holding the pointer when both are in frame. On its own, either hand
    # drives the cursor. Ctrl+Alt+Space swaps it while the program is running.
    dominant_hand: str = "right"  # right | left
    # High threshold: a false detection would send the cursor flying.
    min_detection_confidence: float = 0.75
    min_tracking_confidence: float = 0.5

    # --- active area of the frame mapped onto the screen ---
    # Fraction of the frame used as a "tablet": shrinking it lets you reach the
    # screen edges without leaving the camera's field of view.
    active_x_min: float = 0.22
    active_x_max: float = 0.78
    active_y_min: float = 0.18
    active_y_max: float = 0.72

    # --- One Euro filter (cursor smoothing) ---
    min_cutoff: float = 1.2   # lower = smoother but slower
    beta: float = 0.03        # higher = less latency on fast movements
    d_cutoff: float = 1.0

    # --- pinch (distance normalised by hand size) ---
    pinch_on: float = 0.38    # below this threshold the pinch is closed
    pinch_off: float = 0.52   # above this threshold the pinch is open (hysteresis)

    # --- click and drag ---
    # Seconds between two consecutive clicks. It is what makes a double click
    # possible at all: Windows joins two clicks into one only if they arrive
    # within its double-click time (500 ms by default), so a longer cooldown
    # than this leaves almost no room to pinch twice in time. Raise it if a
    # single pinch ever fires twice.
    click_cooldown: float = 0.18
    drag_hold: float = 0.45        # pinch held longer than this -> drag

    # --- cursor anchoring during a pinch ---
    # Closing the fingers to click moves the index fingertip anyway: as soon as
    # the fingers start closing in, the cursor switches to a point on the palm,
    # which stays put during a pinch. The switch is jump-free because the offset
    # between the two points is frozen and then blended away.
    #
    # The threshold is not absolute but relative to the habitual finger opening,
    # measured continuously: a fixed threshold would stay latched forever for a
    # hand that naturally keeps its fingers close together.
    anchor_point: str = "palm_outer"  # palm_outer | palm_center | pinky_mcp | index_mcp | wrist
    anchor_window: float = 1.5     # seconds over which the habitual opening is measured
    # Better to anchor early: the switch is invisible, while the index drift
    # accumulated before anchoring sticks around.
    anchor_ratio_on: float = 0.80  # closed below 80% of the opening -> anchor
    anchor_ratio_off: float = 0.92 # reopened above 92% -> back to the index finger
    anchor_blend: float = 0.20     # seconds to blend the offset away on release

    # --- closed-fist gestures ---
    # Distances are expressed in "hands" (multiples of the hand size) so the
    # thresholds do not depend on how far you are from the webcam.
    swipe_window: float = 0.35        # time window used to evaluate a flick
    swipe_min_travel: float = 1.1     # minimum travel for a swipe
    swipe_cooldown: float = 0.8       # pause after a recognised swipe
    fist_still_travel: float = 0.20   # movement above which the fist is not still
    fist_hold_seconds: float = 5.0    # how long to hold the fist still

    # --- fingers held out (index + middle, or those two plus the ring) ---
    # The two poses are the same gesture with one more finger out, and share
    # every parameter below: only the actions they are bound to differ.
    axis_lock_travel: float = 0.025   # travel before the axis is picked
    axis_deadzone: float = 0.004      # per-frame movement below which we ignore
    # The opening of a circle *is* a slide - no measurement can tell them apart
    # before the path has come round far enough, a gentle arc being exactly what
    # an arm sliding on its elbow draws. So the slide is not guessed at: for
    # this long its movement is held back instead, and either a circle shows up
    # and the lot is dropped, or it comes out in one go with nothing lost. It
    # only costs this delay once, at the start of the gesture.
    axis_lock_hold: float = 0.45
    # Frames of broken pose tolerated before the gesture is really over. Without
    # them a single frame reading a curled finger as extended - it happens, the
    # finger sits right on the threshold - would wipe a half-drawn circle.
    two_finger_grace: int = 4

    # --- circle (same poses, fingertips drawing a circle) ---
    # The two fingers keep pointing the same way and the hand travels around a
    # circle: it is the path that turns, not the hand. Radii are in "hands", so
    # the circle does not have to grow when you sit further back.
    circle_window: float = 2.0        # length of path kept under observation
    circle_min_samples: int = 12      # too few points fit a circle to anything
    circle_min_radius: float = 0.35   # below this it is hand tremor, not a circle
    circle_max_radius: float = 2.0    # above this it is an arm sliding on its elbow
    circle_tolerance: float = 0.18    # how far off the fitted circle the path may sit
    # The one measurement that really separates a circle from a slide. Half a
    # turn of a circle this small is a hand's worth of travel; half a turn of
    # the arc an arm draws sliding sideways would take it out of the frame.
    # It also sets the slowest circle that can be recognised at all: the arc has
    # to fit inside `circle_window`, so about 5 seconds per turn.
    circle_min_span: float = 150.0    # degrees of arc before it counts as a circle
    circle_aim_drift: float = 40.0    # degrees the fingers may swing while circling
    circle_step_angle: float = 90.0   # degrees of arc per event
    # Steps swallowed before the first event. The span above already proves it
    # is a circle, so these only set how long it takes to engage: the arc
    # already drawn counts towards them, which puts the first event within a
    # quarter turn of the circle being recognised.
    circle_arm_steps: int = 2
    circle_cooldown: float = 0.10     # pause after an event

    # --- misc ---
    show_preview: bool = True
    # Small always-on-top card with the state of both hands: unlike the preview
    # window it stays visible over whatever app you are actually using.
    overlay: bool = True
    dry_run: bool = False  # recognises gestures but never touches the real mouse
    grace_frames: int = 6  # frames without a hand tolerated before resetting state
