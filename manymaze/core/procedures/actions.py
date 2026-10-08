"""The actions of the procedure engine ("do" statements): outputs, pulse trains, shocks, audio, communication,
variables, timers, schedules, test control and the touch screen."""

from __future__ import annotations

from dataclasses import dataclass

from ..operant import Schedule
from .catalog import ACTION_SPECS, EPS, SHOCK_MAX_S, STALL_S
from .expr import MAX_SEQ, ExprError
from .model import _params_text


class _Stop(Exception):
    pass


class _Break(Exception):
    pass


@dataclass
class _Train:
    """A running pulse train (or single pulse): pulse k is on at t0 + k * period for `width` seconds."""

    t0: float
    period: float
    width: float
    n: int  # number of pulses (0 = until stopped)
    hw: bool  # timed by the device itself
    typ: str  # I/O log type
    k: int = 0
    on: bool = False
    first: bool = True
    delayed: float = 0.0  # total delay after stalled frames


@dataclass
class _Timer:
    acc: float = 0.0  # time accumulated before the current run
    start: float | None = None  # start of the current run (None: stopped)
    due: float | None = None  # when it elapses


# actions that are an output switched on / off under a name of its own
_ALIASES = {"door_open": "output_on", "door_close": "output_off", "lever_extend": "output_on",
            "lever_retract": "output_off"}


class Actions:
    """The ``do`` statements of :class:`ProcedureEngine` (a mixin: the engine provides the state and helpers)."""

    def _st_do(self, th, st, p):
        name = st.get("action")
        spec = ACTION_SPECS.get(name)
        if spec is None:
            self._error(th, p, f"unknown action '{name}'")
            return
        args = {}
        for prm in spec["params"]:
            v = st.get(prm["name"], prm["default"])
            typ = prm["type"]
            if typ in ("number", "int"):
                d = prm["default"] if isinstance(prm["default"], (int, float)) else 0
                v = self._num(th, v, p, prm["label"], default=d)
                if typ == "int":
                    v = int(round(v))
            elif typ == "expr":
                try:
                    v = self._eval(th, v)
                except ExprError as e:
                    self._error(th, p, f"{prm['label']}: {e}")
                    return
            elif typ == "text":
                v = self._text(th, v if v is not None else "", p)
            else:
                v = "" if v is None else str(v).strip()
            args[prm["name"]] = v
        self.fired.append((round(self.t, 3), th.label, name, _params_text(spec, st)))
        try:
            getattr(self, "_a_" + _ALIASES.get(name, name))(th, p, **args)
        except (_Stop, _Break):
            raise
        except Exception as e:  # never crash the test (ExprError: a bad parameter value)
            self._error(th, p, f"{spec['label']}: {e}")

    # -- outputs
    def _resolve(self, th, p, device, channel, kinds=("output", "pwm")):
        dev = self._devname(device) or self.devices.find_channel(channel, kinds) or ""
        if not dev:
            dev = "virtual"
        if self.devices.configured and not self.devices.has(dev):
            self._error(th, p, f"device '{dev}' is not configured — simulated")
        return dev, channel

    def _set_out(self, dev, ch, value, t, typ="digital", hw=False, max_s=None, **extra):
        key = (dev, ch)
        old = self.outputs_state.get(key, 0)
        if not hw and (value != old or key not in self.outputs_state):
            try:
                self.devices.set_output(dev, ch, value, max_s=max_s)
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(None, (), f"output {dev}/{ch}: {e}")
        if value != old or extra:
            self.outputs_state[key] = value
            self._log_io(t, dev, ch, "output", value, typ, **extra)
            if bool(value) != bool(old):
                self._emit("output_on" if value else "output_off", {"device": dev, "channel": ch, "value": value}, t)

    def _a_output_on(self, th, p, device, channel, typ="digital"):
        dev, ch = self._resolve(th, p, device, channel)
        self._stop_train((dev, ch), self.t)
        self._set_out(dev, ch, 1, self.t, typ)

    def _a_output_off(self, th, p, device, channel, typ="digital"):
        dev, ch = self._resolve(th, p, device, channel)
        self._stop_train((dev, ch), self.t)
        self._set_out(dev, ch, 0, self.t, typ)

    def _a_light_on(self, th, p, device, channel):
        """An output switched on, logged as a light (the "Light" measures of the I/O results)."""
        self._a_output_on(th, p, device, channel, "light")

    def _a_light_off(self, th, p, device, channel):
        self._a_output_off(th, p, device, channel, "light")

    def _a_output_toggle(self, th, p, device, channel):
        dev, ch = self._resolve(th, p, device, channel)
        self._set_out(dev, ch, 0 if self.outputs_state.get((dev, ch)) else 1, self.t)

    def _a_output_set(self, th, p, device, channel, value):
        dev, ch = self._resolve(th, p, device, channel)
        self._set_out(dev, ch, max(0.0, min(1.0, float(value))), self.t, "pwm")

    def _a_all_outputs_off(self, th, p, device):
        device = self._devname(device)
        for key in list(self._trains):
            if not device or key[0] == device:
                self._stop_train(key, self.t)
        for (dev, ch), v in list(self.outputs_state.items()):
            if v and (not device or dev == device):
                self._set_out(dev, ch, 0, self.t)

    def _start_train(self, th, p, device, channel, period, width, count, typ, t0=None):
        dev, ch = self._resolve(th, p, device, channel)
        key = (dev, ch)
        self._stop_train(key, self.t)
        width = max(0.0005, float(width))
        period = max(width, float(period))
        hw = False
        if getattr(self.devices.device(dev, create=True), "hardware_pulses", False):
            try:
                hw = bool(self.devices.pulse_train(dev, ch, period, width, count))
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(th, p, f"pulse train: {e}")
        t0 = self.t if t0 is None else t0
        self._trains[key] = _Train(t0, period, width, count, hw, typ)
        self._train_tick(key, self.t)

    def _train_tick(self, key, t):
        tr = self._trains.get(key)
        if tr is None:
            return
        dev, ch = key
        guard = 0
        while guard < 100000:
            guard += 1
            on_t = tr.t0 + tr.k * tr.period
            if not tr.on:
                if tr.n and tr.k >= tr.n:
                    del self._trains[key]
                    return
                if on_t > t + EPS:
                    return
                if not tr.hw and t - on_t > STALL_S:
                    # the frames stalled: deliver the remaining pulses from now instead of a burst of zero-length
                    # pulses that the device cannot follow (e.g. pellets counted but not dispensed)
                    lag = t - on_t
                    tr.t0 += lag
                    on_t = t
                    if not tr.delayed:
                        self._log_line(t, f"Output {dev}/{ch}: pulses delayed by {lag:.2f} s (the test stalled)")
                    tr.delayed += lag
                extra = {"train_start": True} if tr.first and tr.typ == "train" else {}
                tr.first = False
                self._set_out(dev, ch, 1, on_t, tr.typ, hw=tr.hw, **extra)
                tr.on = True
            else:
                off_t = on_t + tr.width
                if off_t > t + EPS:
                    return
                if not tr.hw and t - off_t > STALL_S:
                    off_t = t  # the output really stayed on until now
                    tr.t0 = t - tr.width - tr.k * tr.period
                self._set_out(dev, ch, 0, off_t, tr.typ, hw=tr.hw)
                tr.on = False
                tr.k += 1

    def _stop_train(self, key, t):
        tr = self._trains.pop(key, None)
        if tr is None:
            return
        if tr.hw:
            try:
                self.devices.stop_train(*key)
            except Exception:  # pragma: no cover
                pass
        if tr.on:
            self._set_out(key[0], key[1], 0, t, tr.typ, hw=tr.hw)
            if tr.hw:
                try:
                    self.devices.set_output(key[0], key[1], 0)
                except Exception:  # pragma: no cover
                    pass

    def _a_output_pulse(self, th, p, device, channel, duration, typ="pulse"):
        self._start_train(th, p, device, channel, duration, duration, 1, typ)

    def _a_pulse_train(self, th, p, device, channel, frequency, pulse_width, duration):
        if frequency <= 0:
            raise ExprError("the frequency must be positive")
        period = 1.0 / frequency
        count = int(round(duration * frequency)) if duration > 0 else 0
        if duration > 0 and count == 0:
            count = 1
        self._start_train(th, p, device, channel, period, pulse_width / 1000.0, count, "train")

    def _a_pulse_train_stop(self, th, p, device, channel):
        dev, ch = self._resolve(th, p, device, channel)
        self._stop_train((dev, ch), self.t)

    def _a_sync_pulse(self, th, p, device, channel, width):
        self._start_train(th, p, device, channel, width / 1000.0, width / 1000.0, 1, "sync")

    def _a_pellet(self, th, p, device, channel, count, pulse_width, gap):
        n = max(1, int(count))
        width = max(0.001, pulse_width / 1000.0)
        dev, ch = self._resolve(th, p, device, channel)
        self.pellet_counts[(dev, ch)] = self.pellet_counts.get((dev, ch), 0) + n
        self._start_train(th, p, dev, ch, max(width * 2, gap), width, n, "pellet")

    def _a_shock_on(self, th, p, device, channel, max_duration):
        dev, ch = self._resolve(th, p, device, channel)
        m = max_duration if 0 < max_duration <= SHOCK_MAX_S else (2.0 if max_duration <= 0 else SHOCK_MAX_S)
        if max_duration > SHOCK_MAX_S:
            self._error(th, p, f"Shock: safety cut-off limited to {SHOCK_MAX_S:g} s")
        self._stop_train((dev, ch), self.t)
        self._set_out(dev, ch, 1, self.t, "shock", max_s=m)
        self._shock_keys.add((dev, ch))
        due = self.t + m

        def cut(tt, dev=dev, ch=ch):
            if self.outputs_state.get((dev, ch)):
                self._set_out(dev, ch, 0, tt, "shock")
                self._log_line(tt, f"Shock {dev}/{ch} switched off by the safety cut-off")
        self._add_task(due, cut, ("shock", dev, ch))

    def _a_shock_off(self, th, p, device, channel):
        dev, ch = self._resolve(th, p, device, channel)
        self._cancel_task(("shock", dev, ch))
        self._set_out(dev, ch, 0, self.t, "shock")

    def _a_shock_pulse(self, th, p, device, channel, duration):
        d = min(max(0.0, duration), SHOCK_MAX_S)
        if duration > SHOCK_MAX_S:
            self._error(th, p, f"Shock: duration limited to {SHOCK_MAX_S:g} s")
        dev, ch = self._resolve(th, p, device, channel)
        self._shock_keys.add((dev, ch))
        self._start_train(th, p, dev, ch, d, d, 1, "shock")

    # -- audio
    def _audio_dev(self, device):
        return self._devname(device) or self.devices.find_type("audio") or "audio"

    def _audio(self, th, p, device, cmd, channel, value, duration, **kw):
        dev = self._audio_dev(device)
        if self.devices.has(dev):
            try:
                self.devices.audio(dev, cmd, duration=duration, **kw)
            except Exception as e:  # pragma: no cover
                self._error(th, p, f"audio: {e}")
            for err in self.devices.device(dev).errors:
                self._error(th, p, err)
        key = (dev, channel)
        self._audio_on[key] = value
        self._log_io(self.t, dev, channel, "output", value, "audio")
        if duration and duration > 0:
            self._add_task(self.t + duration, lambda tt, key=key: self._audio_off(key, tt), ("audio",) + key)

    def _audio_off(self, key, t):
        if key in self._audio_on:
            del self._audio_on[key]
            self._cancel_task(("audio",) + key)
            self._log_io(t, key[0], key[1], "output", 0, "audio")

    def _a_tone(self, th, p, device, frequency, duration, volume):
        self._audio(th, p, device, "tone", "tone", frequency, duration, frequency=frequency, volume=volume)

    def _a_white_noise(self, th, p, device, duration, volume):
        self._audio(th, p, device, "noise", "noise", 1, duration, volume=volume)

    def _a_play_sound(self, th, p, device, file, duration, volume):
        self._audio(th, p, device, "file", "sound", 1, duration, file=file, volume=volume)

    def _a_stop_sound(self, th, p, device):
        dev = self._audio_dev(device)
        if self.devices.has(dev):
            self.devices.audio(dev, "stop")
        for key in list(self._audio_on):
            if key[0] == dev:
                self._audio_off(key, self.t)

    def _a_beep(self, th, p):
        dev = self.devices.find_type("audio")
        if dev:
            self._a_tone(th, p, dev, 1000, 0.2, 0.5)
        else:
            self.outputs.beep()

    # -- communication
    def _a_serial_send(self, th, p, device, text):
        device = self._devname(device)
        dev = device or self.devices.find_type("serial") or self.devices.find_type("arduino")
        if dev and self.devices.has(dev):
            self.devices.send(dev, text)
            self.outputs.log.append(f"serial: {text}")
        elif device:
            self._error(th, p, f"Send serial command: device '{device}' is not configured")
            self.outputs.log.append(f"serial: {text}")
        else:
            self.outputs.write(text)

    def _a_signal(self, th, p, name):
        self._emit("signal", {"name": name}, self.t)

    def _set_switch(self, name, v, t):
        old = self.switches.get(name, 0)
        self.switches[name] = v
        if v != old:
            self._log_io(t, "virtual", name, "output", v, "switch")
            self._emit(f"virtual_switch_{'on' if v else 'off'}", {"switch": name}, t)

    def _a_virtual_switch_on(self, th, p, switch):
        self._set_switch(switch, 1, self.t)

    def _a_virtual_switch_off(self, th, p, switch):
        self._set_switch(switch, 0, self.t)

    def _a_virtual_switch_toggle(self, th, p, switch):
        self._set_switch(switch, 0 if self.switches.get(switch) else 1, self.t)

    def _a_simulate_input(self, th, p, device, channel, value):
        dev = self._devname(device) or self.devices.find_channel(channel, ("input", "analog", "encoder")) or "virtual"
        self.devices.set_input(dev, channel, value)
        self._poll_inputs(self.t)

    # -- variables, timers, schedules
    def _a_set_variable(self, th, p, var, value):
        self._setvar(th, var, value, p)

    def _a_increment(self, th, p, var, by):
        cur = self._lookup(th)(var) if var in self.vars or var in th.locals else 0
        if not isinstance(cur, (int, float)):
            raise ExprError(f"'{var}' is not a number")
        self._setvar(th, var, cur + by, p)

    def _a_decrement(self, th, p, var, by):
        self._a_increment(th, p, var, -by)

    def _a_array_append(self, th, p, var, value):
        cur = self.vars.get(var, [])
        if not isinstance(cur, list):
            raise ExprError(f"'{var}' is not an array")
        if len(cur) >= MAX_SEQ:
            raise ExprError("array too long")
        self._setvar(th, var, cur + [value], p)

    def _timer_value(self, name):
        tm = self.timers.get(name)
        if not tm:
            return 0.0
        return tm.acc + (self.t - tm.start if tm.start is not None else 0.0)

    def _a_start_timer(self, th, p, timer, seconds):
        tm = self.timers.setdefault(timer, _Timer())
        if tm.start is None:
            tm.start = th.clock if th.clock <= self.t else self.t
        tm.due = (tm.start - tm.acc + seconds) if seconds and seconds > 0 else None

    def _a_stop_timer(self, th, p, timer):
        tm = self.timers.get(timer)
        if tm and tm.start is not None:
            tm.acc += self.t - tm.start
            tm.start = tm.due = None

    def _a_reset_timer(self, th, p, timer):
        tm = self.timers.get(timer)
        if tm:
            tm.acc, tm.start, tm.due = 0.0, (self.t if tm.start is not None else None), None

    def _a_schedule_start(self, th, p, schedule, spec):
        self.schedules[schedule] = Schedule(spec, self.rng, t0=self.t)

    def _a_schedule_response(self, th, p, schedule, spec, var):
        s = self.schedules.get(schedule)
        if s is None:
            if not spec:
                raise ExprError(f"schedule '{schedule}' has not been started")
            s = self.schedules[schedule] = Schedule(spec, self.rng, t0=self.t)
        ok = s.response(self.t)
        if var:
            self._setvar(th, var, int(ok), p)
        if ok:
            self._emit("reinforcer_earned", {"schedule": schedule}, self.t)

    # -- test
    def _log_line(self, t, msg):
        self.log_lines.append((t, msg))
        self.outputs.log.append(f"{t:.2f}s {msg}")
        if self.on_log:
            try:
                self.on_log(msg, t)
            except Exception:  # pragma: no cover
                pass

    def _a_mark(self, th, p, name):
        name = name or "Mark"
        self.marks.append({"behaviour": name, "t": self.t, "t_end": None})
        if self.on_mark:
            self.on_mark(name, self.t)
        self._emit("event_marked", {"name": name}, self.t)

    def _a_mark_start(self, th, p, name):
        if any(m["behaviour"] == name and m["t_end"] is None for m in self.state_events):
            return
        m = {"behaviour": name, "t": self.t, "t_end": None}
        self.state_events.append(m)
        self.marks.append(m)
        self._emit("event_marked", {"name": name}, self.t)

    def _a_mark_end(self, th, p, name):
        for m in self.state_events:
            if m["behaviour"] == name and m["t_end"] is None:
                m["t_end"] = self.t

    def _a_log(self, th, p, text):
        self._log_line(self.t, text)

    def _end_test(self, th=None):
        if self.ended:
            return
        self.ended = True
        if self.on_end:
            self.on_end()

    def _a_end_test(self, th, p):
        self._end_test(th)
        raise _Stop()

    def _pause(self, t):
        if self.paused:
            return
        self.paused = True
        self._pause_t = t
        self.pauses.append([t, None])
        if self.started and not self.stopped:
            self._pause_safety(t)
        self._emit("test_paused", {}, t)
        if self.on_pause:
            self.on_pause(t)
        self._dispatch_now(t)  # "when test paused" runs now, not after the pause

    def _pause_safety(self, t):
        """Pulse trains stop and shocks go off (they cannot be timed while the test clock is stopped); with
        outputs_off_on_pause every output and sound goes off too."""
        for key in list(self._trains):
            self._stop_train(key, t)
        for key in list(self._shock_keys):
            self._cancel_task(("shock",) + key)
            if self.outputs_state.get(key):
                self._set_out(key[0], key[1], 0, t, "shock")
        if not self.outputs_off_on_pause:
            return
        n = 0
        for (dev, ch), v in list(self.outputs_state.items()):
            if v:
                self._set_out(dev, ch, 0, t, "pwm" if isinstance(v, float) and v not in (0.0, 1.0) else "digital")
                n += 1
        for dev in {k[0] for k in self._audio_on}:
            try:
                if self.devices.has(dev):
                    self.devices.audio(dev, "stop")
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(None, (), f"audio: {e}")
        for key in list(self._audio_on):
            self._audio_off(key, t)
            n += 1
        if n:
            self._log_line(t, "Test paused: outputs switched off")

    def _dispatch_now(self, t):
        if not self.started or self.stopped or self._busy:
            return  # inside a frame / handler: the queued event is dispatched by the running loop
        self._busy = True
        try:
            self.t = max(self.t, t)
            self._run(self.t)
        finally:
            self._busy = False
        self._after()

    def _resume_test(self, t):
        if not self.paused:
            return
        self.paused = False
        self._pause_t = None
        if self.pauses and self.pauses[-1][1] is None:
            self.pauses[-1][1] = t
        self._emit("test_resumed", {}, t)
        if self.on_resume:
            self.on_resume(t)
        self._dispatch_now(t)

    def _a_pause_test(self, th, p):
        self._pause(self.t)

    def _a_resume_test(self, th, p):
        self._resume_test(self.t)

    def _procs_named(self, name):
        idx = [i for i, q in enumerate(self.procedures) if q.get("name") == name]
        if not idx:
            raise ExprError(f"no procedure called '{name}'")
        return idx

    def _a_enable_procedure(self, th, p, procedure):
        idx = self._procs_named(procedure)
        for i in idx:
            self.proc_enabled[i] = True
        self._start_procs([i for i in idx if i not in self._started_procs], self.t, defer=True)

    def _a_disable_procedure(self, th, p, procedure):
        for i in self._procs_named(procedure):
            self._disable_proc(i, keep=th)
            if i == th.proc_i:
                raise _Stop()

    # -- touch screen
    def _stimulus(self, cmd, params):
        if self.on_stimulus:
            self.on_stimulus(cmd, params)

    def _a_show_stimulus(self, th, p, area, image, shape, color):
        self.stimuli[area] = {"image": image, "shape": shape, "color": color}
        self._log_io(self.t, "screen", area, "output", 1, "stimulus")
        self._stimulus("show", {"area": area, "image": image, "shape": shape, "color": color})

    def _a_hide_stimulus(self, th, p, area):
        if self.stimuli.pop(area, None) is not None:
            self._log_io(self.t, "screen", area, "output", 0, "stimulus")
        self._stimulus("hide", {"area": area})

    def _a_clear_screen(self, th, p):
        for area in list(self.stimuli):
            self._log_io(self.t, "screen", area, "output", 0, "stimulus")
        self.stimuli.clear()
        self._stimulus("clear", {})
