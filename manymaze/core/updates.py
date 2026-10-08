"""Check for a newer release on GitHub (Help ▸ Check for updates, and optionally at startup).

The latest release of a repository (default ZilliC/many-maze, or $MANYMAZE_UPDATE_REPO) is read from the GitHub
releases API and its tag compared with the running version. Network problems never raise: the result says what
went wrong. Only the public API is contacted, without credentials or any data about the user.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .. import APP_NAME, __version__

DEFAULT_REPO = "ZilliC/many-maze"
API_URL = "https://api.github.com/repos/{repo}/releases/latest"
CHECK_INTERVAL_DAYS = 7  # automatic checks at startup: at most once a week


def update_repo() -> str:
    """owner/name of the repository whose releases are checked ($MANYMAZE_UPDATE_REPO overrides the default)."""
    repo = os.environ.get("MANYMAZE_UPDATE_REPO", "").strip().strip("/")
    return repo if re.fullmatch(r"[\w.-]+/[\w.-]+", repo) else DEFAULT_REPO


def parse_version(text: str) -> tuple:
    """Comparable key of a version / tag: 'v1.2.10' > '1.2.9'; pre-releases ('1.0.0rc1', '1.0.0-beta.2') sort
    before the release. Unparseable text gives ()."""
    m = re.match(r"^\s*[vV]?(\d+(?:\.\d+)*)(.*)$", str(text or ""))
    if not m:
        return ()
    nums = [int(x) for x in m.group(1).split(".")]
    while len(nums) > 1 and nums[-1] == 0:  # 1.0 == 1.0.0
        nums.pop()
    rest = m.group(2).strip().lstrip("-.+_").lower()
    if not rest or rest.startswith(("post", "final")):
        pre = (1,)
    else:
        n = re.search(r"\d+", rest)
        pre = (0, re.sub(r"[\d.]+", "", rest), int(n.group(0)) if n else 0)
    return (tuple(nums), pre)


def is_newer(latest: str, current: str = __version__) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a) and bool(b) and a > b


@dataclass
class UpdateInfo:
    """Result of a check: ok (the server answered), the latest version and its page, or an error message."""

    ok: bool
    current: str = __version__
    latest: str = ""
    url: str = ""
    name: str = ""
    notes: str = ""
    published: str = ""
    error: str = ""

    @property
    def newer(self) -> bool:
        return self.ok and is_newer(self.latest, self.current)

    def message(self) -> str:
        if not self.ok:
            return f"Could not check for updates: {self.error}"
        if self.newer:
            return f"{APP_NAME} {self.latest} is available (you have {self.current})."
        return f"{APP_NAME} {self.current} is up to date (latest release: {self.latest or 'none'})."


def check_for_updates(repo: str | None = None, current: str = __version__, timeout: float = 8.0,
                      opener=None) -> UpdateInfo:
    """Ask GitHub for the latest release of `repo`. opener(request, timeout) -> response (tests) defaults to
    urllib.request.urlopen. Never raises for network / server / format problems."""
    repo = repo or update_repo()
    req = urllib.request.Request(API_URL.format(repo=repo), headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"{APP_NAME}/{current}",
        "X-GitHub-Api-Version": "2022-11-28"})
    opener = opener or urllib.request.urlopen
    try:
        with opener(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = "no release has been published yet" if e.code == 404 else f"the server answered {e.code}"
        if e.code == 403:
            msg = "GitHub's rate limit was reached; try again later"
        return UpdateInfo(False, current, error=msg)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        return UpdateInfo(False, current, error=f"no connection ({reason})")
    except (ValueError, UnicodeDecodeError) as e:
        return UpdateInfo(False, current, error=f"unexpected answer ({e})")
    if not isinstance(data, dict) or not data.get("tag_name"):
        return UpdateInfo(False, current, error="unexpected answer (no release tag)")
    tag = str(data["tag_name"])
    return UpdateInfo(True, current, latest=tag.lstrip("vV"),
                      url=str(data.get("html_url") or f"https://github.com/{repo}/releases/latest"),
                      name=str(data.get("name") or tag), notes=str(data.get("body") or ""),
                      published=str(data.get("published_at") or ""))


def check_due(last_check: str | None, now: _dt.datetime | None = None, days: float = CHECK_INTERVAL_DAYS) -> bool:
    """Whether an automatic check is due, given the ISO time of the last one (None / invalid: due)."""
    now = now or _dt.datetime.now()
    try:
        last = _dt.datetime.fromisoformat(str(last_check))
    except (TypeError, ValueError):
        return True
    return now - last >= _dt.timedelta(days=days) or last > now
