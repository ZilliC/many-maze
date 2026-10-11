import os
import sys
import tempfile

# keep the GUI tests away from the user's recent-experiments list and window preferences
os.environ.setdefault("MANYMAZE_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="manymaze-settings-"), "settings.ini"))

# On Windows the offscreen platform (CI) reads fonts only from QT_QPA_FONTDIR, by default Qt's own lib/fonts, which
# PySide6 does not ship: without this every text is drawn in a placeholder font whose glyphs are as wide as they are
# tall, about twice the width of a real font, and layouts sized by their text (the ribbon) come out twice as wide.
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
