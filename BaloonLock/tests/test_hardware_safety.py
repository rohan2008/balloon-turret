"""FP Task 2: safe defaults for the first hardware run - --first-run, slew
limit, gentle recenter, and surviving a pulled USB cable."""

import re
import unittest
from unittest import mock

from tests.fakes import patch_all, box_at, run_tracker, tt


def sent_angles(out, before="Recentering"):
    """All "pan,tilt" commands printed in dry-run, as (pan, tilt) tuples."""
    text = out[:out.index(before)] if before and before in out else out
    return [tuple(map(int, m.groups())) for m in re.finditer(r"would send: (\d+),(\d+)", text)]


class FirstRun(unittest.TestCase):

    def run_first(self, extra=()):
        # Balloon far to the right and high: the controller wants big angles
        return run_tracker(["--dry-run", "--first-run", "--gain", "0.6", "--lead", "4", *extra],
                           lambda i: box_at(630, 20, 30), frames=120)

    def test_laser_disabled_and_banner(self):
        r = self.run_first()
        self.assertEqual(r.laser_on_frames(), set(), "first run: laser never fires")
        self.assertIn("FIRST RUN MODE", r.out)
        self.assertIn("FIRST RUN MODE", r.lines_at(60))
        self.assertIn("LASER: DISABLED", r.lines_at(60))

    def test_low_gain_no_lead_even_if_asked_for_more(self):
        r = self.run_first()
        self.assertIn("GAIN 0.10", r.lines_at(60))
        self.assertIn("LEAD 0", r.lines_at(60))

    def test_angles_limited_and_slow(self):
        angles = sent_angles(self.run_first().out)
        self.assertTrue(angles)
        for pan, tilt in angles:
            self.assertTrue(45 <= pan <= 135 and 45 <= tilt <= 135, (pan, tilt))
        steps = [max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(angles, angles[1:])]
        self.assertLessEqual(max(steps), 3 + 1, "max 3 deg per command (+1 for rounding)")


class SlewAndRecenter(unittest.TestCase):

    def test_default_slew_limit(self):
        r = run_tracker(["--dry-run", "--gain", "1.0"], lambda i: box_at(630, 20, 30), frames=40)
        angles = [(90, 90)] + sent_angles(r.out)
        steps = [max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(angles, angles[1:])]
        self.assertLessEqual(max(steps), tt.DEFAULT_MAX_STEP_DEG + 1)

    def test_quit_glides_back_to_center(self):
        r = run_tracker(["--dry-run"], lambda i: box_at(560, 240, 30), frames=60,
                        keys={50: ord("q")})
        last_before = sent_angles(r.out)[-1]
        self.assertGreater(abs(last_before[0] - 90), 10, "should be aimed well off-center first")
        glide = sent_angles(r.out[r.out.index("Recentering"):], before=None)
        self.assertGreater(len(glide), 15, "recenter should take many small steps (~1 s)")
        self.assertEqual(glide[-1], (90, 90))
        steps = [abs(a[0] - b[0]) for a, b in zip([last_before] + glide, glide)]
        self.assertLessEqual(max(steps), 3)


class PulledCable(unittest.TestCase):

    def test_disconnect_reconnect_and_no_laser_until_relock(self):
        still = lambda i: box_at(325, 243, 30)
        with patch_all("LASER_ENABLED", True):          # test only!
            r = run_tracker(["--port", "COM9"], still, frames=300, unplug_after=40)
        from tests.fakes import FakeSerial
        self.assertIn(tt.DISCONNECTED_MESSAGE, r.out)
        # every camera frame was shown (plus the vision-fault frames after the fake camera ends)
        self.assertGreaterEqual(len(r.frames), 300, "display must keep running the whole time")
        disconnected = [f["i"] for f in r.frames if tt.DISCONNECTED_MESSAGE in f["text"]]
        self.assertTrue(disconnected, "info bar should say ARDUINO DISCONNECTED")
        self.assertFalse(r.laser_on_frames() & set(disconnected), "laser must be off while disconnected")
        self.assertGreaterEqual(len(FakeSerial.instances), 2, "should have reconnected")
        new_port = FakeSerial.instances[-1].lines
        lasers = [i for i, line in enumerate(new_port) if line in ("L0", "L1")]
        self.assertEqual(new_port[lasers[0]], "L0", "first laser command after reconnect must be L0")
        self.assertIn("L1", new_port, "may fire again after re-locking")
        angle_lines_before_l1 = [l for l in new_port[:new_port.index("L1")] if "," in l]
        self.assertGreaterEqual(len(angle_lines_before_l1), tt.LOCK_FRAMES - 1,
                                "lock must be re-earned (5 frames) before any L1")


if __name__ == "__main__":
    unittest.main()


class ButtonOnDisconnect(unittest.TestCase):

    def test_unplugging_releases_the_fire_button(self):
        link = tt.TurretLink.__new__(tt.TurretLink)      # no real port needed
        link.ser = None
        link.ready = True
        link.button_pressed = True
        link.close()
        self.assertFalse(link.button_pressed, "an unknown button state must count as NOT pressed")
