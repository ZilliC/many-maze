"""Schedules of reinforcement for operant procedures (FR, VR, FI, VI, PR, FT, VT, CRF, EXT).

A schedule is written as a short spec string::

    "CRF"      continuous reinforcement (= FR 1)
    "FR 5"     fixed ratio: every 5th response is reinforced
    "VR 5"     variable ratio: the requirement is drawn uniformly from 1..9 (mean 5)
    "FI 30"    fixed interval: the first response at least 30 s after the last reinforcer
    "VI 30"    variable interval: like FI with Fleshler & Hoffman (1962) intervals of mean 30 s
    "PR"       progressive ratio, Richardson & Roberts (1996): 1, 2, 4, 6, 9, 12, 15, 20, 25, 32, ...
    "PR 3"     linear progressive ratio: 3, 6, 9, ... (the step is the number)
    "FT 60"    fixed time: a reinforcer every 60 s regardless of responses
    "VT 60"    variable time (Fleshler & Hoffman intervals of mean 60 s)
    "EXT"      extinction: never reinforced

``Schedule.response(t)`` registers a response and returns True when it earns a reinforcer;
``Schedule.tick(t)`` returns True when a time-based schedule (FT/VT) delivers one.
"""

from __future__ import annotations

import math
import random
import re

KINDS = ("CRF", "FR", "VR", "FI", "VI", "PR", "FT", "VT", "EXT")


def parse_spec(spec: str) -> tuple[str, float]:
    """'VR 5' -> ('VR', 5.0). Raises ValueError with a readable message."""
    s = str(spec or "").strip().upper().replace("-", " ")
    m = re.fullmatch(r"([A-Z]+)\s*([0-9]*\.?[0-9]*)", s)
    if not m or m.group(1) not in KINDS:
        raise ValueError(f"unknown schedule '{spec}' (use CRF, FR n, VR n, FI s, VI s, PR [step], FT s, VT s or EXT)")
    kind, num = m.group(1), m.group(2)
    if kind in ("CRF", "EXT"):
        return kind, 1.0
    if kind == "PR" and not num:
        return kind, 0.0
    if not num:
        raise ValueError(f"schedule '{spec}' needs a value, e.g. {kind} 5")
    v = float(num)
    if v <= 0:
        raise ValueError(f"schedule '{spec}': the value must be positive")
    return kind, v


def fleshler_hoffman(mean: float, n: int = 10) -> list[float]:
    """The n intervals of a Fleshler & Hoffman (1962) constant-probability progression with the given mean."""
    out = []
    for k in range(1, n + 1):
        a = 1 + math.log(n)
        if k < n:
            a += (n - k) * math.log(n - k) - (n - k + 1) * math.log(n - k + 1)
        out.append(mean * a)
    return out


def progressive_ratio(k: int, step: float = 0.0) -> int:
    """Requirement of the k-th ratio (k = 0, 1, ...). step 0 = Richardson & Roberts exponential series."""
    if step > 0:
        return max(1, int(round(step * (k + 1))))
    return max(1, int(round(5 * math.exp(0.2 * (k + 1)) - 5)))


class Schedule:
    def __init__(self, spec: str = "CRF", rng: random.Random | None = None, t0: float = 0.0):
        self.spec = spec
        self.kind, self.value = parse_spec(spec)
        self.rng = rng or random.Random()
        self.reset(t0)

    def reset(self, t: float = 0.0):
        self.responses = 0  # total responses
        self.reinforcers = 0
        self.count = 0  # responses since the last reinforcer
        self.last_t = t  # time of the last reinforcer (or start)
        self.breakpoint = 0  # last completed ratio (progressive ratio)
        self._bag: list[float] = []
        self.requirement = self._next_requirement()

    def _draw_interval(self) -> float:
        if not self._bag:
            self._bag = fleshler_hoffman(self.value)
            self.rng.shuffle(self._bag)
        return self._bag.pop()

    def _next_requirement(self) -> float:
        k = self.kind
        if k == "CRF":
            return 1
        if k == "FR":
            return max(1, int(round(self.value)))
        if k == "VR":
            n = max(1, int(round(self.value)))
            return self.rng.randint(1, 2 * n - 1)
        if k == "PR":
            return progressive_ratio(self.reinforcers, self.value)
        if k in ("FI", "FT"):
            return self.value
        if k in ("VI", "VT"):
            return self._draw_interval()
        return math.inf  # EXT

    def _reinforce(self, t: float) -> bool:
        if self.kind == "PR":
            self.breakpoint = int(self.requirement)
        self.reinforcers += 1
        self.count = 0
        self.last_t = t
        self.requirement = self._next_requirement()
        return True

    def response(self, t: float) -> bool:
        """Register a response at time t; True if it earns a reinforcer."""
        self.responses += 1
        self.count += 1
        k = self.kind
        if k in ("CRF", "FR", "VR", "PR"):
            if self.count >= self.requirement:
                return self._reinforce(t)
        elif k in ("FI", "VI"):
            if t - self.last_t >= self.requirement - 1e-9:
                return self._reinforce(t)
        return False

    def tick(self, t: float) -> bool:
        """Time-based schedules (FT, VT): True when a response-independent reinforcer is due."""
        if self.kind in ("FT", "VT") and t - self.last_t >= self.requirement - 1e-9:
            due = self.last_t + self.requirement
            return self._reinforce(due)
        return False

    def to_dict(self) -> dict:
        return {"spec": self.spec, "responses": self.responses, "reinforcers": self.reinforcers,
                "requirement": self.requirement, "breakpoint": self.breakpoint}
