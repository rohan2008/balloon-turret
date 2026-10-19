"""
control.py  -  aiming math: angle limits, gains, the lock zone, the Kalman
filter that predicts where the balloon is going, and small helpers.
(The main loop that uses these is in tracker_app.py.)
"""

from collections import deque

import numpy as np


MIN_ANGLE = 20              # same limits as turret.ino (the Arduino clamps too)


MAX_ANGLE = 160


CENTER_ANGLE = 90


# SLEW LIMIT: no single command sent to the Arduino may move a servo more
# than this many degrees from the last angle we sent. Protects the bracket
# (and your fingers) from sudden jumps, whatever the controller asks for.
DEFAULT_MAX_STEP_DEG = 10.0


RECENTER_RAMP_S = 1.0       # quitting: glide back to 90,90 over about this long


# Camera field of view (how many degrees the picture covers left-to-right).
# Typical webcams are 55-70 degrees. Used to convert pixels into degrees.
# We'll measure the real value in Phase 6; 60 is a reasonable guess.
CAMERA_HFOV_DEG = 60.0


# DRY-RUN ONLY: how fast the simulated servo may turn, in degrees per frame.
# 12 deg/frame is ~170 deg/s at 14 FPS. REAL SERVOS WILL DIFFER: a bare SG90
# is rated ~600 deg/s at 5 V with no load, but the bracket's weight, a weak
# power supply, and the servo's own acceleration make it slower in practice,
# and bigger servos (MG996R, ~350 deg/s) are slower still.
DEFAULT_SERVO_SPEED = 12.0


DEFAULT_GAIN = 0.20         # starting value of the GAIN slider (0.01 - 1.00)


DEFAULT_DEADZONE_PX = 10    # ignore leftover errors smaller than this (per axis)


# Balloon-lost handling. Detectors "blink": the balloon can vanish for a
# frame or two even though it's still there. So when it disappears we:
#   1) HOLD: for up to LOST_TIMEOUT_S seconds, keep following the Kalman
#      filter's prediction (or, with --no-predict, keep still).
#   2) LOST: after that, give up and slowly drift back to 90,90 at
#      RETURN_HOME_SPEED degrees per frame (set to 0 to just stay put).
# The laser goes off IMMEDIATELY in both cases - holding never keeps it on.
LOST_TIMEOUT_S = 0.5


RETURN_HOME_SPEED = 1.0


# Lock zone that scales with the balloon's apparent size (see the top of this file).
DEFAULT_LOCK_FRACTION = 0.5 # lock_enter = this x apparent radius (live slider 0.1-1.0)


LOCK_EXIT_RATIO = 1.8       # lock_exit = lock_enter x this (hysteresis)


DEFAULT_LOCK_MIN_PX = 6     # lock_enter never smaller than this...


DEFAULT_LOCK_MAX_PX = 60    # ...or bigger than this (pixels)


DEFAULT_MIN_RADIUS_PX = 8   # balloon smaller than this = too far to lock reliably


RADIUS_SMOOTH_FRAMES = 5    # apparent radius = average of the last 5 detections


LOCK_FRAMES = 5             # frames in a row inside lock_enter needed to lock


# --scale-gain (OFF by default - see the README before using it)
DEFAULT_REFERENCE_RADIUS_PX = 40.0


GAIN_SCALE_LIMITS = (0.5, 2.0)


# Motion prediction ----------------------------------------------------------
# The servo's own reaction time: after a command arrives, how long until it
# has actually turned (hobby servos: roughly 50-150 ms). Added to the measured
# software latency to decide how far ahead to aim (--servo-delay).
DEFAULT_SERVO_DELAY_S = 0.080
# Manual LEAD override (only with --manual-tuning), in MILLISECONDS.
DEFAULT_LEAD_MS = 0
MAX_LEAD_MS = 500

# Slower than this (pixels per SECOND) counts as "not moving" - stops the
# turret creeping because of detection jitter.
VELOCITY_DEADZONE = 15.0

# Physical sanity limits for the filter (pixels per second / per second^2).
# A balloon 1.5 m away: 1 m is ~375 px, so gravity (9.8 m/s^2) is ~3,700 px/s^2.
# These limits stop ONE bad detection from producing a wild prediction.
MAX_ACCEL_PX_S2 = 6000.0
MAX_SPEED_PX_S = 5000.0
REF_RADIUS_PX = 40.0        # "normal" balloon size for the measurement-noise model

# Motion blur handling (fixed - NOT tunable):
RELAX_FLOOR_S = 40          # relaxed HSV thresholds never go below these, so a
RELAX_FLOOR_V = 30          # blurry balloon can't make "everything" count as balloon
MAX_CLOSE_SIZE = 13         # biggest morphological close (pixels) for blurred blobs
STREAK_ELONGATION = 1.4     # blob this many times longer than wide = possible blur streak
STREAK_MAX_ANGLE_DEG = 30   # ...if its long side points along the motion (within this)
# A detection farther than this from the filter's prediction is ignored while
# the balloon has only been missing briefly (it's probably something else):
GATE_MIN_PX = 60            # never gate tighter than this
GATE_RADII = 4.0            # ...or than this many balloon radii
GATE_FRAMES_OF_MOTION = 3.0 # ...or than 3 frames' worth of the balloon's speed

# ---------------------------------------------------------------------------
# AUTO-TUNED PARAMETERS
# These are the ONLY numbers the tuner (tune_params.py) may change. Each one
# has an allowed range. config/auto_tune.json can override them - anything
# else in that file is ignored. NONE of them is a safety setting: the laser
# rules live in safety.py and are not tunable.
# ---------------------------------------------------------------------------
DEFAULT_TUNING = {
    # Kalman filter (IMM = two models blended by how well each predicts)
    "cv_accel_noise": 400.0,    # px/s^2 - how much the steady-motion model expects speed to wobble
    "ca_jerk_noise": 9000.0,    # px/s^3 - how fast the accelerating model lets acceleration change
    "imm_stay": 0.95,           # chance per frame that the motion type stays the same
    "meas_sigma_base": 3.0,     # px - detection jitter for a normal-size, sharp blob
    "meas_sigma_blur": 0.5,     # extra px of jitter per px of blur streak
    # Automatic gain
    "gain_min": 0.10,           # gain when tracking confidence is low (avoid overshoot)
    "gain_max": 0.45,           # gain when confidence is high (crisp lock)
    "confidence_scale": 4.0,    # how quickly confidence drops as prediction errors grow
    # Motion-blur handling in color detection
    "blur_speed_px_s": 700.0,   # speed at which HSV thresholds are relaxed the most
    "blur_relax_s": 40.0,       # max amount S min is lowered for a fast, blurred balloon
    "blur_relax_v": 40.0,       # max amount V min is lowered
    # Servo speed-cap usage
    "speed_cap_gain": 1.5,      # step limit = this x the step needed to keep pace...
    "min_step_deg": 2.0,        # ...but at least this (never above --max-step)
}

# The values actually in use. Starts as DEFAULT_TUNING; the tracker replaces
# them at startup with config/auto_tune.json (see config.load_auto_tune). It is
# updated IN PLACE, so every module that imported it sees the same values.
TUNING = dict(DEFAULT_TUNING)


def set_tuning(values):
    """Use these tuning values (missing keys keep their defaults)."""
    TUNING.clear()
    TUNING.update(DEFAULT_TUNING)
    TUNING.update(values)


TUNING_RANGES = {
    "cv_accel_noise": (50.0, 3000.0),
    "ca_jerk_noise": (1000.0, 60000.0),
    "imm_stay": (0.80, 0.99),
    "meas_sigma_base": (1.0, 10.0),
    "meas_sigma_blur": (0.0, 2.0),
    "gain_min": (0.05, 0.30),
    "gain_max": (0.20, 0.80),
    "confidence_scale": (1.0, 20.0),
    "blur_speed_px_s": (200.0, 3000.0),
    "blur_relax_s": (0.0, 60.0),
    "blur_relax_v": (0.0, 60.0),
    "speed_cap_gain": (1.0, 3.0),
    "min_step_deg": (0.5, 5.0),
}


# Direction of each servo. +1 or -1. If a servo turns AWAY from the balloon
# instead of toward it, flip it with --invert-pan / --invert-tilt.
#   Pan:  +1 means "balloon to the right -> pan angle goes UP".
#   Tilt: -1 means "balloon lower in the picture -> tilt angle goes DOWN"
#         (image y grows downward, so we need the minus sign).
PAN_DIRECTION = +1


TILT_DIRECTION = -1


class RadiusSmoother:
    """Average of the last few apparent radii, so the lock zone doesn't flicker
    with the detector's frame-to-frame jitter."""

    def __init__(self, frames):
        self.values = deque(maxlen=frames)      # keeps only the newest `frames` values

    def add(self, radius):
        self.values.append(radius)

    def reset(self):
        self.values.clear()

    def value(self):
        return sum(self.values) / len(self.values) if self.values else None

    def is_steady(self):
        return len(self.values) == self.values.maxlen


def clamp(value, low, high):
    """Keep value between low and high."""
    return max(low, min(value, high))


def move_toward(current, target, max_step):
    """Move `current` toward `target`, but by no more than `max_step`.
    This is how we fake a servo's limited speed."""
    step = clamp(target - current, -max_step, max_step)
    return current + step


def lock_zone_state(locked, detected, too_far, miss, lock_enter):
    """Which color to draw the lock-zone circle:
         "locked"  (green)  - locked
         "inside"  (yellow) - balloon center inside the zone, but not 5 frames yet
         "outside" (red)    - anything else"""
    if locked:
        return "locked"
    if detected and not too_far and miss is not None and lock_enter is not None \
            and miss < lock_enter:
        return "inside"
    return "outside"


def stable_gain_cap(delay_frames):
    """The biggest gain that won't make the aim swing back and forth.

    The controller always reacts to slightly OLD information (camera +
    processing + servo delay). Correcting too hard on old information means
    over-correcting, again and again: the aim oscillates. For a controller like
    ours with a delay of d frames, the math (discrete-time stability of a
    delayed integrator) says it oscillates forever above
        K_crit = 2 * sin(pi / (2 * (2d + 1)))
    (d=0: 2.0, d=1: 1.0, d=2: 0.62, d=3: 0.44 ...). We stay at HALF of that,
    which settles quickly with little overshoot. This cap is computed, not
    tuned, so the tuner can never push the gain into oscillation."""
    d = max(0.0, delay_frames)
    return 0.5 * 2 * np.sin(np.pi / (2 * (2 * d + 1)))


def auto_gain(confidence, tuning, delay_frames=0.0):
    """Feedback gain from tracking confidence (0..1):
    confident (predictions have been accurate) -> gain_max, for a crisp lock;
    unsure (big prediction errors, sharp turns) -> gain_min, to avoid
    overshooting and wobbling while the filter catches up.
    Never above stable_gain_cap() for the current delay."""
    c = clamp(confidence, 0.0, 1.0)
    gain = tuning["gain_min"] + (tuning["gain_max"] - tuning["gain_min"]) * c
    return min(gain, stable_gain_cap(delay_frames))


def auto_step_limit(speed_px_s, px_per_deg, send_period_s, max_step, tuning):
    """How many degrees one command may move a servo. A slow balloon only needs
    small, smooth steps; a fast one needs bigger steps to keep up. Uses more of
    the slew-limit cap (max_step) the faster the target moves - never more."""
    keep_pace_deg = speed_px_s / px_per_deg * send_period_s
    wanted = tuning["min_step_deg"] + tuning["speed_cap_gain"] * keep_pace_deg
    return clamp(wanted, min(tuning["min_step_deg"], max_step), max_step)


def auto_deadzone(user_deadzone, lock_enter, radius):
    """Leftover error (px) to ignore. Smaller when the balloon is small or far
    away, so small targets still get centered; never bigger than half the
    lock zone, so the turret can always settle INSIDE it."""
    deadzone = user_deadzone
    if lock_enter:
        deadzone = min(deadzone, lock_enter / 2)
    if radius:
        deadzone = min(deadzone, max(2.0, 0.25 * radius))
    return deadzone


def measurement_sigma(radius, box, tuning):
    """How noisy one detection is likely to be, in pixels (1 standard deviation).
    Small blobs (far away) and blurry, stretched blobs (fast motion) give less
    precise centers, so the filter should trust them less."""
    size_factor = clamp(REF_RADIUS_PX / max(radius, 1.0), 0.5, 3.0)
    x1, y1, x2, y2 = box
    blur_px = abs((x2 - x1) - (y2 - y1)) / 2      # how much longer one side is
    return tuning["meas_sigma_base"] * size_factor + tuning["meas_sigma_blur"] * blur_px


def _axis_matrices(dt, model, tuning):
    """Transition matrix F and process noise Q for ONE axis, state [pos, vel, acc].
      CV (constant velocity):     pos += vel*dt,  vel stays,  acc forced to 0
      CA (constant acceleration): pos += vel*dt + acc*dt^2/2, vel += acc*dt, acc stays"""
    if model == "CV":
        F = np.array([[1, dt, 0], [0, 1, 0], [0, 0, 0]], float)
        g = np.array([dt * dt / 2, dt, 0.0])             # random acceleration enters here
        Q = tuning["cv_accel_noise"] ** 2 * np.outer(g, g)
    else:
        F = np.array([[1, dt, dt * dt / 2], [0, 1, dt], [0, 0, 1]], float)
        g = np.array([dt ** 3 / 6, dt * dt / 2, dt])     # random JERK enters here
        Q = tuning["ca_jerk_noise"] ** 2 * np.outer(g, g)
    Q = Q + np.eye(3) * 1e-6                             # keep it numerically healthy
    return F, Q


class MotionTracker:
    """Predicts where the balloon is going, using TWO Kalman filters at once:

      CV = "constant velocity": good for a balloon drifting steadily. It
           doesn't believe in acceleration, so its estimate stays calm.
      CA = "constant acceleration": good for a balloon that FALLS, bounces or
           is thrown - it also estimates acceleration (state x, y, vx, vy, ax, ay).

    Every frame each model predicts where the balloon should appear; the one
    that predicted better gets more weight (an "IMM": Interacting Multiple
    Model filter). So a steady balloon gets the calm CV answer, and a falling
    one gets the CA answer - automatically.

    Time is REAL: each update uses the actual time since the previous frame
    (from the frame's capture timestamp), not an assumed frame rate.

    Units: pixels, pixels/second, pixels/second^2.
    State vector per model: [x, vx, ax, y, vy, ay]."""

    MODELS = ("CV", "CA")
    H = np.array([[1, 0, 0, 0, 0, 0], [0, 0, 0, 1, 0, 0]], float)   # we measure x and y only

    def __init__(self, tuning=None, mode="imm"):
        """mode: "imm" (normal: blend both), or "cv" / "ca" to force one model
        (used by the tests and the tuner to compare against the old filter)."""
        self.tuning = dict(tuning or DEFAULT_TUNING)
        self.mode = mode
        self.reset()

    def reset(self):
        """Forget everything (balloon lost). The next detection starts fresh."""
        self.active = False
        self.t = None
        self.x = {}
        self.P = {}
        self.mu = np.array([0.5, 0.5])     # model probabilities (CV, CA)
        self.nis = 2.0                     # recent normalized prediction error (2 = "as expected")
        self.last_velocity = None
        self.turn = 0.0                    # recent sharp direction change (0..1)

    # --- helpers ----------------------------------------------------------------
    def _F_Q(self, dt, model):
        F3, Q3 = _axis_matrices(dt, model, self.tuning)
        return np.kron(np.eye(2), F3), np.kron(np.eye(2), Q3)   # same model for x and y

    def _clamp_state(self, x):
        for i in (1, 4):
            x[i] = clamp(x[i], -MAX_SPEED_PX_S, MAX_SPEED_PX_S)
        for i in (2, 5):
            x[i] = clamp(x[i], -MAX_ACCEL_PX_S2, MAX_ACCEL_PX_S2)
        return x

    def _transition(self):
        stay = self.tuning["imm_stay"]
        return np.array([[stay, 1 - stay], [1 - stay, stay]])

    # --- main entry point -------------------------------------------------------
    def update(self, t, measurement=None, sigma=3.0):
        """Advance the filter to time t (seconds) and, if given, correct it with
        the measured (x, y) whose noise is `sigma` pixels. Returns the combined
        estimate as a dict (x, y, vx, vy, ax, ay, p_accel), or None if the
        filter has never seen the balloon."""
        if measurement is None and not self.active:
            return None
        if not self.active:
            mx, my = measurement
            for m in self.MODELS:
                self.x[m] = np.array([mx, 0, 0, my, 0, 0], float)
                self.P[m] = np.diag([sigma ** 2, 400.0 ** 2, 2000.0 ** 2] * 2)
            self.mu = {"cv": np.array([1.0, 0.0]), "ca": np.array([0.0, 1.0])}.get(
                self.mode, np.array([0.5, 0.5]))
            self.t = t
            self.active = True
            return self.estimate()

        dt = clamp(t - self.t, 1e-3, 0.5)
        self.t = t

        # 1) MIX: each model starts from a blend of both (standard IMM step)
        PI = self._transition()
        c = PI.T @ self.mu                                 # predicted model probabilities
        mixed_x, mixed_P = {}, {}
        for j, mj in enumerate(self.MODELS):
            weights = PI[:, j] * self.mu / max(c[j], 1e-12)
            xj = sum(w * self.x[mi] for w, mi in zip(weights, self.MODELS))
            Pj = sum(w * (self.P[mi] + np.outer(self.x[mi] - xj, self.x[mi] - xj))
                     for w, mi in zip(weights, self.MODELS))
            mixed_x[mj], mixed_P[mj] = xj, Pj

        # 2) PREDICT each model forward by dt
        for m in self.MODELS:
            F, Q = self._F_Q(dt, m)
            self.x[m] = self._clamp_state(F @ mixed_x[m])
            self.P[m] = F @ mixed_P[m] @ F.T + Q

        if measurement is None:                            # predict-only (dropout)
            self.mu = c / c.sum()
            return self.estimate()

        # 3) CORRECT each model with the measurement, and see how well it predicted
        z = np.asarray(measurement, float)
        R = np.eye(2) * sigma ** 2
        likelihood = np.zeros(2)
        nis = np.zeros(2)
        for j, m in enumerate(self.MODELS):
            innovation = z - self.H @ self.x[m]            # measured - predicted
            S = self.H @ self.P[m] @ self.H.T + R
            S_inv = np.linalg.inv(S)
            K = self.P[m] @ self.H.T @ S_inv
            self.x[m] = self._clamp_state(self.x[m] + K @ innovation)
            self.P[m] = (np.eye(6) - K @ self.H) @ self.P[m]
            nis[j] = float(innovation @ S_inv @ innovation)
            likelihood[j] = np.exp(-0.5 * nis[j]) / (2 * np.pi * np.sqrt(np.linalg.det(S)))

        # 4) RE-WEIGHT the models by how well they predicted
        mu = c * np.maximum(likelihood, 1e-300)
        self.mu = mu / mu.sum() if mu.sum() > 0 else np.array([0.5, 0.5])
        if self.mode != "imm":                             # forced single model
            self.mu = np.array([1.0, 0.0]) if self.mode == "cv" else np.array([0.0, 1.0])
        self.nis = 0.7 * self.nis + 0.3 * float(self.mu @ nis)

        # Sharp direction change? (the velocity turned by more than ~60 degrees)
        est = self.estimate()
        v = np.array([est["vx"], est["vy"]])
        if self.last_velocity is not None and np.linalg.norm(v) > 100 \
                and np.linalg.norm(self.last_velocity) > 100:
            cos_turn = v @ self.last_velocity / (np.linalg.norm(v) * np.linalg.norm(self.last_velocity))
            self.turn = max(self.turn * 0.7, 1.0 if cos_turn < 0.5 else 0.0)
        else:
            self.turn *= 0.7
        self.last_velocity = v
        return est

    def estimate(self):
        """The blended answer of both models."""
        x = self.mu[0] * self.x["CV"] + self.mu[1] * self.x["CA"]
        return {"x": x[0], "vx": x[1], "ax": x[2], "y": x[3], "vy": x[4], "ay": x[5],
                "p_accel": float(self.mu[1])}

    def predict_at(self, t_future):
        """Where the balloon should be at time t_future (seconds), using the
        blended position, velocity and acceleration. Not used to fire the laser -
        only to AIM."""
        est = self.estimate()
        ahead = clamp(t_future - self.t, 0.0, 0.5)
        return (est["x"] + est["vx"] * ahead + 0.5 * est["ax"] * ahead ** 2,
                est["y"] + est["vy"] * ahead + 0.5 * est["ay"] * ahead ** 2)

    def confidence(self):
        """0..1: how well the filter has been predicting lately. Large
        prediction errors or a sharp turn -> low confidence."""
        excess = max(0.0, self.nis - 2.0)                  # 2 = normal for x+y errors
        return float(np.exp(-excess / self.tuning["confidence_scale"]) * (1.0 - 0.6 * self.turn))
