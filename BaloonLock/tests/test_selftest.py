"""--self-test: PASS/FAIL per item, exit code 0/1, no hardware needed."""

import contextlib
import io
import unittest
from unittest import mock

import numpy as np

from tests.fakes import patch_all, run_tracker
import selftest


class GoodCamera:
    def read(self):
        return True, np.zeros((480, 640, 3), np.uint8)

    def release(self):
        pass


def run_quietly(**patches):
    out = io.StringIO()
    with contextlib.ExitStack() as stack:
        for target, value in patches.items():
            stack.enter_context(mock.patch(target.replace("__", "."), value))
        with contextlib.redirect_stdout(out):
            code = selftest.run_self_test(0)
    return code, out.getvalue()


FAKE_MODEL = {"balloon_detect_ai__load_model": lambda: "model",
              "balloon_detect_ai__get_detections": lambda model, frame, conf: []}


class SelfTest(unittest.TestCase):

    def test_all_pass(self):
        code, out = run_quietly(camera__open_camera=lambda i: GoodCamera(), **FAKE_MODEL)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("[PASS]"), 5)
        self.assertIn("camera 0: 640x480", out)
        self.assertIn("LASER_ENABLED is False", out)

    def test_camera_failure_is_reported(self):
        code, out = run_quietly(camera__open_camera=lambda i: None, **FAKE_MODEL)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] camera", out)
        self.assertIn("[PASS] AI model", out)          # other checks still run

    def test_model_failure_is_reported_not_crashed(self):
        def broken():
            raise SystemExit(1)                         # what load_model() does on failure
        code, out = run_quietly(camera__open_camera=lambda i: GoodCamera(),
                                balloon_detect_ai__load_model=broken)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] AI model", out)

    def test_laser_switch_on_is_a_failure(self):
        with patch_all("LASER_ENABLED", True):
            code, out = run_quietly(camera__open_camera=lambda i: GoodCamera(), **FAKE_MODEL)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] laser safety switches", out)

    def test_flag_runs_it_and_exits(self):
        model = [mock.patch(k.replace("__", "."), v) for k, v in FAKE_MODEL.items()]
        with self.assertRaises(SystemExit) as ctx:
            run_tracker(["--dry-run", "--self-test"], frames=3, extra_patches=model)
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
