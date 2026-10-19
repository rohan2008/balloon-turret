"""
servo_test.py  -  Phase 3: type servo angles and send them to the Arduino.

How it works:
  1. Lists the serial (COM) ports on your laptop and asks which one is the
     Arduino.
  2. Opens that port at 115200 baud (must match turret.ino).
  3. Waits for the Arduino to say "READY". Opening the port automatically
     RESETS an Arduino Uno, and it takes about 2 seconds to restart.
  4. You type "pan,tilt" (for example 90,45) and press Enter. The text is
     sent exactly as typed, followed by a newline, and the Arduino's reply
     ("OK ..." or "ERR ...") is printed. Typing junk on purpose is a good
     way to test that the Arduino ignores bad lines.
  5. Type q to quit.

Angles below 20 or above 160 are clamped by the Arduino (the reply shows the
angle it actually used).
"""

import time

import serial
from serial.tools import list_ports


BAUD_RATE = 115200        # must match BAUD_RATE in turret.ino
READY_TIMEOUT_S = 4.0     # how long to wait for "READY" after opening the port


def looks_like_arduino(port):
    """Guess whether a port is an Arduino, from its description."""
    text = f"{port.description} {port.manufacturer}".lower()
    # Official Unos say "Arduino"; cheap clones usually use a CH340 chip.
    return any(word in text for word in ("arduino", "ch340", "usb serial", "usb-serial"))


def choose_port():
    """List serial ports and ask the user to pick one.
    Returns a port name like "COM3", or None to quit."""
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
        print("  - Is the Arduino plugged in? Use a USB DATA cable (some cables only charge).")
        print("  - Check Device Manager > Ports (COM & LPT) for the Arduino.")
        return None

    print("Available serial ports:")
    for number, port in enumerate(ports, start=1):
        hint = "   <-- probably the Arduino" if looks_like_arduino(port) else ""
        print(f"  {number}. {port.device}  ({port.description}){hint}")

    while True:
        choice = input("Pick a number, or type a port name like COM3 ('q' to quit): ").strip()
        if choice.lower() == "q":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(ports):
            return ports[int(choice) - 1].device
        if choice.upper().startswith("COM"):
            return choice.upper()
        print("That's not one of the choices, try again.")


def print_replies(arduino, wait_s):
    """Print every line the Arduino sends during the next `wait_s` seconds.
    Returns the list of lines received."""
    lines = []
    deadline = time.time() + wait_s
    while time.time() < deadline:
        raw = arduino.readline()          # waits up to the port's timeout
        if raw:
            text = raw.decode(errors="replace").strip()
            print(f"  Arduino: {text}")
            lines.append(text)
    return lines


def main():
    port = choose_port()
    if port is None:
        print("Bye.")
        return

    # --- Open the port -------------------------------------------------------
    try:
        # timeout=0.1 means readline() gives up after 0.1 s if nothing arrives
        arduino = serial.Serial(port, BAUD_RATE, timeout=0.1)
    except serial.SerialException as error:
        print(f"ERROR: Could not open {port}.")
        print(f"  Details: {error}")
        print("  - Only ONE program can use a COM port at a time. Close the Arduino")
        print("    IDE's Serial Monitor / Serial Plotter and try again.")
        print("  - Double-check the port number in Device Manager.")
        return

    print(f"Opened {port}. Waiting for the Arduino to restart...")

    # --- Wait for READY ------------------------------------------------------
    lines = []
    deadline = time.time() + READY_TIMEOUT_S
    while time.time() < deadline and "READY" not in lines:
        lines += print_replies(arduino, 0.2)
    if "READY" not in lines:
        print("WARNING: Never heard 'READY' from the Arduino. Continuing anyway, but check:")
        print("  - Is this the right COM port?")
        print("  - Did turret.ino upload successfully?")
        print("  - Is the baud rate 115200 in both turret.ino and this script?")

    # --- Main loop: type angles, send them ------------------------------------
    print("Type angles as pan,tilt (e.g. 90,90 or 40,120). Allowed range 20-160. 'q' quits.")
    try:
        while True:
            text = input("pan,tilt> ").strip()
            if text.lower() == "q":
                break
            if not text:
                continue
            # Send exactly what was typed plus a newline. .encode() turns the
            # Python text into raw bytes, which is what a serial port sends.
            arduino.write((text + "\n").encode())
            print_replies(arduino, 0.3)
    except KeyboardInterrupt:
        print()   # Ctrl+C also quits cleanly
    finally:
        arduino.close()
        print("Port closed. Bye.")


if __name__ == "__main__":
    main()
