"""In-app user guide (rendered from the bundled Markdown)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout

GUIDE = Path(__file__).resolve().parent.parent / "resources" / "USER_GUIDE.md"


def show_user_guide(parent=None):
    dlg = QDialog(parent)
    dlg.setWindowTitle("mANY-MAZE user guide")
    dlg.resize(820, 760)
    view = QTextBrowser()
    view.setOpenExternalLinks(True)
    view.setMarkdown(GUIDE.read_text(encoding="utf-8") if GUIDE.exists() else "User guide not found.")
    lay = QVBoxLayout(dlg)
    lay.addWidget(view)
    dlg.exec()
