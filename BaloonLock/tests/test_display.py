"""Display-only helpers: letterboxing, info bar layout, key draining."""

import unittest
from unittest import mock

import cv2
import numpy as np

import tests.fakes  # noqa: F401  (puts the project folder on the import path)
import display as d


class Letterbox(unittest.TestCase):

    def test_never_stretches(self):
        frame = np.full((480, 640, 3), 200, np.uint8)            # 4:3
        for win in [(640, 480), (1000, 400), (400, 900), (321, 1000)]:
            with self.subTest(window=win):
                canvas = np.zeros((win[1], win[0], 3), np.uint8)
                view = d.letterbox_into(canvas, frame, 0, 0, *win)
                rows, cols = np.nonzero(canvas[:, :, 0] == 200)
                w, h = cols.max() - cols.min() + 1, rows.max() - rows.min() + 1
                self.assertAlmostEqual(w / h, 640 / 480, delta=0.01)
                self.assertTrue(w == win[0] or h == win[1], "should fill one side fully")
                # mouse mapping: the picture's center maps back to the frame's center
                cx, cy = view.to_canvas(320, 240)
                fx, fy = view.to_frame(cx, cy)
                self.assertLessEqual(abs(fx - 320) + abs(fy - 240), 2)

    def test_click_on_black_bar_is_ignored(self):
        canvas = np.zeros((400, 1000, 3), np.uint8)
        view = d.letterbox_into(canvas, np.zeros((480, 640, 3), np.uint8), 0, 0, 1000, 400)
        self.assertIsNone(view.to_frame(5, 200))

    def test_gray_mask_letterboxes_too(self):
        mask = np.full((480, 640), 255, np.uint8)
        with mock.patch.object(cv2, "getWindowImageRect", lambda name: (0, 0, 800, 800)):
            canvas, _ = d.letterbox(mask, "mask")
        self.assertEqual(canvas.shape, (800, 800))


class InfoBar(unittest.TestCase):

    GROUPS = [("STATUS", [("TRACKING miss 12px and a long long long tail of text", (0, 255, 0))] * 8),
              ("TUNING", [("GAIN 0.20 (reaction strength)", (255, 255, 255))] * 5),
              ("KEYS", [("m mode  f fire  q quit", (170, 170, 170))] * 3)]

    def test_lines_never_overlap_and_fit_their_column(self):
        for win in [(640, 700), (1280, 1200), (640, 400), (1600, 500), (320, 300)]:
            with self.subTest(window=win):
                plan = d.plan_info_bar(self.GROUPS, win[0], d.MAX_BAR_FRACTION * win[1])
                (_w, text_h), baseline = cv2.getTextSize("Ag", d.FONT, plan.scale, 1)
                self.assertGreaterEqual(plan.line_h, text_h + baseline, "lines would overlap")
                room = win[0] // len(plan.columns) - 2 * d.PAD
                for _title, lines in self.GROUPS:
                    for text, _c in lines:
                        shown = d.fit_text(text, room, plan.scale)
                        self.assertLessEqual(cv2.getTextSize(shown, d.FONT, plan.scale, 1)[0][0], room)

    def test_splits_into_columns_when_short(self):
        tall = d.plan_info_bar(self.GROUPS, 1280, 10000)
        short = d.plan_info_bar(self.GROUPS, 1280, 250)
        self.assertEqual(len(tall.columns), 1)
        self.assertGreater(len(short.columns), 1)

    def test_text_size_constant_across_zoom(self):
        sizes = {d.plan_info_bar(self.GROUPS, w, d.MAX_BAR_FRACTION * h).scale
                 for w, h in [(704, 770), (900, 1000), (1280, 1400)]}
        self.assertEqual(sizes, {d.FONT_SCALES[0]})


class Zoom(unittest.TestCase):

    def test_zoom_steps_and_clamps(self):
        sizes = {}
        with mock.patch.object(cv2, "resizeWindow", lambda name, w, h: sizes.__setitem__(name, (w, h))), \
                mock.patch.object(cv2, "getWindowImageRect",
                                  lambda name: (0, 0, *sizes.get(name, (0, 0)))):
            zoom = d.WindowZoom(["cam", "mask"])
            zoom.max_width = 1920
            zoom.set_base(640, 480)
            self.assertEqual(sizes["cam"], (640, 480))
            zoom.handle_key(ord("+"))
            self.assertEqual(sizes["cam"], (704, 528))               # +10%, same shape
            zoom.handle_key(ord("0"))
            self.assertEqual(sizes["cam"], (640, 480))
            for _ in range(40):
                zoom.handle_key(ord("-"))
            self.assertEqual(sizes["cam"][0], d.MIN_WIDTH)
            for _ in range(60):
                zoom.handle_key(ord("="))
            self.assertEqual(sizes["cam"][0], 1920)
            self.assertEqual(sizes["mask"], sizes["cam"], "both windows zoom together")


class TrackerDisplay(unittest.TestCase):
    """Render real tracker frames into odd-shaped windows and look at the pixels."""

    def test_canvas_fits_window_and_overlays_are_scaled(self):
        from tests.fakes import box_at, run_tracker
        for window in [(640, 700), (1400, 600), (500, 900)]:
            with self.subTest(window=window):
                r = run_tracker(["--dry-run"], lambda i: box_at(330, 245, 30), frames=8,
                                window=window, keep_canvases=True)
                # Frame 3: not locked yet, so the lock-zone circle is yellow (not green)
                frame = next(f for f in r.frames if f["i"] == 3)
                full = frame["canvas"]
                self.assertEqual(full.shape[:2], (window[1], window[0]))
                self.assertEqual(frame["titles"][0], "STATUS")
                # Only look at the video area: everything above the info bar's
                # gray separator line (the bar has green/red text of its own).
                separator = [y for y in range(full.shape[0])
                             if (full[y, :, :] == 90).all(axis=1).mean() > 0.9]
                canvas = full[:separator[0]]
                # Pure green box outline (0,255,0) must be square -> not stretched
                g = np.argwhere((canvas[:, :, 0] == 0) & (canvas[:, :, 1] == 255) & (canvas[:, :, 2] == 0))
                w = g[:, 1].max() - g[:, 1].min()
                h = g[:, 0].max() - g[:, 0].min()
                self.assertAlmostEqual(w / h, 1.0, delta=0.06)
                # Red raw-centroid dot (0,0,255) sits inside the green box
                red = np.argwhere((canvas[:, :, 0] == 0) & (canvas[:, :, 1] == 0) & (canvas[:, :, 2] == 255))
                self.assertTrue(len(red))
                cy, cx = red.mean(axis=0)
                self.assertTrue(g[:, 1].min() < cx < g[:, 1].max())
                self.assertTrue(g[:, 0].min() < cy < g[:, 0].max())


class Keys(unittest.TestCase):

    def test_reads_every_waiting_key(self):
        flood = iter([ord("f")] * 40 + [ord("q"), -1])
        with mock.patch.object(cv2, "waitKey", lambda delay=0: ord("f")), \
                mock.patch.object(cv2, "pollKey", lambda: next(flood)):
            keys = d.read_all_keys()
        self.assertEqual(len(keys), 42)
        self.assertIn(ord("q"), keys)


if __name__ == "__main__":
    unittest.main()
