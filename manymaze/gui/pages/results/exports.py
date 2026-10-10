"""Spreadsheet output of the Data page: files, clipboard, printing, XML / raw data exports and the HTML report."""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QDialog, QFileDialog

from ....core.export import (display_text, event_log_rows, export_raw_data, export_xml, html_report, results_workbook,
                             TABLE_SUFFIXES, table_text, trial_means, wide_rows, write_table,
                             write_xlsx)
from ....core.project import result_columns
from ....core.stats import is_number, numeric_columns
from ...figures import TABLE_FILTER
from ...widgets import error_box, run_with_progress
from .._results_cache import info_columns
from .dialogs import ReportDialog
from .table import column_label, measure_category

# information columns describing the animal (not the test), kept in the one-row-per-animal export when shown
ANIMAL_INFO = ("Treatment code", "Animal notes")

TABLE_FILTERS = dict(zip((".csv", ".tsv", ".xlsx", ".slk", ".dbf"), TABLE_FILTER.split(";;")))


class ExportsMixin:
    """Saving, copying and printing the spreadsheet of ResultsPage, and its experiment-level exports."""

    def _default_path(self, suffix: str) -> str:
        p = self.project
        report = re.sub(r'[\\/:*?"<>|]+', "-", self.report_name or "").strip()  # the saved report shown, if any
        name = f"{p.name} results{f' - {report}' if report else ''}{' (time periods)' if self.segmented else ''}" \
               f"{suffix}"
        return str(p.exports_dir() / name) if p.path else name

    def save_table(self, path: str | None = None, selection: bool = False, suffix: str = ".csv"):
        """Save the shown spreadsheet, or the selected cells, as CSV, tab-separated text, Excel, SYLK or dBase (the
        format follows the extension; a whole-table Excel workbook also has the time periods, zone visits, animals,
        tests and settings)."""
        if not self.rows:
            return
        rng = self.selection_range(any_cells=True) if selection else None  # one cell / one row is a selection too
        if selection and rng is None:
            self.main.status("Select the cells to save first.")
            return
        rows, cols = rng or (self.shown_rows(), self.shown_columns())
        if path is None:
            default = self._default_path(suffix)
            path, _ = QFileDialog.getSaveFileName(self, "Save selected cells" if selection else "Save results",
                                                  default.replace(" results", " selection") if selection else default,
                                                  TABLE_FILTER, TABLE_FILTERS[suffix])
            if not path:
                return
        if Path(path).suffix.lower() not in TABLE_SUFFIXES:
            path += suffix
        try:
            if path.lower().endswith(".xlsx") and not selection:
                sheets, colmap = results_workbook(self.project, rows, cols, self.segmented)
                write_xlsx(sheets, path, colmap)
            else:
                write_table(rows, path, cols)
        except Exception as e:
            error_box(self, "Save", e)
            return
        self.main.status(f"Exported {len(rows)} rows × {len(cols)} columns to {path}")
        return path

    def _measure_columns(self) -> list[str]:
        """The shown columns that are measures (not animal / test information)."""
        info = set(info_columns(self.project)) | {"Period", "Test"}
        return [c for c in self.shown_columns() if c not in info]

    def wide_rows(self) -> tuple[list[dict], list[str]]:
        """The shown results with one row per animal and one column per measure × stage / trial (× period)."""
        shown = self.shown_columns()
        keep = ("Animal", "Group", "Sex") + tuple(c for c in ANIMAL_INFO if c in shown)
        rows = wide_rows(self.shown_rows(), self._measure_columns(), keep=keep)
        return rows, result_columns(rows)

    def export_wide(self, path: str | None = None):
        if not self.rows:
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export one row per animal", self._default_path(".xlsx").replace(" results", " by animal"),
                TABLE_FILTER)
            if not path:
                return None
        if Path(path).suffix.lower() not in TABLE_SUFFIXES:
            path += ".xlsx"
        rows, cols = self.wide_rows()
        try:
            write_table(rows, path, cols, sheet="By animal")
        except Exception as e:
            error_box(self, "Export one row per animal", e)
            return None
        self.main.status(f"Exported {len(rows)} animals × {len(cols)} columns to {path}")
        return path

    def export_trial_means(self, path: str | None = None):
        """One row per animal and stage with the mean of its trials (e.g. 4 water-maze trials a day)."""
        if not self.rows:
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export the mean of each animal's trials",
                self._default_path(".xlsx").replace(" results", " trial means"), TABLE_FILTER)
            if not path:
                return None
        if Path(path).suffix.lower() not in TABLE_SUFFIXES:
            path += ".xlsx"
        rows = trial_means(self.shown_rows(), self._measure_columns())
        try:
            write_table(rows, path, result_columns(rows), sheet="Trial means")
        except Exception as e:
            error_box(self, "Export trial means", e)
            return None
        self.main.status(f"Exported {len(rows)} animal × stage means to {path}")
        return path

    def export_event_log(self, path: str | None = None):
        """Chronological events (zone entries / exits, keys, I/O, pauses) of every shown test."""
        p = self.project
        if p is None or not self.rows:
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export event log", self._default_path(".csv").replace(" results", " event log"), TABLE_FILTER)
            if not path:
                return None
        if Path(path).suffix.lower() not in TABLE_SUFFIXES:
            path += ".csv"
        tests = self._shown_tests()

        def work(progress, stop):
            out = []
            for i, t in enumerate(tests):
                if stop():
                    return None
                out += [{"Test": t.id, "Stage": t.stage, "Trial": t.trial, **r} for r in event_log_rows(p, t)]
                progress((i + 1) / max(1, len(tests)))
            write_table(out, path, sheet="Event log")
            return out

        def done(out):
            if out is not None:
                self.main.status(f"Exported {len(out)} events of {len(tests)} tests to {path}")

        return self._run("Exporting the event log", work, on_done=done)

    def _shown_tests(self):
        p = self.project
        ids = []
        for r in self.shown_rows():
            if r.get("Test") not in ids:
                ids.append(r.get("Test"))
        return [t for t in (p.get_test(i) for i in ids) if t is not None]

    def export_xml(self, path: str | None = None, include_tracks: bool = True):
        """Whole experiment (settings, apparatus, animals, tests, results, raw tracks, events) as XML."""
        p = self.project
        if p is None:
            return
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export experiment as XML",
                                                  self._default_path(".xml").replace(" results", ""),
                                                  "XML file (*.xml)")
            if not path:
                return
        if not path.lower().endswith(".xml"):
            path += ".xml"
        rows = list(self.rows) if self.rows else None

        def work(progress, stop):
            return export_xml(p, path, rows=rows, segmented=self.segmented, progress=progress, should_stop=stop,
                              include_tracks=include_tracks)

        def done(out):
            if out:
                self.main.status(f"Exported {out}")

        return self._run("Exporting experiment (XML)", work, on_done=done)

    def export_raw(self, folder: str | None = None):
        """One CSV per shown test with the raw track and all per-frame parameters."""
        p = self.project
        if p is None:
            return
        if folder is None:
            base = str(p.exports_dir() / "raw data") if p.path else ""
            folder = QFileDialog.getExistingDirectory(self, "Folder for the per-test raw data", base)
            if not folder:
                return
        tests = self._shown_tests() if self.rows else None

        def work(progress, stop):
            return export_raw_data(p, folder, tests=tests, progress=progress, should_stop=stop)

        def done(paths):
            self.main.status(f"Exported {len(paths or [])} raw data files to {folder}")

        return self._run("Exporting raw data", work, on_done=done)

    def clipboard_text(self, selection: bool = False, header: bool = True) -> str:
        """The selected cell range (selection=True: also a single cell or row), else the selected rows (all rows if
        none), as tab-separated full-precision text."""
        rng = self.selection_range(any_cells=selection)
        rows, cols = rng if rng else (self.shown_rows(selected_only=True), self.shown_columns())
        return table_text(rows, cols, header=header)

    def copy_to_clipboard(self, selection: bool = False):
        if not self.rows or (selection and not self.table.selectionModel().hasSelection()):
            return
        text = self.clipboard_text(selection)
        QGuiApplication.clipboard().setText(text)
        n_cols = text.split("\n", 1)[0].count("\t") + 1
        self.main.status(f"Copied {text.count(chr(10)) - 1} rows × {n_cols} columns")

    def copy_selection(self):
        """Copy only the selected cells (with their column headings)."""
        self.copy_to_clipboard(selection=True)

    def table_html(self) -> str:
        """The spreadsheet as shown (column headings, filters, number format) as an HTML table."""
        cols = self.shown_columns()
        head = "".join(f"<th>{escape(column_label(c))}</th>" for c in cols)
        body = []
        for r in self.shown_rows():
            cells = []
            for c in cols:
                v = r.get(c)
                align = " align='right'" if is_number(v) else ""
                cells.append(f"<td{align}>{escape(display_text(v))}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        name = escape(self.project.name) if self.project is not None else ""
        return (f"<h3>{name} — results</h3><table border='1' cellspacing='0' cellpadding='3' "
                f"style='border-collapse:collapse;border-color:#cccccc;font-size:8pt'><tr>{head}</tr>"
                f"{''.join(body)}</table>")

    def print_table(self, printer=None):
        """Print the spreadsheet (landscape; a print dialog lets the user choose the printer)."""
        from PySide6.QtGui import QPageLayout, QTextDocument
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        if not self.rows:
            return
        if printer is None:
            printer = QPrinter(QPrinter.HighResolution)
            printer.setPageOrientation(QPageLayout.Landscape)
            dlg = QPrintDialog(printer, self)
            accepted = dlg.exec() == QDialog.Accepted
            dlg.deleteLater()  # (the printer it set up is ours)
            if not accepted:
                return
        doc = QTextDocument()
        doc.setHtml(self.table_html())
        doc.print_(printer)
        self.main.status("Spreadsheet sent to the printer")
        return printer

    def _report_preselect(self, measures: list[str]) -> list[str]:
        pre = [m for m in measures if measure_category(m, self._names)[0] == "Test-specific"]
        pre += [m for m in measures if m.startswith(("Total distance", "Centre: time (%)", "Center: time (%)",
                                                     "Freezing (%)"))]
        return pre[:8]

    def _run(self, title, work, **kw):
        w = run_with_progress(self, title, work, **kw)
        self._workers.append(w)
        w.finished.connect(lambda: QTimer.singleShot(0, lambda: w in self._workers and self._workers.remove(w)))
        return w

    def html_report(self, path: str | None = None, stats_measures: list[str] | None = None,
                    include_plots: bool | None = None, heatmap_norm: str = "auto",
                    chart_parameters: list[str] | None = None):
        if self.project is None or not self.rows:
            return
        measures = self.visible_measures()
        if stats_measures is None:
            numeric = numeric_columns(self.rows, measures)
            dlg = ReportDialog(numeric, self._report_preselect(numeric), self,
                               chart_params=self.charts.checked_params()[:4])
            accepted = dlg.exec() == QDialog.Accepted
            dlg.deleteLater()  # (when control returns to the event loop: its values are read below)
            if not accepted:
                return
            stats_measures = dlg.selected()
            include_plots = dlg.plots.isChecked()
            heatmap_norm = dlg.norm.currentData()
            chart_parameters = dlg.chart_params if dlg.charts.isChecked() else None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Save HTML report", self._default_path(".html")
                                                  .replace(" results", " report"), "HTML file (*.html)")
            if not path:
                return
        p = self.project
        tests = self._shown_tests()
        rows = self.shown_rows()
        if not (self.segmented and self.period_combo.currentData()):  # one period chosen, else the whole tests
            rows = [r for r in rows if r.get("Period", "Whole test") == "Whole test"]
        plots_on = True if include_plots is None else include_plots
        color_by = self.color_combo.currentData() or "time"
        report = self.report_name or ""

        def work(progress, stop):
            return html_report(p, path, tests=tests, include_plots=plots_on, measures=measures,
                               stats_measures=stats_measures, heatmap_norm=heatmap_norm,
                               chart_parameters=chart_parameters, color_by=color_by, rows=rows, report=report)

        def done(out):
            self.main.status(f"Report saved: {out}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))

        return self._run("Creating HTML report", work, on_done=done, cancellable=False)
