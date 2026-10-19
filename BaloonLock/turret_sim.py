"""
turret_sim.py  -  Phase 2: a fake turret on the screen.

Before we have servos, we pretend the turret is a crosshair on the screen
(the "aim point"). Each frame, the aim point moves toward the balloon using
PROPORTIONAL CONTROL - the same math the real servos will use later.

Proportional control in one sentence:
    "Move by a fraction (GAIN) of how far away you still are."

    error      = balloon position - aim point        (in pixels)
    aim point += error * GAIN                        (every frame)

  - Far from the balloon  -> big error   -> big step.
  - Close to the balloon  -> small error -> small step.
  So the aim point moves fast at first and gently slows as it arrives.

Extra rules:
  - DEADZONE: if the error is tiny (under DEADZONE_PX), don't move at all.
    The AI's box jitters by a few pixels every frame even if the balloon is
    still; without a deadzone the aim point (and later the servos) would
    twitch constantly.
  - LOCK: if the aim point stays within LOCK_THRESHOLD_PX of the balloon for
    LOCK_FRAMES frames in a row, we are "LOCKED". Losing the balloon (or
    drifting away from it) unlocks immediately. In Phase 5 the laser will
    only be allowed on while LOCKED.
  - CLAMP: the aim point can never leave the picture.

Keys (click on the video window first so it has keyboard focus):
  r = reset the aim point to the center of the picture
  q = quit
"""

import sys
import time

import cv2

# Reuse the detection code from Phase 1 instead of copying it here.
from balloon_detect_ai import (
    CAMERA_INDEX,
    DEFAULT_CONFIDENCE,
    draw_center_crosshair,
    draw_text,
    get_detections,
    load_model,
    nothing,
    open_camera,
    pick_balloon,
)


# ---------------------------------------------------------------------------
# Settings you might want to change
# ---------------------------------------------------------------------------
DEFAULT_GAIN_PERCENT = 10   # starting GAIN on the slider: 10 -> 0.10
DEADZONE_PX = 10            # don't move an axis if its error is smaller than this
LOCK_THRESHOLD_PX = 25      # "close enough to count toward a lock" (must be > DEADZONE_PX)
LOCK_FRAMES = 5             # frames in a row we must be close to become LOCKED

WINDOW = "Turret Simulator"


def clamp(value, low, high):
    """Keep value between low and high."""
    return max(low, min(value, high))


def draw_aim_crosshair(frame, x, y, locked):
    """Draw the turret's aim point: a big crosshair with a gap in the middle.
    Magenta while searching/tracking, red when LOCKED."""
    color = (0, 0, 255) if locked else (255, 0, 255)  # (Blue, Green, Red)
    gap, size = 8, 30
    cv2.line(frame, (x - size, y), (x - gap, y), color, 2)
    cv2.line(frame, (x + gap, y), (x + size, y), color, 2)
    cv2.line(frame, (x, y - size), (x, y - gap), color, 2)
    cv2.line(frame, (x, y + gap), (x, y + size), color, 2)
    cv2.circle(frame, (x, y), 18, color, 2)


def main():
    print("Loading AI model...")
    model = load_model()

    cap = open_camera(CAMERA_INDEX)
    if cap is None:
        print(f"ERROR: Could not open webcam #{CAMERA_INDEX}. "
              "Close other apps using the camera and try again.")
        sys.exit(1)

    print("Webcam opened. Click the video window, then: 'r' = reset aim, 'q' = quit.")

    # --- Window and sliders ---------------------------------------------------
    cv2.namedWindow(WINDOW)
    # Trackbars only hold whole numbers, so GAIN is stored as a percent:
    # slider 1..100  ->  GAIN 0.01..1.00
    cv2.createTrackbar("Gain x100", WINDOW, DEFAULT_GAIN_PERCENT, 100, nothing)
    cv2.createTrackbar("Confidence %", WINDOW, DEFAULT_CONFIDENCE, 100, nothing)

    # --- Turret state ---------------------------------------------------------
    # The aim point is stored as DECIMAL numbers (floats), not whole pixels.
    # Why: with a small gain, one frame's step might be 0.4 pixels. If we
    # rounded each step to a whole pixel it would become 0 and the aim
    # point would never arrive. Floats let tiny steps add up.
    aim_x = None              # set to the frame center on the first frame
    aim_y = None
    close_frames = 0          # how many frames in a row we've been close
    fps = 0.0
    last_time = time.time()

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            print("ERROR: Lost the webcam feed (could not read a frame). Quitting.")
            break

        height, width = frame.shape[:2]
        center_x, center_y = width // 2, height // 2
        if aim_x is None:
            aim_x, aim_y = float(center_x), float(center_y)

        # Read sliders (never allow 0 - a gain of 0 means "never move")
        gain = max(cv2.getTrackbarPos("Gain x100", WINDOW), 1) / 100
        min_confidence = max(cv2.getTrackbarPos("Confidence %", WINDOW), 1) / 100

        # --- Detect the balloon -------------------------------------------------
        balloon = pick_balloon(get_detections(model, frame, min_confidence))

        if balloon is not None:
            x1, y1, x2, y2 = balloon["box"]
            target_x = (x1 + x2) / 2      # balloon centroid (middle of box)
            target_y = (y1 + y2) / 2

            # --- Proportional control ---------------------------------------------
            error_x = target_x - aim_x
            error_y = target_y - aim_y

            # Deadzone, checked separately for x and y. Later, x will drive
            # the PAN servo and y the TILT servo, and each servo should only
            # move if ITS error is big enough.
            if abs(error_x) > DEADZONE_PX:
                aim_x += error_x * gain
            if abs(error_y) > DEADZONE_PX:
                aim_y += error_y * gain

            # Clamp: never leave the picture
            aim_x = clamp(aim_x, 0, width - 1)
            aim_y = clamp(aim_y, 0, height - 1)

            # --- Lock logic ---------------------------------------------------------
            # Distance from aim point to balloon (Pythagoras), after this move
            distance = ((target_x - aim_x) ** 2 + (target_y - aim_y) ** 2) ** 0.5
            if distance <= LOCK_THRESHOLD_PX:
                close_frames += 1
            else:
                close_frames = 0      # drifted away -> start counting over

            # Draw the balloon and a line from the aim point to it
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.circle(frame, (int(target_x), int(target_y)), 5, (0, 255, 0), -1)
            cv2.line(frame, (int(aim_x), int(aim_y)), (int(target_x), int(target_y)),
                     (0, 255, 255), 1)
            status = f"Error x: {error_x:+5.0f}  y: {error_y:+5.0f}   dist: {distance:4.0f} px"
        else:
            # Balloon lost: unlock IMMEDIATELY and hold the aim point still
            close_frames = 0
            status = "No balloon detected"

        locked = close_frames >= LOCK_FRAMES

        # --- Draw everything ----------------------------------------------------
        draw_center_crosshair(frame, center_x, center_y)       # small white = picture center
        draw_aim_crosshair(frame, int(aim_x), int(aim_y), locked)

        draw_text(frame, status, (10, 30),
                  (0, 255, 0) if balloon is not None else (0, 0, 255), scale=0.6)
        if locked:
            draw_text(frame, "LOCKED", (10, 70), (0, 0, 255), scale=1.2)
        else:
            draw_text(frame, f"tracking  lock {min(close_frames, LOCK_FRAMES)}/{LOCK_FRAMES}",
                      (10, 65), (255, 0, 255), scale=0.6)

        now = time.time()
        fps = 0.9 * fps + 0.1 * (1.0 / max(now - last_time, 1e-6))
        last_time = now
        draw_text(frame, f"GAIN {gain:.2f}   deadzone {DEADZONE_PX}px   {fps:4.1f} FPS",
                  (10, height - 15), (255, 255, 255), scale=0.5)

        cv2.imshow(WINDOW, frame)

        # --- Keyboard -------------------------------------------------------------
        key = cv2.waitKey(1) & 0xFF
        if key == ord("r"):
            aim_x, aim_y = float(center_x), float(center_y)
            close_frames = 0
        elif key == ord("q"):
            print("Quitting.")
            break

        if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            print("Window closed. Quitting.")
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
