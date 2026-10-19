"""
ui.py  -  window names, colors, slider names, the key help text, the dry-run
fire key ('f'), and the timing report ('t').
"""

import time

from display import window_has_focus


VELOCITY_ARROW_FRAMES = 5   # on-screen arrow shows where the balloon will be in 5 frames


# Dry-run fire key fallback (only if pynput isn't available): 'f' counts as
# held for this long after each key event. Holding a key repeats it, but the
# FIRST repeat only comes after ~0.5 s, so this must be longer than that.
FIRE_KEY_FALLBACK_HOLD_S = 0.7


WINDOW = "Turret Tracker"


GAIN_TRACKBAR = "GAIN x100 (reaction strength)"


LEAD_TRACKBAR = "LEAD ms (manual override)"


LOCK_TRACKBAR = "LOCK FRACTION x100 (zone size)"


# Colors (Blue, Green, Red)
WHITE, GRAY = (255, 255, 255), (170, 170, 170)


GREEN, RED, ORANGE = (0, 255, 0), (0, 0, 255), (0, 165, 255)


YELLOW, MAGENTA, CYAN = (0, 255, 255), (255, 0, 255), (255, 255, 0)


KEY_HELP = [
    ("m  AUTO/MANUAL    f  fire (hold)", GRAY),
    ("t  timing report  r  record", GRAY),
    ("q  KILL: laser off + quit", GRAY),
    ("+ -  zoom   0  reset   (or drag)", GRAY),
]


class FireKey:
    """Dry-run stand-in for the fire button: True only while 'f' is held
    down AND the tracker window has focus.

    Preferred method: pynput (pip install pynput). It runs a small listener
    in the background that gets real KEY-DOWN and KEY-UP events, so letting
    go of 'f' is noticed instantly. Its callbacks only flip True/False -
    no printing, no serial, no drawing - so holding a key can never slow
    down the main loop.

    Fallback (pynput missing): OpenCV only reports key presses, never
    releases, so 'f' counts as held for FIRE_KEY_FALLBACK_HOLD_S after each
    press/repeat. Release is noticed up to 0.7 s late."""

    def __init__(self, window_name):
        self.window_name = window_name
        self.down = False           # pynput: 'f' is physically down right now
        self.armed = False          # pynput: that press started while our window had focus
        self.last_f_event = 0.0     # fallback: time of the last 'f' key event
        self.listener = None
        try:
            from pynput import keyboard
            self.listener = keyboard.Listener(on_press=self._on_press,
                                              on_release=self._on_release)
            self.listener.daemon = True
            self.listener.start()
            self.method = "pynput (true key-down / key-up)"
        except Exception as error:      # not installed, or no keyboard access
            self.listener = None
            self.method = (f"timeout fallback - 'f' stays active {FIRE_KEY_FALLBACK_HOLD_S}s "
                           f"after the last key event ({error.__class__.__name__}; "
                           f"pip install pynput for exact release)")
        print(f"Fire key 'f': using {self.method}")

    @staticmethod
    def _is_f(key):
        return getattr(key, "char", None) in ("f", "F")

    # These two run in pynput's background thread: keep them tiny.
    def _on_press(self, key):
        if self._is_f(key) and not self.down:      # first press (ignore auto-repeats)
            self.down = True
            # Only a press that STARTS while our window is active counts, so
            # typing 'f' somewhere else and then clicking here can't fire.
            self.armed = window_has_focus(self.window_name)

    def _on_release(self, key):
        if self._is_f(key):
            self.down = False
            self.armed = False

    def saw_keys(self, keys):
        """Give it the keys read this frame (used by the fallback method)."""
        if ord("f") in keys:
            self.last_f_event = time.time()

    def is_held(self, focused):
        if not focused:                 # window lost focus -> released, must re-press
            self.armed = False
            return False
        if self.listener is not None:
            return self.down and self.armed
        return time.time() - self.last_f_event < FIRE_KEY_FALLBACK_HOLD_S

    def stop(self):
        if self.listener is not None:
            self.listener.stop()


class StageTimer:
    """Keeps a running average of how long each stage of the loop takes."""

    STAGES = [
        ("capture wait", "waiting for a NEW camera frame"),
        ("detect", "finding the balloon (AI or color)"),
        ("control", "Kalman + controller + laser logic + serial"),
        ("draw+show", "building the display + showing it + reading keys"),
        ("whole loop", "one full trip around the loop"),
        ("frame age", "how old the frame is when the laser decision is made"),
    ]
    HINTS = {
        "capture wait": "the CAMERA is slow to deliver frames. Add light (webcams slow "
                        "down in dim rooms) and close other apps using the camera.",
        "detect": "DETECTION is the bottleneck. Try --ai-every 3 (AI every 3rd frame, fast "
                  "color tracking in between), --color, or AI_IMAGE_SIZE = 416 in "
                  "balloon_detect_ai.py.",
        "control": "unusual - check the USB serial connection (a stuck port slows writes).",
        "draw+show": "DISPLAY is slow - make the window smaller with '-'.",
    }

    def __init__(self):
        self.avg = {}

    def add(self, name, seconds):
        old = self.avg.get(name)
        self.avg[name] = seconds if old is None else 0.9 * old + 0.1 * seconds

    def report(self, proc_fps, cam_fps):
        ms = {name: self.avg.get(name, 0.0) * 1000 for name, _ in self.STAGES}
        lines = ["", "----- Timing (running averages) -----"]
        for name, meaning in self.STAGES:
            lines.append(f"  {name:<13}{ms[name]:7.1f} ms   {meaning}")
        lines.append(f"  processing {proc_fps:.1f} FPS | camera delivering {cam_fps:.1f} FPS")
        slowest = max(("capture wait", "detect", "control", "draw+show"), key=lambda n: ms[n])
        lines.append(f"  Slowest stage: {slowest} -> {self.HINTS[slowest]}")
        if ms["frame age"] > ms["detect"] + ms["control"] + 50:
            lines.append("  Frames are older than processing explains -> camera BUFFERING. "
                         "Make sure you're NOT using --no-capture-thread.")
        return "\n".join(lines)
