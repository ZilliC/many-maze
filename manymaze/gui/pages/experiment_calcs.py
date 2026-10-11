"""The Protocol page's Calculations element and its analysis plug-ins (mixins of experiment.ExperimentPage)."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QDialog, QListWidgetItem, QMessageBox

from ...core import plugins
from ...core.apparatus import unique_name
from ...core.calculations import Calculation, check_calculation, evaluate_calc, parse
from ...core.export import display_text
from ...core.project import ERROR_COLUMN, result_columns
from ..icons import icon
from ..widgets import run_with_progress
from ._results_cache import cached_rows, info_columns


class CalculationsMixin:
    """Protocol ▸ Calculations: results computed from other results with a formula (core.calculations)."""

    def _fill_calculations(self):
        p = self.project
        cur = self.calc_list.currentRow()
        self.calc_list.blockSignals(True)
        self.calc_list.clear()
        for c in p.calculations if p is not None else []:
            self.calc_list.addItem(QListWidgetItem(icon("calculator"), c.column or "(no name)"))
        n = self.calc_list.count()
        if n:
            self.calc_list.setCurrentRow(min(max(cur, 0), n - 1))
        self.calc_list.blockSignals(False)
        self._calc_rows = self._calc_measures = None
        self._show_calculation()

    def current_calculation(self) -> Calculation | None:
        r = self.calc_list.currentRow()
        p = self.project
        return p.calculations[r] if p is not None and 0 <= r < len(p.calculations) else None

    def _show_calculation(self):
        self.calc_editor.load(self.current_calculation())
        self._check_calculation()

    def _calculation_rows(self) -> list[dict] | None:
        """Results rows to check the formulas against (the cached results, if any: computing them is slow)."""
        if self._calc_rows is None and self.project is not None:
            self._set_calculation_rows(cached_rows(self.project, False))
        return self._calc_rows

    def _set_calculation_rows(self, rows):
        """Keep results rows (made with the current calculations) and their measure columns."""
        self._calc_rows = rows
        calcs = {c.column for c in self.project.calculations} if self.project is not None else set()
        self._calc_measures = [c for c in result_columns(rows) if c not in calcs] if rows is not None else None

    def _check_calculation(self):
        """Show the problems of the selected calculation, or its result for the first test."""
        c, p = self.current_calculation(), self.project
        if c is None:
            self.calc_editor.set_status([])
            return
        rows = self._calculation_rows()
        reserved = info_columns(p) + [ERROR_COLUMN, "Warnings"]
        measures = [m for m in self._calc_measures or [] if m not in reserved] if rows else None
        errs = check_calculation(c, measures, p.calculations, reserved, p.analysis.event_periods)
        text = "The results are worked out when they are shown on the Data page."
        row = next((r for r in rows or [] if ERROR_COLUMN not in r), None)
        if not errs and row is not None:
            if parse(c.formula).functions:
                text = "Worked out for every test when the results are calculated (it uses other trials or periods)."
            else:
                v = evaluate_calc(c, row)
                v = f"{v:.{c.decimals}f}" if isinstance(v, float) and v == v else display_text(v) or "undefined"
                text = f"Result for test {row.get('Test')} (animal {row.get('Animal')}): {v}"
        self.calc_editor.set_status(errs, text)

    def _calculation_edited(self, new: Calculation):
        r = self.calc_list.currentRow()
        p = self.project
        if p is None or not 0 <= r < len(p.calculations) or p.calculations[r] == new:
            return
        old = p.calculations[r].column
        p.calculations[r] = new
        # renamed: the other formulas (unless the old name is a measure's: theirs may mean the measure), the time
        # periods it defines and the training criteria on it follow
        if new.column and old and new.column != old:
            p.rename_calculation(old, new.column, formulas=old not in (self._calc_measures or ()), skip=new)
        self.calc_list.item(r).setText(new.column or "(no name)")
        self.main.mark_dirty()
        self._check_calculation()

    def new_calculation(self):
        p = self.project
        if p is None:
            return
        self._goto_element("calculations")
        name = unique_name("Calculation 1" if not p.calculations else f"Calculation {len(p.calculations) + 1}",
                           [c.name for c in p.calculations])
        p.calculations.append(Calculation(name, ""))
        self.main.mark_dirty()
        self._fill_calculations()
        self.calc_list.setCurrentRow(len(p.calculations) - 1)
        self.calc_editor.name.setFocus()
        self.calc_editor.name.selectAll()

    def duplicate_calculation(self):
        c, p = self.current_calculation(), self.project
        if c is None:
            return
        d = Calculation.from_dict(c.to_dict())
        d.name = unique_name(f"{c.name} copy", [x.name for x in p.calculations])
        p.calculations.insert(self.calc_list.currentRow() + 1, d)
        self.main.mark_dirty()
        row = self.calc_list.currentRow() + 1
        self._fill_calculations()
        self.calc_list.setCurrentRow(row)

    def delete_calculation(self, confirm: bool = True):
        c, p = self.current_calculation(), self.project
        if c is None:
            return
        users = p.calculation_users(c.column) if c.column else []
        msg = f"Delete the calculation “{c.column or c.name}”?"
        if users:
            msg += ("\n\nIts result is used by: " + ", ".join(users) + " (their results will be blank, the time "
                    "periods it defines left out).")
        if confirm and QMessageBox.question(self, "Delete calculation", msg) != QMessageBox.Yes:
            return
        p.calculations.remove(c)
        self.main.mark_dirty()
        self._fill_calculations()


class PluginsMixin:
    """Protocol ▸ Analysis ▸ analysis plug-ins (core.plugins)."""

    def _fill_plugins(self):
        p = self.project
        row = self.plugin_list.currentRow()
        self.plugin_list.clear()
        for c in (p.analysis_plugins if p is not None else []):
            pl = plugins.analysis_plugin(c.get("plugin", ""))
            kind = pl.title if pl is not None else f"{c.get('plugin')} (not installed)"
            off = "" if c.get("enabled", True) else " — not run"
            self.plugin_list.addItem(QListWidgetItem(icon("chart"), f"{c.get('name') or kind}  ·  {kind}{off}"))
        if self.plugin_list.count():
            self.plugin_list.setCurrentRow(min(max(row, 0), self.plugin_list.count() - 1))

    def _fill_plugin_menu(self):
        self.plugin_menu.clear()
        for name in plugins.analysis_names():
            pl = plugins.analysis_plugin(name)
            a = self.plugin_menu.addAction(pl.title)
            a.setToolTip(pl.description)
            a.triggered.connect(lambda _=False, n=name: self.add_plugin(n))

    def add_plugin(self, name: str, dlg=None) -> dict | None:
        """Add a configured analysis plug-in to the protocol (its settings are asked first)."""
        p = self.project
        if p is None:
            return None
        cfg = plugins.new_config(name, [c.get("name") for c in p.analysis_plugins])
        cfg = self._plugin_dialog(cfg, dlg)
        if cfg is None:
            return None
        p.analysis_plugins.append(cfg)
        self.main.mark_dirty()
        self._fill_plugins()
        self.plugin_list.setCurrentRow(self.plugin_list.count() - 1)
        return cfg

    def _plugin_dialog(self, cfg: dict, dlg=None) -> dict | None:
        from ..plugin_dialog import PluginOptionsDialog

        given = dlg is not None
        dlg = dlg or PluginOptionsDialog(self.project, cfg, self)
        if not given and dlg.exec() != QDialog.Accepted:
            return None
        return dlg.values()

    def edit_plugin(self, dlg=None) -> dict | None:
        p = self.project
        i = self.plugin_list.currentRow()
        if p is None or not 0 <= i < len(p.analysis_plugins):
            return None
        cfg = self._plugin_dialog(p.analysis_plugins[i], dlg)
        if cfg is None:
            return None
        p.analysis_plugins[i] = cfg
        self.main.mark_dirty()
        self._fill_plugins()
        return cfg

    def remove_plugin(self):
        p = self.project
        i = self.plugin_list.currentRow()
        if p is None or not 0 <= i < len(p.analysis_plugins):
            return
        del p.analysis_plugins[i]
        self.main.mark_dirty()
        self._fill_plugins()

    def run_plugins(self, wait: bool = False):
        """Run the analysis plug-ins on every test performed (in the background), then save the experiment."""
        p = self.project
        if p is None or not p.analysis_plugins:
            return None
        if p.path is None and not self.main.save():
            return None

        def done(res):
            self.main.mark_dirty()
            self.main.save()
            msg = f"Ran the analysis plug-ins on {len(res['done'])} test(s)."
            self.main.status(msg)
            if res["errors"]:
                lines = [f"Test {tid}: {m}" for tid, m in res["errors"][:20]]
                QMessageBox.warning(self, "Analysis plug-ins", msg + "\n\n" + "\n".join(lines))
            self.last_plugin_run = res

        w = run_with_progress(self, "Running the analysis plug-ins",
                              lambda progress, stop: plugins.run_analysis_plugins(p, progress=progress),
                              on_done=done, on_fail=lambda m: QMessageBox.warning(self, "Analysis plug-ins", m),
                              cancellable=False)
        if wait:
            w.wait()
            QApplication.processEvents()
        return w
