# The `objc` "Class AVFFrameReceiver is implemented in both …" warning (macOS)

On macOS, a process that has loaded both OpenCV (`cv2`) and PyAV (`av`) prints, once, when `av` is first loaded:

```
objc[…]: Class AVFFrameReceiver is implemented in both …/cv2/.dylibs/libavdevice.61.….dylib and
…/av/.dylibs/libavdevice.63.….dylib … One of the duplicates must be removed or renamed.
objc[…]: Class AVFAudioReceiver is implemented in both …
```

Audit 2026-10-08 (Tracking, video and cameras, LOW) asked whether this matters for cameras. It does not; this note
records why and what was checked.

## Why there are two copies

Both wheels bundle their own FFmpeg: `opencv-python-headless` (OpenCV 5.0) ships FFmpeg 7.1
(`libavdevice.61`), `av` 19 ships FFmpeg 8 (`libavdevice.63`). Each copy of `libavdevice` contains FFmpeg's
AVFoundation *input device* (`avfoundation.m`), which defines the two Objective-C classes `AVFFrameReceiver` and
`AVFAudioReceiver`. Objective-C class names are process-global, so the second library to load triggers the warning
and the runtime uses one of the two definitions for both.

Aligning the wheel versions would not help: two libraries at different paths still define the same classes. Only
removing FFmpeg from one of the wheels (a custom OpenCV build) or dropping one of the packages would; both are far
more invasive than the problem warrants (PyAV gives the fast VideoToolbox decoding and the H.264 recorder, OpenCV
the camera capture and random access).

## Why it is harmless here

The duplicated classes are only used when FFmpeg's `avfoundation` *input device* is opened (`ffmpeg -f
avfoundation`, `av.open(…, format="avfoundation")`, or OpenCV's FFmpeg backend asked for that device). mANY-MAZE
never does that:

* Cameras are opened with OpenCV's own AVFoundation backend (`cv2.CAP_AVFOUNDATION`, `core/video.py`), which is
  compiled into `cv2.abi3.so` and uses its own delegate class `CaptureDelegate` — not `libavdevice`.
  (`otool -oV cv2/cv2.abi3.so` lists `CaptureDelegate`, `CVWindow`, `CVView`, `CVSlider`; only
  `libavdevice.*.dylib` defines `AVFFrameReceiver` / `AVFAudioReceiver`.)
* PyAV is used only for files: sequential decoding (`FrameReader`) and recording (`VideoRecorder`), with
  `av.open(path)` — no input devices.

So whichever definition the runtime picks, no code path instantiates either class.

## Keeping PyAV out of processes that do not need it

`av` is imported lazily (`core/video.py: _av()`), only when a file is decoded or recorded; importing the camera,
live-session and tracking modules does not load it (guarded by
`tests/test_tracking_audit.py::test_capture_modules_do_not_load_pyav`). Processes that never touch a file (and
batch workers on other platforms) therefore never print the warning. To avoid PyAV altogether — e.g. to rule it
out while diagnosing a camera problem — set `MANYMAZE_DECODER=opencv`: decoding and recording then use OpenCV only
(slower, no hardware decoding).
