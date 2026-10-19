"""
bringup.py  -  step-by-step checklist for the FIRST time you connect the real
hardware. It only prints instructions (and lists the serial ports it can
see); it never opens a port or moves anything itself.

    python bringup.py              # checklist
    python bringup.py --port COM5  # same, with your port filled into the commands
"""

import argparse

STEPS = """
HARDWARE BRING-UP CHECKLIST  (do the steps in order; stop if anything looks wrong)

 1. Software check, no hardware needed:
        python turret_tracker.py --self-test
        python -m unittest

 2. Upload turret/turret.ino to the Uno (Arduino IDE: Board = Arduino Uno,
    pick the port, Upload). Leave LASER_ENABLED = false in the sketch.
    Close the IDE's Serial Monitor afterwards (only one program can use the port).

 3. Arduino ALONE (no servos wired yet):
        python servo_test.py
    Type 90,90 -> expect "OK 90,90". Type L1 -> expect "ERR laser disabled".

 4. Wire the servos (README > Wiring): separate 5 V supply, SHARED GROUND.
    Servo horns OFF for now. Send 90,90, then attach the horns so 90,90 points
    straight ahead and level.

 5. Slowly try the range by hand in servo_test.py: 60,90  120,90  90,60  90,120.
    Nothing may hit the bracket's end stops.

 6. FIRST RUN of the tracker - use --first-run (RECOMMENDED):
        python turret_tracker.py --port {port} --first-run
    First-run mode: GAIN 0.1, LEAD 0, max 3 degrees per command, angles
    limited to 45-135, and the laser permanently DISABLED.
    Hold a balloon off to one side:
      - does the turret turn TOWARD it? If it turns away, add --invert-pan
        (left/right) or --invert-tilt (up/down) and try again.
      - press q: the turret should glide back to 90,90.

 7. Only when step 6 is boring and predictable, run without --first-run
    (laser is still disabled in BOTH programs until you change that on purpose):
        python turret_tracker.py --port {port}

 Keep people, pets and mirrors out of the turret's view at all times.
"""


def main():
    parser = argparse.ArgumentParser(description="Hardware bring-up checklist")
    parser.add_argument("--port", default="COM5", help="your Arduino's COM port")
    args = parser.parse_args()
    print(STEPS.format(port=args.port))
    try:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
        print("Serial ports this laptop can see right now:")
        for p in ports:
            print(f"   {p.device}  ({p.description})")
        if not ports:
            print("   none - is the Arduino plugged in with a DATA cable?")
    except ImportError:
        print("(pyserial not installed: pip install -r requirements.txt)")


if __name__ == "__main__":
    main()
