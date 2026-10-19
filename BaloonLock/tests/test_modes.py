"""Task 2: AUTO/MANUAL modes, LOCK LED, fire key, Arduino LED/button protocol."""

import glob
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
from unittest import mock

from tests.fakes import patch_all, REPO, box_at, run_tracker, tt

STILL = lambda i: box_at(325, 243, 30)


class Modes(unittest.TestCase):

    def test_default_mode_is_manual(self):
        with mock.patch.object(sys, "argv", ["turret_tracker.py", "--dry-run"]):
            self.assertEqual(tt.parse_args().mode, "manual")

    def test_m_switches_mode_and_laser_obeys(self):
        r = run_tracker(["--dry-run", "--mode", "auto"], STILL, frames=30, keys={15: ord("m")})
        self.assertIn("MODE: AUTO", r.lines_at(14))
        self.assertIn("MODE: MANUAL", r.lines_at(16))
        self.assertIn(14, r.laser_on_frames())
        self.assertFalse(r.laser_on_frames() & set(range(15, 30)),
                         "MANUAL without the button: laser must go off right away")

    def test_lock_led_follows_lock_and_commands_only_on_change(self):
        def det(i):
            return None if 20 <= i <= 22 else STILL(i)
        with patch_all("LASER_ENABLED", True):          # test only!
            r = run_tracker(["--port", "COM9"], det, frames=40)
        lines = r.serial_lines
        # startup sync D0, D1 when locked, D0 when lost, D1 after re-lock, D0 when the fake
        # camera runs out (vision fault), and one final D0 that shutdown ALWAYS sends.
        led = [l for l in lines if l in ("D0", "D1")]
        self.assertEqual(led, ["D0", "D1", "D0", "D1", "D0", "D0"])
        lasers = [l for l in lines if l in ("L0", "L1")]
        self.assertEqual(lasers.count("L1"), 2, "L1 only when the state changes, never per frame")


class FireKeyMethods(unittest.TestCase):

    def test_pynput_true_key_up_and_focus(self):
        try:
            from pynput.keyboard import KeyCode
        except ImportError:
            self.skipTest("pynput not installed")
        with patch_all("window_has_focus", lambda name: True):
            fire = tt.FireKey("Turret Tracker")
            try:
                self.assertTrue(fire.method.startswith("pynput"))
                f = KeyCode.from_char("f")
                fire._on_press(f)
                self.assertTrue(fire.is_held(True))
                fire._on_release(f)
                self.assertFalse(fire.is_held(True), "release must be instant")
                fire._on_press(f)
                self.assertFalse(fire.is_held(False), "focus loss = released")
                self.assertFalse(fire.is_held(True), "must re-press after focus comes back")
            finally:
                fire.stop()

    def test_fallback_without_pynput(self):
        with mock.patch.dict(sys.modules, {"pynput": None}):
            fire = tt.FireKey("Turret Tracker")
        self.assertIn("fallback", fire.method)
        fire.saw_keys([ord("f")])
        self.assertTrue(fire.is_held(True))
        fire.last_f_event -= tt.FIRE_KEY_FALLBACK_HOLD_S + 0.05
        self.assertFalse(fire.is_held(True))


class ArduinoProtocol(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "turret", "turret.ino"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_pins_and_pullup(self):
        self.assertIn("const int LED_PIN = 8;", self.src)
        self.assertIn("const int BUTTON_PIN = 2;", self.src)
        self.assertIn("pinMode(BUTTON_PIN, INPUT_PULLUP);", self.src)
        self.assertRegex(self.src, r"DEBOUNCE_MS = \d+;")

    def test_commands_and_events(self):
        for cmd in ('"D1"', '"D0"', '"L1"', '"L0"'):
            self.assertIn(f"strcmp(text, {cmd})", self.src)
        self.assertIn('"B1" : "B0"', self.src)

    def test_sketch_compiles(self):
        cli = shutil.which("arduino-cli") or next(iter(glob.glob(os.path.expandvars(
            r"%LOCALAPPDATA%\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"))), None)
        if not cli:
            self.skipTest("arduino-cli not found")
        result = subprocess.run([cli, "compile", "--fqbn", "arduino:avr:uno",
                                 os.path.join(REPO, "turret")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
