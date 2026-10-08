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
      SYLK / dBase data) — the XML reader is still unverified against a real ANY-maze file
- [x] Save as SYLK and dBase III/IV
- [x] Animated track playback at variable speed in the track-plot view
- [x] Chart zoom with the mouse wheel

## 11. Product and distribution

- [x] Check for updates from inside the app
- [x] Signed and notarised macOS build when Developer ID secrets are set (see README; untested until a certificate
      is available — without secrets the build stays ad-hoc signed)
- [ ] (Not applicable to a GPL project) licence tiers such as TakeNote-only and network licences — the TakeNote
      functionality itself is the observation-only live mode
