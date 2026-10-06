"""Synthetic test videos of a "mouse" moving in an arena (for tests and the demo project)."""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np


def draw_mouse(img, x, y, angle_deg, length=36, width=18, color=40, tail=True):
    a = math.radians(angle_deg)
    ux, uy = math.cos(a), math.sin(a)
    # body
    cv2.ellipse(img, (int(round(x)), int(round(y))), (int(length / 2), int(width / 2)), angle_deg, 0, 360, color, -1,
                cv2.LINE_AA)
    # head: tapered smaller ellipse in front
    hx, hy = x + ux * length * 0.55, y + uy * length * 0.55
    cv2.ellipse(img, (int(round(hx)), int(round(hy))), (int(length * 0.22), int(width * 0.33)), angle_deg, 0, 360,
                color, -1, cv2.LINE_AA)
    if tail:
        tx0, ty0 = x - ux * length * 0.45, y - uy * length * 0.45
        tx1, ty1 = x - ux * length * 1.25, y - uy * length * 1.25
        cv2.line(img, (int(tx0), int(ty0)), (int(tx1), int(ty1)), color, 2, cv2.LINE_AA)
    nose = (x + ux * length * 0.77, y + uy * length * 0.77)
    return nose


def make_video(path: str | Path, positions: np.ndarray, size=(400, 400), fps=25.0, arena=None, noise=2.0,
               bg_level=200, seed=0, angles: np.ndarray | None = None, extra_animals: list[np.ndarray] | None = None,
               objects: list[tuple[int, int, int]] | None = None) -> np.ndarray:
    """Render a video of a dark mouse following `positions` (N, 2).

    Returns an (N, 2) array of true nose positions.
    arena: ("rect", x, y, w, h) or ("circle", cx, cy, r) drawn as a slightly darker outline.
    """
    rng = np.random.default_rng(seed)
    w, h = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    if not writer.isOpened():
        raise IOError("cannot open writer")
    base = np.full((h, w), bg_level, np.uint8)
    if arena is not None:
        if arena[0] == "rect":
            _, ax, ay, aw, ah = arena
            cv2.rectangle(base, (ax, ay), (ax + aw, ay + ah), bg_level - 60, 3)
        else:
            _, cx, cy, r = arena
            cv2.circle(base, (cx, cy), r, bg_level - 60, 3)
    for ox, oy, orad in objects or []:
        cv2.circle(base, (ox, oy), orad, bg_level - 90, -1)
    n = len(positions)
    if angles is None:
        d = np.diff(positions, axis=0, prepend=positions[:1] - [1, 0])
        angles = np.degrees(np.arctan2(d[:, 1], d[:, 0]))
        # hold angle when not moving
        still = np.hypot(d[:, 0], d[:, 1]) < 1e-6
        for i in range(1, n):
            if still[i]:
                angles[i] = angles[i - 1]
    noses = np.zeros((n, 2))
    for i in range(n):
        img = base.copy()
        noses[i] = draw_mouse(img, positions[i, 0], positions[i, 1], angles[i])
        for ex in extra_animals or []:
            j = min(i, len(ex) - 1)
            jd = ex[min(j + 1, len(ex) - 1)] - ex[max(j - 1, 0)]
            draw_mouse(img, ex[j, 0], ex[j, 1], math.degrees(math.atan2(jd[1], jd[0])) if np.any(jd) else 0)
        if noise:
            img = np.clip(img.astype(np.float32) + rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
        writer.write(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    writer.release()
    return noses


def circle_path(cx, cy, r, n, turns=1.0, start_deg=0.0) -> np.ndarray:
    a = np.radians(start_deg) + np.linspace(0, 2 * np.pi * turns, n)
    return np.column_stack([cx + r * np.cos(a), cy + r * np.sin(a)])


def line_path(p0, p1, n) -> np.ndarray:
    return np.column_stack([np.linspace(p0[0], p1[0], n), np.linspace(p0[1], p1[1], n)])


def waypoint_path(points, speed_px_per_frame=3.0, pauses: dict | None = None) -> np.ndarray:
    """Path visiting waypoints at constant speed; pauses: {waypoint_index: n_frames}."""
    out = [np.asarray(points[0], float)[None]]
    pauses = pauses or {}
    if 0 in pauses:
        out.append(np.repeat(np.asarray(points[0], float)[None], pauses[0], axis=0))
    for i in range(1, len(points)):
        a, b = np.asarray(points[i - 1], float), np.asarray(points[i], float)
        n = max(2, int(np.hypot(*(b - a)) / speed_px_per_frame))
        out.append(line_path(a, b, n)[1:])
        if i in pauses:
            out.append(np.repeat(b[None], pauses[i], axis=0))
    return np.vstack(out)


def random_walk(n, bounds, speed=3.0, seed=0, turn_sd=0.3, pause_prob=0.004, pause_len=(25, 75)) -> np.ndarray:
    """Correlated random walk inside a rectangle bounds=(x0, y0, x1, y1) with occasional pauses."""
    rng = np.random.default_rng(seed)
    x0, y0, x1, y1 = bounds
    p = np.array([(x0 + x1) / 2, (y0 + y1) / 2], float)
    a = rng.uniform(0, 2 * np.pi)
    out = []
    pause = 0
    while len(out) < n:
        if pause > 0:
            pause -= 1
            out.append(p.copy())
            continue
        if rng.random() < pause_prob:
            pause = int(rng.integers(*pause_len))
        a += rng.normal(0, turn_sd)
        step = np.array([math.cos(a), math.sin(a)]) * speed
        q = p + step
        if not (x0 + 20 < q[0] < x1 - 20 and y0 + 20 < q[1] < y1 - 20):
            a += math.pi * rng.uniform(0.6, 1.4)
            continue
        p = q
        out.append(p.copy())
    return np.array(out[:n])
