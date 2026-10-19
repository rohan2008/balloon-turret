"""
Safety rules. These must ALWAYS pass - if one fails, don't use the laser.

Run:  python -m unittest discover -s tests -v
"""

import os
import re
import unittest
from unittest import mock

from tests.fakes import patch_all, FRAME_DT, REPO, box_at, run_tracker, tt

STILL = lambda i: box_at(325, 243, 30)          # balloon ~6 px from center, radius 30


class LaserDecisionFunction(unittest.TestCase):
    """laser_should_fire() is the single place the laser is decided. Check that
    EACH condition on its own is enough to keep the laser off."""

    ALL_OK = dict(mode="AUTO", real_detection=True, locked=True, vision_fault=None,
                  focused=True, too_far=False, calibrating=False, button_held=False,
                  laser_disabled=False, link_ok=True)

    def test_fires_when_everything_is_ok(self):
        self.assertTrue(tt.laser_should_fire(**self.ALL_OK))
        self.assertTrue(tt.laser_should_fire(**{**self.ALL_OK, "mode": "MANUAL", "button_held": True}))

    def test_each_condition_alone_blocks_the_laser(self):
        blockers = {
            "prediction only (no real detection)": {"real_detection": False},
            "not locked": {"locked": False},
            "vision fault": {"vision_fault": "detector error"},
            "window not focused": {"focused": False},
            "target too far/small": {"too_far": True},
            "calibrating": {"calibrating": True},
            "manual without button": {"mode": "MANUAL", "button_held": False},
            "first-run (laser disabled)": {"laser_disabled": True},
            "Arduino disconnected": {"link_ok": False},
        }
        for name, change in blockers.items():
            with self.subTest(name):
                self.assertFalse(tt.laser_should_fire(**{**self.ALL_OK, **change}), name)

    def test_button_alone_never_fires(self):
        # The fire button can't fire unless every other condition passes
        for change in ({"locked": False}, {"real_detection": False},
                       {"vision_fault": "x"}, {"focused": False}):
            with self.subTest(change):
                self.assertFalse(tt.laser_should_fire(
                    **{**self.ALL_OK, "mode": "MANUAL", "button_held": True, **change}))


class LaserRules(unittest.TestCase):

    def test_laser_enabled_is_false_in_python(self):
        self.assertFalse(tt.LASER_ENABLED, "LASER_ENABLED must stay False in turret_tracker.py")

    def test_fires_only_with_real_detection_and_off_same_frame_lost(self):
        # Moving balloon (so the Kalman filter has a velocity and keeps
        # predicting), detection missing on frames 20-23.
        def det(i):
            return None if 20 <= i <= 23 else box_at(300 + i, 240, 40)
        r = run_tracker(["--dry-run"], det, frames=50)
        on = r.laser_on_frames()
        self.assertIn(19, on, "should be firing just before the balloon is lost")
        self.assertIn((20, False), r.laser_events, "laser must go OFF on the very frame it's lost")
        for i in range(20, 24):
            self.assertNotIn(i, on, f"laser on during prediction-only frame {i}")
        self.assertIn("HOLDING (predicting", r.lines_at(21), "turret should be coasting on prediction")
        # It may only come back after 5 fresh locked frames (24..28)
        self.assertFalse(on & set(range(24, 28)), "must re-lock for 5 real frames first")

    def test_vision_fault_when_model_fails_to_load(self):
        broken = mock.patch("balloon_detect_ai.load_model", side_effect=OSError("model missing"))
        r = run_tracker(["--dry-run"], frames=20, real_ai=True, extra_patches=[broken])
        self.assertEqual(r.laser_on_frames(), set())
        self.assertIn(tt.VISION_FAULT_MESSAGE, r.lines_at(10))
        self.assertIn("AI model failed to load", r.out)

    def test_vision_fault_when_detector_errors(self):
        def det(i):
            if 20 <= i <= 22:
                raise RuntimeError("simulated detector crash")
            return STILL(i)
        r = run_tracker(["--dry-run"], det, frames=40)
        on = r.laser_on_frames()
        self.assertIn(19, on)
        self.assertFalse(on & {20, 21, 22})
        self.assertIn(tt.VISION_FAULT_MESSAGE, r.lines_at(21))
        self.assertTrue(on & set(range(27, 40)), "should recover after the errors stop")

    def test_too_small_target_never_fires(self):
        r = run_tracker(["--dry-run"], lambda i: box_at(322, 241, 6), frames=30)
        self.assertEqual(r.laser_on_frames(), set())
        self.assertIn("TOO FAR", r.lines_at(20))

    def test_losing_focus_turns_laser_off(self):
        r = run_tracker(["--dry-run"], STILL, frames=40, focus_fn=lambda i: not 20 <= i <= 25)
        on = r.laser_on_frames()
        self.assertIn(19, on)
        self.assertFalse(on & set(range(20, 26)))
        self.assertIn(30, on)

    def test_manual_mode_fires_only_while_button_held(self):
        r = run_tracker(["--dry-run", "--mode", "manual"], STILL, frames=40,
                        fire_fn=lambda i: 10 <= i <= 20)
        self.assertEqual(r.laser_on_frames(), set(range(10, 21)))

    def test_kill_key_turns_laser_off_before_recentering(self):
        with patch_all("LASER_ENABLED", True):        # test only!
            r = run_tracker(["--port", "COM9"], STILL, frames=60, keys={30: ord("q")})
        lines = r.serial_lines
        self.assertIn("L1", lines, "laser should have been on before 'q'")
        last_l0 = len(lines) - 1 - lines[::-1].index("L0")
        last_d0 = len(lines) - 1 - lines[::-1].index("D0")
        self.assertEqual(lines[-1], "90,90", "recenter must be the last command")
        self.assertLess(last_l0, last_d0)
        self.assertLess(r.out.index("LASER OFF"), r.out.index("Recentering"))

    def test_no_laser_commands_when_laser_disabled(self):
        r = run_tracker(["--port", "COM9", "--mode", "manual"], STILL, frames=40,
                        button_fn=lambda i: i >= 8)
        self.assertTrue(r.laser_on_frames(), "logic says ON...")
        self.assertFalse([l for l in r.serial_lines if l.startswith("L")],
                         "...but with LASER_ENABLED False no L command may ever be sent")


class ArduinoSketchRules(unittest.TestCase):
    """We can't run the Arduino code here, so these check the sketch's text
    for the safety rules (and compile it if arduino-cli is available)."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "turret", "turret.ino"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_laser_enabled_false(self):
        self.assertRegex(self.src, r"const bool LASER_ENABLED = false;")

    def test_set_laser_forces_off_when_disabled(self):
        body = re.search(r"void setLaser\(bool on\) \{(.*?)\n\}", self.src, re.S).group(1)
        self.assertIn("if (!LASER_ENABLED)", body)
        self.assertIn("on = false", body)

    def test_l1_refused_when_disabled(self):
        body = re.search(r"void handleLaserCommand\(bool wantOn\) \{(.*?)\n\}", self.src, re.S).group(1)
        self.assertIn("wantOn && !LASER_ENABLED", body)
        self.assertIn("ERR laser disabled", body)

    def test_500ms_timeout_turns_laser_and_led_off(self):
        self.assertRegex(self.src, r"COMMAND_TIMEOUT_MS = 500;")
        block = re.search(r"if \(\(laserOn \|\| ledOn\) && \(millis\(\) - lastCommandMs > "
                          r"COMMAND_TIMEOUT_MS\)\) \{(.*?)\}", self.src, re.S).group(1)
        self.assertIn("setLaser(false)", block)
        self.assertIn("setLed(false)", block)


if __name__ == "__main__":
    unittest.main()


class BannerAndWatchdog(unittest.TestCase):

    def test_safety_banner(self):
        r = run_tracker(["--dry-run"], STILL, frames=3)
        for item in ("SAFETY STATUS", "LASER_ENABLED (Python) : False",
                     "LOOP WATCHDOG          : ON (200 ms)", "KILL KEY 'q'"):
            self.assertIn(item, r.out)

    def test_watchdog_sends_L0_during_a_stall(self):
        import threading
        from tests.fakes import FakeSerial
        seen = {}

        def det(i):
            if i == 20:
                before = list(FakeSerial.instances[-1].lines)
                threading.Event().wait(0.35)            # main loop stuck for 350 ms (real time)
                seen["during"] = FakeSerial.instances[-1].lines[len(before):]
            return STILL(i)
        with patch_all("LASER_ENABLED", True):          # test only!
            r = run_tracker(["--port", "COM9"], det, frames=40)
        self.assertIn("L0", seen["during"], "the watchdog thread must send L0 while the loop is stuck")
        self.assertIn(tt.WATCHDOG_MESSAGE, r.lines_at(20))
        self.assertIn(tt.WATCHDOG_MESSAGE, r.out)
        on = r.laser_on_frames()
        self.assertIn(19, on)
        self.assertFalse(on & set(range(20, 24)), "lock must be earned again after a stall")
        self.assertIn(30, on)

    def test_watchdog_quiet_when_loop_is_fast(self):
        r = run_tracker(["--dry-run"], STILL, frames=40)
        self.assertNotIn(tt.WATCHDOG_MESSAGE, r.out)


class ArduinoSketchRulesMore(unittest.TestCase):
    """More sketch rules, checked by reading the code (no hardware here)."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "turret", "turret.ino"), encoding="utf-8") as f:
            cls.src = f.read()

    def body(self, signature):
        return re.search(re.escape(signature) + r" \{(.*?)\n\}", self.src, re.S).group(1)

    def test_angles_clamped_20_to_160(self):
        self.assertIn("const int MIN_ANGLE = 20;", self.src)
        self.assertIn("const int MAX_ANGLE = 160;", self.src)
        handle = self.body("void handleLine(char* text)")
        self.assertIn("pan = constrain(pan, MIN_ANGLE, MAX_ANGLE);", handle)
        self.assertIn("tilt = constrain(tilt, MIN_ANGLE, MAX_ANGLE);", handle)
        # the clamp happens BEFORE the servos are written
        self.assertLess(handle.index("constrain(pan"), handle.index("panServo.write"))

    def test_malformed_lines_rejected_before_moving(self):
        handle = self.body("void handleLine(char* text)")
        self.assertGreaterEqual(handle.count("reportBadLine(text);"), 4)
        last_reject = handle.rindex("reportBadLine(text);")
        self.assertLess(last_reject, handle.index("panServo.write"))

    def test_boot_state(self):
        setup = self.body("void setup()")
        self.assertIn("const int START_ANGLE = 90;", self.src)
        self.assertLess(setup.index("digitalWrite(LASER_PIN, LOW);"), setup.index("Serial.begin"))
        self.assertIn("setLaser(false);", setup)
        self.assertIn("setLed(false);", setup)
        self.assertLess(setup.index("panServo.write(START_ANGLE);"), setup.index("panServo.attach"))
        self.assertLess(setup.index("tiltServo.write(START_ANGLE);"), setup.index("tiltServo.attach"))

    def test_line_buffer_cannot_overflow(self):
        loop = self.body("void loop()")
        self.assertIn("lineLength < LINE_MAX - 1", loop)
        self.assertIn("lineTooLong = true;", loop)
        code = re.sub(r"/\*.*?\*/", "", self.src, flags=re.S)       # drop /* */ comments
        code = re.sub(r"//.*", "", code)                               # drop // comments
        self.assertIsNone(re.search(r"\bString\b", code), "no String class (memory fragmentation)")

    def test_rules_block_at_top(self):
        self.assertTrue(self.src.lstrip().startswith("/*\n  SAFETY RULES"))
        self.assertIn("10 kOhm resistor from pin 7 to GND", self.src)
