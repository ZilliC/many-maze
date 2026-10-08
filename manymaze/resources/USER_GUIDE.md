# mANY-MAZE user guide

mANY-MAZE is a libre (GPL-3.0-or-later) video-tracking and behavioural-analysis suite designed as a drop-in
replacement for ANY-maze: the window, the tabs, the terminology and the workflow follow ANY-maze, so ANY-maze users
can start right away — **Protocol → Experiment → Test → Results**.

## The window

* **Ribbon** — the tabs **File · Protocol · Experiment · Test · Results · Help** each show a ribbon of commands,
  grouped like in ANY-maze (e.g. *Apparatus map*, *Navigation*, *Clipboard*, *Spreadsheet*, *All apparatus*). The
  commands change with the page you are on. The save button is at the top right (⌘S / Ctrl+S).
* **File** (the blue tab) — new / open / demo experiment, save, close, recent experiments, *Import from ANY-maze*
  (animals, treatments and test schedules from spreadsheets saved by ANY-maze, ANY-maze's experiment XML export and
  zone maps — see *Importing from ANY-maze* in section 9), *Protocol report*, *Restore a backup*, *Archive
  experiment* / *Open archive*, user guide.
* **Help** — the user guide, *Check for updates* (asks GitHub for the latest release and offers to open its page;
  *Check for updates at startup*, off by default, does so at most once a week) and *About*.
* **Explorer** — the list on the left of each tab: the protocol elements (Protocol, Animal tracking, Stages, Keys,
  Procedures, Analysis, Hardware), each apparatus, the treatments and animals, the test schedule / run tests /
  review pages, the result views (Spreadsheet, Track plots, Heat maps, Charts, Video export) and the statistical
  analyses.

| ANY-maze | mANY-MAZE |
| --- | --- |
| Protocol ▸ Protocol, Animal tracking, Stages, Keys, Procedures | Protocol tab — the same elements |
| Protocol ▸ Apparatus (apparatus map, zones, points, sequences) | Protocol ▸ Apparatus |
| Experiment ▸ View treatments / View animals | Experiment tab |
| Test ▸ test schedule, running tests (panels), TakeNote | Test ▸ Test schedule, Run tests, Review and score |
| Results ▸ data spreadsheet, track plots, statistics | Results ▸ Data, Statistics |
| Treatment | Treatment (stored as the animal's group) |
| Keys: Simple / Toggle / Radio | Keys: Simple (while pressed) / Toggle / Radio / Event |

---

## 1. Create an experiment

*File ▸ New experiment…* asks for a name, a folder and a **protocol** (open field, elevated plus maze, Morris
water maze, Barnes maze, Y/T/radial maze, novel object recognition, light/dark box, three-chamber sociability,
fear conditioning, forced swim / tail suspension, or custom). The experiment is saved as a folder
`Name.mmaze/` containing `project.json`, `tracks/`, `recordings/` and `exports/`. Videos are referenced by
relative path when they live inside the experiment folder, so the folder can be moved or shared.

**Based on another experiment**: choose an existing experiment under *Based on* and the new one gets its protocol —
apparatus, stages, keys, test duration and start, animal tracking and analysis settings, procedures, I/O devices,
training criteria, blind testing and animal ID options, animal columns and (optionally) the treatments. Animals,
tests and results are not copied. Use it for a new cohort or a replication.

**Protocol report** (*File ▸ Protocol report*) saves a printable HTML description of the protocol: the experiment
options, stages, keys, a map of each apparatus with its zones, zone groups, points, lines and sequences (shape,
area, entry rule, options), the animal tracking and analysis settings (changed values are marked), the procedures
statement by statement, the I/O devices and the training criteria — for lab notebooks, methods sections and SOPs.

**Backups**: each time the experiment is saved, the previous experiment file is kept in `backups/` (at most one
backup every 10 minutes, the 30 newest are kept). *File ▸ Restore a backup* lists them by date and time and goes
back to the chosen one (the current state is backed up first). Tracks are not part of the backups.

**Archive experiment** (File tab) writes the whole experiment to one zip file: the experiment file, tracks,
recordings, exports and the video of every test — also videos stored outside the experiment folder, which are copied
into `videos/external/` (the tests in the archive point to the copies). Use it to move an experiment to another
computer or to keep it with a publication. **Open archive** unpacks an archive into a folder and opens it.
Automatic backups are not archived.

*File ▸ Create demo experiment* builds a complete open-field experiment from synthetic videos so you can try
everything without a camera.

### Protocol tab

* **Test duration** – analysed length of each test (0 = until the end of the video).
* **Test starts** – at each test's *start time* (set per test) or automatically **when the animal is first
  detected** in the apparatus.
* **Stages** – e.g. *Habituation, Day 1, Day 2, Probe*. Used for learning curves and repeated-measures
  statistics.
* **Keys** (manually scored behaviours) – name, key stroke and how the key works (*Simple* while pressed, *Toggle*,
  *Radio*; *Event* for instantaneous events — see §6). Old wording: *state* with a duration, e.g. grooming; *point*
  for instantaneous events, e.g. defecation).
* **Analysis ▸ Time periods** – named time windows (e.g. *Tone 1: 120–150 s*) that override regular time bins;
  ideal for fear-conditioning CS periods.
* **Animal tracking** (detection settings) – defaults for all tests (each test can override them, see §5). *Body parts from*
  chooses how head, body centre and tail base are found: from the animal's shape (fast, no model) or with the
  **pose model** (deep learning, see §5.1).
* **Analysis** – thresholds for mobility, freezing, zone entries, thigmotaxis, object exploration,
  social contact and time bins.
* **Analysis ▸ Test end** – *End the test when the animal stays in zone* (e.g. `Platform` in the water maze,
  `Escape box` in the Barnes maze) *for at least* N seconds (0 = on entering it): the test ends at that moment and
  every measure, including the test duration, stops there.

## 2. Experiment tab: treatments and animals

Add animals one by one or in bulk, assign **treatments** (groups/genotypes, each with a colour used in all
graphs), sex and any number of custom columns (age, weight, litter…). The **Notes** column holds free-form notes
about each animal (health, handling, anything worth remembering); they are kept with the experiment, exported and
can be shown in the results (*Animal notes*). Animals can be imported from / exported to CSV
(`ID, Treatment, Sex, …, Notes`; a *Notes*, *Comments* or *Remarks* column is imported as the notes).

**Randomise treatments** allocates the animals (all, or the selected ones) to the ticked treatments at random, in
numbers that differ by at most one. *Balance within* spreads each sex — or each value of a column such as litter or
cage — evenly over the treatments (stratified block randomisation). Enter a *seed* to reproduce an allocation.
Retired animals are left out. Combine it with blind testing so the experimenter never sees the allocation.

Retirement, the dose calculator and blind testing are described in §6.

## 3. Apparatus

Load a frame from a video (or a test's video) as background, then either **create from template** — drag a
rectangle around the apparatus and the template places all zones, points and lines and calibrates distances
from the real size you enter — or draw your own:

| Tool | Use |
| --- | --- |
| Arena | the apparatus boundary; pixels outside are ignored by the tracker |
| Rectangle / ellipse / polygon zone | areas for time, entries, latency, distance … |
| Point of interest | objects, platform centre, cups — distance, time near, exploration |
| Line | crossings in each direction |
| Calibrate | draw a line of known length (cm) |
| Zone groups | unions of zones minus excluded zones (e.g. *Open arms*, *Periphery = Arena − Centre*) |

**Import… / Export…** (Apparatus group) copy apparatus maps between experiments: *Import…* reads the apparatus
of another experiment (choose its `project.json`) or an apparatus file; *Export…* writes the current
apparatus (zones, points, lines, groups, sequences, grids and calibration) to a `.json` file you can share.

Several apparatus can share one video (e.g. four open fields filmed together) — tests that share a video and
start time are tracked in a single pass.

### Zones and areas

Each drawn zone is an *area*. **Zone groups** combine any number of areas (they need not touch) and can subtract
areas (*Periphery = Arena − Centre*); an area can be in several groups. Every zone and group gets results.

**Zone properties** (Apparatus ▸ Zones):
* **Entry rule** – *Default* (analysis setting), *Centre*, *Head*, *Tail base*, **Proportion of the body** (≥ N %
  of the body ellipse inside to enter; leave when < min(N, 100−N) %), or **Not in any other zone**.
* **Investigate** – the animal counts as in the zone while its head is within this distance of the zone's edge
  (object investigation). Such a zone also gets separate *investigation* measures: the animal investigates it
  while its head is in the zone, or within the distance and pointing at it (body orientation within the
  *exploration facing angle* of the direction to the zone centre; without a tracked orientation, within the
  distance is enough). Never while hidden; bouts shorter than the minimum entry duration are ignored.
* **Entry only when facing the zone** – an entry counts only once the animal is oriented towards the zone (its body
  orientation within this angle of the direction from its centre to the zone centre). The visit starts at the
  first frame it faces the zone; a visit in which it never does is not counted (e.g. backing into a zone).
  0 = off. Applied in the analysis and in live tests alike; needs a tracked orientation (head / tail).
* **Hidden zone** – nests, tunnels, shelters: if the animal disappears in or near it (*Hidden zone distance*; 0 =
  half the zone size) the time until it reappears counts as time in that zone, not "not detected", and no movement
  is interpolated.
* **Moveable** – each test can override the zone's shape/position (e.g. the water-maze platform; moveable by
  default); points and moveable zones centred inside it move along.

**Copy / paste** shapes with ⌘C / ⌘V (in the same apparatus the copy is offset; into another apparatus it keeps its
position).

### Grids (Grid tool, G)

Cover the arena, the selected zone or the whole image with: **square** cells (N×M, or a real cell size in cm when
calibrated; named `A1…`; clipped to round arenas), **concentric rings** (`Ring 1` = centre…), **radial sectors**
(`Sector 1…`, clockwise from the start angle) or **rings × sectors**. Each grid also creates a zone group. Measures:
crossings, cells visited (n, %), latency to visit all cells, inner-cell / outer-ring time %. *Delete grid* removes
all cells.

### Sequences

Ordered steps of zones/groups. Options: must begin at the first step (off = rotations such as ABC/BCA/CAB count —
spontaneous alternation), other zones allowed between steps, both directions, overlapping, complete on entering or
leaving the last step, time limit. Entering a step zone out of order is an error and ends the attempt. Measures:
completed, attempts, incomplete, errors, completion %, latency to first (to its completion) and latency to the
start of the first (entry into its first step), first/mean/min/max duration, mean time
between, rate, total time in sequences, completed reversed, and the distance travelled during the completed
sequences (from entering the first step to completing the last; total, mean, max, min) and the mean speed during
them (total distance / total time in sequences).

## 4. Test schedule

A test = one animal × one video (or live recording) × one apparatus × stage/trial. Use *Add tests from
videos…* to import many videos at once. Tests can also be created without a video (for live testing or manual
scoring only). Import tracks from DeepLabCut CSV files (or earlier mANY-MAZE exports) when pose estimation was
done elsewhere.

**Track selected / Track all untracked** runs the tracker in the background.

**Add tests from videos ▾ Join video files into one test…** is for a test filmed in several consecutive files (a split
recording, a camera that starts a new file every 4 GB): choose the files and they are listed, in name order, in an M3U
playlist in the experiment's `videos` folder. The playlist becomes the selected test's video (or a new test's) and
plays, tracks and exports as one continuous video. You can also write a playlist yourself (one file per line, relative
to the playlist) and add it with *Add tests from videos*.

**Schedule… ▾ Print schedule… / Save schedule… / Copy schedule** (also in the right-click menu) output the schedule as
shown (sorted as in the table, with blind codes when blind testing is on): the printout has an empty *Done* column to
tick off tests in the testing room; *Save…* writes CSV, tab-separated text or Excel; *Copy* puts it on the clipboard.

**Variables…** (toolbar or right-click) sets per-test variables that change the analysis: which point of
interest is the *novel object* (novel object recognition) and the *social stimulus side* (three-chamber).

## 5. Review and score

* **Video + overlay**: zones, current position, head (red), tail (blue) and trail.
* **Detection preview**: shows the foreground mask live while you tune settings for this test. Typical
  adjustments: *Animal is* (darker / lighter), *Threshold*, *Min animal area*.
* **Test start**: set the start to the current video time.
* **Manual scoring**: press a behaviour's key during playback to start/stop a state behaviour (or add a point
  event) at the current time. Space = play/pause, ←/→ = frame step (Shift = 1 s).
* **Track corrections**: click to set the animal position on a frame, delete or interpolate ranges.
* **Results / plots** for the test: track plot, occupancy heat map, speed and freezing trace.

### Swapping identities (several animals)

When two animals tracked in the same arena touch, the tracker can exchange their identities. In **Track editing ▸
Swap identities**, choose the animal being edited (*Animal*) and the one to swap it *With*, then press **Swap**: their
positions (centre, head, tail, area, motion) are exchanged over the time range, or from the current time to the end
when no range is set. *Undo* reverts the swap.

### How tracking works

1. A **background** model of the empty arena is built: the median of frames sampled through the test (the
   animal moves so it disappears from the median), a chosen empty-arena frame, or an adaptive model for live
   cameras with changing light.
2. Each frame is compared with the background, thresholded, cleaned (blur, remove specks, fill holes) and
   restricted to the arena.
3. The largest blob is the animal (*n* largest for several animals, with merged animals split by k-means and
   identities kept by optimal assignment between frames).
4. The **centre** is the blob centroid. **Head and tail**: the tail is stripped morphologically, the body axis
   is found by PCA, and the end that the animal moves towards is the head (with frame-to-frame consistency).
5. **Motion** (changed pixels between frames, normalised by body area) is stored for freezing / immobility.
   The **whole-body outline** (a simplified polygon of the animal's blob, up to 24 points) is stored too and drawn
   in the test view, live images and exported videos (*Record the animal's whole-body outline*, on by default;
   tracks from older versions simply have no outline).
6. Gaps up to *Interpolate gaps* seconds are filled; optional smoothing.

### Tracking by colour

With a colour camera, *Detect the animal using: Its colour* finds the pixels of a chosen colour (*Colour of the
animal or mark*, with a hue *tolerance* and a minimum *saturation* so white, grey and black are never taken for a
colour). Use it for coloured animals, dye or paint marks, coloured collars or LEDs, or when the floor and the animal
have similar brightness but different colours. No background model is needed.

**Identifying several animals by colour marks**: with several animals in an arena, enter one colour per animal in
*Identify several animals by colour marks* (e.g. `#ff0000, #0000ff`: the first animal has a red mark, the second a
blue one). The animals are still detected as usual (background, threshold or colour), but each one is then the blob
carrying most of its colour, so identities cannot swap when the animals touch or cross. When no mark is visible in a
frame, identities follow the positions.

### 5.1 Pose model (AI body parts)

With *Body parts from: Pose model*, a deep-learning keypoint model (DeepLabCut's **SuperAnimal-TopViewMouse**,
RTMPose-S, 27 keypoints) is run on a crop around each detected animal and gives the **nose**, **body centre** and
**tail base**. It is much more robust than the shape method to shadows, reflections on walls, bedding and poor
contrast, and it keeps the animal tracked for a while when the blob is lost. Keypoints below *Min keypoint
confidence* fall back to the shape estimate.

* Install it once from **Experiment ▸ Pose model ▸ Install…** (23 MB download, converted on your computer; no
  Python deep-learning framework needed).
* **Licence:** the model *weights* are licensed by the Mathis lab for **academic, non-commercial use only**
  (mANY-MAZE itself is GPL). Please cite Ye et al. 2024, *Nature Communications* 15:5165.
* On Apple Silicon it runs through **Core ML on the Neural Engine / GPU** (*Run pose model on: Auto*); elsewhere on
  the CPU (or an NVIDIA GPU with onnxruntime-gpu).
* **Custom ONNX…** uses your own keypoint model (e.g. exported from DeepLabCut, SLEAP or MMPose): an `.onnx`
  file plus a `.json` beside it with `input_size`, `mean`/`std`, `output` (`"simcc"` or `"heatmap"`),
  `keypoints` and `parts` (`{"nose": …, "centre": …, "tail_base": …}`).

### 5.2 Speed on Apple Silicon

* **Parallel tracking** – *Track all* tracks several videos at once in separate processes (all cores but one,
  limited by memory; two when the pose model shares the Neural Engine). Tests filmed in the same video are still
  tracked in a single pass.
* **Hardware video decoding** – videos are decoded with Apple **VideoToolbox** and tracking reads the luma plane
  directly (no colour conversion). Set `MANYMAZE_HWACCEL=0` to disable, or `MANYMAZE_DECODER=opencv` to use
  OpenCV only.
* **Hardware recording** – live tests are recorded with VideoToolbox's H.264 encoder (MP4), leaving the CPU for
  tracking.

### Apparatus position in a test

If the camera or the apparatus moved between recordings, the apparatus map no longer fits some videos. In
**Review and score ▸ Track editing ▸ Apparatus position in this test**, move the whole map right / down (pixels),
rotate it (degrees, clockwise) and scale it about the arena centre until it fits the video again. The position is
saved with the test and used everywhere: tracking (the arena mask), results, plots, live tests, exports. The scale
also adjusts the calibration, so distances in cm stay right. *Reset* returns to the map as drawn. Track the test
again if the arena moved so much that the animal was cut off by the old arena outline.

### Moveable zones

For zones marked *Moveable* (e.g. the water-maze platform) the **Track editing** tab sets their position for the open test: choose the zone, press *Place* and click its new centre on the video; *Reset* returns to the apparatus position. Results use the per-test position.

## 6. Experiment workflow and behaviour scoring

### Keys (manually scored behaviours)

Define keys on the **Protocol** tab (*Keys* element):

| Column | Meaning |
|---|---|
| Behaviour | Name used in the results (e.g. `Rearing: duration (s)`). |
| Key | One letter, digit or punctuation key (`- = [ ] ; ' , . / \``). Up to **46** keys; each key can be used once. Space and the arrow keys stay reserved for the video. |
| Type | **State** — the key toggles the behaviour on/off. **Hold** — the behaviour is scored while the key (or button) is held down. **Point** — an instantaneous event. |
| Exclusive set | Behaviours with the same set name cannot overlap: starting one stops the others (e.g. *posture*: Freezing, Grooming, Rearing). |
| Colour | Colour of the on-screen scoring button. |

Problems (duplicate keys, unusable keys, more than 46 keys) are listed in red under the table.

#### Scoring in the Review and score page

Open a test and choose the **Scoring** tab.

- **Keys**: click on the video, then press the behaviour keys while the video plays or is paused. Hold behaviours
  start when the key goes down and stop when it is released (keyboard auto-repeat is ignored).
- **On-screen buttons**: one large button per behaviour, usable with the mouse or a touch screen. Press-and-hold a
  *hold* button for the duration of the behaviour; tap a *state* button to switch it on and again to switch it off;
  tap a *point* button when the event occurs. Running behaviours are shown filled.
- Scored events are listed with their start, end and duration; click one to jump to it, delete selected events, or
  clear them all. Behaviours still running when you leave the test are closed at the current time.
- To score several behaviours in repeated viewings, simply replay the video and score other keys: events accumulate.
- **Notes** for the test can be read and edited under the event list (also editable on the Test schedule).

Scoring a video that has not been tracked gives the test the status **scored**; its results then contain the
behaviour measures only (count, duration, % of test, latency, mean bout, rate). A tracked test keeps the status
*tracked* and gets the behaviour measures together with the tracking measures.

#### TakeNote mode: scoring by direct observation

A test without a video (e.g. created with **Add test** or **Schedule…**) can be scored live, with just a timer:

1. Open it in the Review and score page; the Scoring tab shows the **Observation clock**.
2. Press **Start**, then score with the keys or buttons as you watch the animal. **Pause** freezes the test time;
   **Resume** continues it.
3. Press **Stop** (or let the clock reach the test duration). Running behaviours are closed, the observed duration is
   stored as the test duration and the test becomes **scored**.

Starting the clock again on a scored test asks whether to delete the previous events and score it again.

### Stages, trials and schedules

- Up to **50 stages** (Protocol tab, one per line) and **1–99 trials** per stage.
- **Tests → Schedule…** creates tests without video for the chosen animals × stages × trials, in running order:
  - *By animal* — all trials of an animal, then the next animal;
  - *By trial* — trial 1 of every animal, then trial 2, …;
  - *Randomised* — trial by trial, animals in random order (enter a seed to reproduce an order);
  - *Latin square* — trial by trial, each animal runs in every position equally often.
- **Counterbalance** apparatus or a test variable (e.g. `novel_object` = *Object A, Object B*): the levels are
  assigned to each animal's successive tests from the rows of a balanced Latin square (Williams design), so every
  level occurs equally often at every position and after every other level.
- Combinations that already have a test, retired animals and stages an animal has completed (training criteria) are
  skipped. Animal, stage, trial and apparatus of each test can still be edited by hand afterwards.

### Test status actions (Test schedule)

| Status | Meaning |
|---|---|
| pending | Not done yet. |
| tracked | Has a track. |
| scored | Manually scored, no track. |
| skipped | Not performed for now — select it and press **Resume** later. |
| superseded | Replaced by a re-performed attempt. |
| excluded | Kept but left out of results. |

- **Skip / Resume**: skip the selected tests (left out of results and of *Track all untracked*); resume them later.
- **Re-perform**: adds a new attempt of the test (same animal, stage, trial, apparatus and variables, *attempt 2*…);
  the previous attempt is kept, marked *superseded* and left out of results and statistics.
- **Clear tracks**: deletes the tracks of the selected tests (scored events and videos are kept).
- **End stage for animal**: ends the stage of the selected tests for their animals before they have done all their
  trials — exactly what a met training criterion does: the animal's remaining (pending) tests of the stage are
  skipped (noted *stage ended*) and new schedules leave the stage out for it. **Reopen stage for animal** undoes it
  and resumes the tests it skipped.
- **Set user…**: the experimenter of the selected tests (also editable in the *User* column).

### Users (experimenters)

The current user is shown at the top right of the window (**User: name ▾**, or *File ▸ Current user…*). Pick a name
from the experiment's list, or **New user…** to type one; the choice is remembered on this computer. No passwords are
involved: it only records who did what. The current user is stamped on every test as its *experimenter* when the
test is run live (the user who ran it), and when it is tracked or scored if it has no experimenter yet. The Test
schedule shows it in the **User** column, where it can be changed by hand, and the results can show and group by it
(*User* column, Statistics factors). Each experiment keeps its own list of users; **Remove a user from this
experiment…** takes a name off the list (tests keep their experimenter).

### Training criteria

On the Protocol tab (*Training criteria*) add, per stage, a condition on a result measure, e.g.
*Training: Escape latency (s) < 10 on 3 consecutive trials; retire after 10 trials*:

- **Measure** — any column of the results (or a procedure result variable), whole-test value.
- **Op / Value / Consecutive** — the condition and the number of consecutive trials on which it must hold.
- **When met** — *Stage completed*: the animal's remaining trials of the stage are skipped and new schedules do not
  include the stage again for it; *Report only*.
- **Retire after** — animals that have not met the criterion after this many trials are retired.

On the Experiment tab press **Training criteria…** to see, for every animal, the trials done, the trial at which the
criterion was met and the outcome; **Apply** completes stages and retires failing animals (their pending tests are
skipped). To end a stage for one animal without a criterion, use **End stage for animal** in the Test schedule.

### Animals: retirement and dose calculation

- The **Status** column shows *active* or *retired* (hover for the reason). **Retire** withdraws the selected animals
  (pending tests skipped, left out of new schedules); **Reinstate** brings them back and resumes the tests that were
  skipped because of the retirement.
- **Dose calculator…**: injection volume (mL) = weight (g) / 1000 × dose (mg/kg) / concentration (mg/mL). Choose the
  weight column, the default dose and the concentration (remembered for the experiment). An animal with its own
  *Dose (mg/kg)* field uses that dose. The volume is written to the *Volume (mL)* column of each (selected) animal.

### Blind testing

Tick **Blind testing** on the Protocol tab: on the Experiment, Test schedule, Run tests and Review and score pages the treatments are
replaced by stable random codes (e.g. *Group NJ55*) with a neutral colour, and groups cannot be renamed or
re-coloured. Results, statistics and exports keep the real groups. Unticking the box (unblinding) asks for
confirmation. The **Reveal treatment coding** button on the Experiment tab (and unticking the box) unblinds after a
confirmation; Results and Statistics always show the real treatments.

### Animal identification

Tick **Confirm the animal's ID** on the Protocol tab to have the experimenter scan the animal's barcode or
microchip (a USB scanner types like a keyboard) or type its ID before scoring a test (first key/button press, or
starting the observation clock). The input must match the animal ID or one of its fields whose name contains
*barcode*, *microchip*, *RFID*, *chip*, *tag* or *transponder*; a mismatch blocks scoring.

## 7. Running tests (live)

The **Live testing** page tracks animals in real time from cameras — or from video files that simulate cameras, so
everything can be tried without hardware. Choose a mode with the buttons at the top of the page:

| Mode | Use it for |
|---|---|
| **One test** | a single apparatus filmed by one camera |
| **Several tests at once** | several apparatus in one camera image and/or several cameras, run together |
| **Observation only (no camera)** | scoring behaviour by direct observation (a clock and scoring keys) |

### Starting and ending a test

Set these in **Setup ▸ Start and end** (they apply to every mode):

- **Duration** — the test ends automatically after this time; *Until stopped* runs until you stop it (or a
  procedure ends it).
- **Test starts**
  - *Immediately when armed*.
  - *When the animal is detected* — the animal must be seen inside the arena for a short hold time.
  - *When the experimenter leaves the view* — waits for a large object (your hand / arm: bigger than the maximum
    animal area, or touching the arena edge) to appear and leave again, then for the animal to be detected. The
    image shows *WAITING FOR EXPERIMENTER*, *WAITING FOR HAND TO LEAVE*, then *WAITING FOR ANIMAL*.
  - *On a start key (keyboard / remote)* — armed tests wait until a start key is pressed.
  - *At a clock time* — the test starts at the **Start time** (HH:MM). With **every day** (several-tests mode)
    the tests start every day at that time; finished rows are re-armed automatically as new tests (trial + 1).
- **Start keys / Stop keys** — default *Space, PageDown, F5* to start (or resume) and *B, PageUp* to stop and save.
  USB presentation remotes act as keyboards, so their buttons work as remote controls. Keys used for scoring
  behaviours are never used as start / stop keys.
- **Arm / Start test** arms the test; while it waits the button becomes **Start now**.
- **Pause** stops the test clock: no tracking data, no recording and no procedure timing while paused. **Resume**
  (or a start key) continues where it stopped. Pauses are saved with the test (`pauses`; the length of each pause
  is noted in the test notes). For safety, pausing always stops pulse trains and switches shock outputs off, and —
  with *Switch all outputs off while a test is paused* (on by default) — every output and sound; *when test paused /
  resumed* procedure handlers run immediately. Keys still reach procedures while paused (e.g. a "resume test" key).
- **Crash recovery**: during a test the track, events and I/O log are saved every 5 s beside the recording; a test
  interrupted by a crash is restored (marked in its notes) the next time the experiment is opened. Recordings are
  fragmented MP4, playable up to the last fragment even if the app did not close them.
- **Stop** ends the test early (Save keeps the data, Discard deletes the test).
- Why each test ended is saved with it and shown in the results (*Reason for test end*): *Test duration reached*,
  *Stopped by user* (Stop button or stop key), *Ended by procedure* (an *end test* action), *End of the video*
  (simulating with a video file), *Camera or video failed*, *Interrupted (recovered after a crash)*, and *Animal
  reached the end zone* when the analysis ends the test in an end zone (*Analysis ▸ Test end*; this also applies to
  tests tracked from a video, which otherwise have no reason).
- **Adjust calibration** (ribbon ▸ Session) changes the scale of the running test — of the selected panel with
  several tests — by typing the real length of the apparatus's calibration line or the number of pixels per cm.
  Live distances and speeds use it at once (the distance so far is converted, positions being tracked in pixels),
  and it is saved with the test (`zone_overrides["@calibration"]`, shown in the XML export), so the test's results
  are calculated with it; the apparatus map and the other tests keep their calibration. The change is noted in the
  test notes.

If the experiment requires animal ID confirmation, the ID (or a scanned barcode / microchip) is asked before each
test starts.

### Several tests at once

1. **Add source ▾** — add cameras (by number) or video files (simulated cameras). Each source is read in its own
   thread.
2. **Add session** — one row per test: *source × apparatus × animal / stage / trial*. One camera can feed several
   apparatus (each apparatus' arena is tracked independently), and several cameras can run at the same time.
3. **Start cameras**, then **Capture backgrounds** with the arenas empty (video files use their median image).
4. **Arm all** arms every row; **Start all now**, **Pause all**, **Resume all** and **Stop all** act on every test;
   the ▶ ❚❚ ■ buttons of a row act on that test only (▶ arms, starts now or resumes).

The mosaic shows every camera image with the zones, the animal, its recent path and a state label per test.
Like ANY-maze, up to **48 cameras and 40 simultaneous apparatus** are supported (*Scan for cameras* probes camera
numbers 0–63; *Add source ▸ Camera…* accepts any of them). **View ▸ Apparatus layout** arranges the panels in up to
8 × 6 columns × rows, or *Automatic (fit all)*; when the panels no longer fit, the grid scrolls. Every camera is
read in its own thread, so the frame rate each test gets depends on the computer: track at a lower camera
resolution or frame rate when running dozens of tests.
Each test is saved independently as soon as it finishes (track, recording, events, I/O events, procedure result
variables, pauses). The source / session layout is saved with the experiment.

### Real-time monitoring

The **Monitor** tab shows the selected test (click a row or a camera image): distance, speed, whether the animal
is moving / immobile / freezing, the current zone, a live table of **time, entries and latency per zone** (zone
entry rules, entries that require facing the zone, investigation distances, hidden zones and the test's
moveable-zone positions are applied as in the results), a **live chart** of speed, distance, motion, detection or freezing over the last 30 s – 5 min, the
**status of I/O devices**, and **warnings**: animal lost for longer than *Warn if lost for*, dropped camera
frames, recording errors and procedure errors.

### Camera options

**Camera options…** (Setup ▸ Video source, or under the session table for the selected row's source):

- **Region** — drag a rectangle on the camera image to capture only that part of it (or type x, y, w, h).
- **Digital zoom** with **pan** left–right / up–down.
- **Rotate** 90 / 180 / 270° and **flip** (mirror / upside down).
- **Merge with** a second camera, side by side or one above the other, to film one apparatus with two cameras.

The options are saved per camera with the experiment. Draw the apparatus on the transformed image (the apparatus
page shows what the camera delivers); changing the options later moves the image under the apparatus.

**Camera settings** (second tab, for cameras) — exposure, gain, brightness, contrast, saturation, white balance
and focus, each with **Auto** where the camera has an automatic mode (the check box's middle state leaves the
camera's own setting). With the camera image on, every change applies at once; **Cancel** puts the camera back and
**Reset to camera defaults** forgets the settings. They are saved per camera with the other options and applied
every time the camera opens. Values are in the camera's own units: driver units for webcams and capture cards,
µs of exposure and dB of gain for most industrial cameras. Many webcam drivers ignore some settings — and the macOS
camera driver used through OpenCV accepts almost none — so the dialog marks what the camera refused
(**Not supported**) or changed (**camera used …**), and the session log lists the settings a camera did not accept
when it opened.

### Cameras: webcams, capture cards and industrial cameras

- **Webcams, USB (UVC) cameras and analogue capture cards** — a frame grabber or USB video converter for an
  analogue (CCTV / IR) camera shows up as a video device: it is listed as *Camera 0, 1, …* like any webcam.
- **Industrial GigE Vision / USB3 Vision cameras** are read through their vendor's SDK, when installed
  (Add source ▸ **Industrial cameras…** shows which are and what to install):

  | Cameras | Install |
  |---|---|
  | Basler | `pip install pypylon` |
  | FLIR / Teledyne | the Spinnaker SDK and its PySpin wheel |
  | IDS | the IDS peak SDK, then `pip install ids_peak ids_peak_ipl` |
  | Any GenICam camera (Allied Vision, MATRIX VISION, Hikrobot, …) | `pip install harvesters` and add the GenTL producer (`.cti` file) of the vendor's SDK in Industrial cameras (or set `GENICAM_GENTL64_PATH`) |

  **Scan** lists them after the OpenCV cameras (e.g. *Basler acA1300-60gm (40012345)*); they are remembered by
  serial number. Their Camera settings add **pixel format** (Mono8, Bayer, RGB8: converted to colour images) and
  an **external trigger** (one frame per pulse on the chosen input line; the camera waiting for its trigger is not
  an error). Image size and frame rate (Setup ▸ Video source) are set on the camera.

### Recording

**Record video of the test** saves one file per test in the experiment's `recordings` folder and links it to the
test, so it can be re-analysed or reviewed. **Burn time and events into the video** writes the test time, the clock
time and the latest event labels on the recording; leave it off for a clean video.

**Start a new video file every … min** is for long tests (24-hour home cage, circadian activity): the recording is
written as consecutive files (`test_0001_part001.mp4`, `…part002.mp4`, …) listed in a playlist `test_0001.m3u`,
which is the test's video. A crash or power cut loses at most the end of the current file.

### Erasing thin wires and cage bars

**Animal tracking ▸ Erase thin wires / bars (px)** removes thin structures up to that width — tethers, tubes,
wire lids, cage bars — from the image before detection, so they neither split the animal in two nor are mistaken
for it. It also removes thin parts of the animal (the tail), so keep it at 0 when nothing crosses the arena. It
works with infrared cameras like any other camera.

### Observation only

With no camera, press **Start observation** and score with the behaviour keys or the on-screen buttons (*point*
events; *state* behaviours toggle; *hold* behaviours last while the key or button is held; behaviours of an
exclusive set stop each other). Pause / resume stops the clock. **Stop and save** stores the events in the test,
which gets the status *scored*.

### Procedures

Procedures (§8) run in every live test, including each test of a several-tests session; their variables are shared between tests and saved with the experiment. A *Pause the test* action pauses the test like the Pause button; resume it with *Resume* or a start key.

## 8. Procedures and hardware I/O

Procedures automate live tests: they switch lights, dispense pellets, deliver tones and shocks, drive
optogenetic lasers, count lever presses, run reinforcement schedules, end the test when a criterion is met, and
much more, reacting to the animal's position and behaviour, to keys, to hardware inputs and to the touch screen.
Any number of procedures run at the same time. They are saved with the experiment (`Project.procedures`) and
work with real hardware (an Arduino running the bundled firmware, a Firmata board, National Instruments and LabJack
devices, a USB-serial cable's control lines, any text-command serial device, syringe pumps, balances, the
computer's speakers) or with simulated devices. Beyond inputs and outputs they control lights with ramps,
optogenetic lasers (intensity, duty cycle, pulse sequences from a file), shock intensity, odours, liquid dippers
and drippers, syringe pumps and temperature controllers, read sensors (weight, light, temperature, humidity) and
movement detectors, and send e-mail / SMS alerts.

### The procedure editor

The editor has three panes:

* **Procedures** (left) — every ticked procedure runs during each live test. *Add* creates an empty procedure
  or one of the examples (fear conditioning, FR 5 lever pressing, optogenetic stimulation in a zone,
  spontaneous-alternation counter). Double-click a procedure to rename it.
* **Statements** (middle) — the procedure as a tree of blocks. *Add* inserts a statement after the selected
  one, *Add inside* puts it in the selected When / If / Else / Repeat block. Drag & drop statements to move or
  nest them, or use ▲ ▼ (move), → (indent: into the block above) and ← (outdent). *On/off* disables a statement
  without deleting it; *JSON* shows the procedure as text for copying between experiments.
* **Parameters** (right) — the selected statement's settings. Fields accept numbers or expressions
  (e.g. `randint(60, 120)`); *Functions…* lists everything that can be used in expressions.

Procedures are checked as you type: problems (unknown zones, devices or variables, syntax errors, statements in
the wrong place, missing values…) are listed under the tree and highlighted in red on the statement; click a
problem to jump to it. Problems that can only appear while a test runs (division by zero, an array index out of
range…) are reported in the live-test log without stopping the test.

### Statements

| Statement | What it does |
|---|---|
| **When** *event* | Runs its block every time the event happens (top level only). *If it recurs while running*: ignore it (default), restart the block, or run another copy in parallel. *Only the first time* runs it once. |
| **Wait** | Pauses this block: for a time (`30`, `randint(20, 40)`), until a condition is true, or for an event; optionally with a timeout (afterwards `timed_out` is 1 if it timed out). Other procedures and blocks keep running. |
| **If** / **Else** | Runs the block when the condition is true, otherwise the optional Else block. |
| **Repeat** | A number of times, while a condition is true, or forever. An optional loop variable counts 0, 1, 2… |
| **Set** | Gives a variable a value (an expression); with an index, sets one element of an array. |
| **Do** *action* | Performs an action (see below). |
| **Stop** | Exits this block, exits the loop, stops this procedure, stops all procedures, or ends the test. |
| **Comment** | A note; does nothing. |
| **Variable** | Declares a variable and its initial value (top level). *Keep the value between tests* carries it over to the next test (e.g. a session counter or a counterbalancing list); *Save as a test result* stores its final value with the test, where it appears as a result measure. *Record the value* — *Every time it changes* / *Every time it is set* — also records each value with its time, for its mean, max, min, sum, count and list of values (see *I/O results*). |

Statements written at the top level of a procedure (outside any When) run in order from the start of the test,
so a timed protocol is simply: *Wait 120 → Do tone 30 s → Wait 28 → Do shock 2 s → …*.

**Timing.** Procedures are evaluated on every video frame. A wait ends on the first frame at or after its due
time, and the next wait counts from the due time, so long sequences never drift. Pulses, pulse trains and pellet
pulses on the Arduino are timed by the board itself (microsecond resolution), independently of the frame rate;
the I/O log records their exact times. Pulse sequences from a file are timed on the computer's clock by the I/O
service thread (about 1 ms jitter; on the Arduino each pulse's width is timed by the board). Fast analogue inputs
(up to 1 kHz) are sent by the board in batches with its own clock, and each sample is logged at its own time
rather than at the frame's. "Repeat N times" and "Repeat while" loops run instantly; a
"Repeat forever" loop whose block does not wait runs once per frame (a polling loop).

**Several procedures at once.** Every When block that is running is independent: a procedure can wait for 30 s
while another one counts lever presses and a third one turns a light on whenever the animal enters a zone.
Procedures communicate through variables, *Send signal* / *Signal received*, virtual switches and
*Enable/Disable procedure*.

### Events

Events marked *(optional)* match anything when the parameter is left empty (e.g. *Animal enters zone* with no
zone fires for every zone; the zone's name is then in `event_name`). Inside a When block, `event_time`,
`event_name` (zone, input, key, area…) and `event_value` (input value, speed…) describe the event.

| Group | Event | Parameters |
|---|---|---|
| Test | Test starts (`test_start`) | — |
| Test | Test ends (`test_end`) | — |
| Test | Time reached (`time_reached`) — once, when the test time reaches the given time | Time (s) |
| Test | Every N seconds (`every`) | Interval (s), First at (s) *(optional)* |
| Test | Test paused (`test_paused`) | — |
| Test | Test resumed (`test_resumed`) | — |
| Zones | Animal enters zone (`zone_enter`) | Zone *(optional)* |
| Zones | Animal leaves zone (`zone_exit`) | Zone *(optional)* |
| Zones | Head enters zone (`head_zone_enter`) | Zone *(optional)* |
| Zones | Head leaves zone (`head_zone_exit`) | Zone *(optional)* |
| Zones | Total time in zone reaches (`zone_time_reaches`) | Zone, Time (s) |
| Zones | Time in zone (one visit) reaches (`zone_dwell`) — fires once per visit that lasts at least this long | Zone, Time (s) |
| Zones | Zone entries reach (`zone_entries_reach`) | Zone, Entries |
| Zones | Zone sequence completed (`zone_sequence`) | Zones (in order) |
| Animal | Freezing starts (`freezing_start`) | — |
| Animal | Freezing ends (`freezing_end`) | — |
| Animal | Immobility starts (`immobile_start`) | — |
| Animal | Immobility ends (`immobile_end`) | — |
| Animal | Animal not detected (`animal_lost`) | — |
| Animal | Animal detected again (`animal_found`) | — |
| Animal | Speed rises above (`speed_above`) | Speed |
| Animal | Speed falls below (`speed_below`) | Speed |
| Animal | Distance travelled reaches (`distance_reaches`) | Distance |
| Animal | Total freezing time reaches (`freezing_time_reaches`) | Time (s) |
| Animal | Total immobile time reaches (`immobile_time_reaches`) | Time (s) |
| Keyboard | Key pressed (`key_down`) | Key *(optional)* |
| Keyboard | Key released (`key_up`) | Key *(optional)* |
| Inputs | Input switches on (`input_on`) | Device *(optional)*, Input |
| Inputs | Input switches off (`input_off`) | Device *(optional)*, Input |
| Inputs | Input changes (`input_changed`) | Device *(optional)*, Input |
| Inputs | Input activations reach (`input_count_reaches`) | Device *(optional)*, Input, Count |
| Inputs | Analogue input rises above (`analog_above`) | Device *(optional)*, Input, Level |
| Inputs | Analogue input falls below (`analog_below`) | Device *(optional)*, Input, Level |
| Inputs | Encoder count reaches (`encoder_reaches`) | Device *(optional)*, Input, Counts |
| Inputs | Every N encoder counts (`encoder_every`) — e.g. once per wheel revolution | Device *(optional)*, Input, Counts |
| Inputs | Movement detector: movement starts (`movement_start`) | Device *(optional)*, Detector *(optional)* |
| Inputs | Movement detector: movement ends (`movement_end`) | Device *(optional)*, Detector *(optional)* |
| Sensors | Sensor rises above (`sensor_above`) | Device *(optional)*, Sensor, Level |
| Sensors | Sensor falls below (`sensor_below`) | Device *(optional)*, Sensor, Level |
| Sensors | Sensor leaves its alert range (`sensor_out_of_range`) — the channel's alert_min / alert_max options; an alert is also sent if an alert device is configured | Device *(optional)*, Sensor *(optional)* |
| Sensors | Sensor back in its alert range (`sensor_in_range`) | Device *(optional)*, Sensor *(optional)* |
| Sensors | Temperature controller reaches its target (`temperature_reached`) | Device *(optional)*, Temperature controller *(optional)* |
| Pumps | Pump reaches its target volume (`pump_target_reached`) | Device *(optional)*, Pump *(optional)* |
| Pumps | Pump stalled (`pump_stalled`) | Device *(optional)*, Pump *(optional)* |
| Pumps | Volume infused reaches (`pump_volume_reaches`) | Device *(optional)*, Pump, Volume (ml) |
| Outputs | Output switched on (`output_on`) | Device *(optional)*, Output *(optional)* |
| Outputs | Output switched off (`output_off`) | Device *(optional)*, Output *(optional)* |
| Outputs | Light ramp finished (`light_ramp_done`) | Device *(optional)*, Light *(optional)* |
| Outputs | Pulse sequence finished (`pulse_sequence_done`) | Device *(optional)*, Output *(optional)* |
| Outputs | Pellet detected (`pellet_dropped`) — the dispenser's pellet sensor saw the pellet (Dispense pellet with a sensor) | Device *(optional)*, Dispenser *(optional)* |
| Outputs | Pellet dispenser error (`pellet_error`) — no pellet detected after the retries (jammed or empty dispenser) | Device *(optional)*, Dispenser *(optional)* |
| Logic | Variable changes (`variable_changed`) | Variable |
| Logic | Condition becomes true (`condition_true`) | Condition |
| Logic | Condition becomes false (`condition_false`) | Condition |
| Logic | Timer elapses (`timer_elapsed`) | Timer |
| Logic | Signal received (`signal`) — sent by the “Send signal” action of any procedure | Signal |
| Logic | Virtual switch on (`virtual_switch_on`) | Switch |
| Logic | Virtual switch off (`virtual_switch_off`) | Switch |
| Logic | Event marked (`event_marked`) | Event *(optional)* |
| Logic | Reinforcer earned (`reinforcer_earned`) | Schedule |
| Touch screen | Touch in area (`touch`) | Area *(optional)* |
| Touch screen | Touch outside all areas (`touch_outside`) | — |

### Actions

Output actions name a device and a channel; leave the device empty to use whichever device has a channel of that
name. Outputs used by procedures but not configured are simulated (and reported). All outputs, pulse trains,
sounds and virtual switches are switched off when the test ends.

| Group | Action | Parameters |
|---|---|---|
| Outputs | Switch output on (`output_on`) | Device *(optional)*, Output |
| Outputs | Switch output off (`output_off`) | Device *(optional)*, Output |
| Outputs | Toggle output (`output_toggle`) | Device *(optional)*, Output |
| Outputs | Pulse output (`output_pulse`) | Device *(optional)*, Output, Duration (s) |
| Outputs | Set output level (`output_set`) | Device *(optional)*, Output, Level (0–1) |
| Outputs | Switch all outputs off (`all_outputs_off`) | Device *(optional)* |
| Outputs | Pulse train (optogenetics) (`pulse_train`) | Device *(optional)*, Output, Frequency (Hz), Pulse width (ms), Duration (s) |
| Outputs | Stop pulse train (`pulse_train_stop`) | Device *(optional)*, Output |
| Outputs | Sync pulse (e-phys / imaging) (`sync_pulse`) | Device *(optional)*, Output, Width (ms) |
| Operant | Dispense pellet(s) (`pellet`) | Device *(optional)*, Output, Pellets, Pulse (ms) *(optional)*, Gap between pellets (s) *(optional)*, Pellet sensor *(optional)*, Detection time (s) *(optional)*, Retries *(optional)* |
| Operant | Present liquid dipper (`dipper`) | Device *(optional)*, Output, Duration (s) |
| Operant | Deliver liquid drops (dripper) (`liquid_drop`) | Device *(optional)*, Output, Drops, Valve open (ms) *(optional)*, Gap between drops (s) *(optional)* |
| Operant | Present odour (`odour`) | Device *(optional)*, Olfactometer, Odour *(optional)*, Air flow (l/min) *(optional)* |
| Operant | Stop odour (`odour_off`) | Device *(optional)*, Olfactometer |
| Lights | Set light level (`light_level`) | Device *(optional)*, Output, Level (%) |
| Lights | Ramp light level (`light_ramp`) | Device *(optional)*, Output, To level (%), Over (s), From level (%) *(optional)* |
| Operant | Light on (`light_on`) | Device *(optional)*, Output |
| Operant | Light off (`light_off`) | Device *(optional)*, Output |
| Operant | Open door (`door_open`) | Device *(optional)*, Output |
| Operant | Close door (`door_close`) | Device *(optional)*, Output |
| Operant | Extend lever (`lever_extend`) | Device *(optional)*, Output |
| Operant | Retract lever (`lever_retract`) | Device *(optional)*, Output |
| Operant | Start reinforcement schedule (`schedule_start`) | Schedule name, Schedule |
| Operant | Register response (`schedule_response`) | Schedule name, Schedule *(optional)*, Store 1/0 (reinforced) in *(optional)* |
| Shock | Shock on (`shock_on`) | Device *(optional)*, Output, Safety cut-off (s), Intensity (mA) *(optional)* |
| Shock | Shock off (`shock_off`) | Device *(optional)*, Output |
| Shock | Shock for a duration (`shock_pulse`) | Device *(optional)*, Output, Duration (s), Intensity (mA) *(optional)* |
| Shock | Set shock intensity (`shock_intensity`) | Device *(optional)*, Output, Intensity (mA) |
| Optogenetics | Laser pulse train (duty cycle) (`opto_train`) | Device *(optional)*, Output, Frequency (Hz), Duty cycle (%), Duration (s), Intensity (%) *(optional)* |
| Optogenetics | Laser pulse sequence from a file (`opto_sequence`) | Device *(optional)*, Output, File (CSV), Repeat *(optional)*, Intensity (%) *(optional)* |
| Optogenetics | Set laser intensity (`opto_intensity`) | Device *(optional)*, Output, Intensity (%) |
| Temperature | Set temperature (`set_temperature`) | Device *(optional)*, Temperature controller, Target (°C), Ramp (°C/min) *(optional)* |
| Temperature | Temperature control off (`temperature_off`) | Device *(optional)*, Temperature controller |
| Pumps | Infuse (`pump_infuse`) | Device *(optional)*, Pump, Rate (ml/min), Volume (ml) *(optional)* |
| Pumps | Withdraw (`pump_withdraw`) | Device *(optional)*, Pump, Rate (ml/min), Volume (ml) *(optional)* |
| Pumps | Stop pump (`pump_stop`) | Device *(optional)*, Pump |
| Pumps | Select syringe (`pump_syringe`) | Device *(optional)*, Pump, Syringe |
| Sensors | Tare sensor (zero) (`tare_sensor`) | Device *(optional)*, Sensor |
| Sensors | Store sensor reading (`read_sensor`) | Device *(optional)*, Sensor, Store in |
| Sensors | Weigh the animal (balance) (`weigh_animal`) | Balance *(optional)*, Store in *(optional)* |
| Communication | Send alert (e-mail / SMS) (`send_alert`) | Message |
| Audio | Play tone (`tone`) | Audio device *(optional)*, Frequency (Hz), Duration (s), Volume (0–1) *(optional)* |
| Audio | Play white noise (`white_noise`) | Audio device *(optional)*, Duration (s), Volume (0–1) *(optional)* |
| Audio | Play sound file (`play_sound`) | Audio device *(optional)*, File (WAV), Duration (s) *(optional)*, Volume (0–1) *(optional)*, Play *(optional)* |
| Audio | Play sound file repeatedly (`loop_sound`) — until a Stop sounds action or the end of the test | Audio device *(optional)*, File (WAV), Volume (0–1) *(optional)* |
| Audio | Stop sounds (`stop_sound`) | Audio device *(optional)* |
| Audio | Beep (`beep`) | — |
| Communication | Send serial command (`serial_send`) | Device *(optional)*, Command |
| Communication | Send signal (`signal`) | Signal |
| Communication | Virtual switch on (`virtual_switch_on`) | Switch |
| Communication | Virtual switch off (`virtual_switch_off`) | Switch |
| Communication | Toggle virtual switch (`virtual_switch_toggle`) | Switch |
| Communication | Simulate input (`simulate_input`) | Device *(optional)*, Input, Value |
| Variables | Set variable (`set_variable`) | Variable, Value |
| Variables | Increment variable (`increment`) | Variable, By *(optional)* |
| Variables | Decrement variable (`decrement`) | Variable, By *(optional)* |
| Variables | Append to array (`array_append`) | Array, Value |
| Variables | Start timer (`start_timer`) | Timer, Elapses after (s) *(optional)* |
| Variables | Stop timer (`stop_timer`) | Timer |
| Variables | Reset timer (`reset_timer`) | Timer |
| Test | Mark event (`mark`) | Event |
| Test | Start state event (`mark_start`) | Event |
| Test | End state event (`mark_end`) | Event |
| Test | Write to log (`log`) | Message |
| Test | End the test (`end_test`) | — |
| Test | Pause the test (`pause_test`) | — |
| Test | Resume the test (`resume_test`) | — |
| Test | Enable procedure (`enable_procedure`) | Procedure |
| Test | Disable procedure (`disable_procedure`) | Procedure |
| Touch screen | Show stimulus (`show_stimulus`) | Area, Image *(optional)*, Shape *(optional)*, Colour *(optional)* |
| Touch screen | Hide stimulus (`hide_stimulus`) | Area |
| Touch screen | Clear screen (`clear_screen`) | — |

Safety: *Shock on* always has a cut-off (default 2 s, at most 60 s), enforced by mANY-MAZE and, on the Arduino,
by the board itself; *Shock for a duration* is limited to 60 s.

Reinforcement schedules (*Start reinforcement schedule*, *Register response*): `CRF`, `FR n`, `VR n`
(requirements 1…2n−1), `FI s`, `VI s` (Fleshler–Hoffman intervals), `PR` (Richardson–Roberts progressive ratio:
1, 2, 4, 6, 9, 12…; the breakpoint is the last ratio completed), `PR n` (linear: n, 2n, 3n…), `FT s`, `VT s`
(response-independent) and `EXT`. *Register response* stores 1/0 in a variable and fires *Reinforcer earned*.

### Expressions and variables

Expressions use numbers, `'text'`, arrays `[1, 2, 3]`, variables, `+ - * / // % **`, comparisons
(`== != < <= > >=`, also chained: `10 < x <= 20`), `and or not`, `a if condition else b`, indexing `arr[i]`,
`arr[-1]`, slices `arr[1:3]` and `x in arr`. Text parameters can embed expressions in braces:
`Trial {trial}: {round(zone_time('Centre'), 1)} s`.

Functions:

* maths — `abs min max round floor ceil sqrt exp log log10 sin cos tan asin acos atan atan2 hypot degrees
  radians sign clamp int float bool str`
* arrays — `len sum mean sorted reversed index count array(n, fill) range`
* random — `random() uniform(a, b) randint(a, b) gauss(mean, sd) choice(array) shuffle(array)`
* the test — `time()`, `zone('A')`, `head_zone('A')`, `zone_time('A')`, `zone_entries('A')`, `detected()`,
  `freezing()`, `immobile()`, `speed()`, `distance()`, `x()`, `y()`, `key('s')`
* I/O — `input([device,] channel)`, `analog(...)`, `encoder(...)`, `activations(...)`, `output(...)`,
  `pellets([[device,] channel])`, `switch('name')`, `timer('name')`, `responses('schedule')`,
  `reinforcers('schedule')`, `requirement('schedule')`

Expressions are evaluated by a restricted interpreter: they cannot call anything else, read files or access
Python objects, and huge numbers or arrays are refused.

Variables are shared by all procedures. Numeric variables declared with *Save as a test result* are saved with
the test (`Test.result_variables`) and analysed like any other measure ("Result variable" measures).
Variables declared with *Keep the value between tests* are stored in the experiment (`Project.variables`).

### I/O devices

**Experiment ▸ Hardware ▸ I/O devices…** configures the hardware (`Project.io_devices`). When several tests run at
once, each test panel chooses its own **I/O device** (box); arming is refused if two tests would share a box. Boards
with outputs have a **watchdog** (2000 ms by default; *Off* = 0) that switches every output off if the computer stops
sending heartbeats, e.g. after a crash.

| Type | Use |
|---|---|
| **Arduino** | Any Arduino running `firmware/manymaze_io` (see `firmware/README.md` for wiring and the protocol): debounced digital inputs (levers, nose pokes, beams, TTL), digital outputs (lights, pellet dispensers, doors, shocker triggers, laser TTL, sync pulses) with optional maximum on-time, PWM outputs, analogue inputs, quadrature rotary encoders (running wheels) and a heartbeat watchdog. Pulses and pulse trains are generated on the board. |
| **Serial port (text commands)** | Any device driven by text lines: each output channel has an *On* and an *Off* command; each input channel the lines the device sends when it switches on / off. *Send serial command* sends any text. |
| **Audio output** | The computer's speakers: tones (any frequency; high sample rates for ultrasound if the sound card supports them), white noise and WAV files, once or repeated (*Play sound file* ▸ *Play* times, or *Play sound file repeatedly* until *Stop sounds*). |
| **USB-serial cable control lines** | Any USB-serial adapter as a small TTL interface (the role of ANY-maze's USB TTL cable): outputs on RTS and DTR, inputs from CTS, DSR, RI and CD (the channel's *Pin* is the line name). |
| **Firmata board** | A board running StandardFirmata (57600 baud): digital inputs with pull-up, movement detectors, digital and PWM outputs, analogue inputs and sensors. |
| **National Instruments DAQ** | NI devices through NI-DAQmx (`pip install nidaqmx` and NI's driver): digital lines (`port0/line0`), analogue inputs (`ai0`), analogue outputs (`ao0`, level × `max_v`), counters (`ctr0`). |
| **LabJack** | LabJack T4 / T7 / T8 through LJM (`pip install labjack-ljm`): `FIO`/`EIO` digital lines, `AIN` analogue inputs, `DAC` outputs, quadrature encoders on two `DIO` lines. |
| **Syringe pump(s)** | One or several pumps (daisy-chained by address where the protocol allows): New Era / WPI Aladdin and OEMs, Harvard Apparatus (Ultra and legacy command sets), KD Scientific, Chemyx, Cavro-type pumps, a custom text protocol, or *simulated*. Channels of kind *Syringe pump* with `syringe=` (131 predefined syringes from 14 makers — check the inner diameter against your syringe's data sheet) or `diameter_mm=`. Each pump reports `<pump>.running`, `.stalled`, `.target_reached`, `.infused_ml` and `.withdrawn_ml`. |
| **Balance** | Serial balances (Mettler Toledo MT-SICS, Ohaus, Sartorius, A&D, Kern, or any balance that sends its weight continuously): **Animals ▸ Weigh** records each animal's weight with the date (*Weight (g)* column and weight history), and the *Weigh the animal* action does it during a test. |
| **Alerts (e-mail / SMS)** | Where alerts are sent: e-mail through an SMTP server, SMS through Twilio or an e-mail-to-SMS gateway address. Sensors out of their range and the *Send alert* action use every alert device. |
| **Simulated device** | For designing and testing procedures without hardware: outputs are shown, inputs are switched by hand (*Simulate*). |

Channels have a name (used by procedures), a kind (digital input, movement detector, digital output, PWM output,
analogue input, sensor, rotary encoder, temperature controller, olfactometer, syringe pump), a pin, *Invert* for
active-low hardware, and options such as `pullup=0`, `debounce_ms=20`, `counts_per_rev=1024`, `cm_per_rev=50`,
`scale=0.0049`, `period_ms=50`, `deadband=2`, and `role=shocker` / `role=speaker` / `role=light` to group an
output's results with that device type.

**Fast and filtered analogue inputs.** `period_ms=1` samples at 1 kHz (the Arduino firmware sends batches of
samples with its own time stamps). `filter=lowpass` with `cutoff_hz` (and `order`, default 2), `filter=highpass`
(`cutoff_hz`), `filter=bandpass` (`low_hz`, `high_hz`) apply a Butterworth filter to each sample as it arrives;
`filter=average` with `window` (samples) or `window_ms` a moving average. Filtered channels report every sample.

**Sensors** (kind *Sensor*): `sensor=weight|light|temperature|humidity|generic`, `units`, and where the readings
come from — `interface=analog` (an analogue pin: `scale`, `offset`), `interface=hx711` (a load cell through an
HX711 amplifier: *Pin* = DOUT, *Pin B* = SCK, `scale` grams per count, `offset`) or `interface=dht22` (a DHT22
temperature / humidity sensor: two channels on the same pin, one with `sensor=temperature`, one with
`sensor=humidity`). `alert_min` / `alert_max` set the sensor's alert range: leaving it fires *Sensor leaves its
alert range* and sends an alert (at most every `alert_repeat_s`, default 600 s; `alert=0` to only fire the
event). *Tare sensor* zeroes a sensor (e.g. a food hopper on a load cell) and *Store sensor reading* keeps its value
in a variable. Weight sensors give the food / liquid **intake** (what the container lost) in the results.

**Movement detectors** (kind *Movement detector (PIR)*): a digital input (no pull-up by default: PIR modules drive
their output high) with *Movement starts / ends* events and their own results.

**Temperature controllers** (kind *Temperature controller*): `sensor` (a sensor channel in °C), `heat` and
optionally `cool` (outputs: PWM outputs get a PID level, digital ones switch with a hysteresis), `kp`, `ki`, `kd`,
`band` (°C, default 0.5: "at target"), `max_temp` / `min_temp` (safety: outputs off outside, and when the sensor
stops reporting). A serial controller that regulates itself is driven with `set_cmd` (e.g. `SP {value:.1f}`) and
`off_cmd`. *Set temperature* gives a target and optionally a ramp in °C/min; regulation runs in the background
(also while a test is paused) and stops at the end of the test. *Temperature controller reaches its target* fires
when the temperature is within `band` of the target.

**Lights**: *Set light level* (in %) and *Ramp light level* (from the current or a given level to another, over a
time; *Light ramp finished* fires at the end) on PWM outputs (digital outputs are on above 0 %).

**Optogenetics and shock intensity**: give the laser / shocker output the option `intensity=<a PWM or analogue
output that sets its power / current>`. *Laser pulse train (duty cycle)* takes a frequency, a duty cycle and an
optional intensity (%); *Laser pulse sequence from a file* reads a text or CSV file with one pulse per line,
`onset (s), duration (s)[, intensity %]` (header and `#` lines are skipped), repeated a number of times (0 = until
stopped), and fires *Pulse sequence finished*. For shockers, *Calibrate…* in the I/O devices dialog sets each level
of the intensity output in turn while you measure the current, and stores the table (`calibration=0:0|0.5:0.4|…`,
level : mA; or `max_ma` for a linear output). *Set shock intensity* and the *Intensity (mA)* of *Shock on* / *Shock
for a duration* then set the current; the results give each shocker's mean and maximum intensity.

**Odours** (kind *Olfactometer*): `odours=vanilla:valve1|almond:valve2` (odour names and the output that opens each
valve), optionally `blank` (the clean-air valve, open when no odour is presented), `flow` (a PWM / analogue output
driving a mass-flow controller) and `max_flow` (l/min at full level). *Present odour* opens one odour's valve (the
others close), with an optional air flow; *Stop odour* or the odour *none* returns to clean air.

**Liquid rewards**: *Present liquid dipper* raises a dipper for a duration; *Deliver liquid drops (dripper)* opens
a solenoid valve for each drop (`drop_ul` on the channel gives the volume in the results).

**Pellet dispenser errors**: give *Dispense pellet(s)* a *Pellet sensor* (an input that sees each pellet land,
e.g. a beam in the magazine): a pellet not seen within the *Detection time* is dispensed again up to *Retries*
times; *Pellet detected* fires for each pellet seen and *Pellet dispenser error* when pellets are still missing
(jammed or empty dispenser). The results count the pellets not dispensed and the retries.

**Syringe pumps**: *Select syringe*, *Infuse* / *Withdraw* (rate in ml/min, optional target volume) and *Stop
pump*; *Pump reaches its target volume*, *Pump stalled* and *Volume infused reaches* react to the pump. Pumps stop
when the test ends or is paused.

*Connect* opens the devices and shows every input and output live; *Toggle* switches an output, *Simulate*
switches a simulated input, *Test* pulses the selected output for 0.5 s or plays a 1 kHz tone. pyserial
(`pip install pyserial`) is needed for Arduino and serial devices.

Every input and output change during a test is recorded in the test's I/O log (`Test.io_events`).

### Touch screen

A full-screen stimulus window on a second display (the ANY-maze Touch equivalent) divided into response areas
(by default the windows of a chamber mask: left / centre / right). *Show stimulus* draws an image or a shape
(circle, square, triangle, star, cross, bars) in an area, *Hide stimulus* / *Clear screen* remove them, and
*Touch in area* / *Touch outside all areas* react to the animal's touches (mouse clicks work too, for testing).
Enable it in **Experiment ▸ Hardware ▸ Touch screen…** (display, number of response windows; used in one-test live mode). Areas are rectangles given as fractions of the screen, stored with the experiment
(`settings_extra["touchscreen"]`: `areas`, `screen`, `background`, `outline`); by default three windows.

### I/O results

The I/O log is analysed into measures for each test (and for each time period; paused time is removed, and
latencies of things that never happen follow *When an event never occurs, its latency is*):

* **digital inputs** — activations, time on, latency to first activation, mean / longest / shortest activation,
  latency to first deactivation, activations per minute (e.g. lever presses, nose pokes, beam breaks, licks), and
  **positive / negative reversals**: the number of times the input went from off to on (positive) and from on to
  off (negative) in the period. An activation already under way when a period starts is clipped to the period and
  is not a positive reversal (nor an activation) of that period;
* **analogue inputs** — mean (time-weighted), minimum, maximum and the times of the maximum / minimum; the
  **baseline** (time-weighted mean over the first *Analogue inputs: baseline period* seconds of the test or period),
  its **SD**, the **end of the baseline** period, the **mean deviation from baseline** (mean |value − baseline|
  after the baseline period), the **integral above / below baseline** (value × s, after the baseline period), the
  time of the **first positive / negative deviation** (more than *a deviation is more than … baseline SD* SDs above
  / below the baseline, after the baseline period) and of the **return to baseline** after it (back within that
  band). Per zone (and zone group) visit: the mean over the visits of the maximum, the minimum, the time from the
  entry to them, and the mean value at entry and at exit (`temp in Centre: mean max`, …). Values are held from one
  logged sample to the next;
* **rotary encoders** — counts, revolutions, distance, maximum rate (counts/s), mean rate (rev/min); **time
  turning** (between two samples that differ and are at most 1 s apart; a change after a longer still spell
  counts from one typical sample interval before it); **reversals** (the direction changes after turning back by
  more than 10°, so a count of jitter is not one); with *counts_per_rev*: **degrees clockwise / anticlockwise**
  (positive counts are clockwise; swap the encoder's A / B pins to change it), **clockwise / anticlockwise
  rotations** (completed 360° turns within each run in one direction), **half and quarter rotations** (completed
  180° / 90° turns per run, both directions), **total rotations** (revolutions turned in either direction; the
  *revolutions* are net, clockwise positive), **maximum RPM** (the fastest whole second of the period), **minimum
  RPM** (the slowest whole second of turning) and **mean RPM while turning** (revolutions turned in either
  direction / time turning);
* **outputs, virtual switches, sounds and touch-screen stimuli** — times on, time on, latency to first on,
  longest / shortest / mean time on (the average activation duration), latency to first off, activations per
  minute, plus pellets dispensed for pellet dispensers and pulse trains / pulses for optogenetic outputs (lasers);
  shockers, speakers, lights, dippers and drippers also give their activations per minute;
* **shockers, speakers and lights** have their own measure groups, named after the device type: *Shocker
  shock: shocks, time on, latency to first shock, longest / shortest / mean shock, latency to first off*;
  *Speaker tone: sounds, time on, latency to first sound, longest / shortest / mean sound, …*; *Light house: times
  on, time on, latency to first on, longest / shortest on, latency to first off* and, for dimmable (PWM) lights,
  the time-weighted *mean level* (0–1). An output is a shocker when a shock action drove it, a speaker for the
  audio actions, a light for *Light on / off*; any output channel can also be given the option `role=shocker`,
  `role=speaker` or `role=light` in the I/O devices dialog;
* **movement detectors** — movements, time moving / not moving, latency to first movement, mean movement;
* **sensors** — initial and final value, mean (time-weighted), maximum, minimum, change, time out of the alert range
  and times out of range; weight sensors also give the **intake** (initial − final);
* **syringe pumps** — volume infused and withdrawn (ml; from the pump's own counters when it reports them, otherwise
  from the rates and times), infusions, withdrawals, time pumping, latency to first start, stalls;
* **temperature controllers** — time on, mean target, mean set-point, time at target, latency to target;
* **odours** — for each odour: presentations, time presented, latency to first presentation; and the time with any
  odour for each olfactometer;
* **dippers and drippers** — presentations / drops, time on, latencies; drippers with `drop_ul` the volume (µl);
* **shockers with an intensity output** — mean and maximum intensity (mA) of the shocks; **pellet dispensers with
  a sensor** — pellets not dispensed (errors) and retries;
* **animal weight** — the weight taken with *Weigh the animal* during the test (also stored on the animal);
* **virtual switches** — distance travelled before the first activation (in the period; the whole distance if
  never, or blank) and distance travelled while the switch is on;
* **per zone** (and zone group) for inputs, outputs (shockers, speakers, lights, lasers, pellet dispensers…),
  virtual switches and rotary encoders: the activations that start while the animal is in the zone (count,
  latency to the first, activations per minute; *pellets dispensed* for pellet dispensers), the time the channel is
  on while the animal is in the zone, and for encoders the counts (and total rotations, with *counts_per_rev*)
  turned while the animal is in the zone — `lever in Centre: activations`, `Shocker shock in Dark: shocks`, `wheel
  in Nest: total rotations`, …;
* **touches** — activations per area (channel `touch <area>`);
* **result variables** — the final value of each *Save as a test result* variable (`Variable: name`). A variable
  whose *Record the value* is *Every time it changes* or *Every time it is set* also logs each numeric value with
  its time (in the I/O log, as kind `variable`; *every time it changes* skips assignments of the same value, *every
  time it is set* records every assignment — set, increment, append, loop counter; the initial value of the
  declaration is not recorded); these give `Variable: name (count)`, `(mean)`, `(max)`, `(min)`, `(sum)` and
  `(values)` (the list), for the test and per period (a value recorded exactly at a period boundary belongs to the
  later period; one recorded at the very end of the test to the last one).

**Operant plantar assay (OPAD).** In **Protocol ▸ Analysis ▸ I/O measures** name the digital input of the paw
contact with the thermal plate (*OPAD: paw contact input*), the lickometer input and the analogue input of the
plate temperature, and optionally the temperatures of interest (e.g. `10, 45`, each ± *OPAD: at a temperature
within*). Measures: *OPAD: contacts made*, *contacts broken*, *time in contact*, *licks*, *non-lick contacts*
(contacts during which the animal never licked), *mean temperature when contact broken* and the list of
*temperatures when contact broken*; per temperature of interest (`OPAD at 45°: …`): time in contact while the plate
was at that temperature, contacts made and broken and licks at that temperature. Channel names may be written
`device/channel`.

### Projects from older versions

Rules created with earlier versions ("when *trigger*, after *delay*, do *action*") are converted automatically
into equivalent procedures (one per rule) the first time the procedure editor opens.

### For developers

```python
from manymaze.core.iodevices import DeviceManager
from manymaze.core.iomeasures import io_measures
from manymaze.core.procedures import ProcedureEngine, validate

devices = DeviceManager(project.io_devices)            # opens the hardware
engine = ProcedureEngine(project.procedures, devices, on_mark=..., on_end=..., on_log=...,
                         variables=project.variables, context={"zones": [...]},
                         on_pause=..., on_resume=..., on_stimulus=touch_window.handle)
engine.start(0.0)
engine.update_state(t, {"zones": {...}, "head_zones": {...}, "detected": True, "freezing": False,
                        "immobile": False, "x": x, "y": y, "speed": v, "distance": d})   # every frame
engine.key(t, "s", down=True); engine.touch(t, "left", fx, fy); engine.mark_event(t, "Rearing")
engine.stop(t_end)
test.io_events = engine.io_events; test.result_variables = engine.result_variables
test.pauses = engine.pauses; test.events += engine.state_events   # point marks go through on_mark
io_measures(test.io_events, duration, t_range=None, devices=project.io_devices)  # -> {measure: value}
validate(project.procedures, context) # -> [(procedure index, statement path, message)]
```

## 9. Results: data, plots and data transfer

The results table has one row per test (and per time bin / period when enabled). Choose which measures to
show, filter by group or stage, export **CSV**, **Excel** (with Animals, Tests and Settings sheets) or copy to
the clipboard (for Prism / Excel / R), and generate a self-contained **HTML report** with track plots, heat
maps, group heat maps, results and statistics.

### Measures

* **Whole apparatus** (~45): duration, detection %, time not detected, time hidden / not hidden, distance, mean /
  max / mobile speed, mobile & immobile time / episodes / mean & longest episode, latency to immobility, freezing
  (time, %, episodes, latency, mean & longest), path efficiency and tortuosity, turn angle and angular velocity,
  meander, body and path rotations (clockwise, anticlockwise and total), average position (time-weighted mean X /
  Y of the tracked positions, in units from the top-left of the image), thigmotaxis, distance from wall and centre,
  time outside the arena, arena quadrants, zone transitions, grid crossings. **Body rotations** follow the body
  orientation, as in ANY-maze: the tracked body angle, else the tail → head axis when the head and tail are
  tracked; only when neither is tracked do they follow the direction of travel (and there are then no separate
  *path rotations*).
* **Tracking quality**: centre and head positions recorded, head tracked (% of tracked frames), tracking quality
  (% of frames where the animal was detected, its head found when the head is tracked, and its area within half to
  twice its usual area — larger or smaller blobs are usually shadows, reflections or merges).
* **Activity** (from the pixels that change between frames, separate from mobility, which comes from the centre's
  speed): average freezing score (the mean motion, % of body, that freezing is detected from), time active /
  inactive, active and inactive episodes, longest / shortest active and inactive episode. The animal is active when
  its motion reaches *The animal is active when movement reaches*; inactive episodes shorter than *Shortest
  inactive episode* count as active (Protocol ▸ Analysis ▸ Activity). Per zone: time active / inactive in the zone
  and inactive episodes (an episode belongs to the zone it starts in).
* **Head** (when the head is tracked): head distance (smoothed like the centre), head turn angle — absolute,
  clockwise and anticlockwise — the cumulative change of the head direction (tail → head). A jump of more than 90°
  between two frames is a head / tail swap of the tracker and is not counted.
* **Rearing** (*Detect rearing automatically*, Protocol ▸ Analysis ▸ Rearing): rears, time rearing, latency to
  first rear, mean / max / min rear duration, for the whole test and per zone (`Zone: rears` …; a rear belongs to
  the zone the animal was in when it started). Seen from above, an animal standing on its hind legs looks smaller
  and shorter: a frame is a rear when the body area falls below *rear area* % (75 %) of the animal's usual (median)
  area and, when the head and tail are tracked (shape or pose model), the head–tail length falls below *rear
  length* % (80 %) of its usual length. Gaps of up to 0.2 s are bridged and rears shorter than *Shortest rear*
  (0.3 s) are ignored. Works on tracks made before the option existed (it needs no re-tracking). Check a few tests
  against manual scoring and adjust the percentages for your camera height and strain.
* **Per zone / group**: time, %, entries, entries/min, latency to 1st and 2nd entry, last exit, mean & longest
  visit, distance, mean & max speed, time mobile / immobile / freezing, immobile & freezing episodes, head entries /
  time / latency, time facing the zone, distance travelled and path efficiency before the first entry, mean
  distance from the zone (as ANY-maze: over the frames the animal is outside it; blank if it never is), time
  active / inactive and inactive episodes, and:
  * **visit durations** – the duration of each visit, as a comma-separated list (text, so statistics skip it);
  * **investigation** (investigation zones): bouts, time, latency to the first investigation and to its end, *was
    first zone investigated*, longest / shortest / mean bout, list of bout durations, distance and mean speed while
    investigating, distance before the first investigation, time mobile / immobile, immobile episodes, time freezing
    and freezing episodes while investigating;
  * **head**: latency to the first head exit, distance travelled by the head in the zone, time the head is in the
    zone while the centre is outside, mean / max head distance from the zone and min when outside, mean / max /
    min head distance to the border when inside;
  * **distance to the border when inside** (centre): mean / max / min;
  * **towards / away**: time getting closer to / further away from the zone (its distance decreasing /
    increasing, outside it), time moving towards / away (mobile, outside, direction of travel within the
    *exploration facing angle* of the direction to the zone centre, or of the opposite direction);
  * **heading error**: *initial heading error* (absolute, 0–180°, as ANY-maze) and *signed initial heading error*
    (positive = clockwise of the zone direction on screen) — the direction from the first position to the position
    1 s later against the direction to the zone centre (blank if the animal starts in the zone) — and the mean
    absolute heading error while moving outside;
  * **time oriented towards the zone centre when inside** (body orientation within the facing angle);
  * **absolute turn angle** and **absolute head turn angle** (body orientation) while in the zone;
  * **corrected integrated path length** (CIPL, Gallagher): the distance from the zone sampled every second from
    the start of the period until the first entry (or the end), minus the same sum for an ideal path going
    straight to the zone at the animal's mean speed;
  * **line crossings** while in the zone (all lines), and for **hidden zones** the number of *partial exits* (the
    animal seen between two times it is hidden in the zone, never further from it than the hidden-zone distance,
    e.g. peeking out of the nest) and the time partially exited;
  * **Whishaw's corridor** (zones): time (s and %), path % and distance inside a corridor from the release point
    (a *Release point* / *Start* point, else the first position) to the zone centre, *Whishaw corridor width* wide
    (20 cm by default), up to the first entry — as the water maze's corridor to the platform.

  Grid cells get all of these except Whishaw's corridor and the distance-from-zone measures (they are not listed
  among the visited zones). Zone groups get them all except the investigation, hidden-zone, Whishaw and
  distance-from-zone measures: their border is the outline of their zones (an edge shared by two zones of the
  group is not a border) and their centre is the centre of their area.
* **Whole test** lists: **Visited zones** (in the order of their first entry) and **Investigated zones** (order of
  the first investigation), as comma-separated text.
* **Per point**: mean / min / max distance, time near, approaches, latency, exploration time / bouts / latency, time
  and distance moving towards / away, mean speed moving towards (distance travelled while moving towards / that
  time), head oriented towards / away, mean head angle, head turns towards; with a tracked head, mean / max / min
  head distance and the time the head was moving towards / away (the head's distance shrinking / growing while the
  head moves faster than the mobility threshold). **Initial heading error**: the angle between the direction from
  the first position to the position about 1 s later and the direction to the point (0–180°, as the water maze's);
  **mean absolute heading error**: the mean angle between the direction of travel and the direction to the point
  over the frames the animal is mobile. **X / Y**: the point's coordinates (in units, from the top-left of the
  image). **Approximate time at point**: the time (from the start of the test or period) at which the animal was
  closest to the point.
* **Lines**: crossings in each direction, latency.
* **Per other animal**: mean / min / max distance, contact time and count, nose-to-nose and nose-to-body contacts,
  time and episodes following, approaches / approached by.
* **Keys** (scored behaviours): point – count, latency, rate; state and hold – count, duration, %, latency, mean &
  longest bout, rate, list of press durations (`1.5, 0.25, …`); both – distance travelled before the first press
  (the whole distance if never pressed, or blank, as latencies); optionally per zone (*Split the scored
  behaviours by zone*): count, latency, rate, distance before the first press and, for state / hold keys,
  duration, mean / longest / shortest bout, latency to the first release and the list of press durations (a press
  belongs to the zone the animal was in when it started).
* **I/O** measures (inputs, outputs, shockers, speakers, lights, encoders, analogue signals, virtual switches, OPAD)
  and **result variables** (`Variable: name`, and their recorded values) from procedures — see *I/O results*; they
  have their own *I/O* category in the measure chooser.

**Test-specific**

| Test | Extra measures |
| --- | --- |
| Elevated plus / zero maze | open arm time %, open arm entries %, open/closed/total arm entries, head dips |
| Y maze | arm entry sequence, total arm entries, spontaneous alternations, alternation %, same / alternate arm returns |
| Radial arm maze | entries, different arms visited, working-memory errors, correct entries before first error, entries to visit all arms |
| Radial arm place conditioning (RAPC) | type 1 errors, type 2 errors, total errors, door sequence, total arm entries, baited arms visited, correct entries before first error, entries to visit all baited arms |
| T maze | first choice, choice latency, arm alternations |
| Morris water maze | escape latency, found platform, path length to platform, platform crossings (vs other positions), mean / cumulative distance to platform (Gallagher proximity), initial heading error, target / opposite quadrant time %, wall hugging, search strategy |
| Barnes maze | primary latency, primary errors, primary path length, total errors, escape-hole visits, hole sequence, search strategy (direct / serial / random) |
| Novel object | novel / familiar exploration, total exploration, discrimination index, recognition index |
| Light/dark box | latency to enter dark, transitions, time in light % |
| Three-chamber | social / object interaction, sociability index, social chamber preference index |
| Forced swim / tail suspension | immobility time, %, latency |
| Several animals | mean inter-animal distance, time in contact |
| Manual scoring | per behaviour: count, duration, duration %, latency, mean bout (state) or count, latency, rate (point) |

The novel object and the social side can be set per test (test variables) or as experiment defaults.

#### More test-specific measures

* **Water maze**: Whishaw corridor time (s and %), path % and distance (release point → platform, 20 cm wide) and
  *Left Whishaw corridor* (the same corridor is available towards any zone, see the zone measures).
* **Novel tank diving test**: latency to top, top entries, top/bottom time %, top/bottom ratio, mean depth, erratic
  movements.
* **Multi-well plate** (6/12/24/48/96): one apparatus per well (Well, Centre, Edge) — larval zebrafish.
* **Conditioned place preference** (2 or 3 chambers): paired/unpaired time, CPP score, preference index,
  transitions (paired chamber per test or experiment default).
* **Hole board**: head dips per hole and total, head-dip time, latency, holes explored, repeated dips, dips/min.
* **Thermal gradient ring**: preferred sector, time-weighted mean sector, sector entries.
* **Home cage** (food zone, hidden nest) and **activity wheel** (revolutions clockwise / anticlockwise, per minute).
* **Radial arm place conditioning (RAPC)**: a radial arm maze with a door at the entrance of each arm (lines
  *Door 1*, *Door 2*, … — their crossings are counted like any line) and the rewarded arms in the zone group
  *Baited arms* (template parameter *Baited arms*, e.g. `1, 3, 5, 7`; edit the group to change them). Following the
  usual radial-maze convention, a **type 1 error** (working memory) is a re-entry into a baited arm already entered
  in the test (or period) and a **type 2 error** (reference memory) is any entry into an arm that is not baited
  (re-entries included), so each entry is at most one error. The **door sequence** lists the doors (arm numbers)
  the animal went through, in order, e.g. `1 4 2 1`. Without a *Baited arms* group every arm is baited.
* **Operant plantar assay (OPAD)**: measures from the I/O log — see *I/O results*.

### Information columns

Besides Test, Animal, Treatment, Sex, Stage, Trial and Apparatus, the results can show (tick them in the
*Information* branch of the column chooser; empty columns are hidden automatically, and the less common ones start
unticked):

| Column | Content |
|---|---|
| Test date, Day of week, Test time | When a live test was recorded (blank for tests tracked from a video). |
| Time of day | *Morning* (05–12 h), *Afternoon* (12–17 h), *Evening* (17–21 h) or *Night*, from the recording time. |
| User | The experimenter who ran, tracked or scored the test (see *Users*). |
| Test notes, Animal notes | The notes of the test (Test schedule) and of the animal (Animals sheet). |
| Treatment code | The treatment's code as on the Animals sheet: its blind code while testing blind, else A, B, C… |
| Reason for test end | Why a live test ended (see *Starting and ending a test*). |
| Animal lighter / darker | Whether the animal is lighter or darker than the apparatus: the detection *contrast* setting, or with *auto* what tracking found in most frames. Blank for colour tracking. |
| Animal length | The animal's median body length (nose–tail, or from its area), in the apparatus unit (cm when calibrated). |
| Frames tracked (%) | Percentage of the test's frames in which the animal was detected. |
| Source video file | The video a test was tracked from. |
| Recorded video file | The video recorded during a live test. |
| Video time at test start (s) | Where in the video the test starts (0 for recordings). |
| Moveable zone positions | The positions of the moveable zones and points in this test (centre, in video pixels), and the apparatus position when it was moved. |
| Period, Segment of test | With time periods: the period's label (e.g. *0-60 s*) and its number (1, 2, …; blank for the whole test). |

*Day of week*, *Time of day*, *User* and *Animal lighter / darker* can also be used to group results in Statistics.
In the *one row per animal* export, *Treatment code* and *Animal notes* are kept when they are shown.

### Time periods

Besides regular time bins and custom periods, **event-anchored periods**: anchored on test start, first entry to /
exit from a zone, a manual mark, or an input switching on; with offset, duration (0 = to the end) and occurrence
(1 = first, 0 = every occurrence). Example: *the 30 s after the animal first left the start box*. Paused time is
excluded from all times and distances.

### Track plots (Results ▸ Data ▸ Track plots)

Select a row of the results table to see the test's track on its first video frame. The options under the plot apply
to the selected test:

- **Body part**: the centre or the head.
- **Colour**: by time, by speed, in a single colour, or by any per-frame parameter (distance from the wall, head
  angle, distance to a point, and so on). A colour bar gives the scale.
- **Markers**: freezing episodes and manually scored state behaviours are drawn as thick translucent stretches of the
  path. Point events (for example defecation) are drawn as diamonds where the animal was at that moment.
- **Split by period**: shows one small track plot per time period (time bins, custom periods or event-anchored
  periods), all on the same colour scale. If the experiment has no periods, the test is split into quarters.

Selecting a time-period row (with *Time periods* shown — ribbon ▸ Time periods) limits the track plot and heat map to that period.

**Animated playback**: the bar under the track plot replays the track — ▶ draws the path progressively with the
animal's current position as an orange dot (markers appear when their time is reached), at **0.25× to 16×** real
time; drag the **time slider** to show the track up to any moment. At the end (or when paused at the end) the whole
track is shown again. Playback is not available with *Split by period*.

### Heat maps (Results ▸ Data ▸ Heat maps)

- **Heat map of**: where the animal spent its time, or only the frames where a behaviour happened: freezing, immobile,
  mobile, any scored behaviour (`<behaviour>: active`), near the wall, and so on.
- **Scale**: *Automatic* scales each map to its own maximum. *% of time* shows each bin's share of the mapped time.
  *Relative* sets the maximum to 1. *Fixed max* uses the value you type, so you can compare tests on the same scale.
- **Align**: sets the orientation of a test (rotate 90°/180°/270°, mirror, or a combination) so that the same parts
  of the apparatus line up between tests in group heat maps. For example, the target quadrant of a water maze or the
  novel object's side. The setting is saved with the test as `variables["heatmap_transform"]` and does not change any
  results.
- **By treatment** (*Treatment heat maps*): averages the heat maps of each treatment's tests shown in the table. Each test's alignment is
  applied, the maps are drawn on a common apparatus frame and colour scale, and the chosen behaviour, scale and time
  period are used.

### Charts of parameters over time (Results ▸ Data ▸ Charts)

Choose a test (and an animal if several were tracked together), then tick up to 10 parameters. They are drawn on a
shared time axis:

- on/off states as filled bands;
- counts as steps;
- everything else as lines.

Tick zones under **Zone occupancy bands** to shade the times the animal was in them. Scored behaviours appear in an
event strip under the charts.

Available parameters:

- **Position**: X/Y of the centre, head and tail; distance from the start, the arena centre and the wall; in arena;
  near the wall; detected.
- **Locomotion**: speed, smoothed speed (1 s), acceleration, distance travelled, distance in the last second, path
  efficiency, mobile / immobile, time mobile / immobile, immobile episodes.
- **Freezing**: motion (% of the body), freezing, time freezing, freezing episodes.
- **Direction**: movement direction, turn rate, absolute turn angle, head angle, angular velocity, cumulative
  rotation.
- **Body**: head speed, body length, body area, elongation.
- **Each zone and zone group**: in zone, head in zone, distance to zone, time in zone, entries.
- **Each point**: distance, head distance, near, head-to-point angle.
- **Each line**: distance, crossings.
- **Each scored behaviour**: active, or count for point behaviours.
- **Each other animal**: distance.

That is 37 general parameters plus 5 per zone, 4 per point, 2 per line and 1 per behaviour and per other animal.

Chart tools:

- **Mouse wheel** over a chart zooms the time axis of all charts around the pointer (wheel forward to zoom in);
  **double-click** or **Reset zoom** shows the whole time range again.
- Use the matplotlib toolbar to zoom, pan and go back.
- **Period** zooms to a time period.
- **Measure interval**: drag across a chart to get the mean, SD, minimum, maximum (with its time) and change of every
  charted parameter over that interval.
- **Find peaks** marks and counts the peaks of the continuous parameters.
- **Save image…** saves PNG/PDF/SVG, **Copy image** copies the chart, and **Export data…** saves the charted series
  frame by frame as CSV or tab-separated text.
- In the results table, right-click a row and choose **Show in charts** to open that test's charts.

### Video export with overlays (Results ▸ Data ▸ Video export)

Select a test in the results table and click **Export video…**. You can choose:

- zones, points and lines;
- the track trail: none, the last 2/5/15 s, or the whole track so far, coloured by speed, by time or in the animal's
  colour;
- the centre, head and tail;
- labels for scored behaviours and freezing;
- a time stamp and a test caption;
- playback speed (0.5× to 8×) and output size (100 %, 75 % or 50 %).

The video is written in the background with a progress bar and can be cancelled. It is encoded as H.264 where
available (VideoToolbox on Apple Silicon). Frames are streamed, so memory use does not depend on the video's length.
From Python: `manymaze.core.videoexport.export_video(project, test, "out.mp4", OverlayOptions(...))`.

### Data transfer (Data page)

- **Copy**: copies the selected cells as tab-separated text, ready to paste into Excel or Prism. Select any rectangular
  range with the mouse, or click the row numbers. With nothing (or one row) selected, it copies the whole shown table.
  Right-clicking the table also offers *Copy without headers* and *Save selected cells…*.
- **Export → CSV file / Tab-separated text / Excel workbook**: the shown rows and columns. The Excel workbook has extra
  sheets for time periods, animals, tests and settings.
- **Export → SYLK spreadsheet / dBase table**: the formats ANY-maze also offers. SYLK (`.slk`) is a text spreadsheet
  that Excel opens directly (numbers stay numbers, the heading row is bold). dBase III (`.dbf`, readable as dBase
  IV) is read by databases, SPSS, R (`foreign::read.dbf`), LibreOffice and GIS software: number columns become
  numeric fields, true/false columns logical fields, other columns text (at most 254 characters); text is
  Windows-1252 and **field names are limited to 10 characters** (e.g. `Total distance (cm)` → `TOTAL_DIST`, a second
  one → `TOTAL_DI_2`), so keep a CSV or Excel copy for the full column names.
- **Export → Selected cells…**: the selected range as CSV, TSV, xlsx, SYLK or dBase (chosen by the file extension).
- **Export → Experiment as XML (with raw tracks)…**: the whole experiment in one file, described below.
- **Export → Raw data per test (CSV)…**: one file per test and animal, with time, the raw track columns (pixels) and
  every per-frame parameter from the Charts view in calibrated units.
- **Export → One row per animal…**: the shown measures with one row per animal and one column per measure × stage /
  trial (and time period), e.g. `Total distance (cm) [Day 2 · 1]` — the layout Prism, SPSS or Excel need for
  repeated measures.
- **Export → Mean of each animal's trials per stage…**: one row per animal and stage with the mean of its trials
  (e.g. the four water-maze trials of each day) and the number of trials averaged.
- **Export → Event log of the shown tests…**: every test's events in time order — zone (and zone group) entries and
  exits, key presses (on / off), I/O inputs and outputs, pauses — with the test, stage, trial and animal.
- **HTML report…**: you can also set the heat-map scale (including one scale for all tests) and add charts of the
  parameters ticked in the Charts view.

### Importing from ANY-maze

ANY-maze keeps an experiment in a `.szd` file whose format is proprietary, compressed and undocumented ("virtually
impossible for any other programs to read", says ANY-maze's help), so mANY-MAZE cannot open it directly. Export
the data from ANY-maze instead (ANY-maze ▸ **File ▸ Export**) and import it with **File ▸ Import from ANY-maze**:

| In ANY-maze | Import as | What you get |
|---|---|---|
| *Export zone maps* (individual or combined, CSV) | **Zone maps (apparatus zones)…** — select all the files | an apparatus per ANY-maze apparatus, with its zones traced from the pixels (border maps are filled; a moveable zone gets its first position). An existing apparatus of the same name keeps its other elements and calibration |
| *Export experiment as XML* (with the default top-left coordinates) | **Experiment exported as XML…** | the animals (ID or *Animal N*, treatment, notes), every performed test with its stage, trial, date and time, notes, reason for ending, its track (centre, head, tail; frames without a position stay undetected) and its scaling as calibration; zones moved in a test become moveable-zone positions |
| *Export test data*, or spreadsheets (animals, schedule) — CSV, tab-separated, Excel, SYLK or dBase | **Animals and treatments… / Test schedule…**, or *Import track data* on the Test schedule page | through the column-matching import wizard (dBase field names are cut to 10 characters, so match those columns by hand) |

Import the zone maps first: an apparatus that does not exist yet is otherwise created from the zones' bounding
boxes (rectangles), which is all the XML export contains about zone shapes. Positions exported relative to the
apparatus centre (ANY-maze's option) are recognised by their negative coordinates. ANY-maze's documentation names
only the result tags of the XML file, so the other fields are recognised by name (case and separators do not
matter); if a field of your file is not picked up, the import keeps the rest and you can complete it by hand.

#### XML format (`format-version="1"`)

Root element: `<manymaze-experiment format-version software exported>`. It contains, in order:

| Element | Content |
|---|---|
| `experiment` | `name`, `protocol`, `test-duration-s`, `start-mode`, `created`, `blind`; `<description>`; `<detection-settings>` and `<analysis-settings>` with `<setting name value type>` entries; `<variables>` |
| `groups/group` | `name`, `color` |
| `stages/stage` | `name` |
| `behaviours/behaviour` | `name`, `key`, `kind` |
| `apparatus-list/apparatus` | `name`, `template`, `unit`, `px-per-cm`, frame size; `<arena type …>`, `<zone name color><shape type …>`, `<zone-group><member zone/><exclude zone/>`, `<point name x y radius-cm>`, `<line name x1 y1 x2 y2>`. Polygons list `<vertex x y/>` elements; ellipses have `cx cy rx ry` attributes. |
| `experimenters/experimenter` | `name` (only when the experiment has users) |
| `animals/animal` | `id`, `group`, `sex`, `notes`; `<field name value type>` |
| `tests/test` | One element per test (details below) |

Each `tests/test` element has the attributes `id animal stage trial apparatus video start-s duration-s status
recorded-at` (and `experimenter`, `end-reason` when set) and these children:

- `<extra-animal>`, `<notes>`, `<variables>`, `<zone-overrides>`, `<pauses><pause start end>`;
- `<events><event behaviour t t-end>` and `<io-events><io t device channel kind value>`;
- `<result-variables>`;
- `<results animal period>` with one `<result name value type>` per measure;
- one `<track animal index fps samples video-start-s units="px">` per animal, with `<column name>` elements for `t x y
  hx hy tx ty area motion angle detected`.

Values in a `<column>` are separated by spaces, with `NaN` for missing values. `type` is `number`, `integer`, `text`,
`bool` or `json`.

To read the file in MATLAB:

```matlab
doc = xmlread('experiment.xml');
cols = doc.getElementsByTagName('column');
x = sscanf(char(cols.item(1).getTextContent()), '%f');
```

You can also use `readstruct('experiment.xml')` (R2020b+). In Python, use
`manymaze.core.export.read_experiment_xml(path)`.

## 10. Statistics

Compare any measure between groups (or sex, stage, period, custom fields): descriptive statistics, assumption
checks (Shapiro–Wilk, Levene), Welch's t-test / Mann–Whitney U (2 groups), one-way ANOVA + Tukey HSD /
Kruskal–Wallis + Bonferroni-corrected Mann–Whitney (> 2 groups), paired t / Wilcoxon / Friedman for repeated
measures, effect sizes (Cohen's d, η²), bar + SEM or box plots with individual points and significance stars.
Learning curves across stages or time bins with two-way ANOVA (Group × Stage), and correlations
(Pearson / Spearman) between measures.

### Statistics in detail

The left panel chooses the measure, the factor to compare, the time period, a filter (**Only**) and the graph. The
**Graph** options are column, points, box or violin; error bars show the SEM, the SD or the 95 % CI; individual
values can be shown or hidden.

- **Compare groups**:
  - *Automatic* uses Welch t / Mann-Whitney for two levels and ANOVA + Tukey / Kruskal-Wallis + Bonferroni for more.
    With **Repeated measures** it uses paired t / Wilcoxon / repeated-measures ANOVA / Friedman.
  - You can also pick a test yourself. One-sample tests compare every group with a **Test value**, for example 50 %
    alternation or a discrimination index of 0.
  - **Post-hoc** offers Tukey, Bonferroni, Holm, Šidák, FDR, Dunnett (against the chosen **Control**), Games-Howell
    or Dunn.
  - Results include the effect sizes (Cohen's d, Hedges' g, rank-biserial r, eta², omega², epsilon², Kendall's W,
    partial eta²), the descriptive statistics (n, mean, SD, SEM, 95 % CI, median, range) and assumption checks
    (Shapiro-Wilk, D'Agostino-Pearson, Levene, Brown-Forsythe, Bartlett, Fligner-Killeen).
- **Two factors / time course**: learning curves and two-factor designs, with the stage, trial or period on the X axis
  and lines per group. **Design** can be:
  - between subjects (two-way ANOVA, type II);
  - *Repeated on X axis* (a mixed ANOVA, or a one-way repeated-measures ANOVA when there are no lines);
  - Scheirer-Ray-Hare;
  - aligned rank transform ANOVA.

  Repeated-measures effects also get Greenhouse-Geisser corrected p-values. The graph can be a line, column, points,
  box or violin plot.
- **Correlation**: Pearson, Spearman or Kendall, with a least-squares line and its 95 % confidence band. It also
  reports the regression slope (with its CI), intercept and R². Points can be coloured by any factor; the levels
  of that factor are then also compared by **ANCOVA** (analysis of covariance): the Y measure adjusted for the X
  measure as covariate (e.g. distance travelled adjusted for body weight). It reports F and p for the factor and
  the covariate, the common slope, the covariate-adjusted means ± SE and the homogeneity-of-slopes test (if the
  slopes differ between groups, the ANCOVA assumption is violated).
- **Grouped (3 levels)**: descriptive statistics for every combination of up to three factors (for example group ×
  stage × period) and a clustered graph:
  - factor 1 on the X axis;
  - factor 2 as colours;
  - factor 3 as separate panels.

  **Copy table** and **Save table…** export it as CSV, TSV or xlsx.
- **Categorical**: a contingency table of a text result (search strategy, first choice, found platform…) or a factor,
  by group. It reports a chi-square test of independence with Cramér's V, the G-test and, for 2 × 2 tables, Fisher's
  exact test. It warns when expected counts are below 5. The graph shows stacked percentages.

**Copy summary**, **Save figure…** and **Copy figure** work on every tab.

#### Supported procedures (43)

| Category | Procedures |
|---|---|
| Two groups (7) | Student's t, Welch's t, paired t, Mann-Whitney U, Wilcoxon signed-rank, Kolmogorov-Smirnov, Brunner-Munzel |
| One sample (2) | One-sample t-test, Wilcoxon signed-rank against a value |
| Several groups (5) | One-way ANOVA, Welch's ANOVA, Alexander-Govern, Kruskal-Wallis, Mood's median test |
| Repeated measures (2) | Repeated-measures ANOVA (+ Greenhouse-Geisser), Friedman |
| Two factors (5) | Two-way ANOVA, mixed two-way ANOVA, Scheirer-Ray-Hare, aligned rank transform ANOVA, ANCOVA |
| Post-hoc (8) | Tukey HSD, Bonferroni, Holm, Šidák, Benjamini-Hochberg FDR, Dunnett, Games-Howell, Dunn |
| Categorical (4) | Chi-square independence, Fisher's exact, G-test, chi-square goodness of fit |
| Correlation (4) | Pearson, Spearman, Kendall's tau, linear regression with confidence intervals |
| Assumptions (6) | Shapiro-Wilk, D'Agostino-Pearson, Levene, Brown-Forsythe, Bartlett, Fligner-Killeen |

## 11. Command line

```
manymaze                                  # GUI
manymaze demo ~/Desktop/demo.mmaze        # demo experiment
manymaze track video.mp4 --template epm --bbox 100,40,520,520 --size-cm 75 -o results.csv
manymaze project ~/exp.mmaze track        # batch-track untracked tests in parallel (--workers N)
manymaze project ~/exp.mmaze results -o results.xlsx --bins   # also .csv .tsv .slk (SYLK) .dbf (dBase) .xml
manymaze project ~/exp.mmaze results --wide -o by_animal.xlsx   # one row per animal
manymaze project ~/exp.mmaze report -o report.html
manymaze project ~/exp.mmaze events -o events.csv     # event log of every test
manymaze project ~/exp.mmaze protocol -o protocol.html
manymaze project ~/exp.mmaze archive -o exp.zip       # experiment + all videos in one file
manymaze templates                        # list apparatus templates
```

## 12. Tips for good tracking

* Even, diffuse lighting; avoid reflections (water maze: add non-toxic white paint or milk for dark animals).
* Maximise contrast between animal and floor (dark animals on white floor or vice versa).
* Fix the camera above the centre of the apparatus; avoid zooming during an experiment.
* Record the empty apparatus for a few seconds before placing the animal, or use median background.
* For infrared recordings choose *Animal is lighter/darker* accordingly.
