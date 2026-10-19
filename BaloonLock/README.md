# BalloonLock: balloon-tracking laser turret

**Status: software complete, tested in simulation. Hardware build in progress.**

A webcam finds a balloon. A laptop works out where it is and where it's going, and an Arduino turns a pan/tilt bracket to follow it. A pointer-grade laser (under 5 mW) may only switch on when the turret has a steady lock on the balloon and every safety check passes. In MANUAL mode, you also have to hold a fire button.

It's a learning project by a first-year ECE student. Everything runs and is tested on a laptop with just its webcam (`--dry-run`); the Arduino parts are in progress.

## Architecture

```
                      LAPTOP (Python + OpenCV)                              ARDUINO UNO
 ┌────────┐   ┌──────────────────────────────────────────────┐  USB   ┌──────────────────────┐
 │ webcam │──►│ camera thread (newest frame only)   vision.py│ serial │ turret/turret.ino     │
 └────────┘   │   → detector: AI (YOLO) / color / both       │        │                      │
              │   → Kalman filter: position, speed, accel.   │"90,45" │ pan servo   (pin 9)  │
              │                                   control.py │───────►│ tilt servo  (pin 10) │
              │   → aim: feedforward + auto GAIN × error     │"L1/L0" │ laser 2N2222 (pin 7) │
              │   → lock zone (scales with balloon size)     │"D1/D0" │ LOCKED LED  (pin 8)  │
              │   → laser_should_fire()  ◄── ONE decision     │        │                      │
              │                                    safety.py │◄───────│ fire button (pin 2)  │
              │   → laser/LED commands only on change        │"B1/B0" │ 500 ms watchdog      │
              │                               serial_link.py │        │ LASER_ENABLED=false  │
              │   → letterboxed display + info bar           │        └──────────────────────┘
              │                         display.py, ui.py    │
              └──────────────────────────────────────────────┘
```

## Quick start

**1. Install and check everything**, in PowerShell in the project folder:

```
python -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt; .venv\Scripts\python -m unittest; .venv\Scripts\python turret_tracker.py --self-test
```

That command:
1. creates a private Python environment in `.venv`,
2. installs the tested versions from `requirements.txt` (about 1 GB, mostly PyTorch),
3. runs all the tests,
4. runs the self-test (camera, AI model, config files and serial port list, each PASS/FAIL).

Calling `.venv\Scripts\python` directly works even when PowerShell blocks `Activate.ps1`.

**2. Try it with no hardware:**

```
.venv\Scripts\python turret_tracker.py --dry-run
```

Hold a balloon in front of the webcam. The magenta crosshair (the simulated turret) chases it, and the lock circle turns green when locked. Hold **f** to "fire" in MANUAL mode. Press **q** to quit.

**3. First time on real hardware:** run `python bringup.py --port COM5` and follow its checklist. It starts with `--first-run` (slow, gentle, laser disabled).

Notes:
- Use `opencv-python`, **not** `opencv-python-headless`; the headless version can't open windows.
- The AI model `yolov8n-oiv7.pt` (7 MB) downloads automatically the first time.
- **Arduino:** install the **Servo** library in the Arduino IDE, then upload `turret/turret.ino` to an Uno.
- **`requirements-dev.txt`** adds the optional `pytest`.

## The two modes

Press `m` to switch. The tracker starts in **MANUAL**.

| Mode | The laser is on when… |
|---|---|
| **MANUAL** (default) | every safety condition passes (see [Safety](#safety)) **and** you hold the fire button (in dry-run: hold `f`). Let go and it's off immediately. |
| **AUTO** | every safety condition passes. It goes off the same frame the balloon is lost. |

The **LOCKED LED** (pin 8, or on screen in dry-run) lights whenever the turret is locked, in either mode.

## Tuning is automatic

You don't set GAIN or LEAD any more. The tracker works them out every frame.

**What they mean**, in plain language. Each frame, the turret moves by two amounts:
1. **Feedforward:** "move as fast as the balloon is moving". The motion filter estimates the balloon's speed (and acceleration) and keeps pace with it.
2. **GAIN × leftover error:** "fix whatever error is still left". **GAIN is the reaction strength**: too low is sluggish, too high overshoots and wobbles.

**LEAD is "how far ahead in time to aim"**. By the time a frame is captured and processed and the servos have moved, the balloon has moved on.

**How the tracker picks them:**

| What | How it's chosen automatically |
|---|---|
| **Motion filter** | Two Kalman filters run side by side: "steady speed" and "accelerating" (falling, bouncing). Each frame, the one that predicted better gets more weight, so a falling balloon is followed by the acceleration model. Real timestamps are used, so uneven frame times don't confuse it. |
| **LEAD** (milliseconds) | measured *capture → command sent* latency **plus** the servo delay (`--servo-delay`, default 80 ms). It's in seconds, not frames, so it stays right if the frame rate changes. |
| **GAIN** | rises with **tracking confidence** (how well the filter's predictions have been matching). Low confidence (just found, or the balloon jinked) means gentle; high confidence means firmer. It's always capped below the value that would make the turret oscillate for the measured delay. |
| **Step limit** | how far the servos may move per command. It grows when the balloon is fast (so the turret can keep up), up to `--max-step`. |
| **Deadzone** | shrinks for small (far) balloons, so it never hides an error bigger than the lock zone. |
| **Detection** | when the balloon moves fast and smears (motion blur), the color thresholds loosen a little (never below safe floors) and the search looks near the predicted spot first. |

The internal numbers behind this (filter noise, gain curve, blur thresholds…) were tuned by `tune_params.py` on simulated falls, bounces and throws, and are stored in `config/auto_tune.json`. The tracker loads that file at startup. See [docs/AUTO_TUNE.md](docs/AUTO_TUNE.md) for the method and the before/after scores. That file can only change tuning values: anything else in it, like `LASER_ENABLED` or the lock rules, is ignored.

**Manual override (for experiments):** `--manual-tuning` brings back the **GAIN** and **LEAD ms** sliders, and the turret uses them instead of the automatic values. Start with `--gain 0.2 --lead 0`. Raise GAIN until the crosshair jumps onto a still balloon without overshooting. Then raise LEAD while moving the balloon steadily until it stops trailing. Without `--manual-tuning` the sliders aren't shown and nothing can change GAIN or LEAD by hand.

**What the info bar shows** (TUNING section):

| Line | Meaning |
|---|---|
| `GAIN 0.41 (auto, confidence 1.00)` | the gain in use and why: `auto` (with confidence 0–1), `manual` (sliders), `fixed` (`--no-predict`), `first run` |
| `LEAD 100 ms (auto)` | how far ahead in time it's aiming |
| `latency 20 ms + servo 80 ms` | the two parts of LEAD: measured processing latency + servo delay |
| `step limit 2.3 deg (cap 10)  deadzone 8px` | the automatic step limit (and the `--max-step` cap) and deadzone |
| `sent pan … tilt …` / `sim servo pan …` | the last command, and (in dry-run) where the simulated servos are right now |

In STATUS, `HOLDING (predicting, 0.2s)` means the balloon was briefly lost: the turret keeps aiming at the prediction for up to 0.5 s, but **the laser stays off until it's really seen again**. `ignored a far blob` means something red appeared far from where the balloon should be and was ignored.

## The lock zone

The laser may only fire after the *real* detected balloon (never just the prediction) has stayed inside a **lock zone** around the aim point for **5 frames in a row**. On screen, the circle is:
- 🟢 green when locked,
- 🟡 yellow when the balloon is inside but hasn't been there for 5 frames yet,
- 🔴 red when it's outside.

The zone **scales with how big the balloon looks**: `LOCK FRACTION × apparent radius`, kept between 6 and 60 px. A far, small balloon needs a precise aim; a close, big one is forgiving.

Leaving the zone uses a bigger circle (1.8 ×), so small wobbles don't flicker the lock. A balloon that looks smaller than 8 px radius is **too far**: no lock, no laser.

`--calibrate-distance 150` plus the `c` key stores *distance × radius* in `turret_config.json`. From then on the screen shows an estimated distance.

## Every flag and key

| Flag or key | What it does |
|---|---|
| **Running** | |
| `--dry-run` | no Arduino: simulate the turret on screen and print the commands it *would* send |
| `--port COM5` | the Arduino's port (asked for if left out) |
| `--first-run` | **first time on real hardware:** GAIN 0.1, LEAD 0, at most 3° per command, angles limited to 45–135, laser permanently disabled |
| `--self-test` | check the camera, AI model, config files and serial port list; print PASS/FAIL; quit |
| `--mode manual` / `auto` | starting fire mode (default manual) |
| **Camera and detection** | |
| `--camera 1` | use a different camera (0 = built-in webcam) |
| `--list-cameras` | list the cameras that work, then quit |
| `--auto-exposure` | leave auto-exposure and white balance ON (default: locked at startup so colors don't drift) |
| `--color green` | use an HSV color preset from `presets.json` instead of the AI detector |
| `--ai-every 3` | run the AI on every 3rd frame and track the balloon by its learned color in between (faster) |
| `--replay FILE` | play a recording instead of the camera (always dry-run) |
| `--no-capture-thread` | read the camera the old way (for comparing lag) |
| **Aiming** | |
| `--manual-tuning` | show the GAIN / LEAD sliders and use them instead of the automatic values |
| `--gain 0.2` | GAIN for `--manual-tuning` (starting slider value), `--no-predict` or `--scale-gain`. Otherwise automatic. |
| `--lead 50` | LEAD in ms for `--manual-tuning`. Otherwise automatic. |
| `--servo-delay 80` | how long (ms) the servos take to react. Added to the measured latency for LEAD. Dry-run simulates it. |
| `--tuning-file FILE` | load tuned values from FILE instead of `config/auto_tune.json` |
| `--no-auto-tune` | ignore `config/auto_tune.json` and use the built-in defaults |
| `--deadzone 10` | pixels of leftover error to ignore (shrinks automatically to fit the lock zone) |
| `--no-predict` | turn off the Kalman filter and feedforward (plain proportional control) |
| `--smoothing 0.4` | only with `--no-predict`: smooth the balloon position |
| `--max-step 10` | slew limit: the most degrees a servo may move per command |
| `--servo-speed 12` | dry-run: simulated servo speed in degrees per frame |
| `--invert-pan`, `--invert-tilt` | flip a servo's direction if it turns away from the balloon |
| `--scale-gain`, `--reference-radius 40` | experimental, off by default: scale GAIN by balloon size. Not recommended (see the note below this table). |
| **Lock zone** | |
| `--lock-fraction 0.5` | lock zone = this × apparent radius, 0.1–1.0 (also a live slider) |
| `--lock-min 6`, `--lock-max 60` | smallest / largest lock zone radius (px) |
| `--min-radius 8` | balloons smaller than this (px radius) are too far: no lock, no laser |
| `--calibrate-distance 150` | hold the balloon 150 cm away and press `c` to calibrate distance |
| **Keys** (click the video window first) | |
| `q` | **kill key**: laser off first, then LED off, glide back to 90,90, quit |
| `m` | switch MANUAL / AUTO |
| `f` | fire button in dry-run (**hold** it) |
| `t` | print a timing report (where lag comes from) |
| `r` | start/stop recording the raw camera feed to `recordings/<date-time>.mp4` |
| `c` | save the distance calibration (with `--calibrate-distance`) |
| `+` / `=`, `-`, `0` | zoom the window in / out / back to the original size (or drag its corners) |

**Why `--scale-gain` isn't recommended.** It raises GAIN for small, far balloons. Its reasoning, "a far balloon's pixel error is a bigger angle error", isn't true. Degrees per pixel depend only on the camera's field of view (about 10.7 px per degree here), not on distance. More gain on small targets mostly adds overshoot exactly when the lock zone is smallest.

`balloon_detect.py --preset green` is the HSV color tuner. It has sliders, a click to pick a color, `p` to print the HSV values, `s` to save the preset, and the same `+` `-` `0` zoom keys.

## Safety

> **Pointer-grade laser only: under 5 mW.** Even a pointer can hurt eyes with a direct hit. Never aim at eyes, people, pets, vehicles or aircraft. **The turret does not check for people:** it aims at anything the detector thinks is a balloon, and the AI can mistake a head for one. So only run it with nobody in its field of view, pointed at a wall or backstop. No shiny (Mylar) balloons, because they reflect the beam.

The laser is decided in **one function**, `laser_should_fire()` in `safety.py`. It fires only if **all** of these are true on that frame:
- a **real detection** (never prediction alone),
- **locked** for 5 frames,
- **no vision fault**,
- the **watchdog** hasn't tripped,
- the window has **focus**,
- the balloon isn't **too far**,
- not **calibrating**,
- not **first-run**,
- the **Arduino is connected**,
- in MANUAL mode, the **button is held**.

On top of that:
- **Master switches:** `LASER_ENABLED = False` in **both** `safety.py` and `turret.ino`. No laser command is sent until both are changed on purpose.
- **Python watchdog:** if the main loop stalls for more than 200 ms, a separate thread sends laser-off.
- **Arduino watchdog:** the Arduino turns the laser and LED off after 500 ms without a command.
- **Kill key:** `q` turns the laser off *first*. Closing the window, Ctrl+C or any error does the same.

**[docs/SAFETY.md](docs/SAFETY.md)** lists every rule, where it's enforced, and the test that proves it. The Arduino rules can also be checked in a free simulator: [docs/WOKWI.md](docs/WOKWI.md).

**Hardware note:** when wiring the laser, put a **10 kΩ resistor from pin 7 to GND**. The pin floats for 1–2 s while the Uno's bootloader runs after every reset, and no code can control it during that time.

## Hardware

**Parts:**
- Arduino Uno with a USB **data** cable
- 2 hobby servos (e.g. SG90) on a pan-tilt bracket
- a separate 5 V supply rated 2 A or more for the servos (3 A+ for MG996R-size servos)
- a laser pointer module under 5 mW, a 2N2222 transistor and a 10 kΩ pull-down resistor
- 1 LED with a 220 Ω resistor (LOCKED light)
- 1 push button (fire button)
- a breadboard and jumper wires

**Wiring:**
- **Servos.** Never power them from the Arduino's 5V pin.
  - Red wire → the supply's +5 V; brown/black wire → the supply's GND.
  - Signal wires: pan → pin **9**, tilt → pin **10**.
  - **Connect the supply's GND to the Arduino's GND** (shared ground).
- **LOCKED LED:** pin **8** → 220 Ω → LED long leg (+); LED short leg (−) → GND.
- **Fire button:** one leg → pin **2**; the diagonally opposite leg → GND. No resistor is needed (the sketch uses the Arduino's built-in pull-up).
- **Laser (later):** pin **7** → 2N2222 transistor, with a **10 kΩ resistor from pin 7 to GND**.

**Bring-up:** `python bringup.py`. It's a step-by-step checklist from "Arduino alone" to "first tracking run with `--first-run`".

## Record, replay and stable colors

- **Record and replay:** press **`r`** to record the raw camera feed to `recordings/` (git-ignored). Replay it with `--replay FILE` to compare settings on exactly the same footage. Add `--no-capture-thread` to process every frame in order.
- **Exposure lock:** at startup, the camera's auto-exposure and auto white balance are **locked**, because they keep shifting colors and break HSV thresholds. They're switched back on when the program quits. Some drivers ignore this; the program prints what was accepted. Use `--auto-exposure` to skip it.

## Finding where lag comes from

Press **`t`** while running. It prints how long each stage takes and names the slowest one.

| What you see | Cause | Fix |
|---|---|---|
| **detect** is the biggest number | the AI is slow (low FPS) | `--ai-every 3`, `--color`, or `AI_IMAGE_SIZE = 416` in `balloon_detect_ai.py` |
| **capture wait** is big and the camera FPS is low | the camera is slow (dim room) | add light; close other camera apps |
| **frame age** much bigger than detect + control | camera buffering | don't use `--no-capture-thread` |
| timing fine, but the turret trails its commands | servo speed | check the power supply and bracket weight |

## Lessons learned: the lag story

The turret "lagged", and it turned out to be **four different problems** that looked like one:

1. **The picture froze while a key was held.** It looked like the code was blocking, but it wasn't. OpenCV reads only one key per frame, so a held key's auto-repeats piled up in Windows' message queue, and Windows doesn't repaint a window until that queue is empty. An experiment showed 100% of frames were never repainted. Reading *every* waiting key each frame fixed it (0%), and `q` no longer waits behind a queue of `f` presses.
2. **Old frames.** Webcam drivers queue frames, so a slow loop works on pictures that are already old. A background thread that keeps only the newest frame cut the frame age from **72 ms to 9 ms** in a test.
3. **The AI is the slowest part** (about 14 FPS on this laptop, against a 30 FPS camera). `--ai-every 3` runs it on every third frame and uses a fast color tracker in between.
4. **A proportional controller always trails a moving target**, by about *speed ÷ GAIN*. Raising GAIN just causes overshoot. The fix is **feedforward** (move at the balloon's own speed) plus a small **LEAD** for the leftover delay. In tests, a swinging balloon went from locked 21% of the time to 100%.

The lesson: **measure before fixing.** The `t` timing report exists because guessing kept pointing at the wrong problem.

A later example: when the servo delay was added, the simulator showed the turret aiming a whole delay *ahead* of the balloon. The controller was comparing the future target with where the servos *were*. Since they always lag, it kept pushing. Comparing with where the servos had already been *told* to go fixed it.

## Known limitations

- **Untested on real servos.** Every result so far comes from simulation and a laptop webcam.
- **The automatic tuning was done in simulation (and recordings) only.** The simulated servos move at a constant speed with a fixed 80 ms delay. **Real servo lag will change the results**: real servos overshoot, have deadband and gear backlash (a little "play" when changing direction), and slow down under load or when the power supply dips. Measure your servos' delay, set `--servo-delay`, record clips with `r`, and re-run `tune_params.py`.
- **A 30 fps webcam and hobby servos cannot follow fast throws.** In the simulator, a tossed heavy object never gets a lock with an 80 ms servo delay. A slow-falling or drifting balloon is fine. No tuning can fix this: a bounce or a direction change can't be reacted to before the camera sees it.
- **Parallax.** The laser and camera sit a few centimeters apart, so they don't point at exactly the same spot. The error is negligible far away but noticeable up close. Calibrating this offset is on the roadmap.
- **The exposure lock may be ignored** by some webcam drivers. The program prints what the driver accepted.
- **About 30 FPS limits fast targets.** The camera (and the AI at about 14 FPS) only sees the balloon so often. A fast balloon moves a long way between frames, and the prediction can only partly make up for it. Motion blur also smears it. The detector handles moderate blur, but a very long streak can still be missed.
- **The AI detector isn't perfect.** It can miss balloons or mistake other round things, like a head, for one. There is no person detection, so keeping people out of the turret's view is up to you.

## Roadmap

1. **Hardware bring-up:** wire the servos, `bringup.py` checklist, `--first-run`, check the directions (`--invert-pan` / `--invert-tilt`), then measure the real servo delay (`--servo-delay`), record clips, and re-run `tune_params.py`.
2. **Calibration:** measure the camera's real field of view, and the pixel offset between the laser dot and the camera center (parallax) at a few distances.
3. **Laser (pointer-grade only):** wire it with the 10 kΩ pull-down, test it with `LASER_ENABLED` on only in the Wokwi copy first, then on hardware with nobody in the turret's view.
4. **Optional pop mechanism:** a small servo-driven pin or needle to actually pop the balloon, gated by the same lock rules.
5. **Optional gyro/IMU:** sense the turret's real motion, to correct for servo lag and backlash.

## Files

| File | What it is |
|---|---|
| `turret_tracker.py` | **start here:** the entry point (`python turret_tracker.py --dry-run`) |
| `tracker_app.py` | the main loop; its top comment explains the whole system |
| `safety.py` | `LASER_ENABLED`, `laser_should_fire()`, the watchdog, the startup banner |
| `control.py` | aiming math, the lock zone, the motion filter, automatic gain / step / deadzone, tunable ranges |
| `vision.py` | the camera thread, record/replay, the AI / color / hybrid detectors |
| `serial_link.py` | the Arduino link, laser/LED outputs, the recenter glide |
| `display.py`, `ui.py` | letterboxed windows, the info bar, the fire key, the timing report |
| `cli.py`, `config.py`, `camera.py`, `selftest.py` | flags, config validation, the camera, `--self-test` |
| `balloon_detect.py` | HSV color tuner and presets |
| `balloon_detect_ai.py` | AI detection on its own (Phase 1) |
| `turret_sim.py`, `servo_test.py` | the Phase 2 on-screen simulator; type commands to the Arduino by hand |
| `bringup.py` | hardware bring-up checklist |
| `turret/turret.ino` | the Arduino sketch |
| `presets.json` | HSV color presets |
| `simulator.py` | synthetic falling / bouncing / thrown balloons with motion blur (`python simulator.py --show bounce`) |
| `headless.py`, `tune_params.py` | run the tracker without a window and score it; the auto-tuner |
| `config/auto_tune.json` | the tuned values the tracker loads (written by `tune_params.py`) |
| `docs/SAFETY.md`, `docs/WOKWI.md` | the safety rule map; testing the sketch in a simulator |
| `docs/AUTO_TUNE.md` | how auto-tuning works, before/after scores, how to re-run it |
| `tests/` | 100+ tests: `python -m unittest -v` (fake camera, fake windows, fake Arduino) |
