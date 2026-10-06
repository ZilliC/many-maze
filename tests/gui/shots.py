"""Where GUI tests save screenshots for manual review (MANYMAZE_SHOT_DIR, default: the temp directory)."""

import os
import tempfile


def shot_path(name: str) -> str:
    d = os.environ.get("MANYMAZE_SHOT_DIR") or tempfile.gettempdir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)
