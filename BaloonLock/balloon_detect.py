"""
balloon_detect.py  -  Phase 1 of the balloon laser turret project.

What this script does, step by step, for every webcam frame:
  1. Grab a frame (a picture) from the webcam.
  2. Blur it slightly so tiny specks of noise don't confuse us.
  3. Convert it from BGR colors to HSV colors (explained below).
  4. Make a black-and-white "mask": white = pixels whose color is inside
     the range set by the trackbars, black = everything else.
  5. Find the white blobs in the mask and keep the biggest one, but only
     if it is bigger than a minimum area (so random specks are ignored).
  6. Draw a box around that blob, a dot at its center (the "centroid"),
     and a crosshair at the center of the image.
  7. Show how far the blob is from the image center, in pixels.
     That distance is the "error" the turret will try to shrink to zero later.

Why HSV instead of normal RGB colors?
  - H (Hue)        = which color it is (red, orange, green, blue...).
  - S (Saturation) = how strong/vivid the color is (gray = 0, vivid = 255).
  - V (Value)      = how bright it is (black = 0, bright = 255).
  In HSV, "a red balloon" stays roughly the same Hue even when lighting
  changes, which makes it much easier to pick out by color than RGB.
  Note: OpenCV stores Hue as 0-179 (not 0-359), and S and V as 0-255.

Mouse:
  Move the mouse over the "Balloon Detect" window to see the HSV color
  under the cursor. LEFT-CLICK on the balloon to automatically set the
  sliders to a range around that color (then fine-tune by hand if needed).

Presets (saved slider settings per balloon color, stored in presets.json):
  python balloon_detect.py                  -> default sliders
  python balloon_detect.py --preset green   -> start with the "green" preset;
                                               pressing 's' saves back to "green"
  The turret uses them with:  python turret_tracker.py --color green

Keys (click on one of the video windows first so it has keyboard focus):
  p = print the current HSV slider values to the terminal
  s = save the current sliders into presets.json (under the --preset name,
      or "custom" if no --preset was given)
  + / - / 0 = zoom both windows in / out / original size
      (you can also drag their corners). The pictures are letterboxed -
      scaled to fit without stretching, with black bars if needed.
      Resizing is display-only and doesn't change the detection.
  q = quit
"""

import argparse
import json
import os
import sys

import cv2
import numpy as np

from config import ConfigError, read_json, validate_presets
from camera import CAMERA_INDEX, lock_exposure_and_white_balance, open_camera, restore_auto
from display import WINDOW_FLAGS, WindowZoom, letterbox, nothing, read_all_keys


# ---------------------------------------------------------------------------
# Settings you might want to change
# ---------------------------------------------------------------------------
BLUR_SIZE = 11            # Blur strength. Must be an ODD number (5, 7, 11...).

# How wide a range the click-to-pick feature sets around the clicked color.
# Bigger = catches more of the balloon (shadows, shiny spots) but also more
# of the background. These are good starting points.
PICK_H_RANGE = 10         # Hue: clicked hue +/- this
PICK_S_RANGE = 70         # Saturation: clicked saturation minus this
PICK_V_RANGE = 80         # Brightness: clicked brightness minus this

# Presets file lives next to this script.
PRESETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets.json")

# Laser-dot filter. A laser dot seen by a webcam is a SMALL blob that is
# EXTREMELY bright (it often maxes out the camera sensor). Any blob that is
# both smaller than LASER_DOT_MAX_AREA and brighter on average than
# LASER_DOT_MIN_BRIGHTNESS (the V in HSV, 0-255) is ignored, so the turret
# never starts chasing its own laser dot. A real balloon is much bigger.
LASER_DOT_MAX_AREA = 1500
LASER_DOT_MIN_BRIGHTNESS = 230

MAIN_WINDOW = "Balloon Detect"   # Window with the camera image + drawings
MASK_WINDOW = "Mask"             # Window with the black/white mask
CONTROLS_WINDOW = "Controls"     # Window that holds the sliders (trackbars)




# open_camera() now lives in camera.py (shared by all the scripts).


# Starting slider values when no preset is loaded: catch most vivid colors.
DEFAULT_SETTINGS = {"h_min": 0, "h_max": 179, "s_min": 120, "s_max": 255,
                    "v_min": 70, "v_max": 255, "min_area": 500}


def load_presets(path=PRESETS_FILE):
    """Read presets.json and return a dictionary like {"green": {...}, ...}.
    Keys starting with "_" (like "_comment") are notes, not presets.
    Raises config.ConfigError with a clear message if the file is broken."""
    if not os.path.exists(path):
        return {}
    return validate_presets(read_json(path), os.path.basename(path))


def save_preset(name, settings, path=PRESETS_FILE):
    """Add or replace one preset in presets.json, keeping all the others."""
    data = read_json(path) if os.path.exists(path) else {}
    validate_presets({name: settings}, os.path.basename(path))   # don't save bad values
    data[name] = settings
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def create_trackbars(start=DEFAULT_SETTINGS):
    """Create the sliders, starting at the values in `start` (a preset)."""
    cv2.namedWindow(CONTROLS_WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(CONTROLS_WINDOW, 400, 300)

    # cv2.createTrackbar(name, window, starting value, max value, callback)
    cv2.createTrackbar("H min", CONTROLS_WINDOW, start["h_min"], 179, nothing)
    cv2.createTrackbar("H max", CONTROLS_WINDOW, start["h_max"], 179, nothing)
    cv2.createTrackbar("S min", CONTROLS_WINDOW, start["s_min"], 255, nothing)
    cv2.createTrackbar("S max", CONTROLS_WINDOW, start["s_max"], 255, nothing)
    cv2.createTrackbar("V min", CONTROLS_WINDOW, start["v_min"], 255, nothing)
    cv2.createTrackbar("V max", CONTROLS_WINDOW, start["v_max"], 255, nothing)
    # Blobs smaller than this many pixels are ignored (noise filter).
    cv2.createTrackbar("Min area", CONTROLS_WINDOW, start["min_area"], 10000, nothing)


def read_trackbars():
    """Read every slider and return the values as a dictionary."""
    return {
        "h_min": cv2.getTrackbarPos("H min", CONTROLS_WINDOW),
        "h_max": cv2.getTrackbarPos("H max", CONTROLS_WINDOW),
        "s_min": cv2.getTrackbarPos("S min", CONTROLS_WINDOW),
        "s_max": cv2.getTrackbarPos("S max", CONTROLS_WINDOW),
        "v_min": cv2.getTrackbarPos("V min", CONTROLS_WINDOW),
        "v_max": cv2.getTrackbarPos("V max", CONTROLS_WINDOW),
        "min_area": cv2.getTrackbarPos("Min area", CONTROLS_WINDOW),
    }


def sample_hsv(hsv_frame, x, y):
    """Return the (H, S, V) color around pixel (x, y).
    We take the MEDIAN of a small 7x7 square instead of one single pixel,
    so one noisy pixel can't throw off the result."""
    height, width = hsv_frame.shape[:2]
    x0, x1 = max(x - 3, 0), min(x + 4, width)
    y0, y1 = max(y - 3, 0), min(y + 4, height)
    patch = hsv_frame[y0:y1, x0:x1].reshape(-1, 3)
    h, s, v = np.median(patch, axis=0).astype(int)
    return int(h), int(s), int(v)


LOW_SATURATION = 60       # below this a color is "grayish": hue is meaningless


def circular_mean_hue(hues):
    """Average of OpenCV hues (0-179) that respects the wrap-around.
    A plain average of red hues like 178 and 2 would give 90 (cyan!). Hue is
    really an angle on a color circle, so we average the directions instead:
    turn each hue into an arrow (cos, sin), add the arrows, take the angle."""
    angles = np.asarray(hues, np.float64) * (2 * np.pi / 180)
    mean_angle = np.arctan2(np.sin(angles).mean(), np.cos(angles).mean())
    return int(round(mean_angle * 180 / (2 * np.pi))) % 180


def color_range_around(h, s, v, min_area=100):
    """HSV slider settings that catch colors similar to (h, s, v) - the same
    ranges as click-to-pick. For grayish colors (black, white, gray balloons)
    hue means nothing, so we match by brightness instead."""
    if s < LOW_SATURATION:
        return {"h_min": 0, "h_max": 179, "s_min": 0, "s_max": min(255, s + 60),
                "v_min": max(0, v - 60), "v_max": min(255, v + 60), "min_area": min_area}
    return {"h_min": (h - PICK_H_RANGE) % 180, "h_max": (h + PICK_H_RANGE) % 180,
            "s_min": max(s - PICK_S_RANGE, 0), "s_max": 255,
            "v_min": max(v - PICK_V_RANGE, 0), "v_max": 255, "min_area": min_area}


def on_mouse(event, x, y, flags, state):
    """OpenCV calls this every time the mouse moves or clicks on the main
    window. `state` is a dictionary shared with the main loop."""
    hsv_frame = state["hsv"]
    if hsv_frame is None or state["view"] is None:
        return  # no frame yet

    # The window shows a SCALED copy of the frame (with black bars), so the
    # mouse position is in window pixels. Convert it back to frame pixels.
    point = state["view"].to_frame(x, y)
    state["mouse_xy"] = point          # None if the mouse is on a black bar
    if point is None:
        return
    x, y = point

    if event == cv2.EVENT_LBUTTONDOWN:
        h, s, v = sample_hsv(hsv_frame, x, y)

        # Hue range around the clicked hue. "% 180" wraps around the ends of
        # the hue scale: e.g. clicking red with H = 5 gives H min = 175 and
        # H max = 15, which make_mask() understands as "wrap around red".
        h_min = (h - PICK_H_RANGE) % 180
        h_max = (h + PICK_H_RANGE) % 180

        cv2.setTrackbarPos("H min", CONTROLS_WINDOW, h_min)
        cv2.setTrackbarPos("H max", CONTROLS_WINDOW, h_max)
        cv2.setTrackbarPos("S min", CONTROLS_WINDOW, max(s - PICK_S_RANGE, 0))
        cv2.setTrackbarPos("S max", CONTROLS_WINDOW, 255)
        cv2.setTrackbarPos("V min", CONTROLS_WINDOW, max(v - PICK_V_RANGE, 0))
        cv2.setTrackbarPos("V max", CONTROLS_WINDOW, 255)

        print(f"Clicked color HSV = ({h}, {s}, {v})  -> sliders updated")
        if s < 60:
            print("  Warning: that color is very pale/gray (low saturation). Color")
            print("  tracking works best on vivid balloons (red, orange, green, blue).")


def make_mask(hsv_frame, t, close_size=5):
    """Build the black/white mask from the HSV frame and slider values `t`.
    close_size: size of the "close" step below. A bigger close (e.g. 11) glues
    a motion-blurred, streaky balloon back into ONE blob."""
    if t["h_min"] <= t["h_max"]:
        # Normal case: keep hues between H min and H max.
        lower = np.array([t["h_min"], t["s_min"], t["v_min"]])
        upper = np.array([t["h_max"], t["s_max"], t["v_max"]])
        mask = cv2.inRange(hsv_frame, lower, upper)
    else:
        # RED needs special handling: red sits at BOTH ends of the hue
        # scale (near 0 and near 179). If you set H min HIGHER than H max
        # (e.g. H min = 170, H max = 10), we "wrap around" and keep
        # hues 170..179 AND 0..10, then combine the two masks.
        lower1 = np.array([t["h_min"], t["s_min"], t["v_min"]])
        upper1 = np.array([179, t["s_max"], t["v_max"]])
        lower2 = np.array([0, t["s_min"], t["v_min"]])
        upper2 = np.array([t["h_max"], t["s_max"], t["v_max"]])
        mask = cv2.inRange(hsv_frame, lower1, upper1) | cv2.inRange(hsv_frame, lower2, upper2)

    # Clean up the mask:
    #  - "open"  (erode then dilate) removes small white specks.
    #  - "close" (dilate then erode) fills small black holes inside the blob,
    #    e.g. where light reflects off the shiny balloon.
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    close_kernel = np.ones((close_size, close_size), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)
    return mask


def looks_like_laser_dot(contour, area, hsv_frame):
    """True if this blob is small AND extremely bright, like a laser dot."""
    if area >= LASER_DOT_MAX_AREA:
        return False                       # too big to be a laser dot
    # Average brightness (V) of the pixels inside this blob: draw the blob
    # filled-in on a blank image and use it as a stencil for cv2.mean().
    stencil = np.zeros(hsv_frame.shape[:2], np.uint8)
    cv2.drawContours(stencil, [contour], -1, 255, thickness=-1)
    # cv2.mean returns (mean H, mean S, mean V, unused); [2] picks V.
    mean_v = cv2.mean(hsv_frame, mask=stencil)[2]
    return mean_v >= LASER_DOT_MIN_BRIGHTNESS


def find_largest_blob(mask, min_area, hsv_frame=None):
    """Return (contour, area) of the biggest acceptable white blob, or
    (None, 0) if there isn't one. A blob is acceptable if it is at least
    `min_area` pixels big and (when hsv_frame is given) isn't a laser dot."""
    # A "contour" is the outline of a white blob.
    # RETR_EXTERNAL = only outer outlines (ignore holes inside blobs).
    # [-2] picks the contour list in a way that works on any OpenCV version.
    contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]

    # Check blobs from biggest to smallest; return the first one that passes.
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        area = cv2.contourArea(contour)
        if area < min_area:
            break                          # this and all the rest are too small
        if hsv_frame is not None and looks_like_laser_dot(contour, area, hsv_frame):
            continue                       # skip laser dots, try the next blob
        return contour, area
    return None, 0


def draw_center_crosshair(frame, center_x, center_y):
    """Draw a white crosshair at the middle of the image."""
    size = 20
    color = (255, 255, 255)  # OpenCV colors are (Blue, Green, Red)
    cv2.line(frame, (center_x - size, center_y), (center_x + size, center_y), color, 1)
    cv2.line(frame, (center_x, center_y - size), (center_x, center_y + size), color, 1)
    cv2.circle(frame, (center_x, center_y), 4, color, 1)


def main():
    # --- Command line: which preset to start from / save to -----------------
    parser = argparse.ArgumentParser(description="Tune HSV color detection for a balloon")
    parser.add_argument("--preset", help="preset name in presets.json (e.g. green)")
    parser.add_argument("--camera", type=int, default=CAMERA_INDEX,
                        help="which camera to use (0 = built-in; see turret_tracker.py --list-cameras)")
    parser.add_argument("--auto-exposure", action="store_true",
                        help="leave auto-exposure/white-balance ON (default: lock them, so colors "
                             "stay stable while you tune)")
    args = parser.parse_args()

    start = DEFAULT_SETTINGS
    if args.preset:
        try:
            presets = load_presets()
        except ConfigError as error:
            print(f"ERROR: {error}")
            sys.exit(1)
        if args.preset in presets:
            start = {**DEFAULT_SETTINGS, **presets[args.preset]}
            print(f"Loaded preset '{args.preset}': {presets[args.preset]}")
        else:
            print(f"No preset named '{args.preset}' yet - starting from defaults. "
                  f"Press 's' to create it. Existing presets: {', '.join(presets) or 'none'}")
    save_name = args.preset or "custom"

    # --- Open the webcam -----------------------------------------------------
    cap = open_camera(args.camera)
    if cap is None:
        print(f"ERROR: Could not open webcam #{args.camera}.")
        print("Things to check:")
        print("  - Is another app (Zoom, Teams, Camera app, browser) using it? Close it.")
        print("  - Windows Settings > Privacy & security > Camera: allow desktop apps.")
        print("  - Is there a physical privacy shutter or a camera on/off key (F-key)?")
        print("  - Try another camera: --camera 1")
        sys.exit(1)

    # Freeze exposure + white balance so the balloon's HSV numbers don't drift
    # while you tune (see camera.py for why).
    if not args.auto_exposure:
        for line in lock_exposure_and_white_balance(cap):
            print(f"Camera: {line}")

    print("Webcam opened. Click a video window, then press 'p' to print HSV values, 'q' to quit.")

    # --- Create the windows ---------------------------------------------------
    # Resizable windows. We "letterbox" the pictures ourselves (scale to fit
    # without stretching, black bars for leftover space) - see display.py.
    cv2.namedWindow(MAIN_WINDOW, WINDOW_FLAGS)
    cv2.namedWindow(MASK_WINDOW, WINDOW_FLAGS)
    zoom = WindowZoom([MAIN_WINDOW, MASK_WINDOW])   # + - 0 resize both together
    create_trackbars(start)

    # Shared info between the main loop and the mouse function
    # view = how the frame is placed in the window (for mouse -> frame pixels)
    state = {"hsv": None, "mouse_xy": None, "view": None}
    cv2.setMouseCallback(MAIN_WINDOW, on_mouse, state)

    # --- Main loop: runs once per frame ---------------------------------------
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            print("ERROR: Lost the webcam feed (could not read a frame). Quitting.")
            break

        # Image size and center. frame.shape = (height, width, 3 color channels)
        height, width = frame.shape[:2]
        center_x, center_y = width // 2, height // 2
        zoom.set_base(width, height)     # first frame: windows = picture size

        # 1) Blur, 2) convert to HSV, 3) mask by color
        blurred = cv2.GaussianBlur(frame, (BLUR_SIZE, BLUR_SIZE), 0)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
        state["hsv"] = hsv  # let the mouse function see the latest frame
        t = read_trackbars()
        mask = make_mask(hsv, t)

        # 4) Find the biggest blob that is big enough
        blob, area = find_largest_blob(mask, t["min_area"], hsv)

        draw_center_crosshair(frame, center_x, center_y)

        if blob is not None:
            # Bounding box: the smallest upright rectangle around the blob
            x, y, w, h = cv2.boundingRect(blob)
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

            # Centroid = the blob's "center of mass", computed from image
            # moments. m00 is the area; m10/m00 and m01/m00 are the average
            # x and y of all the blob's pixels.
            M = cv2.moments(blob)
            if M["m00"] != 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
            else:
                # Fallback (very rare): use the middle of the bounding box
                cx, cy = x + w // 2, y + h // 2
            cv2.circle(frame, (cx, cy), 6, (0, 0, 255), -1)  # filled red dot

            # Line from image center to the balloon, to visualize the error
            cv2.line(frame, (center_x, center_y), (cx, cy), (0, 255, 255), 1)

            # Pixel error = how far the balloon is from the image center.
            #   error_x > 0  -> balloon is to the RIGHT of center
            #   error_y > 0  -> balloon is BELOW center (image y grows downward!)
            error_x = cx - center_x
            error_y = cy - center_y

            status = f"Error x: {error_x:+4d} px   y: {error_y:+4d} px   area: {int(area)}"
            status_color = (0, 255, 0)
        else:
            status = "No balloon detected"
            status_color = (0, 0, 255)

        # Draw the text twice (thick black, then thin color) so it is
        # readable on any background.
        cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
        cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)

        # Show the HSV color under the mouse cursor (bottom-left corner)
        if state["mouse_xy"] is not None:
            mx, my = state["mouse_xy"]          # already in frame pixels
            h, s, v = sample_hsv(hsv, mx, my)
            info = f"Mouse HSV: ({h}, {s}, {v})  - click balloon to pick"
            cv2.putText(frame, info, (10, height - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
            cv2.putText(frame, info, (10, height - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        # Help line (keys)
        help_text = "click = pick color | p print | s save | + - 0 zoom | q quit"
        cv2.putText(frame, help_text, (10, height - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 4)
        cv2.putText(frame, help_text, (10, height - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1)

        # Show both pictures letterboxed: scaled to fit each window WITHOUT
        # stretching. (Detection above already ran on the original frame.)
        canvas, state["view"] = letterbox(frame, MAIN_WINDOW)
        mask_canvas, _ = letterbox(mask, MASK_WINDOW)
        cv2.imshow(MAIN_WINDOW, canvas)
        cv2.imshow(MASK_WINDOW, mask_canvas)

        # --- Keyboard ---------------------------------------------------------
        # Read EVERY waiting key (not just one), so holding a key down can't
        # flood the window's queue and freeze the picture - see display.py.
        keys = read_all_keys()
        if ord("q") in keys:
            print("Quitting.")
            break
        for key in dict.fromkeys(keys):          # each different key once per frame
            if key == ord("p"):
                print(
                    f"HSV lower = ({t['h_min']}, {t['s_min']}, {t['v_min']})   "
                    f"HSV upper = ({t['h_max']}, {t['s_max']}, {t['v_max']})   "
                    f"min area = {t['min_area']}"
                )
            elif key == ord("s"):
                save_preset(save_name, t)
                print(f"Saved preset '{save_name}' to {PRESETS_FILE}: {t}")
            else:
                zoom.handle_key(key)             # + = - 0 resize both windows

        # Also quit if the user closes the main window with the X button
        if cv2.getWindowProperty(MAIN_WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            print("Window closed. Quitting.")
            break

    # --- Clean up: always release the camera and close windows ---------------
    if not args.auto_exposure:
        restore_auto(cap)        # don't leave your webcam stuck for video calls
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
