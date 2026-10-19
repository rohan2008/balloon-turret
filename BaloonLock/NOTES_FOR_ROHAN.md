# Notes for Rohan: overnight improvements

Everything below happened on the git branch **`overnight-improvements`**. `main` is untouched. When you're back:

```
git log --oneline main..overnight-improvements     # what was added, one commit per task
python -m unittest -v                               # run all the tests (no hardware, no camera)
python turret_tracker.py --dry-run                 # try it with your webcam
```

Each section lists what I did, what I **assumed** (since you couldn't answer questions), and what I verified.

## Latest: auto-tune branch (tracker adapts by itself, no GAIN/LEAD to touch)

Work is on the branch **`auto-tune`** (made from `overnight-improvements`), one commit per task, **not merged**. Sections for each task are at the end of this file under "Auto-tune".

**Assumption about the person veto:** your auto-tune task list says the "person veto" safety rule stays. You had me remove that feature completely in the step just before, so I read "safety rules stay exactly as they are" as *as they are now*, and did **not** re-add it. Every other rule is untouched. Tell me if you want it back.

---

## Earlier: person veto removed (at your request)

The person-in-view feature is **gone completely**:
- no person detection,
- no "PERSON - LASER BLOCKED" message,
- no 1 s hold,
- no `--no-person-veto` flag, which now gives an "unrecognized argument" error, so just leave it out,
- no "refuse to start" override check.

The AI detector now only looks for balloons. **Color mode can now fire too**, since it used to be blocked only because it couldn't check for people. 6 tests that checked the veto were removed, and **108 tests pass**.

Older sections below still mention the veto; they're kept as history.

**Please read:** pointer lasers under 5 mW can still hurt eyes with a direct hit. The veto was the only thing stopping the turret from aiming at a person the AI mistook for a balloon. Nothing in the software checks for people now, so keep everyone out of the turret's view.

Every other safety is unchanged:
- `LASER_ENABLED` is still False in both places,
- lock-before-fire, real-detection-only, the watchdog, focus, too-far, vision-fault, first-run and disconnect rules, and the kill key all remain.

---

## Earlier: final software pass (FP1–FP7)

**Done.** 7 more commits on `overnight-improvements` (**not merged**). **114 tests pass**, the real `--self-test` passes on your laptop, and all 7 deliberate-break checks are caught. Nothing was reverted. `LASER_ENABLED` is still False in both places. Details are in the "Final software pass" sections at the bottom.

**Two things to know before you build anything:**
1. **Bugs found and fixed:**
   - after an Arduino reconnect, the old code could re-send "laser on" without asking `laser_should_fire()`,
   - `--first-run` would have crashed on its first frame.
2. **Hardware:** add a **10 kΩ resistor from pin 7 to GND** when wiring the laser. The pin floats while the Uno's bootloader runs after every reset.

**Try each new thing:**
```
python turret_tracker.py --self-test                               # PASS/FAIL health check
python turret_tracker.py --dry-run                                  # read the SAFETY STATUS banner
python turret_tracker.py --dry-run --first-run     # FIRST RUN MODE: slow, laser disabled
python turret_tracker.py --dry-run --max-step 3    # watch the slew limit; press q to see the glide home
python bringup.py --port COM5                                       # hardware bring-up checklist
python -m unittest -v                                               # all 114 tests
```
Also read **docs/SAFETY.md** (every rule, where it's enforced, which test proves it) and **docs/WOKWI.md** (test the Arduino sketch in a free online simulator, about 10 minutes).

**Review, merge and tag (when you're happy):**
```
git log --oneline main..overnight-improvements          # every commit on the branch
git diff --stat main...overnight-improvements            # files changed
git show 286e375                                         # look at any one commit
git switch main
git merge --no-ff overnight-improvements -m "Merge overnight-improvements: software complete"
git tag -a v0.1-software-complete -m "Software complete, tested in simulation"
python -m unittest                                       # re-check on main
```
(This repo has no remote yet. To publish it later: `git push -u origin main --tags`.)

---

## Earlier: overnight run summary

**All 6 tasks are done** and committed on `overnight-improvements`, one commit each, plus a baseline commit and a safety commit:

```
9d9bbc3 Baseline: lag fixes, AUTO/MANUAL modes, letterboxed display, lock zone (WIP)
a4864cd Safety: person veto on by default with 1 s hold, vision fault on AI errors, unit tests
cff792b Task 1: run the AI every Nth frame with a learned-color tracker in between
a3007a3 Task 2: verify modes, fire key and LED/button protocol with tests
f5afabf Task 3: verify letterboxed display, zoom and HUD with pixel-level tests
792b88e Task 4: dynamic lock zone scaled by apparent radius, distance calibration
abb5332 Task 5: lock exposure/white balance, --camera and --list-cameras
11029f4 Task 6: record the raw camera feed with 'r' and replay it with --replay
```

- **Tests:** **76 tests, all passing** (`python -m unittest -v`, about 20 s). No task had to be reverted.
- **Laser:** `LASER_ENABLED` is still `False` in both `turret_tracker.py` and `turret.ino`, and a test checks both.
- **Hardware:** nothing was run against a serial port or your webcam.

**Please check these. They're decisions I made without you:**
1. **The person veto is ON by default again**, because your safety rules require it. You'll see "PERSON - LASER BLOCKED" while you hold the balloon. Use `--no-person-veto` for dry-run testing.
2. **The feedforward formula** was implemented as "move at the balloon's speed + GAIN × leftover error". Taken literally, "velocity × LEAD" would run at LEAD times the balloon's speed. See Task 1.
3. **`--scale-gain` is built but not recommended.** Its premise (a far balloon's pixel = a bigger angle) isn't true. See Task 4.
4. **Exposure/white-balance lock is untested on your real webcam.** Check the "Camera: … driver accepted" lines the first time you run it.
5. **`--ai-every` defaults to 1** (AI every frame). Try `--ai-every 3` for more FPS.
6. **Your task list ended at "--replay"** and looks cut off. I implemented replay of a recording.

**Things to try (no hardware needed):**
```
python turret_tracker.py --dry-run            # tracking + lock zone + modes
python turret_tracker.py --dry-run --ai-every 3
python turret_tracker.py --dry-run --calibrate-distance 150    # then press c
python turret_tracker.py --list-cameras
# press r to record, r again to stop, then:
python turret_tracker.py --replay recordings/<file>.mp4
```

## How the work was organized

- **Baseline commit.** Tasks 1–3 were mostly already built earlier today (lag fixes, modes, display) and were sitting *uncommitted*. Task 4 (dynamic lock zone) was half-written when your list arrived. All of that is interleaved inside the same files, especially `turret_tracker.py`, so it couldn't be cleanly split into one commit per task. I committed it as one **baseline** commit first, then made one commit per task for everything added afterwards.
- **Tests.** There were no tests in the repo, only throwaway scripts in my scratch folder. I added a real test suite in `tests/` using Python's built-in `unittest`. `pytest` isn't installed, and I didn't want to install packages you didn't ask for. The tests use a fake camera, fake windows and a fake Arduino, so they never touch your webcam or a serial port.

## Safety rules + test suite (commit "Safety: ...")

**What changed**
- **Person veto is ON by default again.** Your overnight list makes it a top-priority rule ("laser forced off and held off 1 s after they leave"), which overrides your earlier request to switch it off.
  - It now **holds for 1 s** after the person leaves, and the screen shows **"PERSON - LASER BLOCKED"**.
  - **Assumption:** because the person veto blocked your testing before, I kept an opt-out, `--no-person-veto`, for dry-run tests where *you* hold the balloon. Without it, you'll see "PERSON - LASER BLOCKED" whenever your hand or face is in view. That's correct behavior.
  - **Assumption:** "Person" includes every person-like label the AI knows (Person, Man, Woman, Boy, Girl and all "Human …" labels, such as a hand or face). That's stricter, so safer.
  - **Assumption:** `--color` mode has no AI to look for people, so with the veto on, the laser **never** fires in color mode (fail safe). Use `--no-person-veto` to test color mode.
- **Vision fault now covers the AI itself.**
  - **Model fails to load:** the program keeps running instead of quitting, with the laser disabled and **"VISION FAULT - LASER DISABLED"** on screen.
  - **Detector error on a frame:** that frame is a vision fault (laser off), and it recovers automatically once detection works again.
  - The older vision faults still apply: no new frame for 0.5 s, or a frame older than 0.3 s.
- **One place decides the laser.** The whole laser decision is now a single small function, `laser_should_fire()`, so it's easy to read and test.

**Tests: the new `tests/` folder** (run them with `python -m unittest -v`)
- `tests/fakes.py` runs the real tracker with a fake camera, fake windows, a fake Arduino, a fake fire key and a fake clock (exactly 1/30 s per frame).
- `tests/test_safety.py` covers every safety rule on your list:
  - laser only with a real detection this frame, and off on the same frame the balloon is lost (while the turret coasts on prediction),
  - person veto plus the 1 s hold,
  - vision fault on model-load failure and on detector errors,
  - kill key order: laser off, then LED off, then recenter,
  - too-small target,
  - focus loss,
  - MANUAL mode needs the button,
  - no `L` commands at all while `LASER_ENABLED = False`.
- **I checked the tests catch real bugs.** I broke the code on purpose (fire on prediction, ignore focus, ignore the veto, manual mode ignores the button, 0 s hold) and confirmed each break makes at least one test fail. The first time I did this, two breaks went *unnoticed*, which is why `laser_should_fire()` now exists and is tested condition by condition.
- **Arduino side (limitation):** I can't run Arduino code on the laptop. Those tests read `turret.ino` and check the safety code is there: `LASER_ENABLED = false`, `setLaser` forcing the laser off, `L1` refused, and the 500 ms timeout turning off the laser **and** the LED. The sketch also compiles with the Arduino IDE's compiler.
- `tests/test_smoke.py` is the headless dry-run smoke test. It runs the full color pipeline and the camera thread, and loads the real AI model once (about 3 s).
- `tests/test_display.py` checks letterboxing never stretches, info-bar lines never overlap, and the key-queue draining works.

## Task 1: lag diagnosis and fixes (commit "Task 1: ...")

**Already there (checked, not rebuilt):**
- the on-screen FPS, plus the camera's own FPS,
- the `t` timing report (capture / detect / control / draw / frame age),
- `CAP_PROP_BUFFERSIZE = 1` and the background capture thread that keeps only the newest frame,
- the Kalman filter (x, y, vx, vy) with predict-only during HOLDING,
- the labelled GAIN and LEAD sliders with help text,
- `--servo-speed` defaulting to 12.

**New: `--ai-every N`.** The AI runs on every N-th frame only, and a fast color tracker fills the frames in between.
- The tracker learns the balloon's color from the middle of each AI box. Red's wrap-around is handled by averaging hue around the color circle.
- Black, white and gray balloons are matched by brightness, since they have no real hue.
- It only searches near the last box, and rejects blobs of very different size.
- If it loses the balloon, the next frame uses the AI again.
- **Assumption: default is N = 1** (AI every frame, the same as before). On your laptop the AI is the bottleneck (about 14 FPS against a 30 FPS camera), so try `--ai-every 3`. I couldn't test it with your real webcam and balloon, so I didn't make it the default.
- **Assumption about the safety rule** "only with a real detection this frame": a color-tracker hit counts as a real detection, because it's measured from *this* frame's pixels, not predicted.
- **Person veto with `--ai-every`:** people are only checked on AI frames. The veto keeps the last AI answer, and its 1 s hold spans the gap, which is at most N−1 frames (about 0.1 s at N = 3).
- The `t` report now suggests `--ai-every 3` when detection is the slowest stage.

**Feedforward formula (assumption, same as before).** The list says "aim = velocity × LEAD + GAIN × error". Taken literally, moving by velocity × LEAD *every frame* would make the turret run at LEAD times the balloon's speed (lead 2 = twice as fast). It was implemented as: each frame, move by **velocity** (to keep pace) **+ GAIN × (predicted point − aim)**, where predicted point = position + velocity × LEAD. That matches "the aim moves at the balloon's speed and GAIN only corrects leftover error". LEAD then means "frames of delay to make up for" (default 1).

**Tests:** `tests/test_tracking.py` covers:
- the hybrid detector: AI call count, tracker accuracy within 3 px, black balloons, hand-back to the AI after a miss, and the lock holding across tracker frames,
- the red hue average,
- feedforward converging on target,
- the buffer-size request,
- the `t` report.

## Task 2: modes (commit "Task 2: ...")

**Already there (checked, not rebuilt):**
- AUTO/MANUAL modes, switched with `m`, defaulting to MANUAL, with the mode shown in the info bar,
- the LOCK LED (a virtual one in dry-run),
- the F key: pynput with true key-down/key-up, a 0.7 s fallback, and the method printed at startup,
- laser and LED serial commands sent only when they change,
- `turret.ino`: LED on pin 8, button on pin 2 with INPUT_PULLUP and debouncing, `D1`/`D0`, `B1`/`B0`.

The F-key **freeze** was fixed earlier. It wasn't blocking code: OpenCV reads only one key per frame, so a held key's auto-repeats piled up in Windows' queue, and Windows doesn't repaint a window until that queue is empty. Every waiting key is now read each frame (`read_all_keys` in `display.py`).

**No code changes in this task, only tests:** `tests/test_modes.py`. It checks:
- the default mode,
- `m` switching, with the laser going off right away in MANUAL without the button,
- the LED following the lock and every L/D command being sent only on a change,
- pynput's instant release and the focus rules, plus the fallback method,
- the pins, pull-up and commands in the sketch,
- that **the sketch compiles** with the Arduino IDE's built-in compiler (this test is skipped automatically if that compiler isn't found).

**Assumption:** the LOCK LED shows the lock in **both** modes, not just MANUAL. It's only an indicator, and it's useful in AUTO too.

## Task 3: display (commit "Task 3: ...")

**Already there (checked, not rebuilt):**
- **Letterboxing:** each frame, the code reads the window size, scales the picture to the largest size with the exact original shape (INTER_AREA when shrinking), and centers it on a black canvas. The mask window in `balloon_detect.py` does the same, and mouse clicks are mapped back to camera pixels.
- **Zoom keys:** `+`/`=`, `-`, `0`, clamped between 320 px and your screen width.
- **Info bar below the video:** text is drawn after scaling, so it stays the same size. Lines are spaced by measured height (`cv2.getTextSize`) and split into 2–3 columns when needed, on a shaded background.
- **Groups:** STATUS (mode, lock, laser), TARGET (size, distance), TUNING (GAIN, LEAD, FPS) and KEYS.
- **Video overlays:** only crosshairs, boxes, the velocity arrow, the raw centroid (red) and the predicted point (yellow), plus small badges (LASER ON / VISION FAULT / PERSON).

Background: `cv2.WINDOW_KEEPRATIO` is the number 0 in OpenCV, and the Windows version ignores it. That's why the window used to stretch.

**No code changes, only tests.** New checks in `tests/test_display.py`:
- zoom steps (+10%, same shape), reset, and both clamps,
- a real tracker frame rendered into 640×700, 1400×600 and 500×900 windows: the canvas always matches the window, the square balloon box stays square, and the red centroid dot lands inside the box.

**Assumption:** I added a TARGET group (detector, radius, lock zone, distance) to the three groups you listed, because Task 4 needs that information on screen.

## Task 4: dynamic lock zone (commit "Task 4: ...")

This was mostly written in the baseline. I finished it, made the zone's color logic a small tested function, wrote tests, and documented it (README section **"How the lock zone works"**).

**Built as asked:**
- **Lock zone size:** `lock_enter = clamp(LOCK_FRACTION × radius, LOCK_MIN, LOCK_MAX)`, and `lock_exit = lock_enter × 1.8`.
- **Controls:** a live "LOCK FRACTION" slider (0.1–1.0, default 0.5), plus the flags `--lock-min 6`, `--lock-max 60` and `--min-radius 8`. The 5-frame rule is unchanged.
- **Circle colors** around the aim: green = locked, yellow = inside but not 5 frames yet, red = outside. A thin outer ring shows the exit zone while locked.
- **Info bar (TARGET group):** apparent radius, lock zone, distance.
- **Distance:** `--calibrate-distance [CM]` plus the `c` key stores K = distance × radius. The display shows "uncalibrated" until it's set.
- **`--scale-gain`:** off by default, clamped to 0.5×–2×, with `--reference-radius` defaulting to 40 px.

**Assumptions and changes worth knowing:**
- **Radius in AI mode.** The AI gives only a box, not an outline, so `minEnclosingCircle` is impossible there. The radius is `(box width + box height) / 4`, which equals the true radius for a round balloon. Color mode uses `minEnclosingCircle` as asked.
- **Smoothing** is a moving average of the last 5 radii, reset when the balloon is lost.
- **The deadzone now shrinks automatically** to at most half the lock zone. Without this, a small far balloon (6–7 px zone) could **never** lock, because the old fixed 10 px deadzone let the aim settle up to about 14 px off. There's a test for this.
- **K is saved in a new `turret_config.json`,** not `presets.json`. That file holds color presets keyed by name, and mixing calibration into it would make "distance" look like a color.
  - K is stored separately for the AI and color detectors, since they measure size differently.
  - K is adjusted automatically if the camera resolution changes.
  - Re-calibrate for a different-size balloon.
- **The laser stays off while calibrating,** because you'd be standing in front of the turret holding the balloon.
- **The `--scale-gain` reasoning is incorrect,** and the README explains it plainly. Degrees per pixel depend only on the camera's field of view, not on distance, so 10 px ≈ 1° at any range. Raising the gain for small balloons mostly adds overshoot. I built it as asked and recommend leaving it off.
- **Removed flags:** `--lock-enter` and `--lock-exit` (the old fixed thresholds) no longer exist; the zone is now dynamic.

**Tests:** `tests/test_lock_zone.py` covers:
- the zone formula, the clamps and flags, and the radius smoothing,
- `minEnclosingCircle` in color mode,
- the 5-frame rule plus hysteresis with the dynamic zone,
- the deadzone shrink,
- the circle colors,
- calibration end to end (uncalibrated → press `c` → 100 cm at 1.5× the size), including "laser off while calibrating" and "no steady balloon yet",
- `--scale-gain` being off by default and its clamps.

## Task 5: detection robustness (commit "Task 5: ...")

**New:**
- **Exposure and white-balance lock** (`camera.py`). At startup the camera gets about 20 frames to settle; then auto-exposure and auto white balance are switched **off**, keeping the values the camera chose. They're switched back **on** when the program quits.
  - It's applied in `turret_tracker.py` and in `balloon_detect.py`, the HSV tuner, which benefits most.
  - `--auto-exposure` turns the locking off.
  - **Why restore on exit:** some webcam drivers *remember* these settings after the program ends, which would leave your webcam stuck for Zoom or Teams.
  - **Limitation:** many webcam drivers ignore these requests, and some say "yes" and do nothing. The program prints what the driver answered. **I did not test this on your real webcam:** you were away, and I didn't want to switch your camera on unattended. The first time you run it, check the "Camera: auto-exposure OFF … driver accepted" lines.
  - OpenCV's values for this setting are odd: 0.25 = manual and 0.75 = auto (DirectShow/V4L2 convention). They're named constants in `camera.py`.
- **`--camera INDEX`** for the tracker and `balloon_detect.py`, and **`--list-cameras`** for the tracker (tries cameras 0–5, prints the ones that work with their resolution, then quits).
- `open_camera()` was copied in two files; it now lives once in `camera.py`.

**Already there (checked, now with tests):** HSV presets in `presets.json`, red hue wrap-around, laser-dot rejection (small + very bright blobs are skipped), and the 0.5 s HOLDING behavior.

**Assumption:** I didn't add `--camera` to `balloon_detect_ai.py` or `turret_sim.py`. They're older single-purpose scripts, and the tracker replaces them.

**Tests:** `tests/test_detection.py` covers:
- exposure locked by default and restored on exit, and left untouched with `--auto-exposure`,
- `--camera`, `--list-cameras` (with a fake camera list),
- presets, red wrap-around at both ends of the hue range, and laser-dot rejection (even when the dot is bigger than the balloon),
- HOLDING for 0.5 s, then LOST.

## Task 6: record and replay (commit "Task 6: ...")

**New:**
- **`r` records.** It toggles recording of the **raw** camera feed to `recordings/<date-time>.mp4`.
  - Recording happens in the capture thread, so *every* camera frame is saved, not just the ones the slower tracker loop processed.
  - The file uses the camera's *measured* frame rate, so it replays at true speed.
  - Quitting while recording closes the file properly.
  - `recordings/` is in `.gitignore`.
- **`--replay FILE`** plays a recording instead of the camera.
  - **Default:** real-time speed, behaving like a live camera (frames are skipped if processing is slow).
  - **With `--no-capture-thread`:** every frame is processed in order, which is repeatable, so it's good for comparing settings on the same footage.
  - The end of the file prints "Replay finished." and quits.

**Assumptions:**
- **Your list stopped at "--replay"** (the message seems cut off). I took it to mean "play a recording instead of the camera". If you meant more, it isn't done.
- **Replay forces `--dry-run`,** because moving real servos to follow a recording makes no sense and could only cause trouble.
- **Off during replay:** recording (pressing `r` just says so) and exposure locking (it's a file, not a camera).
- **Video format:** `.mp4` with the `mp4v` codec, which works with plain `opencv-python` on Windows without extra installs.
- **I also added `turret_config.json` to `.gitignore`.** It's your personal distance calibration, specific to your camera and balloon.

**Tests:** `tests/test_record_replay.py` covers:
- `r` start/stop produces an `.mp4` with the right number of frames, containing the raw picture with nothing drawn on it,
- the file being closed on quit,
- replay running the whole pipeline (tracking and locking) without ever opening a serial port,
- no recording during replay,
- threaded replay playing at real speed rather than instantly,
- a missing file giving a clean error.

---

# Final software pass

## FP Task 1: lock down the safety overrides (commit "FP1: ...")

- **`--no-person-veto` is refused** (the program exits with an error before any serial port is opened) unless it's dry-run **and** `LASER_ENABLED` is False. *Test:* `test_no_person_veto_refused_on_real_serial` / `_when_laser_enabled`.
- **Startup safety banner.** Prints every safety and whether it's ON or OFF, for example `PERSON VETO : OFF (dry-run only)`.
- **Python-side watchdog** (`LoopWatchdog`): a background thread that the main loop "kicks" several times per frame.
  - If no kick arrives for **200 ms**, the watchdog thread itself sends `L0` and shows **"WATCHDOG - LASER OFF"** for 1.5 s, and the lock must be earned again.
  - It has to be a separate thread, because a stuck main loop can't switch anything off.
  - The serial link and laser state now have locks, since two threads use them.
  - *Test:* the fake detector stalls 350 ms in real time; the test checks `L0` went out *during* the stall and the laser stays off for 5 frames afterwards.
- **Proof that only `laser_should_fire()` can produce `L1`** (`tests/test_laser_path.py`):
  - **Code-reading half:** the text `"L1"` appears in exactly one function. Outside `TurretOutputs.set` it can only ever be called with `False`. Every `outputs.set(laser=X)` passes `False` or a variable assigned only from `laser_should_fire()`.
  - **Runtime half:** a fake Arduino fails the test if `L1` arrives while the latest `laser_should_fire()` answer wasn't True. It's checked across auto/manual modes, lock loss, the kill key, the person veto, focus loss and detector errors.

**Real bug this found and fixed:** after the Arduino reconnected, `sync()` re-sent the *old* laser state. That could switch the laser back on without asking `laser_should_fire()`, and without a fresh lock. Now a (re)connect always sends `L0` and drops the lock.

**Assumptions:**
- The watchdog also trips in dry-run, showing the message but sending no serial. It can trip once at startup if the first AI frame is slow, or while you drag or resize the window (Windows pauses the program during a drag). Both are harmless: the laser just goes off.
- With `LASER_ENABLED = False`, the watchdog's `L0` isn't sent either, because the rule "no laser commands at all while disabled" wins.

**Try:** `python turret_tracker.py --dry-run` (read the banner). `python turret_tracker.py --port COM5 --no-person-veto` is refused.

## FP Task 2: safe defaults for the first hardware run (commit "FP2: ...")

- **`--first-run`** forces:
  - GAIN 0.1 and LEAD 0 (the sliders can't override them),
  - at most 3° per command,
  - angles limited to 45–135,
  - **laser permanently disabled,** through a new `laser_disabled` condition in `laser_should_fire()`,
  - "FIRST RUN MODE" in the terminal banner and on screen.
- **`bringup.py` (new):** it didn't exist, so I created it. It's a printed step-by-step hardware bring-up checklist that recommends `--first-run` and lists the serial ports it can see. It never opens a port.
- **Slew limit (`--max-step`, default 10°):** no command can move a servo more than 10° from the last angle sent. The controller is also kept from "winding up" ahead of what was actually sent, which would cause overshoot.
- **Gentle recenter.** On quit, the turret glides from where it is back to 90,90 in small steps over about 1 s.
  - **Startup** (assumption, being honest): the Arduino itself puts the servos at 90,90 the moment it boots, and that move is instant, because hobby servos have no position sensor, so nothing can know where they were. Python starts at 90,90 to match, and the slew limit then keeps every move after that gentle.
- **Pulled-cable handling:**
  - **Reconnecting no longer freezes the video.** It used to wait up to 3 s for the Arduino's "READY".
  - "ARDUINO DISCONNECTED - retrying every 2 s" is shown, and the laser goes OFF on the same frame the cable is lost.
  - After reconnecting, the first laser command is always `L0`, and the lock has to be earned again (5 frames) before any `L1`.
  - A new `link_ok` condition in `laser_should_fire()` covers this.
- **Bug found and fixed:** the FIRST RUN badge's position was a decimal number, which made OpenCV crash on the first frame of `--first-run`. A test caught it.

**Tests:** `tests/test_hardware_safety.py` covers:
- first run: laser never fires, GAIN/LEAD forced, angles 45–135, steps of 3° or less,
- the default slew limit,
- the quit glide (15+ small steps ending at 90,90),
- a pulled cable: display keeps running, laser off while disconnected, `L0` first after reconnect, `L1` only after re-locking.

**Try:**
```
python bringup.py --port COM5
python turret_tracker.py --dry-run --first-run   # see FIRST RUN MODE, slow moves
python turret_tracker.py --dry-run --max-step 3                    # watch the slew limit
```

## FP Task 3: cleanup and audit (commit "FP3: ...")

**The split.** `turret_tracker.py` had grown to about 1,900 lines, so it's now split into modules. The code was moved **verbatim by a script** (no hand-copying), and the script wrote the import lines.

| File | What's in it |
|---|---|
| `turret_tracker.py` | thin entry point: every old command still works |
| `tracker_app.py` | the main loop; its top comment explains the whole system |
| `safety.py` | `LASER_ENABLED`, `laser_should_fire()` (docstring lists every condition), the watchdog, the banner, the override checks |
| `control.py` | angles, gains, the lock zone, the Kalman filter |
| `vision.py` | the camera thread, record/replay, the three detectors |
| `serial_link.py` | the Arduino link, laser/LED outputs, the recenter glide |
| `ui.py` | the fire key, the timing report, colors, slider names |
| `config.py` | JSON reading plus validation |
| `cli.py` | the command-line flags |

- **Exceeds the guideline:** `tracker_app.py` is still about 860 lines (about 125 of them the explanation at the top). The main loop is one long function by nature. Splitting it further means passing ~40 variables between functions, which would make it *harder* for you to follow, so I stopped here.
- **Behavior check:** all 94 existing tests passed unchanged right after the split.

**Cleanup:**
- **Faster startup:** the tracker imported helpers from `balloon_detect_ai.py`, which loads PyTorch, so even `--color` mode paid a multi-second startup. `nothing()` and `draw_center_crosshair()` now live in `display.py`, and `CAMERA_INDEX` in `camera.py`, with no duplicates left.
- **Removed `--person-veto`:** the old no-op flag ("the default now"). The person veto is simply on unless you pass `--no-person-veto`.
- **Scans:** no unused imports and no functions that are never called (I checked every file with a script).

**Config validation** (`config.py`):
- **Broken JSON:** gives "not valid JSON … line X, column Y".
- **`presets.json`:** every preset is checked for missing keys, whole numbers and ranges (hue 0–179, S/V 0–255), and min ≤ max for S and V. Red's hue wrap (h_min > h_max) is still allowed. All problems are listed at once. `save_preset` refuses to save bad values.
- **`turret_config.json`:** each calibration needs a positive `k` and `frame_width`.
- **Assumption, presets:** a bad presets file **stops** the program with the message, because color detection can't work without it.
- **Assumption, calibration:** a bad calibration file only **warns**, and distance shows as "uncalibrated", because it's just an estimate.

**`docs/SAFETY.md`:** every safety rule (21 on the Python side, 5 on the Arduino side), where each is enforced (file → function), and which test covers it. The Arduino rules are marked "verified by code reading and compile only, not by hardware".

**Tests:** `tests/test_config.py` (9 tests). The tests now find names in whichever module defines them. `patch_all()` patches a name in *every* module that imported it, so a test can't accidentally patch only one copy of `LASER_ENABLED`.

## FP Task 4: Arduino sketch checks (commit "FP4: ...")

- **Compiled again:** **5,212 bytes of program memory (16% of 32,256)** and **461 bytes of RAM (22% of 2,048)**.
- **Re-read the sketch against all 6 rules.** Each one holds and is now confirmed in a comment block at the top of `turret.ino`:
  - `L1` is refused while disabled,
  - laser and LED go off after 500 ms of silence,
  - angles are clamped to 20–160 (even 99999999 → 160),
  - malformed lines are ignored before anything moves,
  - boot state is laser LOW first, LED off, servos written to 90 before attach,
  - a serial flood can't crash it (fixed buffer, no `String` class).
- **New tests** (by reading the code): clamp-before-write, reject-before-move, boot order, no buffer overflow, and the comment block being present.
- **Hardware issue found by the review (please read).** Each time the laptop opens the port, the Uno resets and its bootloader runs for about 1–2 s *before* `setup()`. During that time pin 7 floats, and the laser transistor could pick up noise and partly turn on. Code can't fix this. **When you wire the laser, add a 10 kΩ resistor from pin 7 to GND.** It's written in `turret.ino`, `docs/SAFETY.md`, `docs/WOKWI.md` and the README.
- **`docs/WOKWI.md`:** how to run the sketch in the free wokwi.com simulator, with a ready-made `diagram.json` (servos on 9/10, LEDs standing in for the laser and LOCK LED, the fire button) and an 18-row table of exactly what to type and the expected reply for every rule.
  - **Not tried in Wokwi itself.** I checked that the diagram is valid JSON, and the part and pin names follow Wokwi's standard naming, but I couldn't open the site from here. If it complains, the doc includes manual wiring steps.
- **The Arduino rules remain "verified by code reading and compile only, not by hardware".**

**Try:** open wokwi.com and follow `docs/WOKWI.md`, which takes about 10 minutes.

## FP Task 5: packaging (commit "FP5: ...")

- **`requirements.txt`:** pinned to the exact versions installed and tested on your laptop (Python 3.11.9): `opencv-python==4.11.0.86`, `numpy==2.2.6`, `pyserial==3.5`, `pynput==1.8.1`, `ultralytics==8.4.175`, `torch==2.7.0`, `torchvision==0.22.0`.
  - **Assumption, `imageio` left out:** you listed it, but nothing in the project imports it (I checked every import). It's installed on your laptop only as a leftover dependency.
  - **Assumption, `pytest` is dev-only:** it's in `requirements-dev.txt` as `pytest==8.4.2`, a real released version. It's optional because the tests use the built-in `unittest`. pytest isn't installed here, so **running the tests with pytest hasn't been verified.**
- **README "Setup (one command)":** a single PowerShell line that creates `.venv`, installs the requirements, runs the tests and runs `--self-test`. It calls `.venv\Scripts\python` directly, so PowerShell's "running scripts is disabled" error (common with `Activate.ps1`) can't block it. I did **not** run the venv creation itself, since it downloads about 1 GB.
- **`--self-test`** (`selftest.py`): checks the camera opens and gives a picture, the AI model loads and runs, `presets.json` and `turret_config.json` are valid, the serial port list can be read (no port is opened), and both laser switches are off. It prints PASS/FAIL per item; the exit code is 0 if all pass, otherwise 1. A crashing check counts as FAIL rather than crashing the self-test.
- **Tests:** `tests/test_selftest.py` (all pass; camera failure; model failure via `SystemExit`; a laser switch left on counts as FAIL; the `--self-test` flag exits with 0).

**Try:** `python turret_tracker.py --self-test`

## FP Task 6: final docs (commit "FP6: ...")

**The README is rewritten from scratch.** It contains:
- the status line ("Software complete, tested in simulation. Hardware build in progress."),
- an architecture diagram showing which file does what,
- quick start: one-command setup, dry-run, then `bringup.py`,
- the two modes,
- GAIN, LEAD and the lock zone in plain language,
- **every flag and key in one table** (generated from the program's own `--help`, so nothing is missing),
- a safety summary that links to `docs/SAFETY.md` and `docs/WOKWI.md`, including the pin-7 pull-down note,
- parts and wiring,
- record/replay and exposure lock,
- the lag table,
- **"Lessons learned: the lag story"**: four different problems that looked like one,
- **"Known limitations"**, all honest:
  - untested on real servos,
  - servo lag and backlash will change tuning,
  - parallax at close range,
  - the exposure lock may be ignored,
  - ~30 FPS limits fast targets,
  - the AI isn't perfect,
- **"Roadmap"**: hardware bring-up, calibration, a pointer-grade laser, an optional pin/needle pop mechanism, an optional gyro/IMU. No high-power laser is mentioned anywhere.

**`.gitignore`** now covers recordings (`recordings/`, `*.mp4`, `*.avi`), demos (`demos/`), logs (`logs/`, `*.log`), caches (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, coverage), the model file (`*.pt`), the calibration file and Arduino build output. Each was checked with `git check-ignore`.

**Assumption:** the `--scale-gain` explanation moved from its own README section into a short note under the flags table, so the README stays readable.

## FP Task 7: final checks (commit "FP7: ...")

**Everything was run on your laptop:**
- **Full test suite:** `python -m unittest` → **114 tests, all pass** (about 32 s).
- **Headless dry-run smoke test:** `python -m unittest tests.test_smoke -v` → 3/3 pass (the full color pipeline, the camera thread, and the real AI model loading).
- **`python turret_tracker.py --self-test`** on the real laptop → **ALL PASSED**:
  - camera 0 gave 640×480,
  - the AI model loaded and ran,
  - presets.json OK (7 presets), no distance calibration yet,
  - no serial ports (no Arduino plugged in),
  - `LASER_ENABLED` is False in both places.

  This opened your webcam for one frame (nothing saved) and opened no serial port.

**Deliberate-break checks.** I broke the code on purpose, ran the whole suite each time, then restored it (git confirmed no leftover changes). **All 7 were caught:**

| Deliberate break | Caught by |
|---|---|
| fire on prediction alone | `test_each_condition_alone_blocks_the_laser`, `test_button_alone_never_fires` |
| ignore window focus | the same two |
| ignore the person veto | 4 tests, including `test_person_veto_blocks_and_holds_one_second`, `test_color_mode_with_person_veto_never_fires` |
| MANUAL mode ignores the button | `test_each_condition_alone_blocks_the_laser` |
| no 1 s person hold | `test_person_veto_blocks_and_holds_one_second` |
| **new:** watchdog disabled | `test_watchdog_sends_L0_during_a_stall` |
| **new:** `--no-person-veto` allowed with `LASER_ENABLED` True | `test_no_person_veto_refused_when_laser_enabled` |

**Skipped or reverted:** nothing was reverted; every task passed its tests. Things I deliberately did **not** do, with the reasons:
- **Didn't run the venv creation in the one-command setup,** because it downloads about 1 GB.
- **Didn't try the Wokwi project in the simulator itself.** I can't use the website from here.
- **Didn't run the tests with pytest.** It isn't installed.
- **Didn't split `tracker_app.py` below about 860 lines.** The reason is in FP3.


---

# Auto-tune

## AT Task 1: constant-acceleration Kalman filter (commit "AT1: ...")

The old filter assumed **constant velocity** and a **fixed frame rate**. Both are wrong for a falling balloon. The new `MotionTracker` in `control.py`:
- **Runs two Kalman filters side by side:**
  - **CV (constant velocity):** a calm estimate for steady drifting,
  - **CA (constant acceleration):** state x, y, vx, vy, ax, ay, for falls, bounces and throws.
- **Blends them as an "IMM"** (Interacting Multiple Model filter). Each frame, the model that predicted the new detection better gets more weight, which is exactly the "weight by recent prediction error" you asked for.
- **Uses real time:** dt is the actual time between frames, from each frame's capture timestamp, and units are pixels per **second**.
- **Measurement noise depends on the blob.** Small blobs (far) and stretched, blurry blobs (fast) are trusted less: sigma = base × (40 px ÷ radius) + blur × (long side − short side) ÷ 2.
- **Clamps:** acceleration to ±6,000 px/s² and speed to ±5,000 px/s. For scale: a balloon 1.5 m away falling under full gravity is about 3,700 px/s². One bad detection can't throw a wild prediction.

**Measured** (10 runs each, predicting 150 ms ahead, ±2 px detection noise):

| Motion | Old CV filter | CA only | **New IMM blend** |
|---|---|---|---|
| Falling, 30 fps | 55.9 px | 10.3 px | **11.8 px** |
| Falling, 60 fps | 57.0 px | 10.0 px | **13.8 px** |
| Steady, 30 fps | 3.6 px | 10.2 px | **4.6 px** |
| Steady, 60 fps | 2.9 px | 9.5 px | **4.6 px** |

So the blend is about 5× better than the old filter on a fall, and it doesn't get jumpy on steady motion the way an acceleration-only filter does.

**Assumptions:**
- I wrote the filter with numpy instead of `cv2.KalmanFilter`, because the IMM blend and the changing dt need direct access to the matrices.
- The starting filter settings in `DEFAULT_TUNING` are hand-picked. Task 5's tuner refines them.
- In this commit, LEAD is still counted in frames. Task 2 replaces that with latency in seconds.

## AT Task 2: lead based on latency, in seconds (commit "AT2: ...")

- **Latency is measured continuously:** every time an angle command is sent, the tracker measures *now − that frame's capture time* and smooths it.
- **Aim point = the filter's prediction at capture time + measured latency + servo delay.** The servo delay comes from `--servo-delay`, default 80 ms. LEAD is now automatic and in **seconds**: a slow laptop automatically aims further ahead, a fast one less.
- **On screen** (TUNING group): `LEAD 152 ms (auto)` and `latency 72 ms + servo 80 ms`.
- **The dry-run simulated servo now reacts after `--servo-delay` too,** like a real one. So the simulation includes the delay that LEAD has to make up for, and tuning in dry-run is honest.
- **The LEAD slider is gone** (no longer created). `--lead` still exists so old commands don't break, but it's now a manual override in **milliseconds**, used only with `--manual-tuning` (Task 3).
- **`--first-run` still forces LEAD 0.** Aiming at the current position is the most cautious choice.

**Assumption:** "latency" means what you described: capture → command sent, plus the servo delay. The webcam's own internal delay (exposure plus USB transfer, often 30–80 ms) happens *before* our capture timestamp, so software can't measure it. If real-hardware tracking still lags, raise `--servo-delay` to cover it.

## AT Task 3: automatic gain (commit "AT3: ...")

- **GAIN comes from tracking confidence** (`control.auto_gain`).
  - Confidence is based on how well the filter's recent predictions matched the detections (the *normalized innovation*), and drops after a sharp change of direction.
  - High confidence moves the gain toward `gain_max`, for a crisp lock. Low confidence moves it toward `gain_min`, to avoid overshoot.
- **Extra safeguard I added: a stability cap** (`stable_gain_cap`). A controller acting on delayed information oscillates above a certain gain (for a delay of d frames, K = 2·sin(π / (2(2d+1)))). The automatic gain never exceeds half of that, for the measured delay.
  - Without the cap, two existing tests failed: the aim kept swinging around the target once the 80 ms servo delay was simulated.
  - The cap is **computed, not tuned**, so the tuner can't push the gain into oscillation.
  - At the default 80 ms delay and 30 fps it works out to about 0.20.
- **Servo speed-cap usage:** each command may move `min_step + speed_cap_gain × (the step needed to keep pace with the balloon)`, never more than `--max-step` (or 3° in first-run). A slow balloon gets small, smooth steps; a fast one uses more of the cap.
- **Deadzone** shrinks to at most a quarter of the balloon's radius, so small or far balloons still get centered. It's still never more than half the lock zone.
- **Sliders:** the GAIN and LEAD sliders are **hidden by default**. `--manual-tuning` brings them back, and then they override the automatic values (LEAD in ms).
- **HUD (read-only),** showing what it chose:
  - `GAIN 0.20 (auto, confidence 0.93)`,
  - `LEAD 152 ms (auto)`,
  - `latency 72 ms + servo 80 ms`,
  - `step limit 4.2 deg (cap 10)  deadzone 6px`.

**Assumptions:**
- `--no-predict` and `--scale-gain` keep using the fixed `--gain`. They're the comparison and experimental modes, and an existing test relies on that.
- `--first-run` still forces GAIN 0.1 and LEAD 0. Nothing automatic can override first-run.
- The LOCK FRACTION slider stays visible. You only asked to hide GAIN and LEAD.

## AT Task 4: detection that survives motion blur (commit "AT4: ...")

- **The filter now tells the detector what to expect.** Each frame it passes a "hint": predicted position, velocity, speed and size.
- **Faster balloon → looser color thresholds** (color detection and the `--ai-every` color tracker):
  - S min and V min are lowered by up to `blur_relax_s/v` (tunable), but **never below fixed floors of S 40 and V 30**, so a blurry balloon can't make "everything" count as balloon.
  - The morphological close grows from 5 up to 13 px, which glues a streaky balloon back into one blob.
- **Search near the prediction first, then the whole picture:** the color detector searches near the predicted position with the relaxed thresholds, then falls back to the whole picture with the normal thresholds.
- **Far-away detections are rejected:** a detection farther from the prediction than max(60 px, 4 radii, 3 frames of motion) is ignored while the balloon has only been missing briefly. It counts as a dropout, so the laser stays off. After 0.5 s missing, the filter resets and anything is accepted again.
- **Dropouts up to 0.5 s:** the turret keeps aiming at the prediction (HOLDING), and the laser stays off until a real detection. This was already true and is unchanged.
- **Streaks:** a blob stretched along the direction of motion (≥ 1.4× longer than wide, within 30°) is measured at its **center** (center of mass, where the balloon was mid-exposure), and its **short side** gives the radius. The same applies to the AI's box.
- **The laser-dot filter still runs** on the relaxed search, so loosening the thresholds can't let a laser dot count as a balloon.

**Measured** (synthetic red balloon, radius 25, smeared by motion):

| Blur streak | Normal search | With filter hint |
|---|---|---|
| 0 px | found, r = 25.1 | found, r = 25.1 |
| 60 px | found, r = 27.8 (inflated) | found, r = 24.2 |
| 120 px | **missed** | **found** at (319, 240) (true 320, 240), r = 19.4 |

**Assumptions:**
- In live mode, the hint's speed is the balloon's world speed. When the turret is following well, the actual blur in the picture is smaller than that speed suggests, so thresholds may relax a bit more than needed. That's still within the floors.
- On heavy blur the measured radius comes out small (19.4 instead of 25), because blur fades the balloon's edges. That means a smaller lock zone, which errs on the safe side.

## AT Task 5: simulator and auto-tuner (commit "AT5: ...")

- **New files:**
  - `simulator.py`: falling, tossed, bouncing, drifting and reversing balls at 30/60 fps, with motion blur, brightness changes and noise. Try `python simulator.py --show bounce`.
  - `headless.py`: runs the real tracker with no window or webcam and scores it.
  - `tune_params.py`: random search over the tunable parameters, which saves `config/auto_tune.json`.
- **The tracker loads `config/auto_tune.json` automatically.** Use `--no-auto-tune` to ignore it, or `--tuning-file X` to use a different one. Only whitelisted, in-range keys are accepted. Anything else is ignored with a printed warning, so this file can never touch a safety rule.
- **Bug found by the simulator (fixed here, in tracker_app.py):** with servo delay, the controller compared the *future* target to the *lagging* servo position. The servo always looked behind, so it kept pushing, and the turret ended up aiming a whole delay ahead (drift: 29 px error, never locked). It now compares the target with where it has already *commanded* the servos (a "Smith predictor"). The lock check still uses where the servos really are.
  - Dry-run simulates that. Live mode estimates it from the commands it sent, `--servo-delay` ms ago.
- **Results** (full table in docs/AUTO_TUNE.md):

  | Tracker | Combined score (lower is better) |
  |---|---|
  | Old tracker | 54.3 |
  | New + tuned (same conditions) | 29.2 |
  | New with an 80 ms servo delay: defaults | 46.3 |
  | New with an 80 ms servo delay: tuned | 36.7 |

**Assumptions:**
- "Old tracker" means commit 37a3da8 (the end of overnight-improvements). It has no servo-delay model, so it's compared with zero delay.
- Simulated servo speed is 400°/s, and processing takes 30 ms per frame.
- Clips in `recordings/` are scored by the tracker's own "miss" distance, since a clip has no ground truth. You have no clips yet, so this tuning is simulation-only.
- `heavy_toss` doesn't lock with an 80 ms delay. I didn't try to "tune that away". It's a real limitation, documented in docs/AUTO_TUNE.md.

## AT Task 6: tests (commit "AT6: ...")

New file `tests/test_auto_tune.py` (19 tests). The full suite is now **127 tests, all passing**.
- **CA filter on free fall:** the acceleration model's (and the IMM blend's) 150 ms-ahead prediction error is under half the constant-velocity filter's. Acceleration is clamped.
- **Latency lead:** lead = measured latency + servo delay. 20 ms processing gives LEAD ≈ 100 ms at **both 30 and 60 fps** (the old frame-based lead would halve at 60 fps). Slower processing gives more lead, and `--servo-delay` is respected. The gain cap shrinks as delay grows.
- **Blur:** a 120 px red streak is **missed** by the plain search and **found** at its center with the prediction hint. Relaxed thresholds never go below the S/V floors, even at the most extreme tuning.
- **Dropouts:** while HOLDING (still predicting and aiming), the laser stays off. The main loop reports `real_detection=False` to laser_should_fire() on those frames. A far-away red blob during a dropout is ignored and doesn't fire.
- **Sliders:** without `--manual-tuning`, GAIN/LEAD sliders aren't created, and moving them changes nothing (identical serial commands). With `--manual-tuning` they're created and change the commands.
- **Tuning can't loosen safety:**
  - Tunable names don't overlap with any safety name, and safety.py never reads tuning.
  - A tuning file containing `LASER_ENABLED`, `LOCK_FRAMES`, etc. has them ignored with a warning.
  - At 5 extreme corners of the tuning ranges: no L1 while LASER_ENABLED is False; with it patched True, never on without a detection, never without focus, never before LOCK_FRAMES.
  - The existing laser-path proof is re-run with the shipped `config/auto_tune.json`.
- **Mutation checks I ran by hand:**
  - Removing `real_detection` from laser_should_fire() → the existing safety tests fail.
  - Making the main loop treat a prediction as a detection → the new dropout test fails.

**Assumptions:**
- Existing test files are **unchanged**. The shared helper `tests/fakes.py` got two small options:
  - `processing_s`, to simulate slow processing.
  - `--no-auto-tune` by default, so re-running tune_params.py can't change test results.

  The tuned file is tested separately, as above.

## AT Task 7: docs (commit "AT7: ...")

- **README:**
  - "GAIN and LEAD in plain language" is replaced by **"Tuning is automatic"**. It covers what GAIN and LEAD mean, how each is chosen automatically, `--manual-tuning`, and a table of what every TUNING line in the info bar means.
  - New flags in the flag table: `--manual-tuning`, `--servo-delay`, `--tuning-file`, `--no-auto-tune`. `--gain` and `--lead` are now described as manual-only.
  - Known limitations are honest:
    - Tuning was done in simulation only (no recordings yet).
    - Real servo lag will change the results.
    - A 30 fps webcam and hobby servos can't follow fast throws (heavy_toss never locks with an 80 ms delay).
  - Added a Lessons-learned note about the servo-delay bug the simulator caught, and the new files to the file table.
- **docs/AUTO_TUNE.md:** method, before/after table, how to re-run, limits, and which tests guard it.
- **docs/SAFETY.md:** new row 20, "auto-tuning can't loosen safety".
