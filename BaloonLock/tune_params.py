"""
tune_params.py  -  automatically tune the tracker's internal parameters.

It runs the FULL tracker headless (see headless.py) over
  - simulated balloons (simulator.py): light balloon, heavy toss, bounce,
    drift, direction change - each at 30 and 60 fps, and
  - every video in recordings/ (record with 'r' in the tracker),
scores every run (average aiming error, time locked, lock drops, overshoot),
and does a RANDOM SEARCH over the tunable parameters (control.TUNING_RANGES:
filter noise, gain curve, blur thresholds, speed-cap usage). The best set is
saved to config/auto_tune.json, which the tracker loads automatically.

Only parameters in control.TUNING_RANGES can be tuned, and only inside their
ranges. None of them is a safety setting.

    python tune_params.py                        # tune (40 trials) and save
    python tune_params.py --trials 80            # search longer
    python tune_params.py --evaluate-only        # just score the current settings
    python tune_params.py --clip-color green     # detect recordings/ clips with a color preset
                                                 # (default: the AI detector - slower)
    python tune_params.py --baseline-dir OLD     # also score an older checkout (before/after)
"""

import argparse
import datetime
import glob
import json
import math
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import headless                                          # noqa: E402
from control import DEFAULT_TUNING, TUNING_RANGES        # noqa: E402
from config import AUTO_TUNE_FILE, load_auto_tune        # noqa: E402
from simulator import SCENARIOS                          # noqa: E402

SERVO_DEG_PER_S = 400.0          # simulated hobby-servo speed
PROCESSING_MS = 30.0             # simulated time to process one frame
LOG_SCALE_KEYS = {"cv_accel_noise", "ca_jerk_noise", "blur_speed_px_s", "confidence_scale"}


def sim_runs():
    return [{"kind": "sim", "name": n, "fps": f} for n in SCENARIOS for f in (30, 60)]


def clip_runs():
    return [{"kind": "clip", "name": os.path.basename(p), "path": p}
            for p in sorted(glob.glob(os.path.join(REPO, "recordings", "*.mp4")))]


def tracker_args(run, clip_color, servo_delay_ms=80):
    if run["kind"] == "sim":
        return ["--color", "red", "--servo-speed", f"{SERVO_DEG_PER_S / run['fps']:.2f}",
                "--servo-delay", str(servo_delay_ms)]
    args = ["--servo-delay", str(servo_delay_ms)]
    return (["--color", clip_color] if clip_color else []) + args


_frame_cache = {}


def frames_for(run):
    key = (run["kind"], run["name"], run.get("fps"))
    if key not in _frame_cache:
        if run["kind"] == "sim":
            _frame_cache[key] = (headless.simulated_frames(run["name"], run["fps"]), run["fps"])
        else:
            _frame_cache[key] = headless.clip_frames(run["path"])
    return _frame_cache[key]


def evaluate(params, runs, clip_color, servo_delay_ms=80, tuning=True):
    """Score one parameter set on every run. Returns (mean combined score, per-run scores)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"parameters": params}, f)
        tuning_file = f.name
    try:
        per_run = {}
        for run in runs:
            frames, fps = frames_for(run)
            extra = ["--tuning-file", tuning_file] if tuning else ["--no-auto-tune"]
            records = headless.run_tracker(frames, tracker_args(run, clip_color, servo_delay_ms) + extra,
                                           PROCESSING_MS / 1000)
            per_run[label(run)] = headless.score(records, fps)
        combined = float(np.mean([headless.combined(s) for s in per_run.values()]))
        return combined, per_run
    finally:
        os.unlink(tuning_file)


def label(run):
    return f"{run['name']} @{run['fps']}fps" if run["kind"] == "sim" else f"clip {run['name']}"


def sample(rng, around=None, spread=0.25):
    """A random parameter set: fully random, or a small change to `around`."""
    params = {}
    for key, (low, high) in TUNING_RANGES.items():
        if around is not None:
            if key in LOG_SCALE_KEYS:
                value = around[key] * math.exp(rng.normal(0, spread))
            else:
                value = around[key] + rng.normal(0, spread * (high - low) / 2)
        elif key in LOG_SCALE_KEYS:
            value = math.exp(rng.uniform(math.log(low), math.log(high)))
        else:
            value = rng.uniform(low, high)
        params[key] = float(min(max(value, low), high))
    if params["gain_max"] < params["gain_min"] + 0.05:               # keep the gain curve sensible
        params["gain_max"] = min(TUNING_RANGES["gain_max"][1], params["gain_min"] + 0.05)
    return params


def baseline_scores(baseline_dir, runs):
    """Score an OLDER checkout of the tracker (its own code) in a separate process."""
    scores = {}
    for run in runs:
        if run["kind"] != "sim":
            continue
        cmd = [sys.executable, os.path.join(REPO, "headless.py"), "--repo", baseline_dir,
               "--scenario", run["name"], "--fps", str(run["fps"]), "--processing-ms", str(PROCESSING_MS),
               "--", "--color", "red", "--servo-speed", f"{SERVO_DEG_PER_S / run['fps']:.2f}"]
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=baseline_dir)
        lines = [l for l in out.stdout.splitlines() if l.startswith("{")]
        scores[label(run)] = json.loads(lines[-1]) if lines else None
    return scores


def cell(s):
    if not s:
        return "n/a"
    error = "-" if s["error"] != s["error"] else f"{s['error']:.1f}"
    return f"{error} / {s['locked']:.0%} / {s['drops']} / {s['overshoot']:.1f}"


def report(columns, runs):
    """Markdown table: one row per run, one column per tracker version."""
    names = list(columns)
    lines = ["| Trajectory | " + " | ".join(names) + " |",
             "|---|" + "---|" * len(names)]
    for run in runs:
        row = [label(run)] + [cell(columns[n].get(label(run))) for n in names]
        lines.append("| " + " | ".join(row) + " |")
    totals = []
    for n in names:
        valid = [s for s in columns[n].values() if s]
        totals.append(f"**{np.mean([headless.combined(s) for s in valid]):.1f}**" if valid else "n/a")
    lines.append("| **combined score (lower = better)** | " + " | ".join(totals) + " |")
    lines.append("")
    lines.append("Each cell: average aiming error (px) / time locked / lock drops / average overshoot (px).")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Auto-tune the tracker on simulated + recorded runs")
    parser.add_argument("--trials", type=int, default=40)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--evaluate-only", action="store_true")
    parser.add_argument("--clip-color", help="color preset for recordings/ clips (default: AI)")
    parser.add_argument("--baseline-dir", help="older checkout to compare against (before/after)")
    parser.add_argument("--report", default=os.path.join(REPO, "config", "auto_tune_report.md"))
    args = parser.parse_args()

    runs = sim_runs() + clip_runs()
    clips = [r for r in runs if r["kind"] == "clip"]
    print(f"{len(runs)} runs: {len(runs) - len(clips)} simulated + {len(clips)} recorded clips"
          + ("" if clips else " (recordings/ is empty - record some with 'r')"))

    current, _warnings = load_auto_tune()
    start = {**DEFAULT_TUNING, **current}
    t0 = time.time()
    default_score, default_runs = evaluate(dict(DEFAULT_TUNING), runs, args.clip_color)
    print(f"built-in defaults: score {default_score:.2f}")
    best, best_score, best_runs = dict(start), *evaluate(start, runs, args.clip_color)
    print(f"current settings:  score {best_score:.2f}")

    if not args.evaluate_only:
        rng = np.random.default_rng(args.seed)
        for trial in range(args.trials):
            candidate = sample(rng, around=best if trial % 2 else None)
            score, per_run = evaluate(candidate, runs, args.clip_color)
            marker = ""
            if score < best_score:
                best, best_score, best_runs, marker = candidate, score, per_run, "  <- new best"
            print(f"trial {trial + 1:3d}/{args.trials}: score {score:7.2f}   best {best_score:7.2f}{marker}")
        os.makedirs(os.path.dirname(AUTO_TUNE_FILE), exist_ok=True)
        with open(AUTO_TUNE_FILE, "w", encoding="utf-8") as f:
            json.dump({"parameters": {k: round(v, 4) for k, v in best.items()},
                       "score": round(best_score, 3), "default_score": round(default_score, 3),
                       "trials": args.trials, "seed": args.seed,
                       "runs": [label(r) for r in runs],
                       "created": datetime.datetime.now().isoformat(timespec="seconds"),
                       "note": "Written by tune_params.py. Only tuning parameters - no safety settings."},
                      f, indent=2)
        print(f"saved best parameters to {AUTO_TUNE_FILE}  (score {best_score:.2f}, "
              f"defaults {default_score:.2f})")

    columns = {}
    if args.baseline_dir:
        print("scoring the baseline (older) tracker...")
        columns["Old tracker (no servo delay*)"] = baseline_scores(args.baseline_dir, runs)
        _s, columns["New, tuned, no servo delay*"] = evaluate(best, runs, args.clip_color, servo_delay_ms=0)
    columns["New, default params, 80 ms delay"] = default_runs
    columns["New, auto-tuned, 80 ms delay"] = best_runs
    table = report(columns, runs)
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        f.write(table + "\n")
    print("\n" + table)
    print(f"\n(report saved to {args.report}; took {time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
