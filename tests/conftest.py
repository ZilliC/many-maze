import os
import tempfile

# keep the GUI tests away from the user's recent-experiments list and window preferences
os.environ.setdefault("MANYMAZE_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="manymaze-settings-"), "settings.ini"))
