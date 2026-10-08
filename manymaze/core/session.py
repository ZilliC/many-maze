"""What every live session offers — camera (:class:`live.LiveSession`), observation only
(:class:`live.ObservationSession`) or rebuilt after a crash (:class:`autosave.RecoveredSession`) — and how a finished
session is stored in its test."""

from __future__ import annotations

import copy
import datetime as _dt
from pathlib import Path

from .video import playlist_parts, recorded_video

# why a live test ended (Test.end_reason, the "Reason for test end" results column). END_ZONE is decided by the
# analysis (AnalysisSettings.end_zone), for live and video tests alike
END_DURATION = "Test duration reached"
END_USER = "Stopped by user"
END_PROCEDURE = "Ended by procedure"
END_SOURCE = "End of the video"
END_SOURCE_FAILED = "Camera or video failed"
END_RECOVERED = "Interrupted (recovered after a crash)"
END_ZONE = "Animal reached the end zone"


class Session:
    """The defaults of sessions without a camera, procedures or crash-recovery file.  (Every session also has an
    ``apparatus``, None without a camera: a class default here would break LiveSession's dataclass fields.)"""

    name = ""
    duration_s = 0.0
    engine = None  # procedures.ProcedureEngine
    outputs = None  # procedures.Outputs (legacy serial port and action log)
    devices = None
    stats = None  # live.LiveStats
    end_reason = ""  # why the test ended (END_* values), set by finish()

    @property
    def elapsed(self) -> float:
        return 0.0

    @property
    def io_events(self) -> list:
        return list(self.engine.io_events) if self.engine is not None else []

    @property
    def result_variables(self) -> dict:
        return dict(self.engine.result_variables) if self.engine is not None else {}

    @property
    def kept_variables(self) -> dict:
        """Procedure variables marked "keep" (stored in the project variables when the test is saved)."""
        return dict(self.engine.kept_variables) if self.engine is not None else {}

    def track(self):
        """The tracked positions (track.Track), or None without a camera."""
        return None

    def trail(self, n: int) -> list[tuple[float, float]]:
        """The last n positions while the test runs (drawn on the camera image)."""
        return []

    def process(self, frame, timestamp: float | None = None) -> list:
        return []

    def key(self, key: str, down: bool = True):
        pass

    def remove_autosave(self):
        pass


def save_live_test(project, test, session: Session, record_path: str | None = None) -> bool:
    """Store a finished session in its test: track (camera sessions), recording, events, pauses, I/O events and
    procedure result variables.  Returns False if there was nothing to save.  The session's crash-recovery file
    is left to the caller, to remove once the project is saved."""
    tr = session.track()
    if tr is not None:
        if not len(tr):
            return False
        project.save_tracks(test, [tr])
        test.status = "tracked"
    else:
        if not session.events:
            return False
        test.status = "scored"
        if session.duration_s and session.elapsed < session.duration_s - 0.05:
            test.duration_s = round(session.elapsed, 3)
    record_path = recorded_video(record_path)
    if record_path:
        test.video = project.rel_path(record_path)
        test.start_s = 0.0
    test.events = sorted(list(test.events) + [dict(e) for e in session.events], key=lambda e: e.get("t", 0))
    test.pauses = [list(p) for p in session.pauses]
    test.io_events = list(test.io_events) + session.io_events
    rv = session.result_variables
    if rv:
        test.result_variables = {**test.result_variables, **rv}
    kept = session.kept_variables
    if kept:  # procedure variables kept between tests: only from tests that are saved
        project.variables.update(copy.deepcopy(kept))
    test.recorded_at = _dt.datetime.now().isoformat(timespec="seconds")
    test.end_reason = session.end_reason or END_USER
    if getattr(project, "current_user", ""):
        test.experimenter = project.current_user  # who ran the test
    try:
        from .workflow import refresh_status
        refresh_status(project, test)
    except Exception:
        pass
    notes = []
    outs = session.outputs
    if outs is not None and outs.log:
        notes.append("Live procedures: " + "; ".join(outs.log[:50]))
    if session.pause_log:
        notes.append("Paused: " + "; ".join(f"at {p['t']:.2f} s for {p['duration_s']:.1f} s"
                                            for p in session.pause_log))
    if notes:
        test.notes = (test.notes + "\n" + "\n".join(notes)).strip()
    return True


def finish_live_test(project, test, session: Session, record_path: str | None = None, save: bool = True,
                     new_test: bool = False) -> bool:
    """A live test is over: store the session in its test (:func:`save_live_test`), or discard it — its recording
    and crash-recovery file are deleted and a test created for it (new_test) is removed.  Returns True when
    stored; the caller then saves the project and removes the crash-recovery file (``session.remove_autosave()``)."""
    if save and project is not None and test is not None and save_live_test(project, test, session, record_path):
        return True
    session.remove_autosave()
    if record_path:
        files = [record_path]
        playlist = Path(record_path).with_suffix(".m3u")  # a split recording: its parts and playlist
        if playlist.exists():
            try:
                files += playlist_parts(playlist)
            except OSError:
                pass
            files.append(playlist)
        for f in files:
            try:
                Path(f).unlink(missing_ok=True)
            except OSError:
                pass
    if new_test and project is not None and test in project.tests:
        project.tests.remove(test)
    return False
