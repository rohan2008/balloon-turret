"""
simulator.py  -  synthetic "camera" for testing and tuning the tracker without
any balloon, webcam or hardware.

It renders a red ball (a balloon) moving along realistic paths:
    light_balloon  - a light balloon falling with air drag (terminal speed ~1 m/s)
    heavy_toss     - a heavier object tossed up and falling (much less drag)
    bounce         - a balloon dropped onto the floor, bouncing
    drift          - a balloon drifting sideways in a breeze, bobbing a little
    direction_change - moving steadily, then suddenly reversing
at 30 or 60 frames per second, with
    - MOTION BLUR proportional to speed (the ball is smeared across the
      exposure time, like a real webcam), and
    - random BRIGHTNESS changes and sensor noise.

Physics: the camera is 2 m from the ball with a 60 degree view, so
1 meter = PX_PER_M pixels. Gravity is 9.81 m/s^2; air drag grows with speed
squared, which is what gives a balloon its low "terminal velocity".

Try it:
    python simulator.py --show light_balloon          (watch one in a window)
    python simulator.py --write sim.mp4 bounce         (save it as a video, then:
    python turret_tracker.py --replay sim.mp4 --color red)
"""

import argparse
import math

import cv2
import numpy as np

WIDTH, HEIGHT = 640, 480
DISTANCE_M = 2.0
HFOV_DEG = 60.0
PX_PER_M = WIDTH / (2 * DISTANCE_M * math.tan(math.radians(HFOV_DEG / 2)))   # ~277 px per meter
G = 9.81
BALL_RADIUS_PX = 25
BALL_BGR = (40, 40, 210)            # red, matches the "red" preset
EXPOSURE_FRACTION = 0.5             # shutter open for half of each frame time
SUBFRAMES = 8                       # motion blur: ball drawn at 8 points during the exposure

SCENARIOS = ["light_balloon", "heavy_toss", "bounce", "drift", "direction_change"]


# ---------------------------------------------------------------------------
# Trajectories: each returns positions (x, y) in pixels at the given times
# ---------------------------------------------------------------------------
def _integrate(times, x0, y0, vx0, vy0, terminal_speed, floor_y=None, restitution=0.0):
    """Ball under gravity + quadratic air drag, optional bouncing floor.
    Positions in pixels, physics in meters. Fine 1 ms steps, sampled at `times`."""
    k = G / terminal_speed ** 2                 # drag constant: drag = k * v^2 (per unit mass)
    x, y = x0 / PX_PER_M, y0 / PX_PER_M         # meters (y grows DOWN, like the image)
    vx, vy = vx0, vy0
    t, step = 0.0, 0.001
    out = []
    for target in times:
        while t < target:
            speed = math.hypot(vx, vy)
            ax = -k * speed * vx
            ay = G - k * speed * vy
            vx += ax * step
            vy += ay * step
            x += vx * step
            y += vy * step
            if floor_y is not None and y * PX_PER_M > floor_y and vy > 0:
                y = floor_y / PX_PER_M
                vy = -vy * restitution          # bounce: lose some speed
            t += step
        out.append((x * PX_PER_M, y * PX_PER_M))
    return out


def trajectory(name, times):
    if name == "light_balloon":
        return _integrate(times, 200, 50, 0.15, 0.0, terminal_speed=1.0)
    if name == "heavy_toss":
        return _integrate(times, 160, 330, 0.7, -3.0, terminal_speed=12.0)
    if name == "bounce":
        return _integrate(times, 320, 50, 0.1, 0.0, terminal_speed=3.0,
                          floor_y=HEIGHT - 40 - BALL_RADIUS_PX, restitution=0.65)
    if name == "drift":
        return [(80 + PX_PER_M * (0.45 * t + 0.12 * math.sin(2 * math.pi * 0.4 * t)),
                 240 + PX_PER_M * 0.08 * math.sin(2 * math.pi * 0.7 * t)) for t in times]
    if name == "direction_change":
        def x_of(t):
            return 120 + PX_PER_M * (1.2 * t if t < 1.2 else 1.2 * 1.2 - 1.2 * (t - 1.2))
        return [(x_of(t), 220 + 30 * math.sin(2 * math.pi * 0.5 * t)) for t in times]
    raise ValueError(f"unknown scenario {name!r}; choose from {SCENARIOS}")


DURATION_S = {"light_balloon": 1.6, "heavy_toss": 0.9, "bounce": 3.0, "drift": 3.0,
              "direction_change": 2.4}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _background(rng):
    """Gray room-ish background: a gradient plus a few darker shapes (nothing red)."""
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    base = 120 + 40 * (yy / HEIGHT) + 15 * np.sin(xx / 90.0)
    img = np.dstack([base, base * 1.02, base * 0.97]).astype(np.float32)
    for _ in range(4):
        x, y = rng.integers(0, WIDTH), rng.integers(0, HEIGHT)
        w, h = rng.integers(40, 160), rng.integers(40, 160)
        img[y:y + h, x:x + w] *= rng.uniform(0.6, 0.9)
    return img


class Scenario:
    """Frames of one simulated scenario: iterate to get (image, time, true_xy)."""

    def __init__(self, name, fps=30, seed=0, duration=None):
        self.name, self.fps = name, fps
        self.rng = np.random.default_rng(seed)
        self.duration = duration or DURATION_S[name]
        self.count = int(self.duration * fps)
        self.background = _background(self.rng)
        self.noise = [self.rng.normal(0, 3, (HEIGHT, WIDTH, 3)).astype(np.float32) for _ in range(6)]
        exposure = EXPOSURE_FRACTION / fps
        self.times = [k / fps for k in range(self.count)]
        # positions at every sub-exposure moment (for motion blur) and the truth
        offsets = np.linspace(-exposure / 2, exposure / 2, SUBFRAMES)
        sample_times = sorted({max(0.0, t + o) for t in self.times for o in offsets} | set(self.times))
        lookup = dict(zip(sample_times, trajectory(name, sample_times)))
        self.truth = [lookup[t] for t in self.times]
        self.blur_paths = [[lookup[max(0.0, t + o)] for o in offsets] for t in self.times]

    def __len__(self):
        return self.count

    def render(self, k, brightness):
        """Image k: the ball smeared along its path during the exposure.
        (Only the small area around the ball is blended - much faster.)"""
        path = self.blur_paths[k]
        xs, ys = [p[0] for p in path], [p[1] for p in path]
        pad = BALL_RADIUS_PX + 3
        x1, y1 = max(int(min(xs)) - pad, 0), max(int(min(ys)) - pad, 0)
        x2, y2 = min(int(max(xs)) + pad, WIDTH), min(int(max(ys)) + pad, HEIGHT)
        img = self.background.copy()
        if x2 > x1 and y2 > y1:
            coverage = np.zeros((y2 - y1, x2 - x1), np.float32)
            layer = np.zeros((y2 - y1, x2 - x1), np.uint8)
            for (x, y) in path:
                layer[:] = 0
                cv2.circle(layer, (int(round(x)) - x1, int(round(y)) - y1), BALL_RADIUS_PX, 255, -1,
                           cv2.LINE_AA)
                coverage += layer.astype(np.float32) / (255 * SUBFRAMES)
            coverage = coverage[:, :, None]
            roi = img[y1:y2, x1:x2]
            img[y1:y2, x1:x2] = roi * (1 - coverage) + np.array(BALL_BGR, np.float32) * coverage
        img *= brightness
        img += self.noise[k % len(self.noise)]          # sensor noise (a few pre-made patterns)
        return cv2.convertScaleAbs(img)                 # clip to 0..255 and convert to bytes

    def __iter__(self):
        brightness = 1.0
        for k in range(self.count):
            # slow random brightness drift + occasional sudden change (a light flickering)
            brightness = float(np.clip(brightness + self.rng.normal(0, 0.02), 0.75, 1.2))
            if self.rng.random() < 0.03:
                brightness = float(np.clip(brightness * self.rng.uniform(0.85, 1.15), 0.75, 1.2))
            yield self.render(k, brightness), self.times[k], self.truth[k]


def main():
    parser = argparse.ArgumentParser(description="Synthetic falling-balloon camera")
    parser.add_argument("scenario", choices=SCENARIOS)
    parser.add_argument("--fps", type=int, default=30, choices=(30, 60))
    parser.add_argument("--show", action="store_true", help="watch it in a window")
    parser.add_argument("--write", metavar="FILE.mp4", help="save it as a video")
    args = parser.parse_args()
    sim = Scenario(args.scenario, args.fps)
    writer = None
    if args.write:
        writer = cv2.VideoWriter(args.write, cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (WIDTH, HEIGHT))
    for image, t, (x, y) in sim:
        if writer:
            writer.write(image)
        if args.show:
            shown = image.copy()
            cv2.drawMarker(shown, (int(x), int(y)), (0, 255, 0), cv2.MARKER_CROSS, 12, 1)
            cv2.imshow("simulator (green + = true position)", shown)
            if cv2.waitKey(int(1000 / args.fps)) & 0xFF == ord("q"):
                break
    if writer:
        writer.release()
        print(f"Saved {args.write} ({len(sim)} frames at {args.fps} fps)")
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
