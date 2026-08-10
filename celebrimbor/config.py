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

    # --- two-finger gesture (index + middle) ---
    axis_lock_travel: float = 0.025   # travel before the axis is picked
    axis_deadzone: float = 0.004      # per-frame movement below which we ignore

    # --- two-finger rotation (same pose, hand turned like a key) ---
    # The rotation competes with the sliding for the axis lock: whichever
    # crosses its own threshold first wins, and the other one stays quiet until
    # the fingers are lowered.
    # The angle is small and the pause short because the default binding is the
    # volume: one step every 20 degrees turns the hand into a usable knob,
    # whereas a wide angle would need half a turn per notch.
    rotate_window: float = 0.6        # time window over which the turn is measured
    rotate_min_angle: float = 20.0    # degrees of turn needed to fire
    rotate_cooldown: float = 0.15     # pause after a recognised rotation

    # --- misc ---
    show_preview: bool = True
    # Small always-on-top card with the state of both hands: unlike the preview
    # window it stays visible over whatever app you are actually using.
    overlay: bool = True
    dry_run: bool = False  # recognises gestures but never touches the real mouse
    grace_frames: int = 6  # frames without a hand tolerated before resetting state
