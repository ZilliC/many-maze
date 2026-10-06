# mANY-MAZE user guide

mANY-MAZE is a libre (GPL-3.0-or-later) video-tracking and behavioural-analysis suite. It follows the
workflow of commercial packages such as ANY-maze: **experiment → animals → apparatus → tests → tracking /
scoring → results → statistics**. Every page is reachable from the sidebar (or ⌘1 … ⌘8).

---

## 1. Create an experiment

*File ▸ New experiment…* asks for a name, a folder and a **protocol** (open field, elevated plus maze, Morris
water maze, Barnes maze, Y/T/radial maze, novel object recognition, light/dark box, three-chamber sociability,
fear conditioning, forced swim / tail suspension, or custom). The experiment is saved as a folder
`Name.mmaze/` containing `project.json`, `tracks/`, `recordings/` and `exports/`. Videos are referenced by
relative path when they live inside the experiment folder, so the folder can be moved or shared.

*File ▸ Create demo experiment* builds a complete open-field experiment from synthetic videos so you can try
everything without a camera.

### Experiment page

* **Test duration** – analysed length of each test (0 = until the end of the video).
* **Test starts** – at each test's *start time* (set per test) or automatically **when the animal is first
  detected** in the apparatus.
* **Stages** – e.g. *Habituation, Day 1, Day 2, Probe*. Used for learning curves and repeated-measures
  statistics.
* **Manually scored behaviours** – name, keyboard key and type (*state* with a duration, e.g. grooming; *point*
  for instantaneous events, e.g. defecation).
* **Custom analysis periods** – named time windows (e.g. *Tone 1: 120–150 s*) that override regular time bins;
  ideal for fear-conditioning CS periods.
* **Detection settings** – defaults for all tests (each test can override them, see §5). *Body parts from*
  chooses how head, body centre and tail base are found: from the animal's shape (fast, no model) or with the
  **pose model** (deep learning, see §5.1).
* **Analysis settings** – thresholds for mobility, freezing, zone entries, thigmotaxis, object exploration,
  social contact and time bins.

## 2. Animals and groups

Add animals one by one or in bulk, assign **groups** (treatments/genotypes, each with a colour used in all
graphs), sex and any number of custom columns (age, weight, litter…). Animals can be imported from / exported
to CSV (`ID, Group, Sex, …`).

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

Several apparatus can share one video (e.g. four open fields filmed together) — tests that share a video and
start time are tracked in a single pass.

## 4. Tests

A test = one animal × one video (or live recording) × one apparatus × stage/trial. Use *Add tests from
videos…* to import many videos at once. Tests can also be created without a video (for live testing or manual
scoring only). Import tracks from DeepLabCut CSV files (or earlier mANY-MAZE exports) when pose estimation was
done elsewhere.

**Track selected / Track all untracked** runs the tracker in the background.

**Variables…** (toolbar or right-click) sets per-test variables that change the analysis: which point of
interest is the *novel object* (novel object recognition) and the *social stimulus side* (three-chamber).

## 5. Test view — review, tune, score

* **Video + overlay**: zones, current position, head (red), tail (blue) and trail.
* **Detection preview**: shows the foreground mask live while you tune settings for this test. Typical
  adjustments: *Animal is* (darker / lighter), *Threshold*, *Min animal area*.
* **Test start**: set the start to the current video time.
* **Manual scoring**: press a behaviour's key during playback to start/stop a state behaviour (or add a point
  event) at the current time. Space = play/pause, ←/→ = frame step (Shift = 1 s).
* **Track corrections**: click to set the animal position on a frame, delete or interpolate ranges.
* **Results / plots** for the test: track plot, occupancy heat map, speed and freezing trace.

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
6. Gaps up to *Interpolate gaps* seconds are filled; optional smoothing.

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

## 6. Live testing

Pick a camera (AVFoundation on macOS — grant camera permission when asked) or simulate with a video file,
choose the test, capture an empty-arena background (or use the adaptive model), and start. Tests can start
immediately or when the animal is placed in the apparatus. The video is recorded alongside tracking.

**Procedures** react to the animal in real time: *when <trigger> [after delay] do <action>*.
Triggers: start, end, time, zone enter/exit, freezing start/end, immobility start/end, animal lost. Actions:
serial command (e.g. to an Arduino driving LEDs, tones, shockers, feeders — `pip install pyserial`), TTL alias,
beep, mark an event, end the test.

## 7. Results

The results table has one row per test (and per time bin / period when enabled). Choose which measures to
show, filter by group or stage, export **CSV**, **Excel** (with Animals, Tests and Settings sheets) or copy to
the clipboard (for Prism / Excel / R), and generate a self-contained **HTML report** with track plots, heat
maps, group heat maps, results and statistics.

### Measures

**General**: test duration, detection %, total distance, mean / max speed, mean speed while mobile, time
mobile/immobile, immobile episodes, latency to immobility, time freezing, freezing %, freezing episodes,
latency to freezing, mean motion, path efficiency, absolute turn angle, meander, clockwise / anticlockwise
rotations, mean distance from wall, thigmotaxis %, time outside the arena.

**Per zone / zone group**: time, time %, entries, latency to first entry, distance, mean speed, mean visit
duration, time immobile, time freezing, head entries, head time, latency to head entry.

**Per point**: mean / minimum distance, time near, approaches, latency to approach, time exploring (head within
radius *and* pointing at the point), exploration bouts, latency to explore.

**Per line**: crossings, crossings in each direction, latency to first crossing.

**Test-specific**

| Test | Extra measures |
| --- | --- |
| Elevated plus / zero maze | open arm time %, open arm entries %, open/closed/total arm entries, head dips |
| Y maze | arm entry sequence, total arm entries, spontaneous alternations, alternation %, same / alternate arm returns |
| Radial arm maze | entries, different arms visited, working-memory errors, correct entries before first error, entries to visit all arms |
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

## 8. Statistics

Compare any measure between groups (or sex, stage, period, custom fields): descriptive statistics, assumption
checks (Shapiro–Wilk, Levene), Welch's t-test / Mann–Whitney U (2 groups), one-way ANOVA + Tukey HSD /
Kruskal–Wallis + Bonferroni-corrected Mann–Whitney (> 2 groups), paired t / Wilcoxon / Friedman for repeated
measures, effect sizes (Cohen's d, η²), bar + SEM or box plots with individual points and significance stars.
Learning curves across stages or time bins with two-way ANOVA (Group × Stage), and correlations
(Pearson / Spearman) between measures.

## 9. Command line

```
manymaze                                  # GUI
manymaze demo ~/Desktop/demo.mmaze        # demo experiment
manymaze track video.mp4 --template epm --bbox 100,40,520,520 --size-cm 75 -o results.csv
manymaze project ~/exp.mmaze track        # batch-track untracked tests in parallel (--workers N)
manymaze project ~/exp.mmaze results -o results.xlsx --bins
manymaze project ~/exp.mmaze report -o report.html
manymaze templates                        # list apparatus templates
```

## 10. Tips for good tracking

* Even, diffuse lighting; avoid reflections (water maze: add non-toxic white paint or milk for dark animals).
* Maximise contrast between animal and floor (dark animals on white floor or vice versa).
* Fix the camera above the centre of the apparatus; avoid zooming during an experiment.
* Record the empty apparatus for a few seconds before placing the animal, or use median background.
* For infrared recordings choose *Animal is lighter/darker* accordingly.
