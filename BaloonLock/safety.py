"""
safety.py  -  the laser safety rules, in one place.

  LASER_ENABLED        - the master switch. False = no laser command is ever
                         sent. (turret.ino has its own switch too.)
  laser_should_fire()  - THE one function that decides if the laser may be on
                         this frame. Every condition is listed in its docstring.
  LoopWatchdog         - turns the laser off from a separate thread if the
                         main loop stalls for more than 200 ms.
  safety_banner()      - the printed list of which safeties are ON/OFF.

See docs/SAFETY.md for every rule, where it's enforced, and which test covers it.
"""

import threading
import time

from control import LOCK_FRAMES


# False = this program NEVER sends a laser command over serial, no matter
# what. The lock/fire logic still runs and shows "LASER ON/OFF" on screen,
# so you can test it safely. (turret.ino has its own separate switch too -
# BOTH must be true before the laser can physically turn on.)
LASER_ENABLED = False


# --first-run: extra-cautious settings for the very first time on real hardware
FIRST_RUN = {"gain": 0.10, "lead": 0, "max_step": 3.0, "min_angle": 45, "max_angle": 135}


FIRST_RUN_MESSAGE = "FIRST RUN MODE"


# Vision-fault disable: the laser is forced OFF (and the lock dropped) if...
VISION_FAULT_NO_FRAME_S = 0.5       # ...no new camera frame arrives for this long, or


VISION_FAULT_MAX_FRAME_AGE_S = 0.3  # ...the frame is older than this when we decide


VISION_FAULT_MESSAGE = "VISION FAULT - LASER DISABLED"


# Python-side WATCHDOG: a separate thread checks that the main loop keeps
# running. If the loop stalls for more than WATCHDOG_LIMIT_S (frozen camera,
# a very slow frame, a hang), the watchdog sends "L0" by itself - the main
# loop can't, because it's the thing that's stuck. The lock must then be
# earned again (5 fresh frames) before the laser may fire.
WATCHDOG_LIMIT_S = 0.2


WATCHDOG_MESSAGE = "WATCHDOG - LASER OFF"


WATCHDOG_SHOW_S = 1.5           # keep the message on screen this long


def safety_banner(args):
    """The list of safeties printed at startup - what's ON and what's OFF."""
    def state(on, on_text="ON", off_text="OFF"):
        return on_text if on else off_text
    lines = [
        "=" * 62,
        "  SAFETY STATUS",
        f"  LASER_ENABLED (Python) : {LASER_ENABLED}"
        + ("  -> no laser command is ever sent" if not LASER_ENABLED else "  !!! LASER CAN FIRE !!!"),
        f"  Serial                 : {'none (DRY RUN)' if args.dry_run else 'LIVE - real Arduino'}",
        f"  LOCK BEFORE FIRE       : ON ({LOCK_FRAMES} frames, zone {args.lock_fraction:g} x radius, "
        f"{args.lock_min:g}-{args.lock_max:g} px)",
        f"  REAL DETECTION ONLY    : ON (never fires on prediction)",
        f"  TOO-FAR RULE           : ON (radius < {args.min_radius:g} px = no laser)",
        f"  VISION-FAULT DISABLE   : ON",
        f"  LOOP WATCHDOG          : ON ({WATCHDOG_LIMIT_S * 1000:.0f} ms)",
        f"  WINDOW FOCUS RULE      : ON",
        f"  KILL KEY 'q'           : ON (laser off first)",
        f"  FIRE MODE              : {args.mode.upper()}",
        f"  SLEW LIMIT             : max {args.max_step:g} deg per command, angles "
        f"{args.min_angle}-{args.max_angle}",
        f"  {FIRST_RUN_MESSAGE:<23}: "
        + state(args.first_run, f"ON - laser DISABLED, GAIN {FIRST_RUN['gain']}, LEAD 0", "off"),
        f"  ARDUINO SIDE           : its own LASER_ENABLED + 500 ms timeout (in turret.ino)",
        "=" * 62,
    ]
    return lines


def laser_should_fire(mode, real_detection, locked, vision_fault, focused,
                      too_far, calibrating, button_held, laser_disabled=False, link_ok=True):
    """THE one place that decides whether the laser should be on this frame.
    EVERY condition must hold - any single one failing means OFF:
      real_detection : the balloon was actually detected THIS frame
                       (never just predicted by the Kalman filter)
      locked         : inside the lock zone for 5 frames in a row
      vision_fault   : None (camera fresh, detector working)
      focused        : our window is active (so the 'q' kill key works)
      too_far        : False (balloon not too small to lock reliably)
      calibrating    : False (you're standing in front of the turret)
      laser_disabled : False (--first-run disables the laser completely)
      link_ok        : the Arduino is connected (always True in dry-run)
      MANUAL mode    : fire button held as well
    (LASER_ENABLED is checked separately, right where commands are sent.)"""
    if not real_detection or not locked:
        return False
    if vision_fault is not None:
        return False
    if not focused or too_far or calibrating:
        return False
    if laser_disabled or not link_ok:
        return False
    if mode == "MANUAL":
        return bool(button_held)
    return mode == "AUTO"


class LoopWatchdog:
    """A small background thread that watches the MAIN LOOP.

    The main loop calls kick() several times per frame ("I'm alive"). If no
    kick arrives for WATCHDOG_LIMIT_S, the watchdog calls on_trip() - which
    turns the laser off - FROM ITS OWN THREAD, because the main loop is the
    thing that's stuck. It trips once per stall, and the main loop learns
    about it with consume_trip()."""

    def __init__(self, on_trip, limit_s=WATCHDOG_LIMIT_S):
        self.on_trip = on_trip
        self.limit_s = limit_s
        self.last_kick = None           # not watching until the first kick
        self.tripped_this_stall = False
        self.trip_pending = False       # for the main loop to notice
        self.trips = 0
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def kick(self):
        self.last_kick = time.perf_counter()
        self.tripped_this_stall = False

    def consume_trip(self):
        """True (once) if the watchdog tripped since the last call."""
        if self.trip_pending:
            self.trip_pending = False
            return True
        return False

    def _run(self):
        while self.running:
            last = self.last_kick
            if (last is not None and not self.tripped_this_stall
                    and time.perf_counter() - last > self.limit_s):
                self.tripped_this_stall = True
                self.trips += 1
                self.trip_pending = True
                self.on_trip()
            threading.Event().wait(0.02)      # check 50 times per second

    def stop(self):
        self.running = False
        self.thread.join(timeout=1.0)
