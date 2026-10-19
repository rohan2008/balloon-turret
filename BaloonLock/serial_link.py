"""
serial_link.py  -  talking to the Arduino: TurretLink (the USB serial port,
reconnecting, the fire button), TurretOutputs (laser + LED commands, sent
only when they change), and the gentle recenter glide.
"""

import threading
import time

import serial

from control import CENTER_ANGLE, RECENTER_RAMP_S
from safety import LASER_ENABLED, WATCHDOG_LIMIT_S, WATCHDOG_MESSAGE


BAUD_RATE = 115200          # must match turret.ino


SEND_RATE_HZ = 30           # send angles at most this many times per second


DISCONNECTED_MESSAGE = "ARDUINO DISCONNECTED"


READY_TIMEOUT_S = 3.0       # wait this long for the Arduino's "READY" after opening the port


class TurretLink:
    """Sends commands to the Arduino over USB serial - or, in dry-run mode,
    prints them instead - and reads what the Arduino sends back (the fire
    button: "B1" pressed / "B0" released). Survives the USB cable being
    unplugged: it stops sending, keeps the video running, and tries to
    reconnect every RECONNECT_EVERY_S seconds.

    (A "class" just bundles some variables - like the serial port - together
    with the functions that use them.)"""

    RECONNECT_EVERY_S = 2.0

    def __init__(self, port, dry_run):
        self.port = port
        self.dry_run = dry_run
        self.ser = None                  # the open serial port (None = not connected)
        self.connect_count = 0           # +1 every time we (re)connect
        self.last_reconnect_try = 0.0
        self.last_printed = {}           # dry-run: last line printed, per command type
        self.reply_buffer = ""           # partial text received from the Arduino
        self.last_reply_printed = None   # don't print the same Arduino message twice in a row
        self.button_pressed = False      # latest fire-button state from the Arduino
        self.ready = False               # True once the Arduino has said "READY"
        self.ready_deadline = 0.0
        # The watchdog thread may send "L0" while the main loop is busy, so only
        # one thread at a time may use the serial port.
        self.lock = threading.RLock()
        if not dry_run:
            self._open(wait_for_ready=True)     # if this fails at startup, main() reports it

    @property
    def connected(self):
        """Port open AND the Arduino has finished starting up."""
        return self.dry_run or (self.ser is not None and self.ready)

    def _open(self, wait_for_ready):
        """Open the port. Opening it RESETS the Uno, which then needs ~2 s before
        it says "READY". At startup we wait for that here; when RE-connecting we
        don't wait (the video keeps running) - poll() notices "READY" later.
        Raises SerialException if the port can't be opened."""
        print(f"Opening {self.port} ...")
        # timeout=0       -> reading never waits (returns whatever has arrived)
        # write_timeout   -> writing gives up after 0.5 s instead of freezing forever
        self.ser = serial.Serial(self.port, BAUD_RATE, timeout=0, write_timeout=0.5)
        self.ready = False
        self.reply_buffer = ""
        self.ready_deadline = time.time() + READY_TIMEOUT_S
        if wait_for_ready:
            while not self.ready and self.ser is not None:
                self._poll()
                if not self.ready:
                    time.sleep(0.05)

    def _mark_ready(self, heard_ready):
        self.ready = True
        self.connect_count += 1
        if heard_ready:
            print("Arduino says READY." if self.connect_count == 1 else "Reconnected: Arduino READY.")
        else:
            print("WARNING: no READY from the Arduino (wrong port? sketch not uploaded?) "
                  "- continuing anyway.")

    def send_line(self, text):
        """Send one command line, e.g. "90,45" or "D1". Never crashes on USB problems."""
        with self.lock:
            self._send_line(text)

    def _send_line(self, text):
        if self.dry_run:
            kind = "angles" if text[0].isdigit() else text[0]   # print each kind only on change
            if self.last_printed.get(kind) != text:
                print(f"[dry-run] would send: {text}")
                self.last_printed[kind] = text
            return

        if self.ser is None:                       # currently disconnected
            self._try_reconnect()
            return
        if not self.ready:                         # Arduino still starting up
            self._poll()
            return

        try:
            self.ser.write((text + "\n").encode())
        except (serial.SerialException, OSError) as error:
            self._lost_connection(error)

    def poll(self):
        """Read whatever the Arduino has sent: "B1"/"B0" update the button
        state; "OK ..." replies are ignored (dozens per second); anything
        else (errors, warnings) is printed."""
        with self.lock:
            self._poll()

    def _poll(self):
        if self.dry_run or self.ser is None:
            return
        if not self.ready and time.time() > self.ready_deadline:
            self._mark_ready(heard_ready=False)
        try:
            waiting = self.ser.in_waiting
            if not waiting:
                return
            self.reply_buffer += self.ser.read(waiting).decode(errors="replace")
        except (serial.SerialException, OSError) as error:
            self._lost_connection(error)
            return
        *complete_lines, self.reply_buffer = self.reply_buffer.split("\n")
        for line in complete_lines:
            line = line.strip()
            if line == "READY":
                if not self.ready:
                    self._mark_ready(heard_ready=True)
            elif line == "B1":
                self.button_pressed = True
            elif line == "B0":
                self.button_pressed = False
            elif line and not line.startswith("OK") and line != self.last_reply_printed:
                print(f"  Arduino: {line}")
                self.last_reply_printed = line

    def _lost_connection(self, error):
        print(f"{DISCONNECTED_MESSAGE}: {error}")
        print(f"  Video keeps running. Retrying every {self.RECONNECT_EVERY_S:.0f} s...")
        self.close()
        self.last_reconnect_try = time.time()

    def _try_reconnect(self):
        if time.time() - self.last_reconnect_try < self.RECONNECT_EVERY_S:
            return
        self.last_reconnect_try = time.time()
        try:
            self._open(wait_for_ready=False)       # don't freeze the video waiting
        except (serial.SerialException, OSError):
            self.ser = None                        # still unplugged; try again later

    def close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except (serial.SerialException, OSError):
                pass
        self.ser = None
        self.ready = False
        self.button_pressed = False                # unknown = NOT pressed (safe)


def ramp_to_center(link, from_pan, from_tilt, seconds=RECENTER_RAMP_S):
    """Glide back to 90,90 in small steps over about `seconds`, instead of one
    sudden jump (which can shake the bracket and whatever is mounted on it)."""
    steps = max(1, int(seconds * SEND_RATE_HZ))
    for i in range(1, steps + 1):
        fraction = i / steps
        pan = from_pan + (CENTER_ANGLE - from_pan) * fraction
        tilt = from_tilt + (CENTER_ANGLE - from_tilt) * fraction
        link.send_line(f"{round(pan)},{round(tilt)}")
        time.sleep(seconds / steps)


def set_laser(link, laser_on, dry_run):
    """Report the laser state and - ONLY if LASER_ENABLED is True and we're
    not in dry-run - send it to the Arduino as "L1" (on) or "L0" (off)."""
    if dry_run:
        note = "(dry-run)"
    elif not LASER_ENABLED:
        note = "(not sent: LASER_ENABLED = False)"
    else:
        note = ""
        link.send_line("L1" if laser_on else "L0")
    print(f"LASER {'ON ' if laser_on else 'OFF'} {note}")


class TurretOutputs:
    """Keeps track of the laser and the LOCKED LED. A serial command (and a
    printout) happens ONLY when one of them CHANGES - never per frame or per
    key event. (The angle lines, sent ~30x per second, are what keep the
    Arduino's 500 ms safety timeout from firing.)"""

    def __init__(self, link, dry_run):
        self.link = link
        self.dry_run = dry_run
        self.laser_on = False
        self.led_on = False
        self.synced_count = 0       # link.connect_count we last sent our states to
        self.lock = threading.RLock()   # the watchdog thread may call watchdog_off()

    def set(self, laser, led):
        with self.lock:
            # Laser FIRST, so turning things off always kills the laser before anything else.
            if laser != self.laser_on:
                self.laser_on = laser
                set_laser(self.link, laser, self.dry_run)
            if led != self.led_on:
                self.led_on = led
                self.link.send_line("D1" if led else "D0")

    def watchdog_off(self):
        """Called FROM THE WATCHDOG THREAD when the main loop stalls: laser off
        now (an "L0" is sent even if we think it's already off)."""
        with self.lock:
            self._force_laser_off()
            print(f"{WATCHDOG_MESSAGE}: main loop stalled for over "
                  f"{WATCHDOG_LIMIT_S * 1000:.0f} ms")

    def _force_laser_off(self):
        """Laser OFF, and send "L0" even if we think it's already off."""
        if self.laser_on:
            self.laser_on = False
            set_laser(self.link, False, self.dry_run)    # sends L0 (if allowed) + prints
        elif LASER_ENABLED and not self.dry_run:
            self.link.send_line("L0")

    def sync(self):
        """After the Arduino (re)connects it has restarted (laser off, LED off).
        The laser is NEVER switched back on here - we send L0 and forget we were
        firing. Returns True after a (re)connect, so the main loop also drops
        the lock: the laser may only come back after the lock is earned again."""
        if self.link.connect_count == self.synced_count:
            return False
        with self.lock:
            self.synced_count = self.link.connect_count
            self._force_laser_off()
            self.link.send_line("D1" if self.led_on else "D0")
        return True

    def all_off(self):
        """Shutdown: laser off (sent even if we think it's already off), then LED."""
        if LASER_ENABLED and not self.dry_run:
            self.link.send_line("L0")
        if self.laser_on:
            self.laser_on = False
            print(f"LASER OFF {'(dry-run)' if self.dry_run else ''}")
        self.led_on = False
        self.link.send_line("D0")
