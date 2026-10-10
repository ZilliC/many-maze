"""Terminology: the words the program uses for animals, treatments, tests, stages, trials, apparatus and zones,
which a lab can change (ANY-maze's terminology options: *Subject* or *Fish* for *Animal*, *Condition* for
*Treatment*, *Session* for *Test* …).

``Project.terminology`` holds only the terms that were changed, as ``{term: {"singular": …, "plural": …}}``;
experiments without it (and every term not in it) use mANY-MAZE's words. :func:`term` gives the word to show.

What follows the terminology: the main labels of the window (explorer entries, page titles, the column headings of
the Animals and Treatments sheets and of the test schedule, the results filters, the apparatus property panel),
the names of the information columns of the results (``Animal``, ``Group`` → the treatment term, ``Test date``,
``Stage`` …) wherever they are shown or written (results table, HTML report, exported tables, clipboard) and the
field names offered for recorded video file names.

What does not: results rows keep their standard column names inside the program (:func:`column_label` renames them
on the way out), so calculations (``{Trial}``), training criteria, statistics factors, the command line and the
ANY-maze importers keep working whatever the terms; measure names (``Open arms: time (s)``, ``Visited zones``) are
never renamed, because calculations and criteria refer to them by name; the experiment XML export keeps its
element names (it is read back by programs); and file and folder names stay as they are.
"""

from __future__ import annotations

# term: (singular, plural) as mANY-MAZE says them
TERMS: dict[str, tuple[str, str]] = {
    "animal": ("Animal", "Animals"),
    "treatment": ("Treatment", "Treatments"),
    "test": ("Test", "Tests"),
    "stage": ("Stage", "Stages"),
    "trial": ("Trial", "Trials"),
    "apparatus": ("Apparatus", "Apparatus"),
    "zone": ("Zone", "Zones"),
}
MAX_LENGTH = 30  # a term is a word or two

# information columns of the results named after a term: column → (term, heading with {} for the term as it starts
# a heading, {l} for it in the middle of one). "Group" is the treatment: it keeps its name until the treatment term
# is changed
INFO_TERMS: dict[str, tuple[str, str]] = {
    "Test": ("test", "{}"),
    "Animal": ("animal", "{}"),
    "Group": ("treatment", "{}"),
    "Treatment code": ("treatment", "{} code"),
    "Animal notes": ("animal", "{} notes"),
    "Stage": ("stage", "{}"),
    "Trial": ("trial", "{}"),
    "Apparatus": ("apparatus", "{}"),
    "Test date": ("test", "{} date"),
    "Test time": ("test", "{} time"),
    "Test notes": ("test", "{} notes"),
    "Reason for test end": ("test", "Reason for {l} end"),
    "Animal lighter / darker": ("animal", "{} lighter / darker"),
    "Animal length": ("animal", "{} length"),
    "Video time at test start (s)": ("test", "Video time at {l} start (s)"),
}


def plural_of(word: str) -> str:
    """An English plural for a new term ("Subject" → "Subjects", "Box" → "Boxes", "Colony" → "Colonies"); the user
    can type another (fish, mice, apparatus)."""
    w = word.strip()
    if not w:
        return w
    low = w.lower()
    if low.endswith(("s", "x", "z", "ch", "sh")):
        return w + "es"
    if low.endswith("y") and len(low) > 1 and low[-2] not in "aeiou":
        return w[:-1] + "ies"
    return w + "s"


def terminology_from(d) -> dict:
    """The terminology of project.json: known terms with a non-blank singular (the plural defaults to
    :func:`plural_of`); anything else is ignored. A term set to its default is left out."""
    out = {}
    if not isinstance(d, dict):
        return out
    for key, v in d.items():
        if key not in TERMS:
            continue
        if isinstance(v, str):
            v = {"singular": v}
        if not isinstance(v, dict):
            continue
        one = str(v.get("singular", "") or "").strip()[:MAX_LENGTH]
        if not one:
            continue
        many = str(v.get("plural", "") or "").strip()[:MAX_LENGTH] or plural_of(one)
        if (one, many) != TERMS[key]:
            out[key] = {"singular": one, "plural": many}
    return out


def _terms_of(project) -> dict:
    if project is None:
        return {}
    if isinstance(project, dict):
        return project
    return getattr(project, "terminology", None) or {}


def is_custom(project, key: str) -> bool:
    """Whether the experiment changed this term."""
    return key in _terms_of(project)


def term(project, key: str, plural: bool = False, lower: bool = False) -> str:
    """The word for ``key`` ("animal", "treatment", "test", "stage", "trial", "apparatus", "zone") in this experiment
    (a Project, its terminology dict, or None for the defaults). lower: for the middle of a sentence (the first
    letter in lower case, unless the term starts with an abbreviation such as "PND")."""
    t = _terms_of(project).get(key)
    if isinstance(t, dict) and t.get("singular"):
        word = (t.get("plural") or plural_of(t["singular"])) if plural else t["singular"]
    else:
        word = TERMS[key][1 if plural else 0]
    if lower and not word[:2].isupper():
        word = word[:1].lower() + word[1:]
    return word


def column_label(project, column: str) -> str:
    """The heading of a results column in this experiment: an information column named after a changed term gets
    the new term ("Animal" → "Subject", "Group" → "Condition", "Test date" → "Session date"); every other column
    keeps its name."""
    spec = INFO_TERMS.get(column)
    if spec is None or not is_custom(project, spec[0]):
        return column
    word = term(project, spec[0])
    return spec[1].format(word, l=term(project, spec[0], lower=True))


def column_labels(project, columns) -> dict[str, str]:
    """{column: heading} for the columns (see :func:`column_label`). A heading that would be the name or heading
    of another column (e.g. the animal term set to "Group") is not used: that column keeps its standard name."""
    columns = list(columns)
    if not _terms_of(project):
        return {c: c for c in columns}
    out = {c: column_label(project, c) for c in columns}
    for c, lab in list(out.items()):
        if lab != c and (lab in out or list(out.values()).count(lab) > 1):
            out[c] = c
    return out


def relabel(project, rows: list[dict], columns: list[str]) -> tuple[list[dict], list[str]]:
    """Rows and columns with the experiment's headings, for files and the clipboard. Unchanged (the same lists)
    when no term was changed or no column is renamed."""
    labels = column_labels(project, columns)
    if all(k == v for k, v in labels.items()):
        return rows, columns
    return [{labels[c]: r[c] for c in columns if c in r} for r in rows], [labels[c] for c in columns]
