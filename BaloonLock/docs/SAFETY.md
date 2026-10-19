# Safety rules

Every laser-safety rule in the project: **where it's enforced** (file → function) and **which test proves it**. All tests run with `python -m unittest -v` and use a fake camera, fake windows and a fake Arduino. No hardware is needed.

> **The laser is pointer-grade (< 5 mW).** Never aim it at eyes, people, pets, vehicles or aircraft, and never use a stronger laser. **There is no person detection**: the turret aims at anything the detector takes for a balloon (the AI can mistake a head for one). Run it only with nobody in its field of view, pointed at a wall or backstop.

## The core idea

The laser is decided in **exactly one place**: `laser_should_fire()` in `safety.py`. It returns True only if **every** condition below holds on the current frame. A separate master switch, `LASER_ENABLED`, decides whether a laser command may be sent at all. It's `False` in **both** `safety.py` and `turret/turret.ino`.

`tests/test_laser_path.py` proves that `laser_should_fire()` is the only way to produce an `L1` (laser on) command:
- **By reading the source code:** the text `"L1"` exists in one function only, and every route to it comes from `laser_should_fire()`.
- **At runtime:** a fake Arduino fails the test if `L1` arrives without a "yes" from `laser_should_fire()`.

## Python side (laptop)

| # | Rule | Enforced in | Covered by test |
|---|---|---|---|
| 1 | **Master switch:** with `LASER_ENABLED = False`, no laser command is ever sent | `safety.py` → `LASER_ENABLED`; checked in `serial_link.py` → `set_laser()`, `TurretOutputs._force_laser_off()`, `all_off()` | `test_safety.test_laser_enabled_is_false_in_python`, `test_no_laser_commands_when_laser_disabled`; `test_modes.test_lock_led_follows_lock_...` |
| 2 | **One decision point:** only `laser_should_fire()` can turn the laser on | `safety.py` → `laser_should_fire()`; called only in `tracker_app.py` → `main()` | `test_laser_path.*` (static + runtime); `test_safety.LaserDecisionFunction.*` |
| 3 | **Real detection only:** fires only with a real detection *this frame*, never from Kalman prediction alone | `laser_should_fire(real_detection)`; `tracker_app.main()` drops the lock when there's no detection | `test_safety.test_fires_only_with_real_detection_and_off_same_frame_lost`, `test_each_condition_alone_blocks_the_laser` |
| 4 | **Off when lost:** laser OFF on the same frame the balloon is lost | `tracker_app.main()` (lock reset) → `laser_should_fire(locked)` | `test_fires_only_with_real_detection_and_off_same_frame_lost` |
| 5 | **Lock before fire:** 5 frames in a row inside the lock zone; the zone scales with balloon size; exit zone = 1.8 × enter zone | `tracker_app.main()` (lock logic); `control.py` → `lock_zone_state()`, `RadiusSmoother` | `test_lock_zone.test_five_frames_and_hysteresis_with_dynamic_zone`, `test_zone_*` |
| 6 | **Vision fault:** stale frame (> 0.3 s), no frame for 0.5 s, detector error, or model failed to load → laser disabled ("VISION FAULT - LASER DISABLED") | `tracker_app.main()`; `vision.make_ai_detector()` (load failure becomes a vision fault, not a crash) | `test_safety.test_vision_fault_when_model_fails_to_load`, `_when_detector_errors`; `test_modes` (stale frames) |
| 7 | **Watchdog:** if the main loop stalls > 200 ms, a separate thread sends `L0` and shows "WATCHDOG - LASER OFF"; the lock must then be re-earned | `safety.py` → `LoopWatchdog`; `serial_link.TurretOutputs.watchdog_off()` | `test_safety.test_watchdog_sends_L0_during_a_stall` (real 350 ms stall), `test_watchdog_quiet_when_loop_is_fast` |
| 8 | **Window focus:** losing focus turns the laser off (the `q` kill key only works while the window has focus) | `laser_should_fire(focused)`; immediate re-check after keys in `tracker_app.main()` | `test_safety.test_losing_focus_turns_laser_off`; `test_modes.test_pynput_true_key_up_and_focus` |
| 9 | **Too far:** balloon apparent radius < `--min-radius` (default 8 px) → no lock, no laser | `tracker_app.main()` (`too_far`) → `laser_should_fire(too_far)` | `test_safety.test_too_small_target_never_fires` |
| 10 | **MANUAL mode:** fires only while the fire button is held; release = off immediately | `laser_should_fire(mode, button_held)`; `ui.FireKey` (dry-run `f`), `serial_link.TurretLink.poll()` (`B1`/`B0`) | `test_safety.test_manual_mode_fires_only_while_button_held`; `test_modes.test_m_switches_mode_and_laser_obeys` |
| 11 | **The button alone can never fire:** it still needs lock + detection + no fault + focus | `laser_should_fire()` | `test_safety.test_button_alone_never_fires` |
| 12 | **Unplugging:** an unplugged Arduino counts as button *released* | `serial_link.TurretLink.close()` | `test_hardware_safety.test_unplugging_releases_the_fire_button` |
| 13 | **Kill key `q`:** laser off FIRST, then LED off, then recenter, then quit. Also runs on window close, Ctrl+C or any error (`finally`) | `tracker_app.main()`; `serial_link.TurretOutputs.all_off()`, `ramp_to_center()` | `test_safety.test_kill_key_turns_laser_off_before_recentering` |
| 14 | **Calibrating:** the laser stays off while you hold the balloon for `--calibrate-distance` | `laser_should_fire(calibrating)` | `test_lock_zone.test_uncalibrated_then_calibrate_then_measure` |
| 15 | **First run:** `--first-run` disables the laser completely | `laser_should_fire(laser_disabled)`; `cli.parse_args()` | `test_hardware_safety.test_laser_disabled_and_banner` |
| 16 | **Arduino disconnected:** laser off on the same frame; after reconnect the first command is `L0` and the lock must be re-earned before any `L1` | `laser_should_fire(link_ok)`; `serial_link.TurretOutputs.sync()`; `tracker_app.main()` | `test_hardware_safety.test_disconnect_reconnect_and_no_laser_until_relock` |
| 17 | **Commands only on change:** laser/LED commands are sent only when their state changes (never per frame or per key) | `serial_link.TurretOutputs.set()` | `test_modes.test_lock_led_follows_lock_and_commands_only_on_change` |
| 18 | **Gentle motion:** slew limit (`--max-step`, default 10°/command), angles clamped (20–160, or 45–135 in first run), glide back to 90,90 on quit | `tracker_app.main()` (send block); `serial_link.ramp_to_center()` | `test_hardware_safety.test_default_slew_limit`, `test_angles_limited_and_slow`, `test_quit_glides_back_to_center` |
| 19 | **Startup banner:** lists every safety and whether it's ON/OFF | `safety.py` → `safety_banner()` | `test_safety.test_safety_banner` |
| 20 | **Auto-tuning can't loosen safety:** `config/auto_tune.json` (and `--tuning-file`) may only set whitelisted tuning values inside fixed ranges; any other key (e.g. `LASER_ENABLED`, lock rules) is ignored with a warning; the step limit never exceeds `--max-step`; relaxed color thresholds never go below fixed floors | `config.load_auto_tune()`; `control.TUNING_RANGES`, `auto_step_limit()`; `vision.relaxed_settings()` | `test_auto_tune.TuningCannotLoosenSafety` (whitelist, extreme corners), `RuntimeProofWithTunedFile` |

## Arduino side (`turret/turret.ino`)

> **Verified by code reading and compile only, not by hardware.** The sketch can't run on the laptop. The tests read the sketch's code and compile it with the Arduino IDE's built-in compiler. [WOKWI.md](WOKWI.md) explains how to check each rule in a free online Arduino simulator.

| # | Rule | Enforced in (`turret.ino`) | Covered by test |
|---|---|---|---|
| A1 | `LASER_ENABLED = false`: pin 7 can never go HIGH | `setLaser()` forces OFF when disabled | `test_safety.test_laser_enabled_false`, `test_set_laser_forces_off_when_disabled` |
| A2 | `L1` refused while `LASER_ENABLED` is false (replies `ERR laser disabled`) | `handleLaserCommand()` | `test_safety.test_l1_refused_when_disabled` |
| A3 | 500 ms without a valid command → laser AND LED off | `loop()` timeout block | `test_safety.test_500ms_timeout_turns_laser_and_led_off` |
| A4 | Fire button only *reports* (`B1`/`B0`); it never switches the laser itself | `checkButton()` / `sendButtonState()` | `test_modes.test_commands_and_events` |
| A5 | Angles clamped to 20–160 *before* the servos are written (huge numbers saturate, then clamp) | `handleLine()` (`constrain`) | `test_safety.test_angles_clamped_20_to_160` |
| A6 | Malformed lines are rejected before anything moves | `handleLine()` / `reportBadLine()` | `test_safety.test_malformed_lines_rejected_before_moving` |
| A7 | Boot state: laser pin LOW first, LED off, servos written to 90 *before* attach (no twitch) | `setup()` | `test_safety.test_boot_state` |
| A8 | A serial flood can't crash it: fixed-size line buffer, overlong lines dropped, no `String` class | `loop()` | `test_safety.test_line_buffer_cannot_overflow` |
| A9 | Sketch compiles for an Arduino Uno (5,212 bytes flash = 16%, 461 bytes RAM = 22%) | — | `test_modes.test_sketch_compiles` (skipped if arduino-cli isn't found) |

**Hardware note: the laser pin during reset.** Every time the laptop opens the serial port, the Uno resets, and its bootloader runs for about 1–2 s *before* `setup()`. During that time pin 7 isn't driven (it "floats"), so the 2N2222 transistor could pick up noise and partly switch the laser on. Software can't prevent this. **Add a 10 kΩ pull-down resistor from pin 7 to GND** when wiring the laser.

To check rules A1–A8 yourself in a free simulator, follow [WOKWI.md](WOKWI.md).
