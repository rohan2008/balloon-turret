"""Config files are validated on load: clear messages instead of stack traces."""

import contextlib
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tests.fakes import REPO, box_at, patch_all, run_tracker
import balloon_detect as bd
import config


class Presets(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "presets.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text if isinstance(text, str) else json.dumps(text))

    GOOD = {"h_min": 40, "h_max": 85, "s_min": 80, "s_max": 255, "v_min": 50, "v_max": 255,
            "min_area": 500}

    def test_real_presets_file_is_valid(self):
        presets = bd.load_presets(os.path.join(REPO, "presets.json"))
        self.assertIn("red", presets)

    def test_broken_json_says_where(self):
        self.write('{"green": {"h_min": 40,, }}')
        with self.assertRaises(config.ConfigError) as ctx:
            bd.load_presets(self.path)
        self.assertIn("line 1", str(ctx.exception))
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_missing_and_bad_values_listed(self):
        bad = dict(self.GOOD)
        del bad["v_max"]
        bad["h_min"] = 200          # hue only goes to 179
        bad["s_min"] = "high"       # not a number
        self.write({"green": bad})
        with self.assertRaises(config.ConfigError) as ctx:
            bd.load_presets(self.path)
        message = str(ctx.exception)
        self.assertIn("missing 'v_max'", message)
        self.assertIn("'h_min' = 200 is outside 0-179", message)
        self.assertIn("'s_min' must be a whole number", message)

    def test_min_bigger_than_max(self):
        self.write({"green": {**self.GOOD, "s_min": 200, "s_max": 100}})
        with self.assertRaises(config.ConfigError):
            bd.load_presets(self.path)

    def test_red_wraparound_is_allowed(self):
        self.write({"red": {**self.GOOD, "h_min": 170, "h_max": 8}, "_comment": "notes are fine"})
        self.assertEqual(list(bd.load_presets(self.path)), ["red"])

    def test_save_refuses_bad_values(self):
        with self.assertRaises(config.ConfigError):
            bd.save_preset("bad", {**self.GOOD, "v_min": -5}, self.path)
        self.assertFalse(os.path.exists(self.path))

    def test_tracker_exits_cleanly_on_bad_presets(self):
        broken = mock.patch("balloon_detect.load_presets",
                            side_effect=config.ConfigError("presets.json has problems: test"))
        with self.assertRaises(SystemExit):
            r = run_tracker(["--dry-run", "--color", "red"], frames=3, extra_patches=[broken])


class Calibration(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "turret_config.json")
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch_all("CONFIG_FILE", self.path))

    def tearDown(self):
        self.stack.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bad_k_is_reported(self):
        with open(self.path, "w") as f:
            json.dump({"distance_calibration": {"ai": {"k": -3, "frame_width": 640}}}, f)
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_config()
        self.assertIn("positive number for 'k'", str(ctx.exception))

    def test_tracker_warns_and_keeps_running(self):
        with open(self.path, "w") as f:
            f.write("{ this is not json")
        r = run_tracker(["--dry-run"], lambda i: box_at(322, 241, 30), frames=5)
        self.assertIn("WARNING: turret_config.json is not valid JSON", r.out)
        self.assertIn("uncalibrated", r.lines_at(4))


if __name__ == "__main__":
    unittest.main()
