"""
balloon_detect_ai.py  -  Phase 1 (AI version) of the balloon laser turret.

The color version (balloon_detect.py) finds the balloon by its COLOR, so it
can't handle black/white/gray balloons. This version uses a neural network
("AI object detector") that has learned what balloons LOOK like - their
shape, shine and outline - so it works for most balloon colors.

How it works, for every webcam frame:
  1. Grab a frame from the webcam.
  2. Give the frame to YOLO (an object-detection neural network). It returns
     a list of "detections": a box, a label (e.g. "Balloon", "Person") and a
     confidence (how sure it is, 0-100%).
  3. Keep only balloon detections that are at least as confident as the
     "Confidence %" slider AND whose box is roughly square (a balloon is
     round, so its box can't be very long and skinny). Pick the most
     confident one.
     Note: the AI already learned balloon SHAPES from thousands of photos -
     that's how it can find a black balloon. The squareness check is just
     an extra safety rule on top.
  4. Draw its box, its center point (centroid), and a crosshair at the
     center of the image, and show the pixel error between them.

About the model file (yolov8n-oiv7.pt):
  - "yolov8n" = YOLO version 8, "n"ano size (the smallest and fastest).
  - "oiv7"    = trained on Google's Open Images V7 dataset, which has 601
                kinds of objects, including "Balloon". (The more common
                YOLO models don't know balloons at all.)
  - The model sometimes calls a balloon a "Ball", so we accept both.

Keys (click on the video window first so it has keyboard focus):
  a = toggle showing ALL objects the AI sees (gray boxes) - useful to see
      what it thinks your balloon is if it isn't detected as a balloon
  p = print the current detection info to the terminal
  q = quit
"""

import os
import sys
import time

import cv2
from ultralytics import YOLO

from camera import CAMERA_INDEX, open_camera  # noqa: F401  (also re-exported for turret_sim.py)
from display import draw_center_crosshair, nothing  # noqa: F401  (re-exported for turret_sim.py)


# ---------------------------------------------------------------------------
# Settings you might want to change
# ---------------------------------------------------------------------------

# The model file lives in the same folder as this script.
SCRIPT_FOLDER = os.path.dirname(os.path.abspath(__file__))
MODEL_FILE = os.path.join(SCRIPT_FOLDER, "yolov8n-oiv7.pt")

# Labels we treat as "the balloon".
TARGET_LABELS = ("Balloon", "Ball")

# Shape check: a balloon is round, so its box should be roughly square.
# "Aspect ratio" = box width / box height. A perfect square is 1.0.
# Balloons are a bit taller than wide (and your hand/the knot can stretch
# the box), so we allow a generous range. Boxes outside it are ignored.
MIN_ASPECT_RATIO = 0.5    # tall and skinny below this -> not a balloon
MAX_ASPECT_RATIO = 1.6    # wide and flat above this  -> not a balloon

# Size (in pixels) the frame is shrunk to before the AI looks at it.
# 640 = more accurate, ~14 frames/sec on your laptop.
# 416 = faster (~22 frames/sec) but less sure about what it sees.
AI_IMAGE_SIZE = 640

# Starting value of the confidence slider, in percent. Lower = finds the
# balloon more easily but also more false alarms; higher = the opposite.
DEFAULT_CONFIDENCE = 20

MAIN_WINDOW = "Balloon Detect (AI)"


# nothing() and draw_center_crosshair() now live in display.py (shared).


# open_camera() now lives in camera.py (shared by all the scripts).


def load_model():
    """Load the YOLO neural network. Returns the model, or exits on failure."""
    try:
        # If the file is missing, ultralytics tries to download it (needs internet).
        return YOLO(MODEL_FILE)
    except Exception as error:
        print(f"ERROR: Could not load the AI model '{MODEL_FILE}'.")
        print(f"  Details: {error}")
        print("  Make sure yolov8n-oiv7.pt is in the same folder as this script,")
        print("  or connect to the internet so it can be downloaded.")
        sys.exit(1)


def get_detections(model, frame, min_confidence):
    """Run the AI on one frame. Returns a list of detections, where each
    detection is a dictionary: {"label", "confidence", "box": (x1,y1,x2,y2)}.
    (x1, y1) is the box's top-left corner, (x2, y2) its bottom-right corner."""
    results = model.predict(frame, imgsz=AI_IMAGE_SIZE, conf=min_confidence, verbose=False)
    result = results[0]  # we gave it one image, so there is one result

    detections = []
    for box in result.boxes:
        class_number = int(box.cls[0])
        detections.append({
            "label": model.names[class_number],      # e.g. "Balloon"
            "confidence": float(box.conf[0]),        # 0.0 to 1.0
            "box": tuple(int(v) for v in box.xyxy[0]),
        })
    return detections


def is_round_enough(box):
    """Return True if the box is roughly square, like a balloon's would be."""
    x1, y1, x2, y2 = box
    width = x2 - x1
    height = y2 - y1
    if height <= 0:
        return False
    aspect_ratio = width / height
    return MIN_ASPECT_RATIO <= aspect_ratio <= MAX_ASPECT_RATIO


def pick_balloon(detections):
    """From all detections, return the most confident balloon, or None.
    A detection counts as a balloon only if the AI labeled it as one AND
    its box passes the roundness (aspect ratio) check."""
    balloons = [
        d for d in detections
        if d["label"] in TARGET_LABELS and is_round_enough(d["box"])
    ]
    if not balloons:
        return None
    return max(balloons, key=lambda d: d["confidence"])




def draw_text(frame, text, position, color, scale=0.7):
    """Draw text with a black outline so it is readable on any background."""
    cv2.putText(frame, text, position, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4)
    cv2.putText(frame, text, position, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)


def main():
    # --- Load the AI model (takes a few seconds) ------------------------------
    print("Loading AI model...")
    model = load_model()

    # --- Open the webcam -----------------------------------------------------
    cap = open_camera(CAMERA_INDEX)
    if cap is None:
        print(f"ERROR: Could not open webcam #{CAMERA_INDEX}.")
        print("Things to check:")
        print("  - Is another app (Zoom, Teams, Camera app, browser) using it? Close it.")
        print("  - Windows Settings > Privacy & security > Camera: allow desktop apps.")
        print("  - Is there a physical privacy shutter or a camera on/off key (F-key)?")
        print("  - Try changing CAMERA_INDEX at the top of this file to 1.")
        sys.exit(1)

    print("Webcam opened. Click the video window, then: 'a' = show all objects, "
          "'p' = print info, 'q' = quit.")

    # --- Create the window and the confidence slider -------------------------
    cv2.namedWindow(MAIN_WINDOW)
    cv2.createTrackbar("Confidence %", MAIN_WINDOW, DEFAULT_CONFIDENCE, 100, nothing)

    show_all = False          # toggled with the 'a' key
    fps = 0.0                 # frames per second, for display
    last_time = time.time()
    status = ""

    # --- Main loop: runs once per frame ---------------------------------------
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            print("ERROR: Lost the webcam feed (could not read a frame). Quitting.")
            break

        # Image size and center. frame.shape = (height, width, 3 color channels)
        height, width = frame.shape[:2]
        center_x, center_y = width // 2, height // 2

        # Slider is in percent (0-100); the AI wants 0.0-1.0. Never use 0,
        # or the AI would report thousands of junk detections.
        min_confidence = max(cv2.getTrackbarPos("Confidence %", MAIN_WINDOW), 1) / 100

        # Ask the AI what it sees, then pick the balloon
        detections = get_detections(model, frame, min_confidence)
        balloon = pick_balloon(detections)

        # Optional debug view: every object the AI found, in gray
        if show_all:
            for d in detections:
                if d is balloon:
                    continue
                x1, y1, x2, y2 = d["box"]
                cv2.rectangle(frame, (x1, y1), (x2, y2), (160, 160, 160), 1)
                draw_text(frame, f"{d['label']} {d['confidence']:.0%}", (x1 + 3, y1 + 18),
                          (200, 200, 200), scale=0.5)

        draw_center_crosshair(frame, center_x, center_y)

        if balloon is not None:
            x1, y1, x2, y2 = balloon["box"]
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            draw_text(frame, f"{balloon['label']} {balloon['confidence']:.0%}",
                      (x1, max(y1 - 8, 15)), (0, 255, 0), scale=0.6)

            # Centroid = middle of the box
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            cv2.circle(frame, (cx, cy), 6, (0, 0, 255), -1)  # filled red dot

            # Line from image center to the balloon, to visualize the error
            cv2.line(frame, (center_x, center_y), (cx, cy), (0, 255, 255), 1)

            # Pixel error = how far the balloon is from the image center.
            #   error_x > 0  -> balloon is to the RIGHT of center
            #   error_y > 0  -> balloon is BELOW center (image y grows downward!)
            error_x = cx - center_x
            error_y = cy - center_y

            status = f"Error x: {error_x:+4d} px   y: {error_y:+4d} px"
            draw_text(frame, status, (10, 30), (0, 255, 0))
        else:
            status = "No balloon detected"
            draw_text(frame, status, (10, 30), (0, 0, 255))

        # Frames per second, smoothed so the number doesn't jump around
        now = time.time()
        fps = 0.9 * fps + 0.1 * (1.0 / max(now - last_time, 1e-6))
        last_time = now
        draw_text(frame, f"{fps:4.1f} FPS   show all (a): {'ON' if show_all else 'off'}",
                  (10, height - 15), (255, 255, 255), scale=0.5)

        cv2.imshow(MAIN_WINDOW, frame)

        # --- Keyboard ---------------------------------------------------------
        # waitKey(1) waits 1 ms for a key AND lets OpenCV redraw the window.
        key = cv2.waitKey(1) & 0xFF

        if key == ord("a"):
            show_all = not show_all
        elif key == ord("p"):
            print(f"Confidence threshold = {min_confidence:.0%}   {status}")
            for d in detections:
                x1, y1, x2, y2 = d["box"]
                ratio = (x2 - x1) / max(y2 - y1, 1)
                print(f"   saw: {d['label']:<15} {d['confidence']:.0%}   box={d['box']}"
                      f"   aspect ratio={ratio:.2f}")
        elif key == ord("q"):
            print("Quitting.")
            break

        # Also quit if the user closes the window with the X button
        if cv2.getWindowProperty(MAIN_WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            print("Window closed. Quitting.")
            break

    # --- Clean up: always release the camera and close windows ---------------
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
