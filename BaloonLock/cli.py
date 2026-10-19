"""
cli.py  -  the command-line flags (--dry-run, --port, ...) and their checks.
"""

import argparse

from camera import CAMERA_INDEX
from control import (DEFAULT_DEADZONE_PX, DEFAULT_GAIN, DEFAULT_LEAD_MS, DEFAULT_SERVO_DELAY_S,
                     DEFAULT_LOCK_FRACTION, DEFAULT_LOCK_MAX_PX, DEFAULT_LOCK_MIN_PX,
                     DEFAULT_MAX_STEP_DEG, DEFAULT_MIN_RADIUS_PX, DEFAULT_REFERENCE_RADIUS_PX,
                     DEFAULT_SERVO_SPEED, MAX_ANGLE, MIN_ANGLE)
from safety import FIRST_RUN


def parse_args():
    """Read the command-line options (the --flags)."""
    parser = argparse.ArgumentParser(description="Balloon-tracking turret controller")
    parser.add_argument("--dry-run", action="store_true",
                        help="no Arduino: simulate the turret and print commands instead")
    parser.add_argument("--port", help="Arduino COM port, e.g. COM5 (asked if missing)")
    parser.add_argument("--mode", choices=("manual", "auto"), default="manual",
                        help="starting fire mode (default manual; 'm' switches live)")
    parser.add_argument("--color",
                        help="use HSV color detection with this preset from presets.json "
                             "(e.g. green) instead of the AI detector")
    parser.add_argument("--camera", type=int, default=CAMERA_INDEX, metavar="INDEX",
                        help="which camera to use (0 = built-in webcam). See --list-cameras")
    parser.add_argument("--replay", metavar="FILE",
                        help="play a recording (e.g. recordings/20261011-201500.mp4) instead of "
                             "the camera. Always dry-run.")
    parser.add_argument("--self-test", action="store_true",
                        help="check camera, AI model, config files and serial port list "
                             "(no Arduino needed), print PASS/FAIL, then quit")
    parser.add_argument("--list-cameras", action="store_true",
                        help="list the cameras that work, then quit")
    parser.add_argument("--auto-exposure", action="store_true",
                        help="leave the camera's auto-exposure and auto-white-balance ON "
                             "(default: lock them at startup so colors don't drift)")
    parser.add_argument("--ai-every", type=int, default=1, metavar="N",
                        help="run the AI only every N-th frame and use a fast color tracker "
                             "in between (default 1 = AI every frame). Try 3 if 't' shows "
                             "detection is the bottleneck.")
    parser.add_argument("--tuning-file", default=None, metavar="FILE",
                        help="auto-tuned parameters to load (default config/auto_tune.json)")
    parser.add_argument("--no-auto-tune", action="store_true",
                        help="ignore config/auto_tune.json and use the built-in defaults")
    parser.add_argument("--manual-tuning", action="store_true",
                        help="show the GAIN and LEAD sliders and use THEM instead of the "
                             "automatic values (for experiments)")
    parser.add_argument("--gain", type=float, default=DEFAULT_GAIN,
                        help="GAIN is automatic now. This value (0.01-1.0) is used only with "
                             "--manual-tuning (starting slider value), --no-predict or --scale-gain")
    parser.add_argument("--deadzone", type=int, default=DEFAULT_DEADZONE_PX,
                        help="pixels of leftover error to ignore per axis")
    parser.add_argument("--lead", type=float, default=DEFAULT_LEAD_MS,
                        help="LEAD is automatic now (measured latency + --servo-delay). "
                             "This value (milliseconds) is only used with --manual-tuning")
    parser.add_argument("--servo-delay", type=float, default=DEFAULT_SERVO_DELAY_S * 1000,
                        metavar="MS",
                        help="how long the servos take to react to a command, in ms "
                             "(default 80); added to the measured latency to aim ahead")
    parser.add_argument("--no-predict", action="store_true",
                        help="turn the Kalman filter + feedforward off: plain P control")
    parser.add_argument("--smoothing", type=float, default=0.0,
                        help="only with --no-predict: 0 = off, 0.1-0.9 = smooth the balloon "
                             "position (the Kalman filter already smooths when predicting)")
    parser.add_argument("--lock-fraction", type=float, default=DEFAULT_LOCK_FRACTION,
                        help="lock zone = this x the balloon's apparent radius, 0.1-1.0 "
                             "(also a live slider)")
    parser.add_argument("--lock-min", type=float, default=DEFAULT_LOCK_MIN_PX,
                        help="smallest lock zone radius in pixels")
    parser.add_argument("--lock-max", type=float, default=DEFAULT_LOCK_MAX_PX,
                        help="largest lock zone radius in pixels")
    parser.add_argument("--min-radius", type=float, default=DEFAULT_MIN_RADIUS_PX,
                        help="balloon apparent radius (px) below which it's too far to lock")
    parser.add_argument("--scale-gain", action="store_true",
                        help="EXPERIMENTAL, off by default: GAIN x (reference radius / "
                             "apparent radius), limited to 0.5x-2x. See the README.")
    parser.add_argument("--reference-radius", type=float, default=DEFAULT_REFERENCE_RADIUS_PX,
                        help="with --scale-gain: the apparent radius (px) where GAIN is used as-is")
    parser.add_argument("--calibrate-distance", type=float, nargs="?", const=-1.0, default=None,
                        metavar="CM",
                        help="distance calibration: hold the balloon at CM centimeters and "
                             "press 'c' (asks for CM if you leave it out). Laser stays off.")
    parser.add_argument("--first-run", action="store_true",
                        help="FIRST TIME ON REAL HARDWARE: GAIN 0.1, LEAD 0, max 3 deg per "
                             "command, angles limited to 45-135, laser permanently disabled")
    parser.add_argument("--max-step", type=float, default=DEFAULT_MAX_STEP_DEG,
                        help="slew limit: max degrees a servo may move per command sent")
    parser.add_argument("--servo-speed", type=float, default=DEFAULT_SERVO_SPEED,
                        help="dry-run only: max simulated servo speed in degrees per frame")
    parser.add_argument("--no-capture-thread", action="store_true",
                        help="read the camera in the main loop (old way) instead of a thread")
    parser.add_argument("--invert-pan", action="store_true", help="flip pan direction")
    parser.add_argument("--invert-tilt", action="store_true", help="flip tilt direction")
    args = parser.parse_args()

    args.min_angle, args.max_angle = MIN_ANGLE, MAX_ANGLE
    if args.first_run:
        # Override whatever else was asked for: these are the SAFE values.
        args.gain = FIRST_RUN["gain"]
        args.lead = FIRST_RUN["lead"]
        args.max_step = min(args.max_step, FIRST_RUN["max_step"])
        args.servo_speed = min(args.servo_speed, FIRST_RUN["max_step"])
        args.min_angle, args.max_angle = FIRST_RUN["min_angle"], FIRST_RUN["max_angle"]
    if args.replay and not args.dry_run:
        print("--replay: forcing --dry-run (a recording can't aim real servos).")
        args.dry_run = True
    if args.lock_max < args.lock_min:
        print(f"WARNING: --lock-max ({args.lock_max}) is smaller than --lock-min "
              f"({args.lock_min}); using {args.lock_min} for both.")
        args.lock_max = args.lock_min
    return args


def ask_calibration_distance(args):
    """--calibrate-distance without a number: ask for it in the terminal."""
    while args.calibrate_distance is not None and args.calibrate_distance <= 0:
        text = input("Distance from the camera to the balloon, in cm: ").strip()
        try:
            args.calibrate_distance = float(text)
        except ValueError:
            print("Please type a number, e.g. 150")


def choose_port_if_needed(args):
    """Return the COM port to use (asks the user if --port wasn't given)."""
    if args.dry_run or args.port:
        return args.port
    from servo_test import choose_port      # reuse the Phase 3 port picker
    return choose_port()
