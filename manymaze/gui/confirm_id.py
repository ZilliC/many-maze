"""Animal checks before a test: identification (scan a barcode / microchip or type the animal ID) and weighing (the
test waits until the animal is weighed on the balance)."""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from ..core import scales
from ..core.workflow import confirm_id_enabled, id_matches


def exec_dialog(dlg) -> int:
    """Show a dialog modally (replaced in tests)."""
    return dlg.exec()


def weigh_before_test(parent, test, project=None, reader=None) -> bool:
    """When the protocol asks for it (Project.require_weight_before_test) and a balance is connected, a live test
    starts only once its animal has been weighed today: the Weigh dialog opens for it. Returns False when it was not
    weighed (the test is not armed)."""
    project = project if project is not None else _project_of(parent)
    if project is None or test is None or not scales.weight_needed(project, test.animal_id):
        return True
    from .pages.animal_dialogs import WeighDialog

    animal = project.get_animal(test.animal_id)
    main = getattr(parent, "main", None)

    def record(a, grams):
        scales.record_weight(project, a, grams)
        if main is not None:
            main.mark_dirty()

    dlg = WeighDialog(project, [animal], 0, reader or scales.read_weight, record, parent)
    dlg.setWindowTitle(f"Weigh animal {animal.id} before test {test.id}")
    dlg.state.setText("The protocol asks for the animal to be weighed before each test.")
    exec_dialog(dlg)
    if scales.weighed_today(animal):
        return True
    QMessageBox.information(parent, "Weigh the animal", f"Test {test.id} was not armed: animal {animal.id} must be "
                            "weighed first (the protocol asks for a weight before each test).")
    return False


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
        text, ok = QInputDialog.getText(parent, "Animal ID check",
                                        f"Test {test.id}: scan the barcode or microchip, or type the ID of the animal "
                                        "about to be tested.", QLineEdit.Normal, "")
        if not ok:
            return False
        if id_matches(project, test, text):
            return True
        r = QMessageBox.warning(parent, "Wrong animal",
                                f"“{text.strip()}” does not match animal {test.animal_id} of test {test.id}. Check "
                                "the animal and scan again.",
                                QMessageBox.Retry | QMessageBox.Cancel)
        if r != QMessageBox.Retry:
            return False
