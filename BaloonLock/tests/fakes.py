"""
Fakes for headless testing of turret_tracker.py.

run_tracker() runs the REAL main loop, but with:
  - a fake camera (synthetic frames, no webcam),
  - fake OpenCV windows/trackbars/keys (nothing appears on screen),
  - a fake Arduino on the serial port (records every command line),
  - a fake fire key (no global keyboard hook),
  - a fake clock that moves forward exactly FRAME_DT seconds per camera
    frame, so timing rules (0.5 s lost timeout, 1 s calibration...) can be
    tested exactly.
Everything is put back afterwards (unittest.mock restores it).
"""

import contextlib
import io
import os
import sys
from unittest import mock

import numpy as np
import cv2
import serial

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import camera, cli, config, control, display, safety, serial_link, tracker_app, ui, vision  # noqa: E402

# Every module of the program. (turret_tracker.py itself is just a 3-line entry point.)
PROJECT_MODULES = [tracker_app, safety, control, vision, serial_link, ui, config, cli,
                   display, camera]


class _Project:
    """tt.X finds X in whichever project module defines it, so tests don't
    need to know which file each name lives in."""

    def __getattr__(self, name):
        for module in PROJECT_MODULES:
            if name in vars(module):
                return getattr(module, name)
        raise AttributeError(name)


tt = _Project()


@contextlib.contextmanager
def patch_all(name, value):
    """Patch `name` in EVERY project module that has it. (A name brought in
    with 'from safety import LASER_ENABLED' is a separate copy inside each
    module, so patching only one module would miss the others.)"""
    with contextlib.ExitStack() as stack:
        found = False
        for module in PROJECT_MODULES:
            if name in vars(module):
                stack.enter_context(mock.patch.object(module, name, value))
                found = True
        if not found:
            raise AttributeError(name)
        yield

FRAME_DT = 1 / 30          # fake seconds per camera frame


def blank_frame(_index=None):
    return np.zeros((480, 640, 3), np.uint8)


def box_at(x, y, radius):
    """A detection tuple (x, y, box, radius) for a round balloon."""
    return (float(x), float(y),
            (int(x - radius), int(y - radius), int(x + radius), int(y + radius)), float(radius))


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        self.t += 1e-6       # tiny step so "wait until" loops always make progress
        return self.t


class FakeCap:
    """Fake webcam: `frames` frames from frame_fn(index), then read() fails."""

    def __init__(self, frames, frame_fn, clock):
        self.frames = frames
        self.frame_fn = frame_fn or blank_frame
        self.clock = clock
        self.i = 0
        self.props = {}
        self.history = []          # every cap.set(prop, value), in order

    def read(self):
        self.i += 1
        self.clock.t += FRAME_DT
        if self.i > self.frames:
            return False, None
        return True, self.frame_fn(self.i)

    def set(self, prop, value):
        self.props[prop] = value
        self.history.append((prop, value))
        return True

    def get(self, prop):
        return self.props.get(prop, 0.0)

    def isOpened(self):
        return True

    def release(self):
        pass


class FakeSerial:
    """Fake Arduino: answers READY + B0, records every line written.
    It can be 'unplugged': after `fail_after` lines in total, writing raises
    an error, and the next `fail_reopens` attempts to re-open the port fail."""
    instances = []
    fail_after = None
    fail_reopens = 2
    open_fails = 0

    def __init__(self, port, baud, timeout=None, write_timeout=None):
        if FakeSerial.open_fails > 0:
            FakeSerial.open_fails -= 1
            raise serial.SerialException("could not open port (fake: unplugged)")
        self.out = "READY\r\nB0\r\n"
        self.lines = []
        FakeSerial.instances.append(self)

    def read(self, n):
        data, self.out = self.out[:n], self.out[n:]
        return data.encode()

    @property
    def in_waiting(self):
        return len(self.out)

    def write(self, data):
        total = sum(len(port.lines) for port in FakeSerial.instances)
        if FakeSerial.fail_after is not None and total >= FakeSerial.fail_after:
            FakeSerial.fail_after = None
            FakeSerial.open_fails = FakeSerial.fail_reopens
            raise serial.SerialException("device disconnected (fake: cable pulled)")
        line = data.decode().strip()
        self.lines.append(line)
        self.out += f"OK {line}\r\n"

    def press(self, down):
        self.out += "B1\r\n" if down else "B0\r\n"

    def close(self):
        pass


class FakeFireKey:
    """Fire key controlled by fire_fn(frame_index) -> bool."""

    def __init__(self, fire_fn, cap):
        self.fire_fn = fire_fn
        self.cap = cap
        self.method = "fake"

    def is_held(self, focused):
        return bool(focused and self.fire_fn(self.cap.i))

    def saw_keys(self, keys):
        pass

    def stop(self):
        pass


class Result:
    def __init__(self):
        self.out = ""
        self.laser_events = []      # [(frame_index, True/False)]
        self.frames = []            # per rendered frame: {"i": index, "lines": [...], "text": str}
        self.serial_lines = []
        self.cap = None            # the FakeCap (to inspect camera settings)
        self.camera_index = None   # which camera number the tracker asked for

    def lines_at(self, i):
        """All info-bar text lines on frame i, joined (for easy 'in' checks)."""
        for f in self.frames:
            if f["i"] == i:
                return f["text"]
        return ""

    def laser_on_frames(self):
        """Set of frame indexes on which the laser was ON."""
        on, state, frames = set(), False, sorted(f["i"] for f in self.frames)
        events = dict(self.laser_events)
        for i in frames:
            state = events.get(i, state)
            if state:
                on.add(i)
        return on


def _trackbar_defaults(argv):
    def arg(name, default):
        return float(argv[argv.index(name) + 1]) if name in argv else default
    return {
        tt.GAIN_TRACKBAR: int(round(arg("--gain", tt.DEFAULT_GAIN) * 100)),
        tt.LEAD_TRACKBAR: int(arg("--lead", tt.DEFAULT_LEAD_MS)),
        tt.LOCK_TRACKBAR: int(round(arg("--lock-fraction", tt.DEFAULT_LOCK_FRACTION) * 100)),
    }


def run_tracker(argv, detector=None, frames=60, frame_fn=None, keys=None,
                focus_fn=None, fire_fn=None, window=(640, 700), button_fn=None,
                extra_patches=(), real_ai=False, threaded=False, keep_canvases=False,
                unplug_after=None, processing_s=0.0):
    """Run turret_tracker.main() headless and return a Result.

    detector(i)  -> None or box_at(...)  (used unless --color is given)
    keys         -> {frame_index: key_code}
    focus_fn(i)  -> window has focus? (default always)
    fire_fn(i)   -> dry-run fire key held? (default never)
    button_fn(i) -> live mode: Arduino fire button state on frame i
    real_ai      -> use the REAL make_ai_detector (e.g. to test a model that fails to load)
    processing_s -> pretend each detection takes this long (moves the fake clock)
    Unless the test passes --tuning-file, the built-in tuning defaults are used
    (--no-auto-tune), so re-running tune_params.py never changes test results.
    """
    argv = list(argv)
    if not threaded and "--no-capture-thread" not in argv:
        argv.append("--no-capture-thread")       # deterministic: one frame per loop
    if "--mode" not in argv:
        argv += ["--mode", "auto"]
    if "--tuning-file" not in argv and "--no-auto-tune" not in argv:
        argv.append("--no-auto-tune")

    clock = FakeClock()
    cap = FakeCap(frames, frame_fn, clock)
    result = Result()
    pending_keys = dict(keys or {})
    trackbars = _trackbar_defaults(argv)
    FakeSerial.instances = []
    FakeSerial.fail_after = unplug_after
    FakeSerial.open_fails = 0
    last_button = {"state": False}

    def make_ai_detector():
        def detect(_frame, hint=None):
            i = cap.i
            if button_fn is not None and FakeSerial.instances:
                state = bool(button_fn(i))
                if state != last_button["state"]:
                    FakeSerial.instances[-1].press(state)
                    last_button["state"] = state
            clock.t += processing_s
            found = detector(i) if detector else None
            return found
        return detect

    def open_camera(index, *args, **kwargs):
        result.camera_index = index
        return cap

    real_set_laser = tt.set_laser

    def spy_set_laser(link, on, dry_run):
        result.laser_events.append((cap.i, on))
        real_set_laser(link, on, dry_run)

    real_render = tt.render_with_info_bar

    def spy_render(name, frame, groups, draw_overlays=None):
        canvas = real_render(name, frame, groups, draw_overlays)
        lines = [text for _title, group in groups for text, _color in group]
        result.frames.append({"i": cap.i, "lines": lines, "text": " | ".join(lines),
                              "titles": [title for title, _ in groups],
                              "canvas_shape": canvas.shape,
                              "canvas": canvas.copy() if keep_canvases else None})
        return canvas

    patches = [
        mock.patch.object(tt.time, "time", clock.time),
        mock.patch.object(tt.time, "sleep", lambda s: None),
        patch_all("open_camera", open_camera),
        patch_all("EXPOSURE_WARMUP_FRAMES", 0),     # keep frame numbers simple
        *([] if real_ai else [patch_all("make_ai_detector", make_ai_detector)]),
        patch_all("set_laser", spy_set_laser),
        patch_all("render_with_info_bar", spy_render),
        patch_all("window_has_focus", lambda name: bool(focus_fn(cap.i)) if focus_fn else True),
        patch_all("FireKey", lambda name: FakeFireKey(fire_fn or (lambda i: False), cap)),
        mock.patch.object(tt.serial, "Serial", FakeSerial),
        mock.patch.object(cv2, "namedWindow", lambda *a, **k: None),
        mock.patch.object(cv2, "createTrackbar", lambda *a, **k: None),
        mock.patch.object(cv2, "getTrackbarPos", lambda name, win: trackbars[name]),
        mock.patch.object(cv2, "imshow", lambda *a, **k: None),
        mock.patch.object(cv2, "destroyAllWindows", lambda: None),
        mock.patch.object(cv2, "getWindowProperty", lambda *a: 1),
        mock.patch.object(cv2, "waitKey", lambda delay=0: pending_keys.pop(cap.i, -1)),
        mock.patch.object(cv2, "pollKey", lambda: -1),
        mock.patch.object(cv2, "resizeWindow", lambda *a, **k: None),
        mock.patch.object(cv2, "getWindowImageRect", lambda name: (0, 0, *window)),
        mock.patch.object(sys, "argv", ["turret_tracker.py"] + argv),
        *extra_patches,
    ]
    buffer = io.StringIO()
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with contextlib.redirect_stdout(buffer):
            try:
                tt.main()
            finally:
                control.set_tuning({})           # don't leak tuned values into other tests
    result.out = buffer.getvalue()
    result.cap = cap
    result.serial_lines = [line for port in FakeSerial.instances for line in port.lines]
    return result
