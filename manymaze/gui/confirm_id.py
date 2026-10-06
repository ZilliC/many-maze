"""Animal identification check before a test: scan a barcode / microchip or type the animal ID."""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from ..core.workflow import confirm_id_enabled, id_matches


def _project_of(parent):
    for obj in (parent, getattr(parent, "main", None)):
        p = getattr(obj, "project", None)
        if p is not None:
            return p
    return None


def confirm_animal_id(parent, test, project=None, force: bool = False) -> bool:
    """Ask for the animal's ID (scanner input works as typing) and check it against the test's animal.

    Returns True when confirmation is off for the experiment (unless `force`), the test has no animal, or the
    scanned ID / barcode / microchip matches. A mismatch shows a warning and returns False.
    """
    project = project if project is not None else _project_of(parent)
    if project is None or test is None or not test.animal_id:
        return True
    if not force and not confirm_id_enabled(project):
        return True
    while True:
        text, ok = QInputDialog.getText(parent, "Confirm animal",
                                        f"Test {test.id}: scan the barcode / microchip or type the ID of the animal "
                                        "about to be tested.", QLineEdit.Normal, "")
        if not ok:
            return False
        if id_matches(project, test, text):
            return True
        r = QMessageBox.warning(parent, "Wrong animal",
                                f"“{text.strip()}” does not match animal {test.animal_id} of test {test.id}.",
                                QMessageBox.Retry | QMessageBox.Cancel)
        if r != QMessageBox.Retry:
            return False
