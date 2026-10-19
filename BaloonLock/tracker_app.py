"""
tracker_app.py  -  the main loop: the vision code drives the turret.
(Start it with: python turret_tracker.py ...)

Pipeline, every frame:
    camera thread (keeps only the NEWEST frame) -> find balloon
    -> Kalman filter (position + velocity) -> controller (feedforward + GAIN)
    -> pan/tilt angles -> Arduino ("pan,tilt\\n", max ~30 Hz)
    -> laser + LOCKED LED decisions (AUTO or MANUAL mode)

HOW THE REAL TURRET WORKS (camera mounted ON the turret):
    The camera rides on the pan-tilt bracket next to the laser. When the
    servos turn, the whole picture shifts, so the balloon slides toward the
    center of the image. The controller's job is to turn the servos until
    the balloon sits in the middle of the picture.

DRY-RUN MODE (--dry-run, no Arduino needed):
    Your laptop webcam can't turn, so we SIMULATE turning: the simulated
    pan/tilt angles are converted into a "virtual aim point" on the screen
    (the magenta crosshair). Instead of sending commands over serial, we
    print them. Hold 'f' to simulate the fire button.

"WORLD" COORDINATES (why the math below looks the way it does):
    On the real turret, a perfectly still balloon still MOVES in the picture
    whenever the turret turns. So we add back how far the turret has turned:
        world position = picture position + turret turn (in pixels)
    A still balloon then has a still world position, no matter how the
    turret moves. (In dry-run the camera never moves, so world = picture.)

THE CONTROLLER (each frame, the aim point moves by two parts):
    1. FEEDFORWARD = the balloon's velocity (from the Kalman filter).
       If the balloon moves 6 px per frame, the aim also moves 6 px per
       frame, so it keeps pace on its own instead of first falling behind.
    2. CORRECTION  = GAIN x (predicted point - aim point).
       GAIN only has to fix whatever error is LEFT OVER. GAIN is AUTOMATIC
       (control.auto_gain): high when the filter's recent predictions were
       accurate, low after big surprises or sharp turns, and never above the
       value that would make the aim oscillate for the measured delay.
       The step limit per command and the deadzone adapt too (target speed,
       balloon size). --manual-tuning brings back GAIN/LEAD sliders instead.
    predicted point = where the filter says the balloon will be LEAD seconds
    after this frame was captured.
    LEAD is AUTOMATIC: the measured time from frame capture to the command
    being sent (shown as "latency" on screen) + the servo's reaction time
    (--servo-delay, default 80 ms). Faster laptop -> smaller LEAD, slower
    laptop -> bigger LEAD, with no slider to touch.
    (With --no-predict there's no velocity: plain P control.)

MOTION PREDICTION (turn off with --no-predict):
    Two Kalman filters run side by side (control.MotionTracker):
      CV - "constant velocity": calm estimate for a balloon drifting steadily
      CA - "constant acceleration": follows a balloon that falls, bounces or
           is thrown (it also estimates acceleration)
    Each frame, the model that predicted the new detection better gets more
    weight (an "IMM" blend), so you never pick one by hand. The filters use
    the REAL time between frames (capture timestamps), trust small or blurry
    detections less, and clamp acceleration/speed to physically sensible
    limits. If a detection is missing, they coast ("predict-only") for up to
    LOST_TIMEOUT_S.

LASER LOCK ZONE (scales with the balloon's size, with hysteresis):
    "Miss distance" = how far the REAL detected balloon is from where the
    turret points right now (never the predicted point).
    A far-away balloon looks small, a close one looks big, so a fixed
    "within 25 px" would be far too loose for a tiny far balloon and too
    strict for a huge close one. Instead the lock zone follows the
    balloon's APPARENT RADIUS (its size in the picture, smoothed):
        lock_enter = LOCK FRACTION x radius, kept between --lock-min and --lock-max
        lock_exit  = lock_enter x 1.8
    With LOCK FRACTION 0.5 the aim has to be within the inner HALF of the
    balloon's radius.
    - LOCK:   miss < lock_enter for LOCK_FRAMES frames in a row
    - UNLOCK: miss > lock_exit, no detection this frame, a vision fault, or
              the balloon is TOO FAR (radius under --min-radius px)
    The deadzone automatically shrinks to at most half of lock_enter, so the
    turret always settles INSIDE the lock zone.
    The LOCKED LED is on whenever the turret is locked.

DISTANCE ESTIMATE (--calibrate-distance):
    A balloon twice as far away looks half as big, so
        distance x apparent radius = K   (a constant for one balloon)
    Calibrate once: hold the balloon at a known distance, press 'c', and K
    is saved to turret_config.json. After that: distance = K / radius.
    (K depends on the balloon's real size: re-calibrate for a different balloon.)

MODES ('m' switches; starts in MANUAL unless --mode auto):
    AUTO:   the laser fires whenever every safety condition below passes.
    MANUAL: the same, AND only while the fire button is HELD. Letting go
            turns it off immediately. (Dry-run: hold the 'f' key.)

SAFETY (takes priority over everything; the laser fires only if ALL hold):
    - LOCKED, and a REAL detection exists THIS frame (never prediction alone)
    - the balloon is not TOO FAR (apparent radius >= --min-radius)
    - not calibrating distance (you're standing in front of the turret)
    - no vision fault: a fresh camera frame (less than 0.3 s old), and the
      detector loaded and ran without errors. Otherwise the screen shows
      "VISION FAULT - LASER DISABLED".
    - this window has keyboard FOCUS - because the 'q' kill key only works
      while it does. Click another window and the laser goes off.
    - MANUAL mode: fire button held
    - LASER_ENABLED = True here AND in turret.ino (both False by default)
    - 'q' kill key: laser off FIRST, then LED off, recenter, quit
    - the Arduino turns laser AND LED off by itself if it hears nothing
      for 500 ms

KEYS AND THE "FROZEN WINDOW" FIX:
    A held key auto-repeats ~30 times a second. cv2.waitKey() hands back
    only ONE key per call, so with a ~14 FPS loop the keys piled up in
    Windows' message queue - and Windows only repaints a window when that
    queue is empty, so the picture froze while 'f' was held (and a 'q'
    would wait behind all the queued 'f's). Now EVERY waiting key is read
    each frame (read_all_keys in display.py), so the queue never backs up.

DISPLAY (display-only; detection/control always use the ORIGINAL frame):
    The window shows the video letterboxed (never stretched) with an info
    bar of status text below it. Drag the corners to resize, or use the
    zoom keys. Text stays the same size at any zoom.

Command-line examples:
    python turret_tracker.py --dry-run
    python turret_tracker.py --dry-run --mode auto
    python turret_tracker.py --dry-run --servo-delay 120     (slower servos)
    python turret_tracker.py --dry-run --calibrate-distance 150   (balloon held at 150 cm, press c)
    python turret_tracker.py --dry-run --lock-min 4 --lock-max 80 --min-radius 10
    python turret_tracker.py --dry-run --no-predict          (compare without prediction)
    python turret_tracker.py --dry-run --no-capture-thread   (compare without the camera thread)
    python turret_tracker.py --dry-run --color green
    python turret_tracker.py --replay recordings/20261011-201500.mp4   (replay a recording)
    python turret_tracker.py --port COM5 --invert-pan

Keys (click on the video window first):
    m = switch AUTO / MANUAL          f = fire button (dry-run, hold it)
    t = print timing report           + / - / 0 = zoom in / out / original size
    r = start/stop recording the raw camera feed to recordings/<time>.mp4
    c = save distance calibration (only with --calibrate-distance)
    q = KILL KEY: laser off first, then recenter to 90,90 and quit
"""

import os
import sys
import time

import cv2
import numpy as np
import serial

from camera import lock_exposure_and_white_balance, open_camera, print_camera_list, restore_auto
from cli import ask_calibration_distance, choose_port_if_needed, parse_args
from config import AUTO_TUNE_FILE, CONFIG_FILE, ConfigError, load_auto_tune, distance_k, load_config, save_config
from control import (GATE_FRAMES_OF_MOTION, GATE_MIN_PX, GATE_RADII, auto_deadzone, auto_gain, auto_step_limit, MAX_LEAD_MS, CAMERA_HFOV_DEG, CENTER_ANGLE, clamp, TUNING, set_tuning, GAIN_SCALE_LIMITS,
                     LOCK_EXIT_RATIO, LOCK_FRAMES, lock_zone_state, LOST_TIMEOUT_S,
                     measurement_sigma, MotionTracker, move_toward,
                     PAN_DIRECTION, RADIUS_SMOOTH_FRAMES, RadiusSmoother, RETURN_HOME_SPEED,
                     TILT_DIRECTION, VELOCITY_DEADZONE)
from display import (WINDOW_FLAGS, WindowZoom, bar_height_for, draw_badge,
                     draw_center_crosshair, nothing, read_all_keys, render_with_info_bar,
                     window_has_focus)
from safety import (FIRST_RUN, FIRST_RUN_MESSAGE, LoopWatchdog, VISION_FAULT_MAX_FRAME_AGE_S,
                    VISION_FAULT_MESSAGE, VISION_FAULT_NO_FRAME_S, WATCHDOG_MESSAGE,
                    WATCHDOG_SHOW_S, laser_should_fire, safety_banner)
from serial_link import (DISCONNECTED_MESSAGE, SEND_RATE_HZ, TurretLink, TurretOutputs,
                         ramp_to_center)
from ui import (CYAN, FireKey, GAIN_TRACKBAR, GRAY, GREEN, KEY_HELP, LEAD_TRACKBAR,
                LOCK_TRACKBAR, MAGENTA, ORANGE, RED, StageTimer, VELOCITY_ARROW_FRAMES, WHITE,
                WINDOW, YELLOW)
from vision import (EXPOSURE_WARMUP_FRAMES, FrameGrabber, RECORDINGS_DIR, make_ai_detector,
                    make_color_detector, make_hybrid_detector)


def main():
    args = parse_args()
    predicting = not args.no_predict

    pan_dir = -PAN_DIRECTION if args.invert_pan else PAN_DIRECTION
    tilt_dir = -TILT_DIRECTION if args.invert_tilt else TILT_DIRECTION

    if args.self_test:
        from selftest import run_self_test
        sys.exit(run_self_test(args.camera))

    port = choose_port_if_needed(args)
    if not args.dry_run and port is None:
        print("No port chosen. (Use --dry-run to test without an Arduino.)")
        return

    if args.list_cameras:
        print("Looking for cameras...")
        print_camera_list()
        return

    # --- Auto-tuned parameters (filter noise, gain curve, blur thresholds...) ---
    # Never safety settings: config.load_auto_tune only accepts whitelisted keys.
    if args.no_auto_tune:
        set_tuning({})
        print("Auto-tune: OFF (--no-auto-tune) - using built-in defaults.")
    else:
        try:
            values, warnings = load_auto_tune(args.tuning_file or AUTO_TUNE_FILE)
        except ConfigError as error:
            values, warnings = {}, [str(error)]
        set_tuning(values)
        for warning in warnings:
            print(f"Auto-tune: {warning}")
        if values:
            print(f"Auto-tune: loaded {len(values)} tuned values from "
                  f"{args.tuning_file or AUTO_TUNE_FILE}")

    ask_calibration_distance(args)          # only asks if --calibrate-distance had no number
    calibrating = args.calibrate_distance is not None
    try:
        config = load_config()
    except ConfigError as error:
        # Only the distance ESTIMATE depends on this file, so don't stop - warn.
        print(f"WARNING: {error}")
        print("  Distance will show as 'uncalibrated' until it's fixed.")
        config = {}
    detector_name = "color" if args.color else "ai"   # radius is measured differently by each

    detect = make_color_detector(args.color) if args.color else make_ai_detector()
    if not args.color and args.ai_every > 1:
        detect = make_hybrid_detector(detect, args.ai_every)
        print(f"AI runs every {args.ai_every} frames; a fast color tracker fills the frames in between.")
    if calibrating:
        print(f"DISTANCE CALIBRATION: hold the balloon {args.calibrate_distance:g} cm from the "
              "camera, wait for a steady box, then press 'c'. The laser stays OFF in this mode.")
    if args.scale_gain:
        print(f"--scale-gain ON: GAIN x (reference {args.reference_radius:g}px / apparent radius), "
              f"limited to {GAIN_SCALE_LIMITS[0]}x-{GAIN_SCALE_LIMITS[1]}x.")

    replay_fps = None
    if args.replay:
        cap = cv2.VideoCapture(args.replay)
        if not cap.isOpened():
            print(f"ERROR: Could not open the recording '{args.replay}'.")
            sys.exit(1)
        fps = cap.get(cv2.CAP_PROP_FPS)
        replay_fps = fps if 1 <= fps <= 240 else 30.0
        print(f"Replaying {args.replay} at {replay_fps:.1f} FPS "
              f"({int(cap.get(cv2.CAP_PROP_FRAME_COUNT))} frames).")
    else:
        cap = open_camera(args.camera)
        if cap is None:
            print(f"ERROR: Could not open webcam #{args.camera}. Close other apps using it, "
                  "or try --list-cameras.")
            sys.exit(1)
    # Lock exposure + white balance so colors (and HSV thresholds) don't drift.
    exposure_locked = not args.auto_exposure and not args.replay
    if exposure_locked:
        for line in lock_exposure_and_white_balance(cap, EXPOSURE_WARMUP_FRAMES):
            print(f"Camera: {line}")
    # Ask the driver to queue at most 1 frame (many webcam drivers ignore
    # this - the capture thread below keeps things fresh either way).
    accepted = cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    print(f"Camera buffer size 1: {'accepted' if accepted else 'not supported by this driver'}. "
          f"Capture thread: {'OFF' if args.no_capture_thread else 'ON'}.")
    grabber = FrameGrabber(cap, threaded=not args.no_capture_thread, replay_fps=replay_fps)

    try:
        link = TurretLink(port, args.dry_run)
    except (serial.SerialException, OSError) as error:
        print(f"ERROR: Could not open {port}: {error}")
        print("  Close the Arduino IDE's Serial Monitor and check the port number.")
        grabber.stop()
        if exposure_locked:
            restore_auto(cap)
        cap.release()
        sys.exit(1)

    outputs = TurretOutputs(link, args.dry_run)
    watchdog = LoopWatchdog(outputs.watchdog_off)
    watchdog_until = 0.0        # show the WATCHDOG message until this time
    fire_key = FireKey(WINDOW) if args.dry_run else None   # live mode uses the real button
    timer = StageTimer()

    def detector_label():
        """Which detector made this frame's detection (for the info bar)."""
        if args.color:
            info = getattr(detect, "last_info", "")
            return f"color preset '{args.color}'" + (f" ({info})" if info else "")
        if args.ai_every > 1:
            return f"{getattr(detect, 'last_source', 'AI')} (AI every {args.ai_every})"
        return "AI"

    # The latest info-bar text, so the zoom keys know how much room it needs.
    hud = {"groups": []}
    zoom = WindowZoom([WINDOW],
                      extra_height=lambda w, video_h: bar_height_for(hud["groups"], w, video_h))

    # WINDOW_NORMAL = resizable; FREERATIO = OpenCV doesn't fit/stretch the
    # picture itself - we hand it a canvas that's exactly the window's size.
    cv2.namedWindow(WINDOW, WINDOW_FLAGS)
    # Trackbars only hold whole numbers, so GAIN is stored x100 (1..100 -> 0.01..1.00)
    # GAIN and LEAD are automatic. Their sliders only appear with --manual-tuning,
    # and only then do they override the automatic values.
    if args.manual_tuning:
        cv2.createTrackbar(GAIN_TRACKBAR, WINDOW, int(clamp(args.gain, 0.01, 1.0) * 100), 100, nothing)
        cv2.createTrackbar(LEAD_TRACKBAR, WINDOW, int(clamp(args.lead, 0, MAX_LEAD_MS)),
                           MAX_LEAD_MS, nothing)
    # Lock fraction is stored x100 too (10..100 -> 0.10..1.00)
    cv2.createTrackbar(LOCK_TRACKBAR, WINDOW,
                       int(round(clamp(args.lock_fraction, 0.1, 1.0) * 100)), 100, nothing)

    # --- Turret state -----------------------------------------------------------
    # Angles are floats so tiny corrections (e.g. 0.3 degrees) can add up.
    # pan/tilt         = the COMMANDED angles (what we send to the Arduino)
    # servo_pan/tilt   = where the servos ACTUALLY are. In dry-run we simulate
    #                    them lagging behind the command; on real hardware we
    #                    can't measure it, so we just assume they arrived.
    # sent_pan/tilt   = the last angles actually SENT (after the slew limit).
    # The Arduino starts its servos at 90,90 when it boots, so we start there too.
    pan = float(CENTER_ANGLE)
    tilt = float(CENTER_ANGLE)
    sent_pan = float(CENTER_ANGLE)
    sent_tilt = float(CENTER_ANGLE)
    servo_pan = float(CENTER_ANGLE)
    servo_tilt = float(CENTER_ANGLE)

    predictor = MotionTracker(TUNING)    # CV + CA Kalman filters, blended (IMM)
    frame_dt = 1 / 30           # measured time between frames (seconds), smoothed
    prev_frame_time = None
    smooth_x = None             # --no-predict smoothing history (None = no history)
    smooth_y = None
    close_frames = 0            # frames in a row with miss < lock_enter
    locked = False
    radius_smoother = RadiusSmoother(RADIUS_SMOOTH_FRAMES)
    apparent_r = None           # smoothed apparent radius (px), None = unknown
    lock_fraction = clamp(args.lock_fraction, 0.1, 1.0)
    lock_enter = lock_exit = None   # current lock zone (px), None = unknown
    too_far = False
    gain_eff = clamp(args.gain, 0.01, 1.0)
    confidence = 0.0            # filter confidence 0..1 (drives the automatic gain)
    tuning_source = "auto"      # "auto" / "manual" / "fixed" / "first run" (for the HUD)
    step_limit = args.max_step  # degrees one command may move (adapts to target speed)
    deadzone = float(args.deadzone)
    distance_cm = None
    zone_state = "outside"      # "locked" / "inside" / "outside" (for the circle's color)
    mode = args.mode.upper()    # "MANUAL" or "AUTO"
    gain = clamp(args.gain, 0.01, 1.0)
    # LEAD (how far ahead to aim) is AUTOMATIC: the measured time from frame
    # capture to sending the command, plus the servo's reaction time.
    latency_s = 0.0             # measured capture -> command-sent time, smoothed
    servo_delay_s = args.servo_delay / 1000
    lead_s = servo_delay_s
    servo_history = []          # (time, pan, tilt) of sent commands, for the servo delay
    servo_target = (float(CENTER_ANGLE), float(CENTER_ANGLE))   # latest command the servos have "received"
    laser_allowed = False
    button_held = False
    vision_fault = None         # None = OK, otherwise the reason (text)
    last_fault_printed = None
    last_seen_time = time.time()   # when we last saw the balloon
    last_send_time = 0.0
    last_frame_id = 0
    last_frame = None           # last camera frame (shown again during a vision fault)
    proc_fps = 0.0              # frames we PROCESS per second
    cam_fps = 0.0               # frames the CAMERA delivers per second
    cam_count_time = time.time()
    cam_count_id = 0
    last_loop_end = time.time()

    mode_name = "DRY RUN" if args.dry_run else f"LIVE {port}"
    print("\n".join(safety_banner(args)))
    if args.first_run:
        print(f"*** {FIRST_RUN_MESSAGE}: laser disabled, slow and gentle. Check every servo "
              f"moves the right way before removing --first-run. ***")
    print(f"{mode_name}. Mode {mode}. Prediction {'ON' if predicting else 'OFF'}. "
          "Click the video window; 'q' = laser off + recenter + quit.")

    try:
        while True:
            # ===== 1. Get the newest camera frame ==================================
            watchdog.kick()                     # "main loop still alive"
            t_start = time.perf_counter()
            got = grabber.get_newest(last_frame_id, VISION_FAULT_NO_FRAME_S)
            watchdog.kick()
            timer.add("capture wait", time.perf_counter() - t_start)
            if grabber.dead:
                if args.replay:
                    print("Replay finished.")
                else:
                    print("ERROR: Lost the webcam feed (camera stopped sending frames). Quitting.")
                break
            link.poll()                         # read button changes from the Arduino
            if outputs.sync():                  # (re)connected: laser off, lock must be re-earned
                locked = False
                close_frames = 0
                # The Arduino just rebooted: its servos are at 90,90 again.
                sent_pan = sent_tilt = servo_pan = servo_tilt = float(CENTER_ANGLE)
                servo_history.clear()
                servo_target = (float(CENTER_ANGLE), float(CENTER_ANGLE))
            focused = window_has_focus(WINDOW)

            if got is None:
                # ===== VISION FAULT: no new frame arrived in time ====================
                t_draw = time.perf_counter()
                vision_fault = f"no new camera frame for {VISION_FAULT_NO_FRAME_S:.1f}s"
                locked = False
                close_frames = 0
                zone_state = "outside"
                outputs.set(laser=False, led=False)
                status, status_color = "NO CAMERA FRAME", ORANGE
                video = last_frame if last_frame is not None else np.zeros((480, 640, 3), np.uint8)

                def draw_overlays(canvas, box):
                    draw_badge(canvas, VISION_FAULT_MESSAGE, box.off_x + 8, box.off_y + 26, ORANGE)
            else:
                frame, frame_time, last_frame_id = got
                last_frame = frame
                # Real time between frames (from capture timestamps), smoothed
                if prev_frame_time is not None:
                    frame_dt = 0.8 * frame_dt + 0.2 * clamp(frame_time - prev_frame_time, 1e-3, 0.5)
                prev_frame_time = frame_time

                height, width = frame.shape[:2]
                center_x, center_y = width / 2, height / 2
                px_per_deg = width / CAMERA_HFOV_DEG     # pixels per degree of turning
                # --- GAIN and LEAD: automatic unless --manual-tuning ------------------
                confidence = predictor.confidence() if predictor.active else 0.0
                if args.manual_tuning:
                    gain = max(cv2.getTrackbarPos(GAIN_TRACKBAR, WINDOW), 1) / 100
                    lead_s = cv2.getTrackbarPos(LEAD_TRACKBAR, WINDOW) / 1000 if predicting else 0.0
                    tuning_source = "manual"
                elif not predicting or args.scale_gain:
                    gain = clamp(args.gain, 0.01, 1.0)    # comparison / experimental modes
                    lead_s = latency_s + servo_delay_s if predicting else 0.0
                    tuning_source = "fixed"
                else:
                    lead_s = latency_s + servo_delay_s
                    # delay in frames = total latency + 1 frame (we act once per frame)
                    # Remaining delay inside the loop: processing (+1 frame). The servo
                    # delay is handled by comparing with the commanded aim (below).
                    gain = auto_gain(confidence, TUNING, latency_s / frame_dt + 1)
                    tuning_source = "auto"
                if args.first_run:              # nothing can override first-run limits
                    gain = FIRST_RUN["gain"]
                    lead_s = 0.0
                    tuning_source = "first run"
                lock_fraction = max(cv2.getTrackbarPos(LOCK_TRACKBAR, WINDOW), 10) / 100

                # --- Where is the turret pointing? (world coordinates) ----------------
                turn_x = PAN_DIRECTION * (servo_pan - CENTER_ANGLE) * px_per_deg
                turn_y = TILT_DIRECTION * (servo_tilt - CENTER_ANGLE) * px_per_deg
                aim_x = center_x + turn_x
                aim_y = center_y + turn_y
                # World -> picture shift. Real turret: the camera turned with it.
                # Dry-run: the laptop camera never moves.
                if args.dry_run:
                    cam_dx, cam_dy = 0.0, 0.0
                else:
                    cam_dx, cam_dy = turn_x, turn_y

                # ===== 2. Detect (always on the ORIGINAL frame) ===========================
                t_detect = time.perf_counter()
                detector_error = None
                # What the motion filter expects (helps the detector find a
                # fast, blurry balloon and ignore far-away look-alikes).
                hint = None
                if predicting and predictor.active:
                    expected_wx, expected_wy = predictor.predict_at(frame_time)
                    estimate = predictor.estimate()
                    hint = {"predicted": (expected_wx - cam_dx, expected_wy - cam_dy),
                            "vx": estimate["vx"], "vy": estimate["vy"],
                            "speed": float(np.hypot(estimate["vx"], estimate["vy"])),
                            "radius": radius_smoother.value() or 30.0, "frame_dt": frame_dt}
                rejected_far = False
                try:
                    found = detect(frame, hint)
                except Exception as error:              # a broken detector must never crash
                    found = None                        # the program - it's a vision fault
                    detector_error = str(error) or error.__class__.__name__
                # GATE: a detection far from where the filter expects the balloon
                # is probably something else - ignore it (counts as a dropout, so
                # no laser) while the balloon has only been missing briefly. After
                # LOST_TIMEOUT_S the filter resets and anything is accepted again.
                if found is not None and hint is not None:
                    gate = max(GATE_MIN_PX, GATE_RADII * hint["radius"],
                               GATE_FRAMES_OF_MOTION * hint["speed"] * frame_dt)
                    if np.hypot(found[0] - hint["predicted"][0], found[1] - hint["predicted"][1]) > gate:
                        found = None
                        rejected_far = True
                t_control = time.perf_counter()
                timer.add("detect", t_control - t_detect)
                watchdog.kick()

                now = time.time()
                if found is not None:
                    raw_x, raw_y, box, raw_r = found                # picture coordinates
                    raw_wx, raw_wy = raw_x + cam_dx, raw_y + cam_dy  # world coordinates
                    last_seen_time = now
                missing_for = now - last_seen_time
                holding = found is None and missing_for <= LOST_TIMEOUT_S
                is_lost = found is None and not holding

                # --- Apparent size -> lock zone, too-far check, distance ----------------
                if found is not None:
                    radius_smoother.add(raw_r)
                elif is_lost:
                    radius_smoother.reset()             # (while HOLDING, keep the last size)
                apparent_r = radius_smoother.value()
                if apparent_r is not None:
                    lock_enter = clamp(lock_fraction * apparent_r, args.lock_min, args.lock_max)
                    lock_exit = lock_enter * LOCK_EXIT_RATIO
                    too_far = apparent_r < args.min_radius
                    k = distance_k(config, detector_name, width)
                    distance_cm = k / apparent_r if k else None
                else:
                    too_far = False
                    distance_cm = None
                # The deadzone must be smaller than the lock zone, or the turret could
                # stop correcting while still OUTSIDE it and never lock. Half of
                # lock_enter per axis keeps the leftover miss under ~0.7 x lock_enter.
                # It also shrinks when the balloon is small/far (control.auto_deadzone).
                deadzone = auto_deadzone(args.deadzone, lock_enter, apparent_r)
                # --scale-gain (experimental, see README): bigger GAIN for smaller balloons
                if args.scale_gain and apparent_r:
                    gain_eff = gain * clamp(args.reference_radius / apparent_r, *GAIN_SCALE_LIMITS)
                else:
                    gain_eff = gain

                # ===== 3. Control =======================================================
                # Decide what point to steer toward (world coordinates), and the
                # feedforward step (the balloon's own velocity).
                target = None               # None = don't steer this frame
                filtered = None             # filter estimate dict (x, y, vx, vy, ax, ay), for drawing
                ff_x = ff_y = 0.0
                if is_lost:
                    predictor.reset()
                    smooth_x = smooth_y = None
                elif predicting:
                    # Real capture time + how noisy this detection is (small or
                    # blurry blobs are trusted less).
                    sigma = measurement_sigma(raw_r, box, TUNING) if found is not None else 0
                    filtered = predictor.update(frame_time,
                                                (raw_wx, raw_wy) if found is not None else None,
                                                sigma)
                    if filtered is not None:
                        vx, vy = filtered["vx"], filtered["vy"]          # pixels per SECOND
                        if (vx ** 2 + vy ** 2) ** 0.5 > VELOCITY_DEADZONE:
                            # keep pace with the balloon: how far it moves in one frame
                            ff_x, ff_y = vx * frame_dt, vy * frame_dt
                        # Aim where the balloon WILL be when the servos get there:
                        # capture time + measured latency + servo delay (uses
                        # velocity AND acceleration). Never outside the picture.
                        pred_x, pred_y = predictor.predict_at(frame_time + lead_s)
                        pred_x = clamp(pred_x - cam_dx, 0, width - 1) + cam_dx
                        pred_y = clamp(pred_y - cam_dy, 0, height - 1) + cam_dy
                        target = (pred_x, pred_y)
                elif found is not None:
                    # --no-predict: aim straight at the (optionally smoothed) detection
                    if smooth_x is None or args.smoothing <= 0:
                        smooth_x, smooth_y = raw_wx, raw_wy
                    else:
                        s = clamp(args.smoothing, 0.0, 0.95)
                        smooth_x = s * smooth_x + (1 - s) * raw_wx
                        smooth_y = s * smooth_y + (1 - s) * raw_wy
                    target = (smooth_x, smooth_y)

                # Step = feedforward + GAIN x leftover error (deadzone per axis)
                if target is not None:
                    # Compare the FUTURE target with where the turret has been
                    # COMMANDED to point (it gets there after the delay that LEAD
                    # already accounts for) - not with where it points right now.
                    # Comparing with the current, lagging position would make it
                    # aim a whole delay's worth AHEAD of the balloon. (This is
                    # called a "Smith predictor".) The LOCK check below still uses
                    # where the turret ACTUALLY points.
                    commanded_x = center_x + PAN_DIRECTION * (pan - CENTER_ANGLE) * px_per_deg
                    commanded_y = center_y + TILT_DIRECTION * (tilt - CENTER_ANGLE) * px_per_deg
                    error_x = target[0] - commanded_x
                    error_y = target[1] - commanded_y
                    step_x = ff_x + (gain_eff * error_x if abs(error_x) > deadzone else 0.0)
                    step_y = ff_y + (gain_eff * error_y if abs(error_y) > deadzone else 0.0)
                    pan += pan_dir * step_x / px_per_deg       # pixels -> degrees
                    tilt += tilt_dir * step_y / px_per_deg
                if is_lost:
                    pan = move_toward(pan, CENTER_ANGLE, RETURN_HOME_SPEED)
                    tilt = move_toward(tilt, CENTER_ANGLE, RETURN_HOME_SPEED)
                pan = clamp(pan, args.min_angle, args.max_angle)
                tilt = clamp(tilt, args.min_angle, args.max_angle)

                # --- Lock with hysteresis: uses the REAL detection, never the prediction
                miss = None
                if found is not None:
                    miss = ((raw_wx - aim_x) ** 2 + (raw_wy - aim_y) ** 2) ** 0.5
                if found is None or too_far:
                    locked = False                      # no detection THIS frame, or too far
                    close_frames = 0
                elif locked:
                    if miss > lock_exit:
                        locked = False                  # drifted out of the (bigger) exit zone
                        close_frames = 0
                else:
                    close_frames = close_frames + 1 if miss < lock_enter else 0
                    if close_frames >= LOCK_FRAMES:
                        locked = True
                zone_state = lock_zone_state(locked, found is not None, too_far, miss, lock_enter)

                # --- Vision fault: is this frame too old to trust? ---------------------
                frame_age = time.time() - frame_time
                timer.add("frame age", frame_age)
                if detector_error is not None:
                    vision_fault = f"detector error: {detector_error}"
                    locked = False
                    close_frames = 0
                    zone_state = "outside"
                elif frame_age > VISION_FAULT_MAX_FRAME_AGE_S:
                    vision_fault = f"frame {frame_age * 1000:.0f} ms old " \
                                   f"(limit {VISION_FAULT_MAX_FRAME_AGE_S * 1000:.0f})"
                    locked = False
                    close_frames = 0
                    zone_state = "outside"
                else:
                    vision_fault = None


                # ===== 4. Laser + LED decision: ALL conditions must hold THIS frame ======
                # If the watchdog tripped (this frame was too slow), the laser is
                # already off; drop the lock so it must be earned again.
                if watchdog.consume_trip():
                    locked = False
                    close_frames = 0
                    zone_state = "outside"
                    watchdog_until = time.time() + WATCHDOG_SHOW_S
                link.poll()                             # freshest button state
                button_held = fire_key.is_held(focused) if args.dry_run else link.button_pressed
                link_ok = link.connected
                if not link_ok:                         # no Arduino: no lock either
                    locked = False
                    close_frames = 0
                conditions = dict(real_detection=found is not None, locked=locked,
                                  vision_fault=vision_fault, focused=focused,
                                  too_far=too_far, calibrating=calibrating,
                                  laser_disabled=args.first_run, link_ok=link_ok)
                # "allowed" = would fire in AUTO (used to show "READY - hold fire")
                laser_allowed = laser_should_fire("AUTO", button_held=False, **conditions)
                want_laser = laser_should_fire(mode, button_held=button_held, **conditions)
                outputs.set(laser=want_laser, led=locked)   # sends ONLY on a change
                watchdog_active = time.time() < watchdog_until

                # --- Send angles (at most SEND_RATE_HZ times per second) -----------------
                # SLEW LIMIT: each command may move at most --max-step degrees
                # from the previous one, however far the controller wants to go.
                # The limit itself adapts: a fast balloon gets bigger steps (more of
                # the --max-step cap), a slow one small smooth steps.
                speed = (filtered["vx"] ** 2 + filtered["vy"] ** 2) ** 0.5 if filtered else 0.0
                step_limit = auto_step_limit(speed, px_per_deg, max(frame_dt, 1.0 / SEND_RATE_HZ),
                                             args.max_step, TUNING)
                if now - last_send_time >= 1.0 / SEND_RATE_HZ:
                    sent_pan = move_toward(sent_pan, pan, step_limit)
                    sent_tilt = move_toward(sent_tilt, tilt, step_limit)
                    link.send_line(f"{round(sent_pan)},{round(sent_tilt)}")
                    last_send_time = now
                    # End-to-end latency: from this frame's capture to the command going out
                    latency_s = 0.8 * latency_s + 0.2 * max(0.0, time.time() - frame_time)
                    servo_history.append((time.time(), sent_pan, sent_tilt))
                # If the Arduino just disappeared (e.g. while sending above), the laser
                # state goes OFF right now - not next frame.
                if not link.connected and (outputs.laser_on or outputs.led_on):
                    outputs.set(laser=False, led=False)
                    locked = False
                    close_frames = 0
                # Don't let the controller "wind up" far ahead of what was actually
                # sent (it would overshoot when the slew limit lets go).
                pan = clamp(pan, sent_pan - step_limit, sent_pan + step_limit)
                tilt = clamp(tilt, sent_tilt - step_limit, sent_tilt + step_limit)

                # --- Servo position ------------------------------------------------------
                # Servos react to each command only after --servo-delay. The
                # newest command older than that is what they're doing now.
                while servo_history and time.time() - servo_history[0][0] >= servo_delay_s:
                    _t, target_pan, target_tilt = servo_history.pop(0)
                    servo_target = (target_pan, target_tilt)
                if args.dry_run:
                    # Simulated servos also turn at most --servo-speed degrees per frame
                    servo_pan = move_toward(servo_pan, servo_target[0], args.servo_speed)
                    servo_tilt = move_toward(servo_tilt, servo_target[1], args.servo_speed)
                else:
                    # Real servos: our best estimate of where they actually point
                    servo_pan, servo_tilt = servo_target

                t_draw = time.perf_counter()
                timer.add("control", t_draw - t_control)

                # --- Status text for the info bar ----------------------------------------
                if found is not None:
                    status = f"TRACKING  miss {miss:3.0f}px"
                    if filtered is not None:
                        status += f"  v=({filtered['vx']:+.0f},{filtered['vy']:+.0f})px/s"
                    status_color = GREEN
                elif holding:
                    status = f"HOLDING ({'predicting' if predicting else 'still'}, " \
                             f"{missing_for:.1f}s)" + (" - ignored a far blob" if rejected_far else "")
                    status_color = ORANGE
                else:
                    status, status_color = "LOST - returning to center", RED

                # --- Small overlays drawn on the SCALED video ------------------------------
                # (view.to_canvas turns original-frame positions into canvas positions,
                # so lines and text stay crisp and the same size at any zoom)
                def draw_overlays(canvas, view):
                    def pic(wx, wy):
                        """World coordinates -> canvas point."""
                        return view.to_canvas(wx - cam_dx, wy - cam_dy)

                    cx, cy = view.to_canvas(center_x, center_y)
                    draw_center_crosshair(canvas, cx, cy)
                    ax, ay = pic(aim_x, aim_y)
                    if args.dry_run:
                        # magenta = simulated turret aim
                        cv2.line(canvas, (ax - 25, ay), (ax + 25, ay), MAGENTA, 2)
                        cv2.line(canvas, (ax, ay - 25), (ax, ay + 25), MAGENTA, 2)
                    # LOCK ZONE around the aim point: the balloon's center must be
                    # inside this circle. It grows/shrinks with the balloon's size.
                    #   green = locked, yellow = inside but not 5 frames yet, red = outside
                    if lock_enter is not None:
                        zone_color = {"locked": GREEN, "inside": YELLOW, "outside": RED}[zone_state]
                        cv2.circle(canvas, (ax, ay), max(2, int(lock_enter * view.scale)),
                                   zone_color, 2, cv2.LINE_AA)
                        if locked:      # thin outer ring = where the lock would break
                            cv2.circle(canvas, (ax, ay), max(3, int(lock_exit * view.scale)),
                                       zone_color, 1, cv2.LINE_AA)
                    if found is not None:
                        x1, y1, x2, y2 = box
                        cv2.rectangle(canvas, view.to_canvas(x1, y1), view.to_canvas(x2, y2), GREEN, 2)
                        raw_pt = view.to_canvas(raw_x, raw_y)
                        cv2.line(canvas, pic(aim_x, aim_y), raw_pt, (160, 160, 160), 1)  # miss
                        cv2.circle(canvas, raw_pt, 6, RED, -1)                           # raw
                    if filtered is not None:
                        fx_, fy_ = filtered["x"], filtered["y"]
                        if (filtered["vx"] ** 2 + filtered["vy"] ** 2) ** 0.5 > VELOCITY_DEADZONE:
                            # arrow tip = where the filter says it'll be in 5 frames
                            # (curves when the balloon is accelerating)
                            ahead = predictor.predict_at(frame_time + VELOCITY_ARROW_FRAMES * frame_dt)
                            cv2.arrowedLine(canvas, pic(fx_, fy_), pic(*ahead),
                                            CYAN, 2, tipLength=0.3)                      # velocity
                        if target is not None:
                            cv2.circle(canvas, pic(*target), 7, YELLOW, 2)               # predicted
                    if not link.connected:
                        draw_badge(canvas, DISCONNECTED_MESSAGE, view.off_x + 8,
                                   view.off_y + 56, ORANGE)
                    if args.first_run:
                        draw_badge(canvas, FIRST_RUN_MESSAGE, view.off_x + 8,
                                   int(view.off_y + view.scale * height) - 12, CYAN)
                    if outputs.laser_on:
                        draw_badge(canvas, "LASER ON", view.off_x + 8, view.off_y + 26, RED)
                    elif watchdog_active:
                        draw_badge(canvas, WATCHDOG_MESSAGE, view.off_x + 8, view.off_y + 26, ORANGE)
                    elif vision_fault:
                        draw_badge(canvas, VISION_FAULT_MESSAGE, view.off_x + 8, view.off_y + 26, ORANGE)

                video = frame

            watchdog_active = time.time() < watchdog_until
            # ===== 5. Build the display (display-only) ====================================
            # Info bar lines, grouped. Always the same number of lines, so the
            # layout doesn't jump around.
            if outputs.laser_on:
                laser_line = ("LASER: ON", RED)
            elif args.first_run:
                laser_line = (f"LASER: DISABLED ({FIRST_RUN_MESSAGE})", CYAN)
            elif not link.connected:
                laser_line = (f"LASER: OFF ({DISCONNECTED_MESSAGE})", ORANGE)
            elif watchdog_active:
                laser_line = (WATCHDOG_MESSAGE, ORANGE)
            elif vision_fault:
                laser_line = (VISION_FAULT_MESSAGE, ORANGE)
            elif calibrating:
                laser_line = ("LASER: OFF (calibrating distance)", ORANGE)
            elif too_far:
                laser_line = ("LASER: OFF (too far)", ORANGE)
            elif not focused:
                laser_line = ("LASER: OFF (window not focused)", ORANGE)
            elif mode == "MANUAL" and laser_allowed:
                laser_line = ("LASER: READY - hold fire", YELLOW)
            else:
                laser_line = ("LASER: OFF", GRAY)
            if locked:
                lock_line = (f"LOCKED (breaks if miss > {lock_exit:.0f}px)", GREEN)
            elif too_far:
                lock_line = (f"TOO FAR to lock (radius {apparent_r:.1f} < {args.min_radius:g}px)",
                             ORANGE)
            elif lock_enter is None:
                lock_line = ("lock: waiting for a balloon size", GRAY)
            else:
                lock_line = (f"lock {min(close_frames, LOCK_FRAMES)}/{LOCK_FRAMES} "
                             f"(miss < {lock_enter:.0f}px)",
                             YELLOW if zone_state == "inside" else MAGENTA)
            # Target size / lock zone / distance
            radius_text = f"{apparent_r:.1f}px" if apparent_r is not None else "--"
            zone_text = (f"{lock_enter:.1f}px (exit {lock_exit:.1f})" if lock_enter is not None
                         else "--")
            if distance_k(config, detector_name, 1) is None:
                distance_text = "uncalibrated (--calibrate-distance)"
            elif distance_cm is None:
                distance_text = "--"
            else:
                distance_text = f"~{distance_cm:.0f} cm"
            if args.scale_gain:
                gain_text = f"GAIN {gain:.2f} -> {gain_eff:.2f} (--scale-gain)"
            elif tuning_source == "auto":
                gain_text = f"GAIN {gain:.2f} (auto, confidence {confidence:.2f})"
            else:
                gain_text = f"GAIN {gain:.2f} ({tuning_source})"
            fire_source = "f key" if args.dry_run else "button"
            if args.dry_run:
                link_line = (f"sim servo pan {servo_pan:5.1f} tilt {servo_tilt:5.1f}", MAGENTA)
            else:
                if link.connected:
                    link_line = (f"serial {port}: OK", WHITE)
                elif link.ser is not None:
                    link_line = (f"serial {port}: waiting for READY", ORANGE)
                else:
                    link_line = (f"{DISCONNECTED_MESSAGE} - retrying every "
                                 f"{TurretLink.RECONNECT_EVERY_S:.0f} s", ORANGE)
            if args.replay:
                record_line = (f"REPLAY {os.path.basename(args.replay)}", CYAN)
            elif grabber.recording:
                record_line = (f"REC {grabber.recorded_frames} frames (r to stop)", RED)
            else:
                record_line = ("recording: off (r to start)", GRAY)
            calibrate_lines = []
            if calibrating:
                calibrate_lines = [(f"CALIBRATING: hold balloon at {args.calibrate_distance:g} cm, "
                                    f"press c", CYAN)]
            hud["groups"] = [
                ("STATUS", calibrate_lines + [
                    (f"MODE: {mode}  ({mode_name})"
                     + (f"  {FIRST_RUN_MESSAGE}" if args.first_run else ""), WHITE),
                    (status, status_color),
                    lock_line,
                    laser_line,
                    (f"FIRE ({fire_source}): {'HELD' if button_held else '--'}   "
                     f"LED: {'ON' if outputs.led_on else 'off'}", YELLOW if button_held else WHITE),
                    (f"VISION: {vision_fault or 'OK'}", ORANGE if vision_fault else GREEN),
                    (f"FOCUS: {'yes' if focused else 'NO'}", WHITE if focused else ORANGE),
                ]),
                ("TARGET", [
                    (f"detector: {detector_label()}", WHITE),
                    (f"apparent radius {radius_text}  (min {args.min_radius:g})", WHITE),
                    (f"lock zone {zone_text}  fraction {lock_fraction:.2f}", WHITE),
                    (f"distance {distance_text}", WHITE),
                ]),
                ("TUNING", [
                    (gain_text, WHITE),
                    (f"LEAD {lead_s * 1000:.0f} ms ({tuning_source})" if predicting
                     else "PREDICT OFF", WHITE),
                    (f"latency {latency_s * 1000:.0f} ms + servo {servo_delay_s * 1000:.0f} ms", WHITE),
                    (f"FPS {proc_fps:4.1f}  (camera {cam_fps:4.1f})", WHITE),
                    (f"step limit {step_limit:.1f} deg (cap {args.max_step:g})  deadzone "
                     f"{deadzone:.0f}px", WHITE),
                    (f"sent pan {sent_pan:5.1f} tilt {sent_tilt:5.1f}", WHITE),
                    link_line,
                    record_line,
                ]),
                ("KEYS", KEY_HELP + ([("c  save distance calibration", CYAN)]
                                     if calibrating else [])),
            ]
            if last_frame is not None:
                zoom.set_base(last_frame.shape[1], last_frame.shape[0])   # first frame: zoom 1.0
            canvas = render_with_info_bar(WINDOW, video, hud["groups"], draw_overlays)

            # ===== 6. Show + keys (runs for normal frames AND vision faults) ===============
            if vision_fault != last_fault_printed:
                print(f"VISION FAULT: {vision_fault} - laser off" if vision_fault else "Vision OK")
                last_fault_printed = vision_fault

            cv2.imshow(WINDOW, canvas)
            keys = read_all_keys()                  # EVERY waiting key, not just one
            if fire_key is not None:
                fire_key.saw_keys(keys)

            if ord("q") in keys:                    # kill key beats everything else
                outputs.set(laser=False, led=outputs.led_on)
                print("'q' pressed: laser off, quitting.")
                break
            for key in dict.fromkeys(keys):         # each different key once per frame
                if key == ord("m"):
                    mode = "AUTO" if mode == "MANUAL" else "MANUAL"
                    print(f"Mode: {mode}")
                elif key == ord("t"):
                    print(timer.report(proc_fps, cam_fps))
                elif key == ord("r"):
                    if args.replay:
                        print("Recording is off while replaying.")
                    elif grabber.recording:
                        path, count = grabber.stop_recording()
                        print(f"Recording saved: {path} ({count} frames)")
                    else:
                        # Record at the camera's REAL frame rate, so replays play at true speed
                        fps = cam_fps if cam_fps >= 1 else (cap.get(cv2.CAP_PROP_FPS) or 30.0)
                        path = os.path.join(RECORDINGS_DIR, time.strftime("%Y%m%d-%H%M%S") + ".mp4")
                        grabber.start_recording(path, fps)
                        print(f"Recording raw camera feed to {path} ({fps:.1f} FPS). Press r to stop.")
                elif key == ord("c") and calibrating:
                    # K = distance x apparent radius. Needs a steady size (5 frames).
                    if last_frame is None or not radius_smoother.is_steady() or apparent_r is None:
                        print("Calibration: no steady balloon yet - hold it still in view "
                              "until the box is steady, then press c again.")
                    else:
                        k = args.calibrate_distance * apparent_r
                        config.setdefault("distance_calibration", {})[detector_name] = {
                            "k": round(k, 2),
                            "distance_cm": args.calibrate_distance,
                            "radius_px": round(apparent_r, 2),
                            "frame_width": last_frame.shape[1],
                        }
                        save_config(config)
                        print(f"Calibration saved to {CONFIG_FILE}: K = "
                              f"{args.calibrate_distance:g} cm x {apparent_r:.1f} px = {k:.0f} "
                              f"(detector: {detector_name})")
                else:
                    zoom.handle_key(key)            # + = - 0 (display only)

            # Turn the laser off RIGHT NOW (not next frame) if the window lost
            # focus, or in MANUAL mode the fire button was released.
            if outputs.laser_on:
                focused = window_has_focus(WINDOW)
                link.poll()
                held = fire_key.is_held(focused) if args.dry_run else link.button_pressed
                if not focused or (mode == "MANUAL" and not held):
                    outputs.set(laser=False, led=outputs.led_on)

            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                print("Window closed.")
                break
            watchdog.kick()

            # --- Timing + FPS -------------------------------------------------------------
            t_end = time.perf_counter()
            timer.add("draw+show", t_end - t_draw)
            timer.add("whole loop", t_end - t_start)
            now = time.time()
            proc_fps = 0.9 * proc_fps + 0.1 / max(now - last_loop_end, 1e-6)
            last_loop_end = now
            if now - cam_count_time >= 1.0:          # camera FPS, measured once per second
                cam_fps = (grabber.frame_id - cam_count_id) / (now - cam_count_time)
                cam_count_id = grabber.frame_id
                cam_count_time = now

    except KeyboardInterrupt:
        print("Ctrl+C pressed.")

    finally:
        # This block ALWAYS runs - after 'q', closing the window, Ctrl+C, or
        # even an unexpected error - so the turret is always left in a safe state.
        outputs.all_off()               # 1) laser OFF first, then the LED
        watchdog.stop()                 # (recentering below is slow on purpose)
        print(f"Recentering to {CENTER_ANGLE},{CENTER_ANGLE} and shutting down.")
        ramp_to_center(link, sent_pan, sent_tilt)    # 2) then glide back to center
        time.sleep(0.1)                 # give the last commands time to go out
        if fire_key is not None:
            fire_key.stop()
        if grabber.recording:
            path, count = grabber.stop_recording()
            print(f"Recording saved: {path} ({count} frames)")
        grabber.stop()
        link.close()
        if exposure_locked:
            restore_auto(cap)           # don't leave the webcam stuck for video calls
        cap.release()
        cv2.destroyAllWindows()
