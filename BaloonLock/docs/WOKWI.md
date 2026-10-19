# Testing turret.ino in the Wokwi simulator (no hardware needed)

[Wokwi](https://wokwi.com) is a free website that simulates an Arduino Uno with servos, LEDs and buttons in your browser. You can check every safety rule of `turret/turret.ino` before you own any parts.

> Wokwi is a simulator. It checks the *code's* behavior, not your real wiring, power supply or servos.

## 1. Set up the project

1. Go to **wokwi.com** → **Start from scratch** → **Arduino Uno**.
2. In the `sketch.ino` tab, delete everything and paste the whole contents of `turret/turret.ino`.
3. Click the **Library Manager** tab (the books icon) → **+** → add **Servo**.
4. Click the `diagram.json` tab, delete everything, and paste the block below. It adds the servos, the "laser" LED, the LOCK LED and the fire button, already wired.

```json
{
  "version": 1,
  "author": "BalloonLock",
  "editor": "wokwi",
  "parts": [
    { "type": "wokwi-arduino-uno", "id": "uno", "top": 120, "left": 0, "attrs": {} },
    { "type": "wokwi-servo", "id": "pan", "top": -120, "left": -40, "attrs": {} },
    { "type": "wokwi-servo", "id": "tilt", "top": -120, "left": 200, "attrs": {} },
    { "type": "wokwi-led", "id": "laser", "top": -20, "left": 420, "attrs": { "color": "red", "label": "LASER (pin 7)" } },
    { "type": "wokwi-resistor", "id": "r1", "top": 60, "left": 400, "rotate": 90, "attrs": { "value": "220" } },
    { "type": "wokwi-led", "id": "lock", "top": -20, "left": 500, "attrs": { "color": "green", "label": "LOCK (pin 8)" } },
    { "type": "wokwi-resistor", "id": "r2", "top": 60, "left": 480, "rotate": 90, "attrs": { "value": "220" } },
    { "type": "wokwi-pushbutton", "id": "fire", "top": 0, "left": 580, "attrs": { "color": "red", "label": "FIRE (pin 2)" } }
  ],
  "connections": [
    [ "pan:PWM", "uno:9", "orange", [] ],
    [ "pan:V+", "uno:5V", "red", [] ],
    [ "pan:GND", "uno:GND.1", "black", [] ],
    [ "tilt:PWM", "uno:10", "orange", [] ],
    [ "tilt:V+", "uno:5V", "red", [] ],
    [ "tilt:GND", "uno:GND.1", "black", [] ],
    [ "uno:7", "r1:1", "green", [] ],
    [ "r1:2", "laser:A", "green", [] ],
    [ "laser:C", "uno:GND.2", "black", [] ],
    [ "uno:8", "r2:1", "green", [] ],
    [ "r2:2", "lock:A", "green", [] ],
    [ "lock:C", "uno:GND.2", "black", [] ],
    [ "uno:2", "fire:1.l", "blue", [] ],
    [ "fire:2.l", "uno:GND.3", "black", [] ]
  ],
  "serialMonitor": { "display": "terminal", "newline": "lf" }
}
```

If Wokwi complains about the diagram, add the parts by hand with the **+** button and wire them like this:
- **Servos:** signal to pins **9** (pan) and **10** (tilt), V+ to 5V, GND to GND.
- **"Laser" LED** (red): pin **7** → 220 Ω → LED → GND.
- **LOCK LED** (green): pin **8** → 220 Ω → LED → GND.
- **Fire button:** one leg to pin **2**, the opposite leg to GND.

In the real world, the servos get their own 5 V supply (see the README). In the simulator, the Uno's 5V is fine.

5. Press the green **▶ Start** button. The **serial monitor** opens at the bottom. Type each command into its input box and press **Enter**. The `"newline": "lf"` setting above makes Enter send the newline the sketch needs.

## 2. Check each safety rule

| # | Rule being checked | Type this | Expected reply / what you should see |
|---|---|---|---|
| 1 | **Boot state** | *(nothing, just start)* | `READY` then `B0`. Both servo horns point to the middle (90°). Both LEDs are **off**. |
| 2 | Normal move | `90,45` | `OK 90,45`. The tilt servo moves. |
| 3 | **Angles clamped to 20–160** | `10,200` | `OK 20,160` (not 10,200) |
| 4 | Clamped, negative | `-50,90` | `OK 20,90` |
| 5 | Clamped, huge number | `99999999,90` | `OK 160,90` |
| 6 | **Malformed lines ignored** | `hello` | `ERR bad format: hello` (servos don't move) |
| 7 | Malformed | `90` | `ERR bad format: 90` |
| 8 | Malformed | `90,` | `ERR bad format: 90,` |
| 9 | Malformed | `90.5,45` | `ERR bad format: 90.5,45` |
| 10 | Malformed | `1,2,3` | `ERR bad format: 1,2,3` |
| 11 | **Line too long / flood can't crash it** | `1234567890123456789012345678901234567890` (40 characters) | `ERR line too long` |
| 12 | Still alive after the flood | `90,90` | `OK 90,90` |
| 13 | **L1 refused while LASER_ENABLED is false** | `L1` | `ERR laser disabled in sketch (LASER_ENABLED = false)`. The red "laser" LED stays **off**. |
| 14 | L0 always accepted | `L0` | `OK L0` |
| 15 | LOCK LED | `D1` | `OK D1`, and the green LED turns **on**... |
| 16 | **500 ms timeout turns LED (and laser) off** | *(type nothing for 1 second after step 15)* | ...`TIMEOUT: laser and LED off (no command for 500 ms)`, and the green LED goes **off** by itself |
| 17 | Fire button press | click and hold the button | `B1` |
| 18 | Fire button release | let go | `B0` |

Step 16 happens fast. Typing by hand is far slower than the laptop program (which sends about 30 commands a second), so the green LED switches off half a second after `D1`. That's the timeout working.

## 3. Optional: see the laser timeout too (simulator only)

In the Wokwi copy **only**, change `const bool LASER_ENABLED = false;` to `true` and restart. Then:

| Type | Expected |
|---|---|
| `L1` | `OK L1`, and the red "laser" LED turns on... |
| *(wait 1 s)* | ...`TIMEOUT: laser and LED off (no command for 500 ms)`, and the red LED goes **off** |

**Never** make that change in the project's `turret/turret.ino` until the laser hardware has been checked; it stays `false` there.

## What Wokwi can't show you

- **Real servo behavior:** speed, jitter, power-supply dips.
- **The real wiring:** the shared ground and the separate 5 V supply.
- **What pin 7 does during reset.** While the Uno's bootloader runs (about 1–2 s after the laptop opens the port), pin 7 floats. On real hardware, add a **10 kΩ resistor from pin 7 to GND** so the laser transistor stays off during that time (see the note at the top of `turret.ino`).
