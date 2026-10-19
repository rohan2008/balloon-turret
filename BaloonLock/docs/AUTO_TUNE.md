# Auto-tune: how the tracker tunes itself

You don't set GAIN or LEAD any more. The tracker works them out every frame
(see README, "Tuning is automatic"). A few **internal** numbers decide *how*
it does that, such as how jumpy the motion filter is or how fast gain rises
with confidence. Those are tuned **offline** by `tune_params.py`, which saves
the best set to `config/auto_tune.json`. The tracker loads that file
automatically at startup.

## What gets tuned (and what never can)

Only the keys in `control.TUNING_RANGES` can be tuned, and only inside these ranges:

| Parameter | Range | What it does |
|---|---|---|
| cv_accel_noise | 50 - 3000 | how much the steady-speed filter expects the speed to wobble |
| ca_jerk_noise | 1000 - 60000 | how quickly the acceleration filter expects acceleration to change |
| imm_stay | 0.80 - 0.99 | how "sticky" the choice between the two filters is |
| meas_sigma_base | 1 - 10 | how much the filter trusts one detection (pixels) |
| meas_sigma_blur | 0 - 2 | extra distrust for a smeared (blurred) detection |
| gain_min, gain_max | 0.05 - 0.30, 0.20 - 0.80 | gain at low / high tracking confidence (always capped by the stability limit for the measured delay) |
| confidence_scale | 1 - 20 | how quickly confidence drops when predictions miss |
| blur_speed_px_s | 200 - 3000 | speed at which the detector counts the balloon as "fully blurred" |
| blur_relax_s, blur_relax_v | 0 - 60 | how far the color thresholds may loosen for a fast balloon (never below the fixed floors S 40 / V 30) |
| speed_cap_gain | 1 - 3 | how much of the speed cap is used to keep up with a fast balloon |
| min_step_deg | 0.5 - 5 | the smallest per-command step limit |

`config.load_auto_tune()` **ignores** any other key (with a warning), as well
as any value outside its range. `LASER_ENABLED`, the lock rules, the
watchdog, the too-far rule and the first-run limits are not in this list, so
the file can't change them. The tests check this.

## Method

1. **Simulator** (`simulator.py`) renders a red ball on a gray, slightly
   cluttered background, with motion blur proportional to speed (8 sub-exposures
   across half the frame time), random brightness drift and flicker, and sensor
   noise. The physics: the camera is 2 m away with a 60° view (~277 px per
   meter), gravity, and quadratic air drag. There are 5 trajectories:
   - `light_balloon`: falls with terminal speed about 1 m/s
   - `heavy_toss`: thrown up, comes down fast (terminal speed 12 m/s)
   - `bounce`: dropped onto a floor, bounces (restitution 0.65)
   - `drift`: drifting sideways in a breeze, bobbing
   - `direction_change`: moving steadily, then suddenly reversing

   Each one is rendered at **30 and 60 fps**.
2. **Headless runner** (`headless.py`) runs the *real* `tracker_app.main()`
   in `--dry-run` with fake camera, window, keyboard and clock. Each frame
   takes 30 ms of simulated processing time. If that's slower than the camera,
   older frames are skipped, just like the real capture thread. The simulated
   servos move at 400°/s and respond `--servo-delay` (80 ms) late. The aim is
   compared with where the balloon *really is at that moment*.
3. **Score** for each run (the first 0.25 s, while it acquires the target, is skipped):
   - average aiming error (px)
   - fraction of frames LOCKED
   - number of lock drops
   - average overshoot (how far the aim runs *ahead* of the balloon)

   The combined score is `error + 0.5*overshoot + 40*(1 - locked) + 3*drops`
   (lower is better), averaged over all runs.
4. **Search**: random search, 150 trials (seed 1). Odd trials are small random
   changes around the best set so far, and even trials are fully random. That
   took about 4 minutes. Clips in `recordings/` are included automatically.
   There, the score uses the tracker's own "miss" distance, because a clip has
   no ground truth. There were no clips this time.

## Before / after

- **Old tracker** = commit `37a3da8` (the end of `overnight-improvements`),
  run unchanged through the same headless runner (`--color red`, same servo
  speed).
- The old tracker has **no idea of servo delay**. For a fair comparison, the
  first two columns use instant servos ("no servo delay*"). The last two
  columns use the 80 ms delay that real hobby servos roughly have, which the
  new tracker compensates for.

| Trajectory | Old tracker (no servo delay*) | New, tuned, no servo delay* | New, default params, 80 ms delay | New, auto-tuned, 80 ms delay |
|---|---|---|---|---|
| light_balloon @30fps | 12.3 / 0% / 0 / 10.7 | 10.3 / 0% / 0 / 8.9 | 7.8 / 85% / 0 / 0.3 | 7.1 / 88% / 0 / 3.9 |
| light_balloon @60fps | 8.9 / 0% / 0 / 6.1 | 8.4 / 71% / 1 / 5.6 | 7.7 / 82% / 1 / 0.8 | 6.4 / 87% / 1 / 2.9 |
| heavy_toss @30fps | 61.9 / 0% / 0 / 0.3 | 15.2 / 0% / 0 / 7.2 | 53.2 / 0% / 0 / 0.0 | 38.0 / 0% / 0 / 0.0 |
| heavy_toss @60fps | 57.4 / 0% / 0 / 0.0 | 13.1 / 75% / 0 / 1.9 | 46.6 / 0% / 0 / 0.0 | 36.3 / 0% / 0 / 0.0 |
| bounce @30fps | 14.7 / 62% / 0 / 8.4 | 10.7 / 78% / 1 / 6.0 | 19.5 / 57% / 0 / 15.0 | 17.5 / 61% / 0 / 12.3 |
| bounce @60fps | 13.9 / 63% / 0 / 6.2 | 10.3 / 66% / 1 / 3.5 | 19.3 / 59% / 0 / 15.0 | 17.3 / 57% / 0 / 9.8 |
| drift @30fps | 11.2 / 74% / 0 / 4.5 | 6.9 / 96% / 0 / 2.0 | 14.6 / 37% / 1 / 1.8 | 9.9 / 94% / 0 / 1.3 |
| drift @60fps | 9.6 / 76% / 0 / 3.2 | 6.7 / 96% / 0 / 1.5 | 14.9 / 78% / 0 / 2.5 | 9.5 / 84% / 0 / 1.4 |
| direction_change @30fps | 19.8 / 0% / 0 / 9.6 | 12.7 / 56% / 1 / 5.5 | 23.7 / 42% / 1 / 1.0 | 19.7 / 69% / 1 / 2.0 |
| direction_change @60fps | 16.3 / 0% / 0 / 6.1 | 10.5 / 86% / 1 / 3.1 | 22.4 / 56% / 1 / 2.3 | 17.2 / 60% / 1 / 2.5 |
| **combined score (lower = better)** | **54.3** | **29.2** | **46.3** | **36.7** |

Each cell: average aiming error (px) / time locked / lock drops / average overshoot (px).

**Reading it:**
- **Same conditions (columns 1 vs 2):** the new tracker roughly halves the
  combined score (54.3 to 29.2). The biggest wins are on the falling and
  accelerating cases. On `heavy_toss`, error drops from ~60 px to ~14 px,
  because the acceleration filter predicts the fall.
- **Auto-tuning (columns 3 vs 4):** with the realistic 80 ms delay, tuning
  improves the score from 46.3 to 36.7. Locking on `drift` goes from 37% to 94%
  at 30 fps.
- **Delay hurts:** compare columns 2 and 4. An 80 ms servo delay costs a lot
  on fast, changing motion: `heavy_toss` never locks with delay, and
  `bounce` and `direction_change` lose accuracy. This is physics (you can't
  react to a bounce before you see it), not something tuning can remove.
- The old tracker shows 0% locked on `light_balloon` despite a ~10 px error.
  Its aim sat just outside its lock zone the whole time.
- Several tuned values ended up at the edge of their range (gain_min 0.30,
  gain_max 0.80, meas_sigma_base 1.0, meas_sigma_blur 2.0). I left the ranges
  as they are on purpose. Gain is still capped by the stability limit, and
  wider ranges would tune harder to the *simulator*, which is cleaner than a
  real webcam.

## Run it again (e.g. after recording new clips)

```
python turret_tracker.py --dry-run          (press r to start/stop recording -> recordings/)
python tune_params.py                       (40 trials, ~1-2 min + clips; saves config/auto_tune.json)
python tune_params.py --trials 150          (longer search)
python tune_params.py --clip-color red      (detect the clips with a color preset instead of the AI)
python tune_params.py --evaluate-only       (just score the current settings)
```

To go back to the built-in defaults, delete `config/auto_tune.json` or run the
tracker with `--no-auto-tune`.

## Limits of this tuning

- It was tuned in **simulation only** (no recordings yet). A real webcam has
  auto-exposure, rolling shutter, compression and messier colors.
- The simulated servos are ideal: constant speed plus a fixed delay. Real
  servos overshoot, have deadband and slow down under load, so **real servo lag
  will change these results**. Record clips with the real turret and re-run
  the tuner.
- At 30 fps with hobby servos, fast throws (`heavy_toss`) can't be followed
  well enough to lock, with or without tuning.

## Tests that guard this

`tests/test_auto_tune.py`:
- **Filter:** on a free fall, the acceleration filter predicts 150 ms ahead with less than half the error of the old constant-velocity filter.
- **Lead:** it is latency + servo delay, the same in milliseconds at 30 and 60 fps.
- **Blur:** a blurred blob that the plain search misses is found with the prediction.
- **Dropouts:** during a dropout the laser stays off, and predictions are never reported as detections.
- **Sliders:** they do nothing without `--manual-tuning`.
- **Safety:** the tuning file can't set safety values, and at the extreme corners of every tuning range the laser rules still hold. The laser-path proof is re-run with the shipped tuned file.
