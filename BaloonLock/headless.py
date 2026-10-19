"""
headless.py  -  run the REAL tracker (tracker_app.main) with no window, no
webcam and no Arduino, on simulated frames or a recorded clip, and measure
how well it tracked. Used by tune_params.py.

How it works: the camera, the window functions, the keyboard and the clock
are swapped for fakes (like the tests do). The tracker runs in --dry-run, so
it simulates its own servos; we read where it was aiming, whether it was
LOCKED, and its "miss" distance from the info bar it draws every frame. That
way the same runner also works on OLDER versions of the tracker (for
before/after comparisons): `python headless.py --repo OLD_CHECKOUT ...`.

Timing is simulated too: each frame's processing takes `processing_s`
(default 30 ms), and if that's slower than the camera, older frames are
skipped - just like the real capture thread does.
"""

import argparse
import contextlib
import io
import json
import math
import os
import re
import sys
from unittest import mock

import numpy as np

PROJECT_MODULE_NAMES = ["tracker_app", "safety", "control", "vision", "serial_link", "ui", "config",
                        "cli", "display", "camera"]
PX_PER_DEG = 640 / 60.0          # same conversion the tracker uses (640 px, 60 deg view)
SKIP_START_S = 0.25              # ignore the first moments while the tracker acquires the target


class _Clock:
    def __init__(self):
        self.t = 0.0

    def time(self):
        self.t += 1e-6
        return self.t


class _FrameSource:
    """Fake camera. Like the capture thread: always hands out the NEWEST frame
    that exists at the current time (skipping older ones), or waits for the
    next one."""

    def __init__(self, frames, clock):
        self.frames = frames          # list of (image, t, truth_xy or None)
        self.clock = clock
        self.index = -1               # index of the frame last returned
        self.props = {}

    def read(self):
        nxt = self.index + 1
        if nxt >= len(self.frames):
            return False, None
        # skip frames that are already older than "now" (we were too slow)
        while nxt + 1 < len(self.frames) and self.frames[nxt + 1][1] <= self.clock.t:
            nxt += 1
        self.index = nxt
        self.clock.t = max(self.clock.t, self.frames[nxt][1])
        return True, self.frames[nxt][0]

    def set(self, prop, value):
        self.props[prop] = value
        return True

    def get(self, prop):
        return self.props.get(prop, 0.0)

    def isOpened(self):
        return True

    def release(self):
        pass


class _NoFireKey:
    method = "none (headless)"

    def __init__(self, *args):
        pass

    def is_held(self, focused):
        return False

    def saw_keys(self, keys):
        pass

    def stop(self):
        pass


def _patch_everywhere(name, value):
    patches = []
    for module_name in PROJECT_MODULE_NAMES:
        module = sys.modules.get(module_name)
        if module is not None and name in vars(module):
            patches.append(mock.patch.object(module, name, value))
    return patches


def _truth_at(frames, t):
    """True balloon position at time t (straight-line between frames), or None."""
    if not frames or frames[0][2] is None:
        return None
    times = [f[1] for f in frames]
    k = int(np.searchsorted(times, t))
    if k <= 0:
        return frames[0][2]
    if k >= len(frames):
        return frames[-1][2]
    (t0, p0), (t1, p1) = (times[k - 1], frames[k - 1][2]), (times[k], frames[k][2])
    a = (t - t0) / (t1 - t0)
    return (p0[0] + a * (p1[0] - p0[0]), p0[1] + a * (p1[1] - p0[1]))


def run_tracker(frames, argv, processing_s=0.03):
    """Run tracker_app.main() over `frames` [(image, t, truth_xy or None), ...]
    with the given command-line flags. Returns one record per processed frame:
    {"index", "t", "aim": (x, y) or None, "locked", "tracking", "miss", "truth"}."""
    import cv2
    import tracker_app                    # imports the rest of the project

    clock = _Clock()
    source = _FrameSource(frames, clock)
    records = []
    gain_default = 20

    def trackbar_pos(name, window):
        if "GAIN" in name:
            return gain_default
        if "LOCK" in name:
            return 50
        return 1 if "frames" in name else 0          # old LEAD (frames) default 1; LEAD ms 0

    def wrap_detector(make):
        def factory(*args, **kwargs):
            detect = make(*args, **kwargs)

            def timed(*a, **k):
                clock.t += processing_s           # the detection + processing takes time
                return detect(*a, **k)
            for attr in ("last_source", "last_info"):
                if hasattr(detect, attr):
                    setattr(timed, attr, getattr(detect, attr))
            return timed
        return factory

    def render_spy(name, frame, groups, draw_overlays=None):
        text = " | ".join(t for _title, lines in groups for t, _color in lines)
        aim = None
        m = re.search(r"sim servo pan\s+([-\d.]+)\s+tilt\s+([-\d.]+)", text)
        if m:
            pan, tilt = float(m.group(1)), float(m.group(2))
            aim = (320 + (pan - 90) * PX_PER_DEG, 240 - (tilt - 90) * PX_PER_DEG)
        miss = re.search(r"miss\s+(\d+)px", text)
        index = source.index
        if records and records[-1]["index"] == index:
            return np.zeros((8, 8, 3), np.uint8)          # no new frame (camera ran out)
        # Compare where the turret points NOW (after processing) with where the
        # balloon is NOW - not where it was when the frame was captured.
        records.append({"index": index, "t": frames[index][1] if 0 <= index < len(frames) else None,
                        "aim": aim, "locked": "LOCKED" in text, "tracking": "TRACKING" in text,
                        "miss": float(miss.group(1)) if miss else None,
                        "truth": _truth_at(frames, clock.t)})
        return np.zeros((8, 8, 3), np.uint8)

    module_patches = []
    for name, value in [("open_camera", lambda *a, **k: source), ("EXPOSURE_WARMUP_FRAMES", 0),
                        ("window_has_focus", lambda *a: True), ("FireKey", _NoFireKey),
                        ("render_with_info_bar", render_spy),
                        ("make_color_detector", wrap_detector(tracker_app.make_color_detector)),
                        ("make_ai_detector", wrap_detector(tracker_app.make_ai_detector))]:
        module_patches += _patch_everywhere(name, value)
    other = [
        mock.patch.object(tracker_app.time, "time", clock.time),
        mock.patch.object(tracker_app.time, "sleep", lambda s: None),
        mock.patch.object(cv2, "namedWindow", lambda *a, **k: None),
        mock.patch.object(cv2, "createTrackbar", lambda *a, **k: None),
        mock.patch.object(cv2, "getTrackbarPos", trackbar_pos),
        mock.patch.object(cv2, "imshow", lambda *a, **k: None),
        mock.patch.object(cv2, "destroyAllWindows", lambda: None),
        mock.patch.object(cv2, "getWindowProperty", lambda *a: 1),
        mock.patch.object(cv2, "waitKey", lambda delay=0: -1),
        mock.patch.object(cv2, "pollKey", lambda: -1),
        mock.patch.object(cv2, "resizeWindow", lambda *a, **k: None),
        mock.patch.object(cv2, "getWindowImageRect", lambda name: (0, 0, 640, 700)),
        mock.patch.object(sys, "argv", ["turret_tracker.py", "--dry-run", "--no-capture-thread",
                                        "--mode", "auto"] + list(argv)),
    ]
    with contextlib.ExitStack() as stack:
        for p in module_patches + other:
            stack.enter_context(p)
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                tracker_app.main()
            except SystemExit:
                pass
    return records


def score(records, fps):
    """Turn per-frame records into the four scores (lower error/drops/overshoot
    and higher locked % are better):
      error      - average distance (px) between where the turret aims and where
                   the balloon really is (or, for real clips, the detection)
      locked     - fraction of frames LOCKED
      drops      - how many times an existing lock was lost
      overshoot  - average distance (px) the aim ran AHEAD of the balloon along
                   its direction of motion (0 for real clips: no ground truth)"""
    usable = [r for r in records if r["t"] is not None and r["t"] >= SKIP_START_S]
    errors, locked, overshoot = [], [], []
    drops, was_locked = 0, False
    previous = {}
    for r in usable:
        truth = r["truth"]
        in_view = truth is None or (0 <= truth[0] < 640 and 0 <= truth[1] < 480)
        if not in_view:
            continue
        if truth is not None and r["aim"] is not None:
            errors.append(math.dist(r["aim"], truth))
            prev = previous.get("truth")
            if prev is not None:
                vx, vy = truth[0] - prev[0], truth[1] - prev[1]
                speed = math.hypot(vx, vy)
                if speed * fps > 50:
                    ahead = ((r["aim"][0] - truth[0]) * vx + (r["aim"][1] - truth[1]) * vy) / speed
                    overshoot.append(max(0.0, ahead))
            previous["truth"] = truth
        elif r["miss"] is not None:
            errors.append(r["miss"])
        locked.append(r["locked"])
        if was_locked and not r["locked"]:
            drops += 1
        was_locked = r["locked"]
    n = max(len(locked), 1)
    return {"error": float(np.mean(errors)) if errors else float("nan"),
            "locked": sum(locked) / n, "drops": drops,
            "overshoot": float(np.mean(overshoot)) if overshoot else 0.0, "frames": len(locked)}


def combined(s):
    """One number to minimize (used by the tuner)."""
    error = s["error"] if s["error"] == s["error"] else 200.0          # NaN -> bad
    return error + 0.5 * s["overshoot"] + 40.0 * (1.0 - s["locked"]) + 3.0 * s["drops"]


def simulated_frames(name, fps, seed=0):
    from simulator import Scenario
    return [(image, t, truth) for image, t, truth in Scenario(name, fps, seed)]


def clip_frames(path):
    import cv2
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames, k = [], 0
    while True:
        ok, image = cap.read()
        if not ok:
            break
        frames.append((image, k / fps, None))
        k += 1
    cap.release()
    return frames, fps


def main():
    """Command line, mainly so tune_params.py can score an OLD checkout in a
    separate process:  python headless.py --repo DIR --scenario bounce --fps 30"""
    parser = argparse.ArgumentParser(description="Run the tracker headless and print scores (JSON)")
    parser.add_argument("--repo", help="folder of the tracker version to run (default: this one)")
    parser.add_argument("--scenario", help="simulator scenario name")
    parser.add_argument("--clip", help="recorded video instead of a scenario")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--processing-ms", type=float, default=30.0)
    parser.add_argument("tracker_args", nargs=argparse.REMAINDER,
                        help="flags for the tracker, after --  (e.g. -- --color red)")
    args = parser.parse_args()
    if args.repo:
        sys.path.insert(0, os.path.abspath(args.repo))
    tracker_args = [a for a in args.tracker_args if a != "--"]
    if args.clip:
        frames, fps = clip_frames(args.clip)
    else:
        frames, fps = simulated_frames(args.scenario, args.fps), args.fps
    records = run_tracker(frames, tracker_args, args.processing_ms / 1000)
    print(json.dumps(score(records, fps)))


if __name__ == "__main__":
    main()
