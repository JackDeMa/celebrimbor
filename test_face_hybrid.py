"""Self-check of the head + eyes pointer on synthetic landmarks: python test_face_hybrid.py"""

from types import SimpleNamespace

from celebrimbor.config import Config
from celebrimbor.face import extract
from celebrimbor.face_gestures import FaceRecognizer

DT = 1.0 / 30.0
IOD = 0.2  # the synthetic face's eye distance, in image units (aspect 1)


def face(gaze=0.0, head=0.0, open_left=1.0, gaze_right=None):
    """478 landmarks: `gaze` and `head` are x offsets in eye distances."""
    lm = [SimpleNamespace(x=0.5, y=0.7) for _ in range(478)]

    def put(i, x, y):
        lm[i] = SimpleNamespace(x=x, y=y)

    eyes = (
        (0.4, (33, 133), ((160, 144), (158, 153)), range(468, 473), open_left, gaze),
        (0.6, (362, 263), ((385, 380), (387, 373)), range(473, 478), 1.0,
         gaze if gaze_right is None else gaze_right),
    )
    for cx, (a, b), lids, ring, opening, g in eyes:
        put(a, cx - 0.05, 0.4)
        put(b, cx + 0.05, 0.4)
        for up, down in lids:
            put(up, cx, 0.4 - 0.015 * opening)
            put(down, cx, 0.4 + 0.015 * opening)
        ix = cx + g * IOD
        for i, (dx, dy) in zip(ring, ((0, 0), (0.01, 0), (0, -0.01), (-0.01, 0), (0, 0.01))):
            put(i, ix + dx, 0.4 + dy)
    put(1, 0.5 + head * IOD, 0.5)
    put(13, 0.5, 0.60)
    put(14, 0.5, 0.61)
    return extract(lm, aspect=1.0)


class Rig:
    def __init__(self, **settings):
        cfg = Config(use_hand=False, use_head=True, use_eyes=True,
                     face_calibration_seconds=0.3,
                     eyes_recentre_seconds=0.0, eyes_fine_deadband=0.0)
        for key, value in settings.items():
            setattr(cfg, key, value)
        self.cfg, self.rec, self.t = cfg, FaceRecognizer(cfg), 0.0
        self.run(1.0)
        assert self.rec.neutral is not None, "calibration did not finish"

    def run(self, seconds, **pose):
        """Hold a pose; returns (every cursor x, last mode)."""
        xs, mode = [], ""
        for _ in range(round(seconds / DT)):
            self.t += DT
            result = self.rec.update(face(**pose), self.t)
            mode = result.mode
            xs += [e.value[0] for e in result.events if e.name == "face_move"]
        return xs, mode


def close(a, b, tol=2e-3):
    return abs(a - b) <= tol


def demo():
    feats = face(gaze=0.02, head=0.03)
    assert close(feats.gaze["left"][0], 0.02, 1e-9) and close(feats.head[0], 0.03, 1e-9)

    # clamp: the pupils never take the cursor further than eyes_fine_x
    rig = Rig()
    xs, _ = rig.run(4.0, gaze=0.06)  # a whole eyes_range_x: half a screen unclamped
    assert max(xs) <= 0.5 + rig.cfg.eyes_fine_x + 1e-9, max(xs)
    assert close(xs[-1], 0.5 + rig.cfg.eyes_fine_x), xs[-1]

    # inside the patch, gain 1: the nudge is the gaze itself
    rig = Rig()
    xs, _ = rig.run(4.0, gaze=0.006)
    assert close(xs[-1], 0.5 + 0.5 * 0.006 / rig.cfg.eyes_range_x), xs[-1]

    # head compensation: the iris shift a head turn causes moves nothing
    rig = Rig()
    turn = 0.03
    xs, _ = rig.run(6.0, head=turn, gaze=rig.cfg.eyes_head_comp_x * turn)
    assert close(xs[-1], 0.5 + 0.5 * turn / rig.cfg.head_range_x), xs[-1]

    # wink: the head goes on steering, the pupils' share stays as it was
    rig = Rig()
    xs, _ = rig.run(4.0, gaze=0.006)
    fine = xs[-1] - 0.5
    comp = rig.cfg.eyes_head_comp_x * 0.015
    xs, mode = rig.run(3.0, gaze=-0.06 + comp, head=0.015, open_left=0.2)
    assert mode in ("WINK L", "DRAG"), mode
    assert close(xs[-1], 0.5 + 0.5 * 0.015 / rig.cfg.head_range_x + fine), xs[-1]

    # half-shut eyes, or eyes that disagree: the pupils are not read
    for pose in ({"open_left": 0.75}, {"gaze_right": 0.06}):
        rig = Rig()
        xs, _ = rig.run(1.0, gaze=-0.05, **pose)
        assert close(xs[-1], 0.5, 1e-6), (pose, xs[-1])

    # recentring: a gaze held off-centre is slowly taken as the new centre
    rig = Rig(eyes_recentre_seconds=1.0)
    xs, _ = rig.run(10.0, gaze=0.006)
    assert close(xs[-1], 0.5), xs[-1]

    # eyes alone: absolute, over the whole screen
    rig = Rig(use_head=False)
    xs, _ = rig.run(6.0, gaze=0.03)
    assert close(xs[-1], 0.5 + 0.5 * 0.03 / rig.cfg.eyes_range_x), xs[-1]

    print("ok")


if __name__ == "__main__":
    demo()
