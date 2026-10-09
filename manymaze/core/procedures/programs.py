"""Which programs the "Run a program" action may start on this computer.

A procedure comes from a project file, and a project file may come from anyone (a colleague, a download, an
archive): the action would let it run any command. So a program runs only when it is on this computer's list of
allowed programs — a per-user setting, never stored in the project — or when the user allows it when asked.

The list is a JSON file in the user's settings folder (beside ``$MANYMAZE_SETTINGS`` when it is set; or
``$MANYMAZE_PROGRAMS``). Programs are kept by their real path (links followed), so allowing ``python3`` allows
that one executable only.

The GUI asks the user by setting a confirmation callback::

    from manymaze.core.procedures import programs
    programs.policy.confirm = lambda path, arguments, context: ask_user(path, arguments)   # True: allow it

The callback runs in the thread that runs the procedures (e.g. the frame thread): ask before the test starts with
:func:`unauthorised_programs` where possible. A program allowed by the callback is added to the list; a refused one
is not asked about again during this session. Without a callback, a program that is not on the list is not run and
the procedure reports an error."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)


def allowlist_path() -> Path:
    """The per-user file of the programs allowed to run ($MANYMAZE_PROGRAMS, else beside $MANYMAZE_SETTINGS, else
    the user's application settings folder)."""
    env = os.environ.get("MANYMAZE_PROGRAMS")
    if env:
        return Path(env).expanduser()
    settings = os.environ.get("MANYMAZE_SETTINGS")
    if settings:
        return Path(settings).expanduser().parent / "allowed_programs.json"
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "mANY-MAZE"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "mANY-MAZE"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "manymaze"
    return base / "allowed_programs.json"


def resolve(program: str) -> str | None:
    """The real path of a program given as a path or a name on the PATH (None if there is no such program)."""
    exe = str(program or "").strip()
    if not exe:
        return None
    p = Path(exe).expanduser()
    found = str(p) if p.is_file() else shutil.which(exe)
    return os.path.realpath(found) if found else None


class ProgramPolicy:
    """The programs allowed to run on this computer, and how to ask about the others.

    allowed: the allowed programs (real paths); None reads them from ``path`` (default :func:`allowlist_path`).
    confirm(path, arguments, context) -> bool: asked about a program that is not allowed (None: refuse); True adds
    it to the list (saved when ``persist``)."""

    def __init__(self, allowed=None, confirm: Callable | None = None, path: Path | str | None = None,
                 persist: bool = True):
        self.confirm = confirm
        self.persist = persist
        self._path = Path(path) if path is not None else None
        self._allowed: set[str] | None = {os.path.realpath(str(a)) for a in allowed} if allowed is not None else None
        self._refused: set[str] = set()
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else allowlist_path()

    def allowed(self) -> set[str]:
        """The allowed programs (real paths)."""
        with self._lock:
            return set(self._load())

    def _load(self) -> set[str]:
        if self._allowed is None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._allowed = {str(x) for x in data.get("programs", [])} if isinstance(data, dict) else set()
            except FileNotFoundError:
                self._allowed = set()
            except (OSError, ValueError) as e:
                log.warning("could not read the allowed programs (%s): %s", self.path, e)
                self._allowed = set()
        return self._allowed

    def _save(self):
        if not self.persist:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"programs": sorted(self._allowed or ())}, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:  # pragma: no cover - file system dependent
            log.warning("could not save the allowed programs (%s): %s", self.path, e)

    def is_allowed(self, program: str) -> bool:
        real = resolve(program) or os.path.realpath(str(program))
        with self._lock:
            return real in self._load()

    def allow(self, program: str):
        """Allow a program on this computer (saved in the user's settings)."""
        real = resolve(program) or os.path.realpath(str(program))
        with self._lock:
            self._load().add(real)
            self._refused.discard(real)
            self._save()

    def disallow(self, program: str):
        real = resolve(program) or os.path.realpath(str(program))
        with self._lock:
            self._load().discard(real)
            self._save()

    def reload(self):
        """Read the list again (e.g. after another window changed it)."""
        with self._lock:
            if self._path is not None or self.persist:
                self._allowed = None

    def authorise(self, program: str, arguments: str = "", context=None) -> bool:
        """Whether the program may run now: allowed on this computer, or allowed by the user when asked."""
        real = resolve(program) or os.path.realpath(str(program))
        with self._lock:
            if real in self._load():
                return True
            if self.confirm is None or real in self._refused:
                return False
        try:
            ok = bool(self.confirm(real, arguments, context))
        except Exception as e:  # a failing dialog refuses
            log.warning("program confirmation failed: %s", e)
            ok = False
        if ok:
            self.allow(real)
        else:
            with self._lock:
                self._refused.add(real)
        return ok


policy = ProgramPolicy()  # the default for every procedure engine (ProcedureEngine.program_policy overrides it)


def unauthorised_programs(procedures, policy_: ProgramPolicy | None = None) -> list[str]:
    """The programs the procedures' "Run a program" actions would start that are not allowed on this computer (the
    program as written, for a confirmation before the test starts). A program given by an expression or not found
    is listed as written."""
    from .model import iter_statements, normalize_procedures

    pol = policy_ or policy
    out: list[str] = []
    for proc in normalize_procedures(procedures):
        if proc.get("enabled", True) is False:
            continue
        for _path, st in iter_statements(proc.get("statements")):
            if st.get("type") == "do" and st.get("action") == "run_program" and st.get("enabled", True) is not False:
                prog = str(st.get("program") or "").strip()
                if prog and not pol.is_allowed(prog) and prog not in out:
                    out.append(prog)
    return out
