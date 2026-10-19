/*
  SAFETY RULES - checked by reading this code (2026-10-11) and by compiling it.
  NOT yet verified on real hardware. docs/WOKWI.md shows how to check each
  one in the free online simulator.

    [x] L1 is refused while LASER_ENABLED is false
          handleLaserCommand() replies "ERR laser disabled..." and setLaser()
          forces the pin LOW whenever LASER_ENABLED is false.
    [x] Laser AND LED turn off after 500 ms without a valid command
          loop(): if (laserOn || ledOn) and millis() - lastCommandMs > 500.
          Only VALID commands (angles, L0/L1, D0/D1) reset the timer.
    [x] Angles are clamped to 20..160
          handleLine(): constrain(pan/tilt, MIN_ANGLE, MAX_ANGLE). Huge numbers
          (e.g. 99999999) saturate in strtol() and are then clamped too.
    [x] Malformed lines are ignored (servos don't move)
          handleLine() rejects anything that isn't exactly "number,number"
          (or L0/L1/D0/D1) with "ERR bad format: ...".
    [x] Boot state: both servos at 90, laser LOW, LED off
          setup(): the laser pin is driven LOW first, servo.write(90) is called
          BEFORE attach(), then setLed(false).
    [x] A flood of serial data can't crash it
          The line buffer has a fixed size (LINE_MAX); extra characters are
          dropped and the line is answered with "ERR line too long". The
          Arduino's own 64-byte receive buffer simply drops bytes when full.
          No String class is used, so memory can't fragment.

  !!! HARDWARE NOTE (Phase 5 wiring): while the Uno RESETS (every time the
  laptop opens the serial port) its bootloader runs for ~1-2 s BEFORE setup().
  During that time pin 7 is not driven (it "floats"), so the 2N2222's base
  could pick up noise and partly switch the laser on. No code can prevent
  this. Fix: a 10 kOhm resistor from pin 7 to GND (a "pull-down") so the
  transistor is held OFF whenever the pin isn't driven.

  turret.ino  -  drive the pan/tilt servos, laser, LOCKED LED and fire
                 button from the laptop.

  What it does:
    - Listens on the USB serial port at 115200 baud for lines of text like
          90,45
      meaning "pan servo to 90 degrees, tilt servo to 45 degrees".
    - Clamps (limits) both angles to 20..160 so the pan-tilt bracket never
      slams into its mechanical end stops (that can strip servo gears).
    - Ignores anything that isn't exactly "number,number" and keeps running.
    - Replies to every line so the laptop knows what happened:
          "OK 90,45"         -> servos moved (shows the angles after clamping)
          "ERR bad format"   -> line was ignored
    - Prints "READY" once at startup.

  Laser commands (pin 7, through the 2N2222 transistor):
          L1   -> laser ON   (ONLY if LASER_ENABLED below is true!)
          L0   -> laser OFF
    With LASER_ENABLED = false (the default) "L1" is refused with an
    "ERR laser disabled" reply and the pin stays LOW. Pin 7 can never
    go HIGH.

  LOCKED LED (pin 8, through a 220 ohm resistor):
          D1   -> LED on      D0 -> LED off

  FIRE BUTTON (pin 2 to GND, using the Arduino's built-in pull-up resistor):
    The Arduino reports the button to the laptop:
          B1   -> button pressed      B0 -> button released
    It sends the current state once after "READY", then on every change.
    The button does NOT switch the laser by itself - the laptop decides
    (it must also be locked, etc.), and "L1" is still refused unless
    LASER_ENABLED is true here.

  SAFETY TIMEOUT: if no valid command arrives for 500 ms (laptop program
  froze, crashed, or the USB cable was pulled), the laser AND the LED turn
  OFF by themselves. The laptop sends commands ~30 times per second while
  running, so in normal use this never triggers.

  Hobby servo basics:
    A servo has 3 wires: power, ground and signal. The Arduino sends a pulse
    on the signal wire about 50 times a second; the pulse's LENGTH
    (about 0.5 to 2.4 milliseconds) tells the servo which angle to hold.
    The Servo library generates those pulses for us - we just call
    servo.write(angle).
*/

#include <Servo.h>
#include <stdlib.h>   // for strtol(), which turns text into a number
#include <string.h>   // for strcmp(), which compares two pieces of text

// ---------------------------------------------------------------------------
// LASER SAFETY SWITCH
// false = the laser can NEVER turn on, whatever the laptop sends.
// Only change to true in Phase 5, after the laser wiring is checked.
// ---------------------------------------------------------------------------
const bool LASER_ENABLED = false;

// If no valid command arrives for this long, turn the laser and LED off.
const unsigned long COMMAND_TIMEOUT_MS = 500;

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
const int PAN_PIN = 9;          // pan servo signal wire (left/right)
const int TILT_PIN = 10;        // tilt servo signal wire (up/down)
const int LASER_PIN = 7;        // laser transistor (only used if LASER_ENABLED)
const int LED_PIN = 8;          // "LOCKED" LED
const int BUTTON_PIN = 2;       // fire button (other leg to GND)

// A mechanical button "bounces": for a few milliseconds after you press it,
// the contacts flicker on/off many times. We only accept a new state once
// it has stayed the same for DEBOUNCE_MS.
const unsigned long DEBOUNCE_MS = 30;

const int MIN_ANGLE = 20;       // never go below this angle
const int MAX_ANGLE = 160;      // never go above this angle
const int START_ANGLE = 90;     // both servos start pointing straight ahead

const long BAUD_RATE = 115200;  // must match the Python script

// ---------------------------------------------------------------------------
// Global variables
// ---------------------------------------------------------------------------
Servo panServo;
Servo tiltServo;

// Incoming characters are collected here until a newline ('\n') arrives.
// We use a fixed-size char array instead of Arduino's String class because
// String can slowly fragment the Uno's tiny 2 KB of memory and cause
// random crashes in programs that run for a long time.
const int LINE_MAX = 32;        // longest line we accept (plenty for "160,160")
char line[LINE_MAX];
int lineLength = 0;
bool lineTooLong = false;

bool laserOn = false;
bool ledOn = false;
unsigned long lastCommandMs = 0;   // time (from millis()) of the last valid command

// Button debounce state. With INPUT_PULLUP the pin reads HIGH when the
// button is NOT pressed and LOW when it IS pressed (it connects to GND).
int buttonStable = HIGH;           // the accepted (debounced) state
int buttonLastReading = HIGH;      // the raw reading last time we looked
unsigned long buttonChangedMs = 0; // when the raw reading last changed


// The ONLY function that touches the laser pin.
void setLaser(bool on) {
  if (!LASER_ENABLED) {
    on = false;                    // safety switch off: force OFF, always
  }
  laserOn = on;
  digitalWrite(LASER_PIN, on ? HIGH : LOW);
}


void setLed(bool on) {
  ledOn = on;
  digitalWrite(LED_PIN, on ? HIGH : LOW);
}


void sendButtonState() {
  Serial.println(buttonStable == LOW ? "B1" : "B0");
}


// Read the button, debounce it, and report changes as B1 / B0.
void checkButton() {
  int reading = digitalRead(BUTTON_PIN);
  if (reading != buttonLastReading) {
    buttonChangedMs = millis();    // still bouncing: restart the wait
    buttonLastReading = reading;
  }
  if (millis() - buttonChangedMs > DEBOUNCE_MS && reading != buttonStable) {
    buttonStable = reading;        // steady long enough: accept it
    sendButtonState();
  }
}


void setup() {
  // Laser OFF first thing, before anything else can happen.
  digitalWrite(LASER_PIN, LOW);
  pinMode(LASER_PIN, OUTPUT);
  setLaser(false);

  pinMode(LED_PIN, OUTPUT);
  setLed(false);

  // INPUT_PULLUP turns on a resistor inside the Arduino that pulls the pin
  // up to 5V, so we don't need an external resistor for the button.
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  buttonStable = digitalRead(BUTTON_PIN);
  buttonLastReading = buttonStable;

  // Calling write() BEFORE attach() makes the very first pulse already
  // be 90 degrees, so the servos don't twitch to a random angle at boot.
  panServo.write(START_ANGLE);
  tiltServo.write(START_ANGLE);
  panServo.attach(PAN_PIN);
  tiltServo.attach(TILT_PIN);

  Serial.begin(BAUD_RATE);
  Serial.println("READY");
  sendButtonState();               // tell the laptop the starting button state
}


void loop() {
  // Read every character that has arrived so far (if any).
  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\r') {
      // Windows sometimes sends "\r\n" at the end of a line. Ignore the \r.
      continue;
    }

    if (c == '\n') {
      // End of line: process what we collected, then start a new line.
      line[lineLength] = '\0';   // '\0' marks the end of a C text string
      if (lineTooLong) {
        Serial.println("ERR line too long");
      } else if (lineLength > 0) {
        handleLine(line);
      }
      lineLength = 0;
      lineTooLong = false;
    } else if (lineLength < LINE_MAX - 1) {
      // Normal character: add it to the line (leave room for the '\0').
      line[lineLength] = c;
      lineLength++;
    } else {
      // Line is longer than our buffer: remember to throw it away at '\n'.
      lineTooLong = true;
    }
  }

  checkButton();

  // Safety timeout. millis() = milliseconds since the Arduino started.
  // (Subtracting like this still works when millis() wraps around after
  // ~50 days, because both numbers are "unsigned long".)
  if ((laserOn || ledOn) && (millis() - lastCommandMs > COMMAND_TIMEOUT_MS)) {
    setLaser(false);
    setLed(false);
    Serial.println("TIMEOUT: laser and LED off (no command for 500 ms)");
  }
}


// Handle "D1" (LED on) or "D0" (LED off).
void handleLedCommand(bool wantOn) {
  lastCommandMs = millis();
  setLed(wantOn);
  Serial.println(wantOn ? "OK D1" : "OK D0");
}


// Handle "L1" (on) or "L0" (off).
void handleLaserCommand(bool wantOn) {
  lastCommandMs = millis();
  if (wantOn && !LASER_ENABLED) {
    setLaser(false);
    Serial.println("ERR laser disabled in sketch (LASER_ENABLED = false)");
    return;
  }
  setLaser(wantOn);
  Serial.println(wantOn ? "OK L1" : "OK L0");
}


// Parse a line like "90,45" and move the servos.
// Anything else (e.g. "hello", "90", "90,", "90.5,45", "90,45,10")
// is rejected and the servos don't move.
void handleLine(char* text) {
  // Laser commands first: exactly "L1" or "L0"
  if (strcmp(text, "L1") == 0) {   // strcmp() returns 0 when the texts match
    handleLaserCommand(true);
    return;
  }
  if (strcmp(text, "L0") == 0) {
    handleLaserCommand(false);
    return;
  }
  // LED commands: exactly "D1" or "D0"
  if (strcmp(text, "D1") == 0) {
    handleLedCommand(true);
    return;
  }
  if (strcmp(text, "D0") == 0) {
    handleLedCommand(false);
    return;
  }

  char* end;   // strtol() sets this to the first character it didn't use

  // 1) First number (pan)
  long pan = strtol(text, &end, 10);
  if (end == text) {               // no digits at all
    reportBadLine(text);
    return;
  }

  // 2) A comma, possibly with spaces around it
  while (*end == ' ') end++;
  if (*end != ',') {
    reportBadLine(text);
    return;
  }
  char* tiltStart = end + 1;

  // 3) Second number (tilt)
  long tilt = strtol(tiltStart, &end, 10);
  if (end == tiltStart) {          // nothing after the comma
    reportBadLine(text);
    return;
  }

  // 4) Nothing else allowed after it (except trailing spaces)
  while (*end == ' ') end++;
  if (*end != '\0') {
    reportBadLine(text);
    return;
  }

  // 5) Clamp to the safe range, then move.
  //    constrain(x, low, high) returns x limited to low..high.
  pan = constrain(pan, MIN_ANGLE, MAX_ANGLE);
  tilt = constrain(tilt, MIN_ANGLE, MAX_ANGLE);

  panServo.write((int)pan);
  tiltServo.write((int)tilt);
  lastCommandMs = millis();        // valid command -> reset the safety timeout

  Serial.print("OK ");
  Serial.print(pan);
  Serial.print(",");
  Serial.println(tilt);
}


void reportBadLine(const char* text) {
  Serial.print("ERR bad format: ");
  Serial.println(text);
}
