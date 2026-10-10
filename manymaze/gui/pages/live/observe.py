"""Observation only (TakeNote): no camera, a clock and the scoring keys."""

from __future__ import annotations

from ....core.live import ObservationSession
from ...confirm_id import confirm_animal_id, weigh_before_test
from ...widgets import fmt_time


class ObservationMixin:
    """Observation only (TakeNote): no camera, a clock and the scoring keys."""

    def obs_start(self) -> bool:
        if self.obs is not None and self.obs.state == "paused":
            self.obs.resume()
            return True
        if self.obs is not None and self.obs.state == "waiting":
            self.obs.start()
            return True
        if self.obs is not None and self.obs.state != "finished":
            return False
        test, new = self._prepare_test(need_apparatus=False)
        if test is None:
            return False
        if not confirm_animal_id(self, test) or not weigh_before_test(self, test, reader=self.scale_reader):
            if new:
                self._remove_test(test)
            return False
        self.obs_test, self._obs_new = test, new
        self.obs = ObservationSession(self.duration.value(), start_mode="manual",
                                      name=f"Test {test.id} · {test.animal_id} (observation)")
        if self.start_mode.currentData() == "scheduled":
            self._schedule = self._new_schedule()
            self._log(f"Observation of test {test.id} will start {self._schedule.describe()}.")
        else:
            self.obs.start()
            self._log(f"Observation of test {test.id} started — animal {test.animal_id}.")
        self._enable_shortcuts(True)
        self.obs_panel.show_session(self.obs, self.obs.duration_s)
        self._update_single_title()
        self._update_buttons()
        return True

    def obs_pause(self) -> bool:
        o = self.obs
        if o is None:
            return False
        ok = o.pause() if o.state == "running" else o.resume()
        self.obs_panel.show_session(o, o.duration_s)
        return ok

    def obs_stop(self, save: bool = True):
        o, test = self.obs, self.obs_test
        if o is None:
            return
        o.finish()
        self.obs, self.obs_test = None, None
        self._schedule = None
        if self._store(test, o, None, save, self._obs_new):
            self._log(f"Observation of test {test.id} saved: {len(o.events)} events in {fmt_time(o.elapsed)}.")
            self._show_results(test)
        else:
            self._log("Observation discarded.")
        if self.session is None and not any(e.state in ("waiting", "running", "paused")
                                             for e in self.group.entries):
            self._enable_shortcuts(False)
        self.obs_panel.show_session(None)
        self._update_buttons()
        self.on_show()
