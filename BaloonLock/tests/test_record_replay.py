"""Task 6: record the raw camera feed ('r') and replay it (--replay)."""

import contextlib
import glob
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import cv2
import numpy as np

from tests.fakes import patch_all, FakeSerial, run_tracker, tt


def red_balloon(i):
    img = np.full((480, 640, 3), 90, np.uint8)
    cv2.circle(img, (300 + i, 240), 40, (30, 30, 210), -1)
    return img


def write_video(path, frames, fps=30):
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (640, 480))
    for i in range(1, frames + 1):
        writer.write(red_balloon(i))
    writer.release()


def count_frames(path):
    cap = cv2.VideoCapture(path)
    n = 0
    while cap.read()[0]:
        n += 1
    cap.release()
    return n


class Recording(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch_all("RECORDINGS_DIR", os.path.join(self.tmp, "recordings")))

    def tearDown(self):
        self.stack.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_r_starts_and_stops_a_raw_recording(self):
        r = run_tracker(["--dry-run", "--color", "red"], frames=40,
                        frame_fn=red_balloon, keys={5: ord("r"), 25: ord("r")})
        files = glob.glob(os.path.join(self.tmp, "recordings", "*.mp4"))
        self.assertEqual(len(files), 1, r.out)
        self.assertIn("Recording saved", r.out)
        self.assertAlmostEqual(count_frames(files[0]), 20, delta=1)     # frames 6..25
        self.assertIn("REC", r.lines_at(15))
        # It's the RAW feed: no crosshairs/boxes drawn on it
        cap = cv2.VideoCapture(files[0])
        ok, first = cap.read()
        cap.release()
        self.assertTrue(ok)
        self.assertLess(np.abs(first.astype(int) - red_balloon(6).astype(int)).mean(), 4)

    def test_recording_is_closed_on_quit(self):
        r = run_tracker(["--dry-run"], None, frames=15, keys={3: ord("r")})
        self.assertIn("Recording saved", r.out)
        files = glob.glob(os.path.join(self.tmp, "recordings", "*.mp4"))
        self.assertGreater(count_frames(files[0]), 5)


class Replay(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.video = os.path.join(self.tmp, "test.mp4")
        write_video(self.video, 40)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_replay_runs_the_whole_pipeline(self):
        r = run_tracker(["--replay", self.video, "--color", "red"], frames=0)
        self.assertIn("forcing --dry-run", r.out)
        self.assertEqual(FakeSerial.instances, [], "replay must never open a serial port")
        self.assertIn("Replay finished.", r.out)
        self.assertEqual(len([f for f in r.frames]), 40, "every frame processed (no thread)")
        self.assertTrue(any(on for _i, on in r.laser_events), "should track and lock on the recording")
        # (frame numbers come from the fake camera, which replay doesn't use,
        # so look at the 20th rendered frame directly)
        self.assertIn("REPLAY test.mp4", r.frames[20]["text"])

    def test_no_recording_during_replay(self):
        r = run_tracker(["--replay", self.video, "--color", "red"], frames=0, keys={0: ord("r")})
        self.assertIn("Recording is off while replaying.", r.out)
        self.assertNotIn("Recording raw camera feed", r.out)

    def test_replay_plays_at_real_speed_in_thread_mode(self):
        cap = cv2.VideoCapture(self.video)
        grabber = tt.FrameGrabber(cap, threaded=True, replay_fps=50)
        start, last_id = time.perf_counter(), 0
        while not grabber.dead:
            got = grabber.get_newest(last_id, 1.0)
            if got:
                last_id = got[2]
        elapsed = time.perf_counter() - start
        grabber.stop()
        cap.release()
        self.assertGreater(elapsed, 0.6, "40 frames at 50 FPS should take ~0.8 s, not instantly")
        self.assertLess(elapsed, 3.0)

    def test_missing_file(self):
        with self.assertRaises(SystemExit):
            run_tracker(["--replay", os.path.join(self.tmp, "nope.mp4")], frames=0)


if __name__ == "__main__":
    unittest.main()
