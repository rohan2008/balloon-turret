"""
selftest.py  -  `python turret_tracker.py --self-test`

Quick health check that needs NO Arduino and moves nothing. It checks:
  1. the camera opens and delivers a picture
  2. the AI model loads and runs
  3. presets.json and turret_config.json are valid
  4. the list of serial ports can be read (no port is opened)
  5. the laser safety switches are off (Python and turret.ino)
and prints PASS / FAIL for each. Exit code 0 = all passed, 1 = something failed.
"""

import os
import re

import numpy as np

REPO = os.path.dirname(os.path.abspath(__file__))


def check_camera(camera_index):
    from camera import open_camera
    cap = open_camera(camera_index)
    if cap is None:
        return False, f"camera {camera_index} did not open (another app using it? try --camera 1)"
    try:
        for _ in range(10):                      # first frames can be empty while it starts
            ok, frame = cap.read()
            if ok and frame is not None:
                return True, f"camera {camera_index}: {frame.shape[1]}x{frame.shape[0]}"
        return False, f"camera {camera_index} opened but gave no picture"
    finally:
        cap.release()


def check_ai_model():
    try:
        from balloon_detect_ai import get_detections, load_model
        model = load_model()
        get_detections(model, np.zeros((480, 640, 3), np.uint8), 0.2)
        return True, "yolov8n-oiv7 loaded and ran on a test image"
    except (Exception, SystemExit) as error:     # load_model() calls sys.exit on failure
        return False, f"AI model problem: {error.__class__.__name__}: {error}"


def check_config_files():
    from balloon_detect import load_presets
    from config import ConfigError, load_config
    try:
        presets = load_presets()
        config = load_config()
    except ConfigError as error:
        return False, str(error)
    calibrated = ", ".join(config.get("distance_calibration", {})) or "none yet"
    return True, f"presets.json OK ({len(presets)} presets); distance calibration: {calibrated}"


def check_serial_ports():
    try:
        from serial.tools import list_ports
        ports = [p.device for p in list_ports.comports()]
    except Exception as error:
        return False, f"could not list serial ports: {error}"
    return True, f"serial ports visible: {', '.join(ports) or 'none (Arduino not plugged in)'}"


def check_laser_switches():
    import safety
    with open(os.path.join(REPO, "turret", "turret.ino"), encoding="utf-8") as f:
        sketch_off = re.search(r"const bool LASER_ENABLED = false;", f.read()) is not None
    if safety.LASER_ENABLED or not sketch_off:
        return False, (f"LASER_ENABLED is ON (python={safety.LASER_ENABLED}, "
                       f"sketch false={sketch_off}) - only intended after the laser hardware check")
    return True, "LASER_ENABLED is False in safety.py and in turret.ino"


def run_self_test(camera_index=0):
    """Run every check, print PASS/FAIL lines, and return 0 (all passed) or 1."""
    checks = [
        ("camera", lambda: check_camera(camera_index)),
        ("AI model", check_ai_model),
        ("config files", check_config_files),
        ("serial port list", check_serial_ports),
        ("laser safety switches", check_laser_switches),
    ]
    print("SELF-TEST (no Arduino needed, nothing moves)")
    failures = 0
    for name, check in checks:
        try:
            ok, detail = check()
        except Exception as error:                # a crashing check is a FAIL, not a crash
            ok, detail = False, f"{error.__class__.__name__}: {error}"
        failures += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<22} {detail}")
    print("RESULT:", "ALL PASSED" if failures == 0 else f"{failures} FAILED")
    return 0 if failures == 0 else 1
