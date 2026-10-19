"""
turret_tracker.py  -  start the balloon-tracking turret.

This file is only the entry point; the program lives in:
    tracker_app.py  - the main loop (read this first: its top comment explains everything)
    safety.py       - LASER_ENABLED, laser_should_fire() and the watchdog
    control.py      - aiming math, lock zone, Kalman filter
    vision.py       - camera thread, recording/replay, detectors
    serial_link.py  - talking to the Arduino
    ui.py, display.py, cli.py, config.py, camera.py - window, flags, files, camera

Examples:
    python turret_tracker.py --dry-run
    python turret_tracker.py --self-test
    python turret_tracker.py --port COM5 --first-run
"""

from tracker_app import main

if __name__ == "__main__":
    main()
