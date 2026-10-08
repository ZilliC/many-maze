"""Experiment archives: the whole experiment — experiment file, tracks, recordings and the videos of every test,
wherever they are stored — in one zip file, to move it to another computer or keep it after publication."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Callable

from .explock import LOCK_FILE
from .project import BACKUP_DIR, PROJECT_FILE, SECRETS_FILE, Project, dumps_json
from .video import is_playlist, playlist_parts

ARCHIVE_SUFFIX = ".zip"
_STORED = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv", ".mpg", ".mpeg", ".webm", ".mts", ".png", ".jpg",
           ".xlsx", ".zip"}  # already compressed


def _safe_name(name: str) -> str:
    return "".join(c for c in name.strip() if c not in '/\\:*?"<>|') or "experiment"


def archive_project(project: Project, zip_path, include_videos: bool = True,
                    progress: Callable[[float], None] | None = None,
                    should_stop: Callable[[], bool] | None = None) -> Path | None:
    """Write the experiment to a zip file. Videos stored outside the experiment folder are copied into
    ``videos/external/`` and the tests point to the copies (the experiment itself is not changed). Automatic
    backups, the lock file and the I/O device passwords and tokens (io-secrets.json; project.json has none) are
    left out. Returns the zip path, or None if stopped."""
    if project.path is None:
        raise ValueError("Save the experiment first")
    root = Path(project.path).resolve()
    top = _safe_name(project.name) + ".mmaze"
    d = project.to_dict()
    files: list[tuple[Path, str]] = []  # (source file, name in the archive)
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root)
        if not f.is_file() or rel.parts[0] == BACKUP_DIR or f.suffix == ".tmp" or \
                (len(rel.parts) == 1 and rel.name in (PROJECT_FILE, SECRETS_FILE, LOCK_FILE)):
            continue
        files.append((f, rel.as_posix()))
    inside = {src.resolve() for src, _ in files}
    taken = {name for _, name in files}
    playlists: dict[str, str] = {}  # archive name → playlist text for external playlists
    missing = []
    if include_videos:
        for t, td in zip(project.tests, d["tests"]):
            if not t.video:
                continue
            src = Path(project.abs_path(t.video))
            if not src.exists():
                missing.append(t.video)
                continue
            if src.resolve() in inside and not is_playlist(src):
                continue
            if is_playlist(src) and src.resolve() in inside and all(
                    Path(x).resolve() in inside for x in playlist_parts(src)):
                continue
            parts = playlist_parts(src) if is_playlist(src) else [str(src)]
            names = []
            for part in parts:
                pp = Path(part).resolve()
                if pp in inside:
                    names.append(next(n for s_, n in files if s_.resolve() == pp))
                    continue
                name = f"videos/external/{pp.name}"
                k = 2
                while name in taken and not any(s_.resolve() == pp and n == name for s_, n in files):
                    name = f"videos/external/{pp.stem}_{k}{pp.suffix}"
                    k += 1
                if name not in taken:
                    files.append((pp, name))
                    taken.add(name)
                    inside.add(pp)
                names.append(name)
            if is_playlist(src):
                pl = f"videos/external/{src.stem}.m3u"
                k = 2
                while pl in taken:
                    pl = f"videos/external/{src.stem}_{k}.m3u"
                    k += 1
                taken.add(pl)
                base = Path(pl).parent
                playlists[pl] = "#EXTM3U\n" + "\n".join(os.path.relpath(n, base) for n in names) + "\n"
                td["video"] = pl
                td.pop("video_abs", None)
            else:
                td["video"] = names[0]
                td.pop("video_abs", None)
    if missing:
        d.setdefault("settings_extra", {})["archive_missing_videos"] = missing
    total = sum(src.stat().st_size for src, _ in files) or 1
    done = 0
    zip_path = Path(zip_path)
    tmp = zip_path.with_name(zip_path.name + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.writestr(f"{top}/{PROJECT_FILE}", dumps_json(d))
            for name, text in playlists.items():
                z.writestr(f"{top}/{name}", text)
            for src, name in files:
                if should_stop and should_stop():
                    raise InterruptedError
                comp = zipfile.ZIP_STORED if src.suffix.lower() in _STORED else zipfile.ZIP_DEFLATED
                z.write(src, f"{top}/{name}", compress_type=comp)
                done += src.stat().st_size
                if progress:
                    progress(min(1.0, done / total))
        os.replace(tmp, zip_path)
    except InterruptedError:
        tmp.unlink(missing_ok=True)
        return None
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return zip_path


def extract_archive(zip_path, dest_dir) -> Path:
    """Unpack an experiment archive into dest_dir and return the experiment folder (``Name.mmaze``)."""
    dest = Path(dest_dir).resolve()
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        tops = {n.split("/", 1)[0] for n in names if "/" in n}
        projects = [n for n in names if n.count("/") == 1 and n.endswith("/" + PROJECT_FILE)]
        if len(projects) != 1 or len(tops) != 1:
            raise ValueError("This is not an mANY-MAZE experiment archive")
        top = projects[0].split("/", 1)[0]
        target = dest / top
        if (target / PROJECT_FILE).exists():
            raise FileExistsError(f"{target} already exists")
        for n in names:
            out = (dest / n).resolve()
            if not str(out).startswith(str(dest) + os.sep):  # no paths outside the destination
                raise ValueError(f"Unsafe path in archive: {n}")
        z.extractall(dest)
    return target
