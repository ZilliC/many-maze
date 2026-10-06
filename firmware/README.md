# mANY-MAZE I/O firmware

`manymaze_io/manymaze_io.ino` turns an Arduino (Uno, Nano, Mega, Leonardo, Due, Zero, RP2040, ESP32…)
into an I/O box for live tests: levers, nose pokes, beam breaks, TTL inputs, lights, pellet dispensers,
doors, shocker triggers, optogenetic lasers, sync pulses, running wheels and analogue sensors. It is the
libre equivalent of the AMi / ANY-maze interface boxes.

## Installing

1. Open `manymaze_io/manymaze_io.ino` in the Arduino IDE (or `arduino-cli compile -b arduino:avr:uno manymaze_io`).
2. Upload it to the board.
3. In mANY-MAZE: **Experiment › I/O devices › Add**, type *Arduino*, choose the serial port and list the
   channels (name, kind, pin). Press **Connect** to check the live status and toggle outputs.

pyserial is needed on the computer: `pip install pyserial`.

## Wiring

| Kind | Typical use | Wiring |
|---|---|---|
| Digital input (`input`) | lever, nose poke, beam break, push button, TTL from another device | Switch between the pin and GND (internal pull-up, the default: *closed = on*). For a TTL/active-high signal set `"pullup": false` (signal → pin, grounds connected). `invert` flips the logic. Debounce default 20 ms. |
| Digital output (`output`) | LED / house light, pellet dispenser, door, solenoid, shocker trigger, laser TTL, sync pulse | Pin → TTL input of the device, or → a logic-level MOSFET / relay module / opto-isolator for anything that draws more than ~10 mA. **Never drive motors, solenoids or relays directly from a pin**; use a driver with a flyback diode. `invert` for active-low inputs. |
| PWM output (`pwm`) | dimmable light, LED intensity, analogue level via an RC filter | PWM-capable pin (marked `~`). Level 0–1 from procedures. |
| Analogue input (`analog`) | force sensor, photodiode, lickometer, temperature | Sensor output (0–5 V or 0–3.3 V, as the board) → A*n*; the channel `pin` is *n* (0 = A0). `scale` converts the 0–1023 reading (e.g. 0.00489 for volts on a 5 V board). `deadband` (counts) limits reports. |
| Rotary encoder (`encoder`) | running wheel, rotarod, treadmill | Quadrature A → `pin`, B → `pin_b`, encoder V+ and GND. Interrupt pins (Uno: 2 and 3) are best; other pins are polled. `counts_per_rev` (4 × the encoder's pulses per revolution) and `cm_per_rev` (wheel circumference) give revolutions and distance. |

Shockers: use a commercial constant-current shocker with a TTL *enable* input; the box only sends the
trigger. Every shock action has a safety cut-off (default 2 s, hard limit 60 s) enforced both by
mANY-MAZE and by the board (`W pin 1 max_ms`). All outputs go off when a test ends or is paused, when
mANY-MAZE disconnects normally (it sends `R` before closing the port) and when the heartbeat watchdog fires.
The board cannot notice that the serial port was closed: if mANY-MAZE crashes or the USB cable is pulled,
only the watchdog switches the outputs off. mANY-MAZE turns the watchdog on (2000 ms) for every board that has
outputs unless *Watchdog* is set otherwise in the device settings, and sends the heartbeat from a background
thread, so pausing a test or a stalled camera does not trip it.

Optogenetics: connect the laser/LED driver's TTL modulation input to an output pin. Pulse trains are timed
on the board with microsecond resolution (e.g. 20 Hz, 5 ms pulses), independently of the video frame rate.

## Protocol

ASCII lines at 115200 baud, terminated by `\n`. Pins are the Arduino pin numbers.

Computer → board:

| Command | Meaning |
|---|---|
| `?` | identify: replies `MANYMAZE_IO <version> <board>` |
| `Z` | clear the whole configuration (all outputs off) |
| `I pin pullup debounce_ms` | configure a digital input (pullup 1/0); its state is reported at once and on every change |
| `O pin invert` | configure a digital/PWM output (off) |
| `W pin 0\|1 [max_ms]` | switch an output; with `max_ms` it switches itself off after that time |
| `P pin 0..255` | PWM level |
| `T pin period_ms width_ms count` | pulse train (count 0 = until stopped); decimals allowed, e.g. `T 9 50 5 200` = 20 Hz, 5 ms, 10 s |
| `X pin` | stop a pulse train, output off |
| `A n period_ms deadband` | report analogue input A*n* every period when it changed by more than deadband |
| `E pinA pinB` | quadrature encoder (count reset to 0) |
| `R` | all outputs off, pulse trains stopped (configuration kept) |
| `Q` | report all inputs now |
| `H timeout_ms` | heartbeat watchdog: all outputs off if no line arrives within the timeout (0 = off; mANY-MAZE sends `H 2000` by default when outputs are configured) |
| `.` | heartbeat (no reply) |

Board → computer:

| Report | Meaning |
|---|---|
| `D pin level ms` | digital input level changed (raw level; with the pull-up, 0 = switch closed) |
| `A n value ms` | analogue reading 0–1023 |
| `E pinA count ms` | encoder count (signed, at most every 20 ms) |
| `WATCHDOG` | the watchdog switched all outputs off |
| `ERR text` | the last command was invalid |

`ms` is the board's `millis()` clock, useful to align with electrophysiology recordings; mANY-MAZE keeps it in
the I/O log of the test (`board_ms` of each input event).

Any other text device (a commercial controller, a Raspberry Pi script…) can be driven with the *Serial
port (text commands)* device type instead: each output channel has an "on" and an "off" command string,
each input channel the strings that the device sends when it switches on/off.
