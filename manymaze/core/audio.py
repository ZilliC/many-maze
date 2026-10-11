"""The ``audio`` I/O device: tones, white noise and sound files, once or repeated, on the computer's sound output
(WAV generated with NumPy and played with ``afplay``/``paplay``/``aplay`` or PowerShell on Windows, or by a player
installed by the GUI in ``AudioDevice.player``)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np

from .iobase import Device


def tone_samples(freq: float, duration: float, rate: int = 44100, volume: float = 0.5, ramp_s: float = 0.005,
                 start: int = 0, total: int | None = None) -> np.ndarray:
    """int16 sine samples [start, start+n) of a tone of `duration` s with raised-cosine on/off ramps."""
    total = int(round(duration * rate)) if total is None else total
    n = max(0, min(int(round(duration * rate)), total) - start)
    i = np.arange(start, start + n)
    y = np.sin(2 * np.pi * float(freq) * i / rate)
    return _finish(y, i, total, rate, volume, ramp_s)


def noise_samples(duration: float, rate: int = 44100, volume: float = 0.5, ramp_s: float = 0.005, start: int = 0,
                  total: int | None = None, seed: int | None = None) -> np.ndarray:
    total = int(round(duration * rate)) if total is None else total
    n = max(0, total - start)
    i = np.arange(start, start + n)
    y = np.random.default_rng(seed).uniform(-1, 1, n)
    return _finish(y, i, total, rate, volume, ramp_s)


def _finish(y, i, total, rate, volume, ramp_s):
    r = max(1, int(ramp_s * rate))
    env = np.minimum(1.0, np.minimum(i / r, (total - 1 - i) / r).clip(0))
    env = 0.5 - 0.5 * np.cos(np.pi * env)
    return (y * env * max(0.0, min(1.0, float(volume))) * 32767).astype(np.int16)


def write_wav(path, kind: str, duration: float, freq: float = 1000.0, volume: float = 0.5,
              rate: int = 44100) -> str:
    """Write a tone ('tone') or white noise ('noise') WAV in 1-second chunks (low memory)."""
    duration = max(0.01, min(float(duration), 3600.0))
    total = int(round(duration * rate))
    rng_seed = 12345
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        for s in range(0, total, rate):
            if kind == "tone":
                block = tone_samples(freq, duration, rate, volume, start=s, total=total)[:rate]
            else:
                block = noise_samples(duration, rate, volume, start=s, total=total, seed=rng_seed + s)[:rate]
            w.writeframes(block.tobytes())
    return str(path)


def _audio_dir() -> Path:
    d = Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / "manymaze_audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


class AudioDevice(Device):
    """Computer audio. ``player`` (class attribute) may be set by the GUI to a callable(path, volume) returning
    an object with ``stop()`` (e.g. a Qt Multimedia player); otherwise a command-line player is used."""

    type = "audio"
    player = None

    def __init__(self, cfg, transport=None):
        super().__init__(cfg, transport)
        self.played: list[tuple[str, dict]] = []
        self._procs: list = []
        self.backend = self._pick_backend(cfg.get("backend", "auto"))

    @staticmethod
    def _pick_backend(want: str):
        if want == "none":
            return None
        if want not in ("auto", None, ""):
            return want if shutil.which(want) else None
        cands = (["afplay"] if sys.platform == "darwin" else ["powershell"] if sys.platform.startswith("win")
                 else ["paplay", "aplay"])
        return next((c for c in cands if shutil.which(c)), None)

    def audio(self, cmd: str, **kw) -> bool:
        self.played.append((cmd, dict(kw)))
        if cmd == "stop":
            self.stop()
            return True
        rate = int(self.cfg.get("rate", 44100))
        vol = float(kw.get("volume", 0.5))
        if cmd == "tone":
            f, d = float(kw.get("frequency", 1000)), float(kw.get("duration", 1))
            if f >= rate / 2:
                rate = int(min(192000, max(rate, 2.2 * f)))
            path = _audio_dir() / f"tone_{f:g}_{d:g}_{vol:g}_{rate}.wav"
            if not path.exists():
                write_wav(path, "tone", d, f, vol, rate)
        elif cmd == "noise":
            d = float(kw.get("duration", 1))
            path = _audio_dir() / f"noise_{d:g}_{vol:g}_{rate}.wav"
            if not path.exists():
                write_wav(path, "noise", d, volume=vol, rate=rate)
        elif cmd == "file":
            path = Path(str(kw.get("file", "")))
            if not path.exists():
                self._error(f"{self.name}: sound file not found: {path}")
                return False
        else:
            return False
        repeat = int(kw.get("repeat", 1) or 0) if cmd == "file" else 1
        if repeat != 1:
            loop = _Loop(self, str(path), vol, repeat)
            self._procs.append(loop)
            loop.start()
            return True
        return self._play(str(path), vol if cmd == "file" else 1.0)

    def _play(self, path: str, volume: float, track: bool = True):
        if track:  # forget the sounds that have ended
            self._procs = [p for p in self._procs if not _finished(p)]
        if AudioDevice.player is not None:
            try:
                h = AudioDevice.player(path, volume)
                if not track:
                    return h
                self._procs.append(h)
                return True
            except Exception as e:  # pragma: no cover - GUI dependent
                self._error(f"{self.name}: audio player failed: {e}")
                return False
        if self.backend is None:
            return False
        args = player_args(self.backend, path, volume)
        try:  # pragma: no cover - depends on the sound system
            h = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not track:
                return h
            self._procs.append(h)
            return True
        except Exception as e:  # pragma: no cover
            self._error(f"{self.name}: could not play sound: {e}")
            return False

    def stop(self):
        for p in self._procs:
            try:
                if hasattr(p, "terminate"):
                    p.terminate()
                else:
                    p.stop()
            except Exception:  # pragma: no cover
                pass
        for p in self._procs:
            if isinstance(p, subprocess.Popen):  # reaped, not left as zombies
                try:
                    p.wait(timeout=0.5)
                except Exception:  # pragma: no cover
                    pass
        self._procs = []

    def close(self):
        self.stop()
        super().close()


def player_args(backend: str, path: str, volume: float) -> list[str]:
    """The command line that plays a WAV file with ``backend``: ``afplay`` (macOS, with the volume), Windows'
    PowerShell (.NET SoundPlayer; WAV only, at the file's own volume) or ``paplay`` / ``aplay`` (Linux)."""
    if backend == "afplay":
        return ["afplay", "-v", f"{volume:g}", path]
    if backend == "powershell":
        quoted = "'" + path.replace("'", "''") + "'"
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"(New-Object Media.SoundPlayer {quoted}).PlaySync()"]
    return [backend, path]


def _finished(p) -> bool:
    """Whether a played sound (a player process, a :class:`_Loop`) has ended; GUI player handles: never known."""
    poll = getattr(p, "poll", None)
    try:
        return poll is not None and poll() is not None
    except Exception:  # pragma: no cover
        return False


def _wav_seconds(path: str) -> float | None:
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return None


class _Loop:
    """A sound file played `repeat` times (0 = until stopped) from a background thread; ``stop()`` ends it."""

    def __init__(self, dev: AudioDevice, path: str, volume: float, repeat: int):
        self.dev, self.path, self.volume, self.repeat = dev, path, volume, max(0, int(repeat))
        self.plays = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()  # stop() and the thread starting the next play
        self._cur = None
        self._thread = threading.Thread(target=self._run, name="audio-loop", daemon=True)

    def start(self):
        self._thread.start()

    def poll(self):
        """None while playing (or about to), as Popen.poll."""
        return 0 if self._thread.ident is not None and not self._thread.is_alive() else None

    def _run(self):
        length = _wav_seconds(self.path)
        while not self._stop.is_set() and (not self.repeat or self.plays < self.repeat):
            cur = self.dev._play(self.path, self.volume, track=False)
            with self._lock:
                self._cur = cur
                stopped = self._stop.is_set()
            if stopped:  # stopped while this play was starting: stop() did not see it
                self._halt(cur)
                return
            self.plays += 1
            if self._cur is None or self._cur is False:
                if self.dev.backend is None and AudioDevice.player is None:
                    # no sound system (e.g. tests): keep counting plays at the file's pace
                    if self._stop.wait(length or 0.05):
                        return
                    continue
                return
            if hasattr(self._cur, "wait"):
                while not self._stop.is_set():
                    try:
                        self._cur.wait(timeout=0.05)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            elif self._stop.wait(length or 1.0):  # a GUI player: wait for the length of the file
                return

    def stop(self):
        with self._lock:
            self._stop.set()
            cur = self._cur
        self._halt(cur)

    @staticmethod
    def _halt(cur):
        if cur is not None and cur is not False and cur is not True:
            try:
                cur.terminate() if hasattr(cur, "terminate") else cur.stop()
            except Exception:  # pragma: no cover
                pass

    terminate = stop
