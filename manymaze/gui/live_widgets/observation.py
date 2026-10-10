"""The observation-only (TakeNote) panel: a clock, the scoring keys and the scored events."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QToolButton, QVBoxLayout, QWidget

from .. import theme
from ..icons import icon
from ..scoring_pad import ScoringPad
from ..widgets import fmt_time
from .panels import STATE_STYLE, ElidedLabel, panel_qss


class ObservationPanel(QFrame):
    """Live observation without a camera, like ANY-maze's TakeNote direct observation mode: a toolbar (start, pause,
    stop), the test title, a big test clock, the scoring keys as on-screen buttons and the list of scored events."""

    start_clicked = Signal()
    pause_clicked = Signal()
    stop_clicked = Signal()
    undo_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TestPanel")
        theme.style(self, panel_qss)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QWidget()
        head.setObjectName("PanelHead")
        head.setAttribute(Qt.WA_StyledBackground, True)
        hv = QVBoxLayout(head)
        hv.setContentsMargins(6, 3, 6, 4)
        hv.setSpacing(2)
        tb = QHBoxLayout()
        tb.setSpacing(2)
        self.start_btn = QToolButton()
        self.pause_btn = QToolButton()
        self.stop_btn = QToolButton()
        self.undo_btn = QToolButton()
        for b, name, text, sig in ((self.start_btn, "play", "Start observation", self.start_clicked),
                                   (self.pause_btn, "pause", "Pause", self.pause_clicked),
                                   (self.stop_btn, "stop", "Stop and save", self.stop_clicked),
                                   (self.undo_btn, "undo", "Undo", self.undo_clicked)):
            b.setObjectName("PanelTool")
            b.setIcon(icon(name))
            b.setIconSize(QSize(22, 22))
            b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(sig.emit)
            tb.addWidget(b)
        self.undo_btn.setToolTip("Undo the last scored event")
        self.undo_btn.setEnabled(False)
        tb.addStretch()
        self.state_pill = QLabel()
        tb.addWidget(self.state_pill)
        hv.addLayout(tb)
        trow = QHBoxLayout()
        self.title = ElidedLabel("Observation")
        self.title.setObjectName("PanelTitle")
        self.title.setProperty("large", True)
        src = ElidedLabel("TakeNote direct observation · no camera")
        src.setObjectName("PanelSource")
        trow.addWidget(self.title, 3)
        trow.addWidget(src, 2)
        hv.addLayout(trow)
        v.addWidget(head)

        body = QHBoxLayout()
        body.setContentsMargins(24, 14, 18, 14)
        body.setSpacing(24)
        left = QVBoxLayout()
        left.setSpacing(6)
        intro = QLabel("Score the animal's behaviour by direct observation with the keys or the buttons below. "
                       "The test clock runs from Start and stops while paused; the events are saved in the test "
                       "(testing status “scored”).")
        intro.setWordWrap(True)
        intro.setObjectName("Hint")
        left.addWidget(intro)
        left.addStretch(1)
        self.clock = QLabel("00:00.00")
        self.clock.setAlignment(Qt.AlignCenter)
        theme.style(self.clock, lambda: f"font-size:64px;font-weight:300;color:{theme.TEXT}")
        left.addWidget(self.clock)
        self.state = QLabel("Not started")
        self.state.setAlignment(Qt.AlignCenter)
        theme.style(self.state, lambda: f"font-size:15px;color:{theme.MUTED}")
        left.addWidget(self.state)
        left.addStretch(1)
        kt = QLabel("Keys")
        kt.setObjectName("SectionTitle")
        left.addWidget(kt)
        self.pad = ScoringPad()  # mouse / touch-screen scoring
        left.addWidget(self.pad)
        self.keys = QLabel()
        self.keys.setWordWrap(True)
        self.keys.setObjectName("Hint")
        left.addWidget(self.keys)
        body.addLayout(left, 3)
        right = QVBoxLayout()
        et = QLabel("Events")
        et.setObjectName("SectionTitle")
        right.addWidget(et)
        self.events = QListWidget()
        self.events.setStyleSheet("QListWidget::item{padding:3px 4px;}")
        right.addWidget(self.events, 1)
        body.addLayout(right, 2)
        v.addLayout(body, 1)
        self._pill = None
        self._sig = None
        self._set_pill("idle")

    def set_title(self, text: str):
        self.title.setText(text)

    def _set_pill(self, state):
        if state == self._pill:
            return
        self._pill = state
        text, col = STATE_STYLE.get(state, STATE_STYLE["idle"])
        if state == "idle":
            text = "Not started"
        self.state_pill.setText(text)
        self.state_pill.setStyleSheet(f"background:{col};color:white;border-radius:3px;padding:1px 8px;"
                                      "font-size:12px;font-weight:600")

    def show_session(self, session, duration_s: float = 0.0):
        if session is None:
            self.clock.setText("00:00.00")
            self.state.setText("Not started")
            self.start_btn.setText("Start observation")
            self.start_btn.setIcon(icon("play"))
            self.start_btn.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self._set_pill("idle")
            self.pad.set_active([])
            return
        el = session.elapsed
        self.clock.setText(fmt_time(el))
        st = session.state
        self._set_pill(st)
        rem = f" · {fmt_time(max(0.0, duration_s - el))} left" if duration_s else ""
        self.state.setText({"waiting": "Waiting for the start key", "running": "Running" + rem,
                            "paused": "Paused", "finished": "Finished"}.get(st, st))
        self.start_btn.setText("Resume" if st == "paused" else "Start observation")
        self.start_btn.setIcon(icon("resume" if st == "paused" else "play"))
        self.start_btn.setEnabled(st in ("waiting", "paused"))
        self.pause_btn.setEnabled(st == "running")
        self.stop_btn.setEnabled(st in ("running", "paused", "waiting"))
        self.pad.set_active(list(session.open_states))
        sig = (len(session.events), sum(1 for e in session.events if e.get("t_end") is not None))
        if sig != self._sig:
            self._sig = sig
            self.events.clear()
            for e in session.events:
                end = f" – {fmt_time(e['t_end'])}" if e.get("t_end") is not None else ""
                self.events.addItem(f"{fmt_time(e['t'])}{end}   {e['behaviour']}")
            self.events.scrollToBottom()
