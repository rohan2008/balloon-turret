"""
vision.py  -  the camera thread (newest frame only, recording, replay) and
the three balloon detectors (AI, AI + color tracker, color preset).
"""

import os
import sys
import threading
import time

import cv2
import numpy as np

from config import ConfigError
from control import (TUNING, GATE_MIN_PX, MAX_CLOSE_SIZE, RELAX_FLOOR_S, RELAX_FLOOR_V,
                     STREAK_ELONGATION, STREAK_MAX_ANGLE_DEG, clamp)


CAMERA_FAILED_READS_LIMIT = 30      # this many failed reads in a row = camera is gone


# Recordings ('r' key) go here, next to this script
RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")


RECORD_CODEC = "mp4v"      # MPEG-4 video, works with plain opencv-python on Windows


# Frames to let the camera's auto-exposure settle before locking it (see camera.py)
EXPOSURE_WARMUP_FRAMES = 20


class FrameGrabber:
    """Reads the camera in a background thread and keeps ONLY the newest frame.

    Why: webcams (and their drivers) keep a small queue of frames. If our
    loop is slower than the camera - the AI runs ~14 FPS, the camera ~30 -
    the queue fills up and every frame we read is already old: the turret
    would be reacting to where the balloon WAS. The thread reads frames as
    fast as the camera makes them and throws away all but the latest, so
    whenever the main loop asks, it gets the freshest picture.

    (A "thread" is a second line of work running at the same time as the
    main program. The "lock"/"condition" stops both from touching the
    newest-frame variables at the exact same moment.)

    It also does two extra jobs:
      - RECORDING ('r'): every frame the camera delivers is written to an
        .mp4 file right here - so the recording has ALL frames, not just the
        ones the (slower) main loop had time to process.
      - REPLAY (--replay FILE): `cap` is a video file instead of a camera.
        With replay_fps set, the thread plays it at its real speed, like a
        live camera (frames are skipped if processing is slow). The end of
        the file counts as "camera gone"."""

    def __init__(self, cap, threaded, replay_fps=None):
        self.cap = cap
        self.threaded = threaded
        self.replay_fps = replay_fps
        self.next_replay_time = None
        self.record_path = None     # recording to this file (None = not recording)
        self.record_fps = 30.0
        self.writer = None          # cv2.VideoWriter, created at the first recorded frame
        self.recorded_frames = 0
        self.frame = None
        self.frame_time = 0.0       # time.time() when the newest frame arrived
        self.frame_id = 0           # counts frames; bigger = newer
        self.dead = False           # True once the camera has stopped working
        self.failed_reads = 0
        self.running = True
        self.condition = threading.Condition()
        if threaded:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def _read_one(self):
        """Read one frame from the camera and store it as the newest."""
        if self.replay_fps and self.threaded:
            self._wait_for_replay_time()
        ok, frame = self.cap.read()
        if not ok or frame is None:
            self.failed_reads += 1
            # A video file that runs out won't come back - stop at once.
            limit = 1 if self.replay_fps else CAMERA_FAILED_READS_LIMIT
            if self.failed_reads >= limit:
                with self.condition:
                    self.dead = True
                    self.condition.notify_all()
            return
        self.failed_reads = 0
        with self.condition:
            self.frame = frame
            self.frame_time = time.time()
            self.frame_id += 1
            if self.record_path is not None:
                self._record(frame)
            self.condition.notify_all()        # wake up the main loop if it's waiting

    def _wait_for_replay_time(self):
        """Replay: wait until it's time for the next frame (real playback speed)."""
        now = time.perf_counter()
        if self.next_replay_time is None:
            self.next_replay_time = now
        if self.next_replay_time > now:
            time.sleep(self.next_replay_time - now)
        self.next_replay_time = max(self.next_replay_time, now) + 1.0 / self.replay_fps

    def _record(self, frame):
        """Write one raw camera frame to the recording (called with the lock held)."""
        if self.writer is None:
            height, width = frame.shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*RECORD_CODEC)
            self.writer = cv2.VideoWriter(self.record_path, fourcc, self.record_fps, (width, height))
            if not self.writer.isOpened():
                print(f"ERROR: could not create {self.record_path} - recording cancelled.")
                self.writer = None
                self.record_path = None
                return
        self.writer.write(frame)
        self.recorded_frames += 1

    @property
    def recording(self):
        return self.record_path is not None

    def start_recording(self, path, fps):
        with self.condition:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.record_path = path
            self.record_fps = fps
            self.recorded_frames = 0
            self.writer = None

    def stop_recording(self):
        """Finish the file. Returns (path, number of frames written)."""
        with self.condition:
            path, frames = self.record_path, self.recorded_frames
            if self.writer is not None:
                self.writer.release()
            self.writer = None
            self.record_path = None
            return path, frames

    def _run(self):
        while self.running and not self.dead:
            self._read_one()

    def get_newest(self, last_id, timeout):
        """Return (frame, frame_time, frame_id) for a frame NEWER than last_id,
        waiting up to `timeout` seconds. Returns None if none arrived in time."""
        if not self.threaded:
            self._read_one()                   # old way: read right here
        with self.condition:
            if self.threaded:
                self.condition.wait_for(lambda: self.frame_id > last_id or self.dead, timeout)
            if self.frame_id > last_id and not self.dead:
                return self.frame.copy(), self.frame_time, self.frame_id
            return None

    def stop(self):
        self.running = False
        if self.threaded:
            self.thread.join(timeout=1.0)


# Each detector is a function:  frame, hint -> (x, y, box, radius) of the balloon, or None.
#   radius = apparent radius in pixels (how big it looks)
#   hint   = None, or what the motion filter expects (see tracker_app):
#            {"predicted": (x, y) picture position, "vx", "vy", "speed": px/s,
#             "radius": px, "frame_dt": s}


def blur_amount(hint, tuning):
    """0 (still) .. 1 (fast enough that motion blur is likely)."""
    if not hint:
        return 0.0
    return clamp(hint["speed"] / tuning["blur_speed_px_s"], 0.0, 1.0)


def relaxed_settings(settings, blur, tuning):
    """HSV thresholds widened for a blurred balloon: blur smears the color into
    the background, so its saturation (S) and brightness (V) drop. Lower the
    S and V minimums - but never below RELAX_FLOOR_S / RELAX_FLOOR_V."""
    relaxed = dict(settings)
    for key, amount, floor in (("s_min", tuning["blur_relax_s"], RELAX_FLOOR_S),
                               ("v_min", tuning["blur_relax_v"], RELAX_FLOOR_V)):
        lowered = int(round(settings[key] - blur * amount))
        relaxed[key] = max(min(floor, settings[key]), lowered)
    return relaxed


def close_size_for(blur):
    """Morphological close size: 5 normally, up to MAX_CLOSE_SIZE when blurry (odd)."""
    size = 5 + int(round(blur * (MAX_CLOSE_SIZE - 5)))
    return size if size % 2 else size + 1


def blob_measurement(blob, offset=(0, 0), velocity=(0.0, 0.0)):
    """Center, box and apparent radius of a blob, with MOTION BLUR handling.

    A fast balloon is smeared into a streak along its direction of motion.
    The blob's center of mass is the CENTER of the streak (where the balloon
    was mid-exposure) - not its leading edge - and the streak's SHORT side is
    the balloon's real size. Without this, the long streak would inflate the
    apparent radius (and the lock zone and distance estimate)."""
    ox, oy = offset
    x, y, w, h = cv2.boundingRect(blob)
    box = (ox + x, oy + y, ox + x + w, oy + y + h)
    m = cv2.moments(blob)
    (_cx, _cy), radius = cv2.minEnclosingCircle(blob)
    if m["m00"] == 0:
        return ox + x + w / 2, oy + y + h / 2, box, radius
    cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
    # Shape from the blob's "second moments": the long and short axis lengths
    mu20, mu02, mu11 = m["mu20"] / m["m00"], m["mu02"] / m["m00"], m["mu11"] / m["m00"]
    spread = np.sqrt(((mu20 - mu02) / 2) ** 2 + mu11 ** 2)
    long_var, short_var = (mu20 + mu02) / 2 + spread, (mu20 + mu02) / 2 - spread
    speed = float(np.hypot(*velocity))
    if short_var > 0 and np.sqrt(long_var / short_var) > STREAK_ELONGATION and speed > 1e-6:
        long_axis_angle = 0.5 * np.arctan2(2 * mu11, mu20 - mu02)
        along = abs(np.cos(long_axis_angle) * velocity[0] + np.sin(long_axis_angle) * velocity[1]) / speed
        if along > np.cos(np.radians(STREAK_MAX_ANGLE_DEG)):
            radius = 2 * np.sqrt(short_var)     # half the streak's width = real radius
    return ox + cx, oy + cy, box, float(radius)


def make_ai_detector():
    """Load the AI model and return a detector function (see above).
    If the model can't be loaded, the program does NOT quit: it returns a
    detector that raises an error every frame, which the main loop treats as
    a VISION FAULT (laser disabled, message on screen)."""
    print("Loading AI model...")
    try:
        # Imported here (not at the top) because loading PyTorch takes a few seconds.
        from balloon_detect_ai import DEFAULT_CONFIDENCE, get_detections, load_model, pick_balloon
        model = load_model()
    except (Exception, SystemExit) as error:     # load_model() calls sys.exit on failure
        reason = f"AI model failed to load ({error.__class__.__name__}: {error})"
        print(f"ERROR: {reason}. Running with the laser DISABLED.")

        def broken_detect(frame):
            raise RuntimeError(reason)
        return broken_detect
    balloon_confidence = DEFAULT_CONFIDENCE / 100

    def detect(frame, hint=None):
        balloon = pick_balloon(get_detections(model, frame, balloon_confidence))
        if balloon is None:
            return None
        x1, y1, x2, y2 = balloon["box"]
        # The AI only gives a box, not the balloon's outline, so the radius is
        # the average of half the width and half the height. (For a round
        # balloon that's exactly its radius.)
        radius = ((x2 - x1) + (y2 - y1)) / 4
        # Motion blur stretches the box along the motion: then the SHORT side
        # is the balloon's real size.
        if hint and hint["speed"] > 1e-6:
            long_side, short_side = max(x2 - x1, y2 - y1), min(x2 - x1, y2 - y1)
            horizontal = (x2 - x1) >= (y2 - y1)
            along = abs(hint["vx"] if horizontal else hint["vy"]) / hint["speed"]
            if short_side > 0 and long_side / short_side > STREAK_ELONGATION \
                    and along > np.cos(np.radians(STREAK_MAX_ANGLE_DEG)):
                radius = short_side / 2
        return (x1 + x2) / 2, (y1 + y2) / 2, balloon["box"], radius

    return detect


def make_hybrid_detector(ai_detect, ai_every):
    """Speed-up for slow laptops: run the AI only on every `ai_every`-th frame,
    and follow the balloon with a fast COLOR TRACKER on the frames in between.

    How the color tracker works:
      - On each AI frame that finds the balloon, it LEARNS the balloon's color
        from the middle of the AI's box (the middle, so background at the
        box's corners doesn't get mixed in).
      - On the frames in between, it looks for that color only NEAR where the
        balloon was last seen (a search window around the last box), and only
        accepts a blob of about the same size. That stops it jumping to
        something else of a similar color.
      - If it loses the balloon, the very next frame uses the AI again.
    Black/white/gray balloons have no real hue, so they're tracked by
    brightness instead (see color_range_around in balloon_detect.py)."""
    from balloon_detect import (BLUR_SIZE, circular_mean_hue, color_range_around,
                                find_largest_blob, make_mask)

    state = {"frames_until_ai": 0, "settings": None, "box": None}

    def learn(frame, box):
        x1, y1, x2, y2 = box
        w, h = x2 - x1, y2 - y1
        # Middle half of the box (in both directions)
        inner = frame[max(y1 + h // 4, 0):max(y2 - h // 4, 1), max(x1 + w // 4, 0):max(x2 - w // 4, 1)]
        if inner.size == 0:
            state["settings"] = None
            return
        hsv = cv2.cvtColor(cv2.GaussianBlur(inner, (BLUR_SIZE, BLUR_SIZE), 0), cv2.COLOR_BGR2HSV)
        pixels = hsv.reshape(-1, 3)
        hue = circular_mean_hue(pixels[:, 0])
        sat = int(np.median(pixels[:, 1]))
        val = int(np.median(pixels[:, 2]))
        state["settings"] = color_range_around(hue, sat, val)

    def track(frame, hint):
        x1, y1, x2, y2 = state["box"]
        w, h = x2 - x1, y2 - y1
        height, width = frame.shape[:2]
        # Search window: the last box grown by one box-size on every side
        sx1, sy1 = max(int(x1 - w), 0), max(int(y1 - h), 0)
        sx2, sy2 = min(int(x2 + w), width), min(int(y2 + h), height)
        if sx2 - sx1 < 2 or sy2 - sy1 < 2:
            return None
        window = frame[sy1:sy2, sx1:sx2]
        hsv = cv2.cvtColor(cv2.GaussianBlur(window, (BLUR_SIZE, BLUR_SIZE), 0), cv2.COLOR_BGR2HSV)
        blur = blur_amount(hint, TUNING)
        mask = make_mask(hsv, relaxed_settings(state["settings"], blur, TUNING),
                         close_size_for(blur))
        last_area = max(w * h, 1)
        blob, area = find_largest_blob(mask, 0.25 * last_area, hsv)
        if blob is None or area > 3 * last_area:      # nothing, or suspiciously big
            return None
        velocity = (hint["vx"], hint["vy"]) if hint else (0.0, 0.0)
        cx, cy, box, radius = blob_measurement(blob, (sx1, sy1), velocity)
        state["box"] = box
        return cx, cy, box, radius

    def detect(frame, hint=None):
        use_ai = (state["frames_until_ai"] <= 0 or state["settings"] is None
                  or state["box"] is None)
        if use_ai:
            detect.last_source = "AI"
            # (only pass the hint when there is one, so simple one-argument detectors still work)
            found = ai_detect(frame, hint) if hint is not None else ai_detect(frame)
            state["frames_until_ai"] = ai_every - 1
            if found is not None:
                state["box"] = found[2]
                learn(frame, found[2])
            else:
                state["box"] = None
            return found
        detect.last_source = "color tracker"
        state["frames_until_ai"] -= 1
        found = track(frame, hint)
        if found is None:
            state["frames_until_ai"] = 0              # lost it: use the AI next frame
        return found

    detect.last_source = "AI"
    return detect


def make_color_detector(preset_name):
    """Load an HSV preset and return a detector function (see above).
    Uses the Phase 1 color code (red wrap-around, min area, laser-dot filter)."""
    from balloon_detect import BLUR_SIZE, PRESETS_FILE, find_largest_blob, load_presets, make_mask

    try:
        presets = load_presets()
    except ConfigError as error:
        print(f"ERROR: {error}")
        sys.exit(1)
    if preset_name not in presets:
        print(f"ERROR: no preset named '{preset_name}' in {PRESETS_FILE}.")
        print(f"  Available: {', '.join(presets) or 'none'}")
        print(f"  Create one with: python balloon_detect.py --preset {preset_name}  (then press s)")
        sys.exit(1)
    settings = presets[preset_name]
    print(f"Color detection, preset '{preset_name}': {settings}")

    def find(image, used_settings, close_size, offset, velocity):
        blurred = cv2.GaussianBlur(image, (BLUR_SIZE, BLUR_SIZE), 0)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
        mask = make_mask(hsv, used_settings, close_size)
        blob, _area = find_largest_blob(mask, used_settings["min_area"], hsv)
        if blob is None:
            return None
        return blob_measurement(blob, offset, velocity)

    def detect(frame, hint=None):
        blur = blur_amount(hint, TUNING)
        close_size = close_size_for(blur)
        velocity = (hint["vx"], hint["vy"]) if hint else (0.0, 0.0)
        detect.last_info = f"blur relax {blur:.0%}"
        # 1) Search where the filter EXPECTS the balloon, with thresholds
        #    relaxed for motion blur.
        if hint and hint.get("predicted"):
            height, width = frame.shape[:2]
            px, py = hint["predicted"]
            reach = max(3 * hint["radius"], 40) + 2 * hint["speed"] * hint["frame_dt"]
            x1, y1 = int(clamp(px - reach, 0, width)), int(clamp(py - reach, 0, height))
            x2, y2 = int(clamp(px + reach, 0, width)), int(clamp(py + reach, 0, height))
            if x2 - x1 > 4 and y2 - y1 > 4:
                found = find(frame[y1:y2, x1:x2], relaxed_settings(settings, blur, TUNING),
                             close_size, (x1, y1), velocity)
                if found is not None:
                    detect.last_info += ", found near prediction"
                    return found
        # 2) Fall back to the whole picture with the normal thresholds.
        return find(frame, settings, close_size, (0, 0), velocity)

    detect.last_info = ""
    return detect
