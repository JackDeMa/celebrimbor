"""Self-check for the calibration state machine: python test_calibration.py."""

from celebrimbor.calibration import (
    MIN_SIDE,
    STILL_SECONDS,
    BothFistsWatch,
    Calibration,
    _clean,
)


class FakeState:
    def __init__(self, mode, progress=0.0):
        self.mode = mode
        self.fist_hold_progress = progress


def trace(calib, points, t0=0.0, step=1 / 30):
    """Feed a list of points one frame apart, returning the last Progress."""
    t = t0
    prog = None
    for p in points:
        prog = calib.update(p, t)
        t += step
    return prog, t


def hold(calib, point, t0, seconds=STILL_SECONDS + 0.2, step=1 / 30):
    """Hold the hand still at one point for `seconds`."""
    return trace(calib, [point] * int(seconds / step), t0, step)


def rect_path(x0, y0, x1, y1, n=10):
    """The four sides of a rectangle, n points each."""
    def edge(a, b):
        return [
            (a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
            for i in range(n)
        ]

    return (
        edge((x0, y0), (x1, y0))
        + edge((x1, y0), (x1, y1))
        + edge((x1, y1), (x0, y1))
        + edge((x0, y1), (x0, y0))
    )


def test_full_run():
    """Hold still, trace a rectangle, hold still: the box comes out."""
    c = Calibration()
    _, t = hold(c, (0.3, 0.3), 0.0)
    assert c.state == "DRAWING", c.state
    _, t = trace(c, rect_path(0.3, 0.3, 0.7, 0.7), t)
    _, t = hold(c, (0.3, 0.3), t)
    assert c.done and c.result is not None, c.message
    x0, y0, x1, y1 = c.result
    # The trim eats a little of the starting corner, so the box is close to the
    # traced one rather than exactly it.
    assert 0.28 < x0 < 0.42 and 0.28 < y0 < 0.42, c.result
    assert 0.6 < x1 <= 0.71 and 0.6 < y1 <= 0.71, c.result


def test_too_small_is_rejected():
    c = Calibration()
    _, t = hold(c, (0.5, 0.5), 0.0)
    _, t = trace(c, rect_path(0.5, 0.5, 0.53, 0.53), t)
    _, t = hold(c, (0.5, 0.5), t)
    assert c.done and c.result is None
    assert "too small" in c.message


def test_off_frame_is_rejected():
    """A box running into the frame edge is not a box you drew on purpose."""
    c = Calibration()
    _, t = hold(c, (0.5, 0.5), 0.0)
    _, t = trace(c, rect_path(0.0, 0.1, 0.9, 0.9), t)
    _, t = hold(c, (0.0, 0.1), t)
    assert c.done and c.result is None, c.result


def test_slow_creep_is_not_still():
    """A hand drifting a hair per frame must not read as held still."""
    c = Calibration()
    creep = [(0.3 + i * 0.004, 0.3) for i in range(60)]  # 24% of the frame
    prog, _ = trace(c, creep, 0.0)
    assert c.state == "ARMED", "a creeping hand started the drawing"
    assert prog.ratio < 1.0


def test_missing_hand_keeps_the_path():
    """Dropping out of frame mid-trace does not restart or end the drawing."""
    c = Calibration()
    _, t = hold(c, (0.3, 0.3), 0.0)
    _, t = trace(c, rect_path(0.3, 0.3, 0.7, 0.7)[:20], t)
    drawn = len(c.path)
    for _ in range(30):  # a full second with no hand at all
        c.update(None, t)
        t += 1 / 30
    assert c.state == "DRAWING", "losing the hand ended the drawing"
    assert len(c.path) == drawn, "the path was thrown away"


def test_clean_needs_enough_points():
    assert _clean([(0.3, 0.3)] * 4) is None


def test_both_fists_watch():
    w = BothFistsWatch(seconds=15.0)
    two = [FakeState("FIST", 0.5), FakeState("FIST", 0.5)]
    assert w.update(two, 0.0) == 0.0
    assert 0.0 < w.update(two, 7.5) < 1.0
    assert w.update(two, 15.0) >= 1.0

    # One hand leaving the pose restarts the count from zero.
    w2 = BothFistsWatch(seconds=15.0)
    w2.update(two, 0.0)
    w2.update([FakeState("FIST", 0.5), FakeState("NO HAND")], 5.0)
    assert w2.update(two, 10.0) == 0.0

    # A fist that is moving (progress 0) does not count as held.
    w3 = BothFistsWatch(seconds=15.0)
    w3.update([FakeState("FIST", 0.0), FakeState("FIST", 0.5)], 0.0)
    assert w3.update(two, 20.0) < 1.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all good")
