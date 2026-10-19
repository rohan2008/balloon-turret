"""
camera.py  -  opening the webcam, listing cameras, and locking exposure /
white balance. Shared by the scripts.

WHY LOCK EXPOSURE AND WHITE BALANCE?
    Webcams constantly re-adjust themselves:
      - auto EXPOSURE makes the picture brighter/darker depending on what's
        in view (a white wall or a dark shirt changes everything), and
      - auto WHITE BALANCE shifts all colors warmer/cooler.
    Both change a balloon's H, S and V numbers from moment to moment, so HSV
    thresholds that worked a second ago suddenly stop matching. Locking them
    freezes the camera's settings at whatever it chose at startup.

    It's "when possible" because many webcam drivers ignore some of these
    requests. We report what the driver SAID, but some drivers say yes and
    do nothing - if colors still drift, that's why.
"""

import sys

import cv2

CAMERA_INDEX = 0          # 0 = built-in laptop webcam. Try 1 for a USB webcam.

# OpenCV's odd convention for CAP_PROP_AUTO_EXPOSURE (on the Windows
# DirectShow and Linux V4L2 backends): 0.25 = manual, 0.75 = automatic.
AUTO_EXPOSURE_MANUAL = 0.25
AUTO_EXPOSURE_AUTO = 0.75


def open_camera(index):
    """Try to open webcam number `index`. Returns the camera object, or None."""
    # On Windows, CAP_DSHOW (DirectShow) usually opens the camera much faster
    # than the default backend. If it fails, we fall back to the default.
    if sys.platform.startswith("win"):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap.isOpened():
            return cap
        cap.release()

    cap = cv2.VideoCapture(index)
    if cap.isOpened():
        return cap

    cap.release()
    return None


def list_cameras(max_index=5):
    """Try camera numbers 0..max_index and return [(index, width, height), ...]
    for the ones that open and deliver a picture."""
    found = []
    for index in range(max_index + 1):
        cap = open_camera(index)
        if cap is None:
            continue
        ok, frame = cap.read()
        if ok and frame is not None:
            found.append((index, frame.shape[1], frame.shape[0]))
        cap.release()
    return found


def print_camera_list(max_index=5):
    cameras = list_cameras(max_index)
    if not cameras:
        print("No cameras found. Is one plugged in, and is no other app using it?")
    for index, width, height in cameras:
        print(f"  camera {index}: {width}x{height}   (use --camera {index})")
    return cameras


def lock_exposure_and_white_balance(cap, warmup_frames=20):
    """Let the camera settle for a few frames (so auto-exposure has picked
    good values for the room), then switch auto-exposure and auto-white-
    balance OFF, keeping the values it chose. Returns a list of report lines."""
    for _ in range(warmup_frames):
        cap.read()

    report = []
    exposure = cap.get(cv2.CAP_PROP_EXPOSURE)
    ok = cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_MANUAL)
    if ok:
        cap.set(cv2.CAP_PROP_EXPOSURE, exposure)           # keep the value it chose
    report.append(f"auto-exposure OFF (exposure {exposure:g}): "
                  f"{'driver accepted' if ok else 'not supported by this camera/driver'}")

    temperature = cap.get(cv2.CAP_PROP_WB_TEMPERATURE)
    ok = cap.set(cv2.CAP_PROP_AUTO_WB, 0)
    if ok and temperature > 0:
        cap.set(cv2.CAP_PROP_WB_TEMPERATURE, temperature)
    report.append(f"auto-white-balance OFF (temperature {temperature:g}): "
                  f"{'driver accepted' if ok else 'not supported by this camera/driver'}")
    return report


def restore_auto(cap):
    """Turn auto-exposure and auto-white-balance back ON. Some drivers REMEMBER
    these settings after our program ends, which would leave your webcam stuck
    for video calls - so we always put them back when we quit."""
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_AUTO)
    cap.set(cv2.CAP_PROP_AUTO_WB, 1)
