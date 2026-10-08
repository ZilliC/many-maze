# TODO — ANY-maze parity

Features ANY-maze has that mANY-MAZE does not (yet), from the ANY-maze feature pages and its complete measure list
(<https://www.any-maze.com/features/>, <https://www.any-maze.com/support/guides/results-available-in-any-maze/>),
checked against the code on 2026-10-06 and updated on 2026-10-08. Roughly in order of impact within each section.
Hardware support below is tested against fake devices and simulated replies only; items marked *(untested on
hardware)* still need a check with the real device.

## 1. Tracking and video

- [x] Automatic rearing detection, with measures: number of rears, time rearing, latency to first rear,
      mean / max / min rear duration — whole test and per zone
- [x] Zone entry that requires the animal to be oriented towards the zone
- [x] Camera hardware settings: exposure, gain, brightness, contrast, saturation, white balance, focus (auto / manual),
      saved per camera and adjustable live; unsupported settings are reported (macOS' AVFoundation accepts few)
      *(untested on hardware)*
- [x] Native industrial cameras (GigE / USB3 Vision through harvesters / GenTL, Basler pypylon, FLIR Spinnaker, IDS
      peak; external trigger) and analogue capture cards (UVC / OpenCV devices) *(untested on hardware)*
- [x] Verify / support ANY-maze's scale: up to 48 cameras and 40 simultaneous apparatus
- [x] Adjust apparatus calibration during a running test
- [x] Whole-body outline tracking and display

## 2. Whole-apparatus measures

- [x] Activity (pixel-change based, separate from mobility): time active / inactive, active / inactive episodes,
      longest / shortest active and inactive episode
- [x] Average freezing score
- [x] First zone entered
- [x] Visited zone list, investigated zone list
- [x] Total distance travelled by the head
- [x] Head turn angle: absolute, clockwise, anticlockwise
- [x] Average speed when not hidden
- [x] Latency to first mobile episode; latency to start of last mobile / last immobile episode
- [x] Shortest mobile / immobile / freezing episode
- [x] Tracking quality; number of centre positions and head positions recorded; % of tracked frames head tracked
- [x] Total number of line crossings (all lines)
- [x] On/off inputs positive / negative reversal

## 3. Information columns in the results

Today: Test, Animal, Group, Sex, Stage, Trial, Apparatus, Period.

- [x] Test date, day of the week, test time (from the live recording time; blank for video tests)
- [x] Time of day
- [x] User (experimenter) — needs user identity per test
- [x] Test notes
- [x] Animal notes (animals have no notes field yet)
- [x] Treatment code (blind code)
- [x] Reason for test end
- [x] Animal lighter / darker than apparatus; animal length
- [x] Percentage of frames tracked
- [x] Source video file, recorded video file, video time when the test started
- [x] Location of moveable zones
- [x] Segment of test

## 4. Zone measures (ANY-maze: 64, mANY-MAZE: ~20)

- [x] Investigation as separate measures (today an investigation zone just counts as being in the zone):
  - [x] bouts of investigation, total time investigating, latency to first investigation, latency to end of
        first investigation, was first zone investigated
  - [x] longest / shortest / mean investigation bout, list of investigation durations
  - [x] distance travelled while investigating, distance before first investigation, mean speed while investigating
  - [x] time mobile / immobile, immobile episodes, freezing bouts and time while investigating
- [x] Number of exits, latency to first exit, latency to last entry
- [x] Latency to first head exit
- [x] Was first zone entered
- [x] Shortest visit
- [x] List of the duration of each visit
- [x] Hidden zones: number of partial exits, time partially exited
- [x] Distance travelled by the head in the zone
- [x] Time the head was in the zone while the centre was outside
- [x] Distance from the zone when outside: initial, max, min, cumulative
- [x] Head distance from the zone: mean / max / min
- [x] Distance to the zone border when inside: mean / max / min, for the centre and the head
- [x] Time getting closer to / further away from the zone
- [x] Time moving towards / away from the zone
- [x] Initial heading error (signed and absolute) to any zone; mean absolute heading error
- [x] Time oriented towards the zone; time oriented towards the zone centre when inside
- [x] Absolute turn angle and absolute head turn angle while in the zone
- [x] Time active / inactive, inactive episodes in the zone
- [x] Rearing in the zone (see §1)
- [x] Whishaw's corridor distance travelled (time / path % exist)
- [x] Corrected integrated path length (CIPL)
- [x] Number of line crossings while in the zone

## 5. Point, sequence and key measures

- [x] Point: mean / max / min distance of the head from the point
- [x] Point: time the head was moving towards / away from the point
- [x] Point: mean speed moving towards the point
- [x] Point: initial heading error, mean absolute heading error
- [x] Point: X / Y coordinate; approximate time at the point
- [x] Sequence: total / mean / max / min distance travelled during sequences
- [x] Sequence: mean speed during the sequence
- [x] Key: latency to first release
- [x] Key: distance travelled before first press
- [x] Key: shortest press
- [x] Key: list of press durations

## 6. I/O and procedure measures

- [x] On/off inputs and outputs: longest / shortest activation, latency to first deactivation
- [x] Separate measure groups for speakers, shockers and light controllers
- [x] Rotary encoder: time turning, clockwise / anticlockwise rotations, reversals, half and quarter rotations,
      degrees clockwise / anticlockwise, minimum RPM, mean RPM while turning
- [x] Analogue signals: time of max / min, baseline, baseline deviation and SD, end of baseline period,
      first positive / negative deviation and return to baseline, integral above / below baseline,
      mean max / min (and time to them) per zone visit, mean value at zone entry / exit
- [x] Virtual switches: distance travelled before first activation, distance travelled while active
- [x] Result variables recorded "every time it is changed" / "only when explicitly set":
      mean, max, min, sum, count, list of values (today only the final value)

## 7. Hardware and devices

- [x] Interfaces with the roles of ANY-maze's own (operant, digital, optogenetic, synchronisation, relay, audio, touch,
      analogue, USB TTL cable, remote): the Arduino firmware, Firmata boards, National Instruments (NI-DAQmx), LabJack,
      a USB-serial cable's control lines, text-command serial devices, the speakers, the touch screen and keys /
      presenter remotes. ANY-maze's own boxes (AMi, ANY-box) use an undocumented protocol and cannot be driven
      *(NI, LabJack, Firmata, serial lines untested on hardware)*
- [x] Analogue signal filters (low-pass, high-pass, band-pass Butterworth, moving average) and sampling at up to
      1 kHz (batched by the firmware, samples logged at their own times)
- [x] Sensors (weight through an HX711 load cell, light, temperature / humidity through a DHT22, any analogue sensor)
      with initial / final / mean / max / min / change / intake / time out of range measures and out-of-range alerts by
      e-mail (SMTP) and SMS (Twilio or e-mail gateway)
- [x] Movement detectors (PIR) with movements, time moving, latency to first movement
- [x] Animal scale integration: serial balances (MT-SICS, Ohaus, Sartorius, A&D, Kern, continuous), Animals ▸ Weigh
      with weight history, *Weigh the animal* during a test *(untested on hardware)*
- [x] Syringe pumps: 18 manufacturers (20 families incl. DIY / other), 131 predefined syringes, custom syringes, rate,
      direction, target volume, stall detection, daisy chains; volume infused / withdrawn measures *(untested on
      hardware; some protocols and syringe diameters flagged as unverified in pumps.py)*
- [x] Temperature controllers (PID heat / cool from a sensor, or a serial controller's set-point; ramps; safety
      limits)
- [x] Lighting controllers (light levels, ramps)
- [x] Odour delivery (olfactometer valves, clean air, air flow through a mass-flow controller)
- [x] Liquid dippers / drippers
- [x] Pellet dispenser error detection (pellet sensor, retries, error event and measures)
- [x] Shock intensity control (mA) and shocker calibration (I/O devices ▸ Calibrate…)
- [x] Optogenetic laser intensity, duty cycle, pulse sequences from a file
- [x] Play a sound file repeatedly (loop)
- [x] Procedure events / actions for all of the above (mANY-MAZE now: 62 events, 72 actions)

## 8. Specific apparatus

- [x] OPAD (operant plantar assay): temperature when contact broken, non-lick contacts, per temperature of interest:
      time in contact, contacts made / broken, licks
- [x] RAPC (radial arm place conditioning): type 1 errors, type 2 errors, door sequence

## 9. Experiment management

- [x] Free-form notes per animal
- [x] User accounts / experimenter recorded with each test
- [x] Manually end a stage for one animal before it completes all trials (check: may only be possible through
      training criteria today)

## 10. Results and data transfer

- [ ] (Not possible) Open native ANY-maze experiment files: the .szd format is proprietary and undocumented, so it is not read;
      File ▸ Import from ANY-maze now reads ANY-maze's documented exports instead (experiment XML export, zone maps,
      SYLK / dBase data); the XML reader is checked against a real ANY-maze export (§15)
- [x] Save as SYLK and dBase III/IV
- [x] Animated track playback at variable speed in the track-plot view
- [x] Chart zoom with the mouse wheel

## 11. Product and distribution

- [x] Check for updates from inside the app
- [x] Signed and notarised macOS build when Developer ID secrets are set (see README; untested until a certificate
      is available — without secrets the build stays ad-hoc signed)
- [ ] (Not applicable to a GPL project) licence tiers such as TakeNote-only and network licences — the TakeNote
      functionality itself is the observation-only live mode

---

Sections 12–15 come from a second audit (2026-10-08) of the code against ANY-maze's full measure list, its feature pages,
the 7.x release notes and the procedure help (<https://www.anymaze.co.uk/help/>). Hardware-only items are in §7.

## 12. Measures still missing (audit 2026-10-08)

- [ ] Zone: time active, time inactive, inactive episodes (activity is whole-test only, measures.py:1132)
- [ ] Time not hidden (only "Time hidden" today)
- [ ] Total body rotations (only clockwise / anticlockwise columns)
- [ ] Whishaw's corridor time in seconds, and the corridor for any zone (today a water-maze template measure, % only)
- [ ] Average position (mean X / Y of the animal; ANY-maze 7.50)
- [ ] Sequence: latency to start of first sequence (only latency to completion, sequences.py:181)
- [ ] Average activation duration for plain outputs, light controllers, optogenetic lasers and virtual switches
      (generic branch of `_output`, iomeasures.py:226)
- [ ] Frequency of activations (per minute) for outputs, speakers, shockers, lights, lasers and virtual switches
- [ ] Rotary encoder: total number of rotations (today net signed revolutions) and maximum RPM (today counts/s)
- [ ] Per-zone device measures for inputs, outputs, speakers, shockers, lights, lasers, pellets, encoders and virtual
      switches (only analogue signals have them)
- [ ] Per-zone key measures: longest / shortest press, latency to first release, distance before first press,
      list of press durations (today only count, duration, latency, mean bout, rate)
- [ ] Grid cells: the advanced zone measures (visit list, head, border, heading, turning, CIPL, line crossings);
      zone groups: border, heading and CIPL measures (measures.py:616, 737)
- [ ] Check definitions against ANY-maze:
  - [ ] Average distance from zone: ANY-maze averages only while outside; we count inside frames as 0 (measures.py:596)
  - [ ] Initial heading error: ANY-maze's "Initial heading error" is the absolute value and "Signed …" the signed
        one; our names are the other way round (matters for the ANY-maze import / column mapping)
  - [ ] Body rotations fall back to direction of travel when no body angle is tracked; ANY-maze uses body orientation
- [ ] Results table: add the missing whole-test measures (mobile episodes, latency to last mobile episode, longest
      mobile episode, mean speed when not hidden, path tortuosity, first zone entered, visited zones …) to
      `GENERAL_PREFIXES` (gui/pages/results/table.py:13) so they are not grouped under "Other"

## 13. Procedures (audit 2026-10-08; ANY-maze ~71 events / 75 actions, mANY-MAZE now 90 / 99)

Structure and statements:
- [x] Pre-test section ("Test is waiting to start") with Prevent / Allow test start
- [x] Sub-procedures: Run sub-procedure statement and action
- [x] Go to / Label statements
- [x] Repeat until (body runs at least once)
- [x] If … else-if chains (today only if / else, engine.py:858)
- [x] Wait until event A *or* event B, then test which one fired (today one event + timeout, engine.py:914)
- [x] Set timer resolution (accepted and recorded; waits and timers already run on their exact due times)

Actions:
- [x] End test with a reason, allow continuation, "Test continuation" event (reason is always END_PROCEDURE, live.py:420)
- [x] Schedule another test for this animal
- [x] Set / remove zone label; set moveable zone location (by x / y: mANY-MAZE has no predefined positions for a
      moveable zone, which is what ANY-maze's action chooses between)
- [x] Video recorder: start / stop / pause / unpause / label
- [x] Pop-up message, text on the display (output / remove / clear), SMS, e-mail
- [x] Generate error / warning (today only `log`)
- [x] Run a program; trigger a plug-in
- [x] Separate setters for output frequency / duty / duration and speaker volume (today action parameters only)

Events:
- [x] Investigation of a zone starts / stops
- [x] Animal oriented towards / away from a zone or point
- [x] Hidden-zone partial exit
- [x] Centre / head / tail position changed
- [x] Rearing starts / stops (and rearing variables)
- [x] Rotary encoder: turning clockwise / anticlockwise, direction reversed, RPM (today count only)
- [x] Speaker starts / stops / end of sound file
- [x] Analogue output changed
- [x] Disk full, disk space low, recording error
- [x] Event-wizard triggers: time until test end, random interval, time of day, X times in Y s, value held for a
      duration, change within a time, apply only to some trials; "fails to enter zone for a time" as a real trigger

Variables and functions:
- [x] Built-in variables: test running / paused, stage, trial, apparatus, treatment, animal number, date and time of
      day, cumulative freezing / immobile time, distance from zone / point, head and tail x / y, position as % of
      width, sequence duration, animal field values, speaker state (engine.py:55)
- [x] Retained variables per animal and per apparatus (today one project-wide store, project.py:159)
- [x] Randomise an array with a maximum number of consecutive repeats (only `shuffle`, expr.py:78)
- [x] #N/A constant and Is undefined function
- [x] ANY-maze compatible trigonometry (degrees) and Log (base 10): degree functions and a per-procedure
      "ANY-maze maths" option (no ANY-maze protocol importer exists to set it automatically)
- [x] Analogue output level in volts as in ANY-maze (today 0–1)

## 14. Application features (audit 2026-10-08, ANY-maze feature pages and 7.x release notes)

- [x] Automatic freezing threshold with a sensitivity setting (ANY-maze 7.00; today manual on / off thresholds)
- [x] Live charts of more parameters (ANY-maze 40+, today 5: speed, distance, motion, detected, freezing — live.py:202)
- [x] Live point, sequence and input statistics in the monitor during a test
- [x] Show the animal's orientation live (7.00 "flashlight beam"; tracking.py:778 draws no heading)
- [x] Change apparatus geometry (not only calibration) during a running test
- [x] Auto-start when the operator's hand leaves the image (today: start when the animal is first detected)
- [x] Automatic recovery when video capture drops (camera.py:134 just stops)
- [x] Export the zone map as an image (PNG / SVG)
- [x] Low-disk-space check when opening / recording
- [x] Count the statistical tests against ANY-maze's "more than 30" (17 named in statistics.py:29 plus post-hoc)
- [ ] (Obsolete, not done) DVD as a video source

## 15. Not verified

- [ ] Numerical agreement of our measures with ANY-maze on the same video: still needs a video together with
      ANY-maze's results for it (none is public). Zone occupancy is verified: on a real ANY-maze water-maze export
      (600 tests, CowenLab/Ovariectomy_and_development, no licence so not shipped) zone entries, time in zone and
      latency to first entry agree with ANY-maze's own entries / exits in 94–100 % of test × zone pairs
      (`scripts/verify_anymaze.py`; opt-in test with `MANYMAZE_ANYMAZE_XML`). Measure definitions were checked
      against ANY-maze's "A detailed description of the ANY-maze measures"
- [x] ANY-maze XML import against a real ANY-maze file (nested dates and times, the apparatus box, zone entries /
      exits; zone shapes fitted to ANY-maze's occupancy)
- [x] Hardware-related procedure elements (sensors, pellet out / failed, syringe pumps, temperature, light ramps,
      laser intensity / pulse files, selectors, shock mA) belong to §7
