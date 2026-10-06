"""Figures: track plots, occupancy heatmaps, time courses and group comparisons (matplotlib)."""

from __future__ import annotations

import io
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402
from scipy.ndimage import gaussian_filter  # noqa: E402

from .apparatus import Apparatus  # noqa: E402
from .stats import descriptive, stars  # noqa: E402
from .track import Track  # noqa: E402


def _draw_apparatus(ax, app: Apparatus, zones=True, labels=False):
    if app is None:
        return
    try:
        arena = app.arena_or_bounds()
        ax.add_patch(MplPolygon(arena.polygon(), closed=True, fill=False, ec="#334155", lw=1.5))
    except ValueError:
        pass
    if zones:
        for z in app.zones:
            ax.add_patch(MplPolygon(z.shape.polygon(), closed=True, fill=False, ec=z.color, lw=0.8, alpha=0.8))
            if labels:
                cx, cy = z.shape.centroid()
                ax.text(cx, cy, z.name, fontsize=6, ha="center", va="center", color=z.color)
    for p in app.points:
        ax.plot(p.x, p.y, "o", color=p.color, ms=4)
    for l in app.lines:
        ax.plot([l.x1, l.x2], [l.y1, l.y2], "-", color=l.color, lw=1)


def _limits(ax, app: Apparatus | None, track: Track | None, frame=None):
    if frame is not None:
        h, w = frame.shape[:2]
        ax.set_xlim(0, w)
        ax.set_ylim(h, 0)
        return
    try:
        x0, y0, x1, y1 = app.arena_or_bounds().bounds()
    except Exception:
        x0, y0 = np.nanmin(track.x), np.nanmin(track.y)
        x1, y1 = np.nanmax(track.x), np.nanmax(track.y)
    pad = 0.05 * max(x1 - x0, y1 - y0, 1)
    ax.set_xlim(x0 - pad, x1 + pad)
    ax.set_ylim(y1 + pad, y0 - pad)


def track_plot(track: Track, app: Apparatus | None = None, frame=None, title: str = "", color_by: str = "time",
               ax=None, show_head: bool = False, size=(4, 4)) -> Figure:
    fig = None
    if ax is None:
        fig = Figure(figsize=size, dpi=100)
        ax = fig.add_subplot(111)
    if frame is not None:
        img = frame[..., ::-1] if frame.ndim == 3 else frame
        ax.imshow(img, cmap="gray", alpha=0.6)
    _draw_apparatus(ax, app)
    x, y = track.x, track.y
    if color_by == "time" and len(x) > 1:
        from matplotlib.collections import LineCollection

        pts = np.column_stack([x, y]).reshape(-1, 1, 2)
        segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
        lc = LineCollection(segs, cmap="viridis", lw=0.9)
        lc.set_array(track.t[:-1])
        ax.add_collection(lc)
    else:
        ax.plot(x, y, "-", lw=0.8, color="#2563eb")
    ok = np.flatnonzero(np.isfinite(x))
    if len(ok):
        ax.plot(x[ok[0]], y[ok[0]], "o", color="#16a34a", ms=6, label="start")
        ax.plot(x[ok[-1]], y[ok[-1]], "s", color="#dc2626", ms=6, label="end")
    if show_head and track.has_head():
        ax.plot(track.hx, track.hy, ",", color="#f97316", alpha=0.5)
    _limits(ax, app, track, frame)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    if title:
        ax.set_title(title, fontsize=9)
    if fig is not None:
        fig.tight_layout()
    return fig


def occupancy(tracks: list[Track], app: Apparatus | None, bins: int = 60, sigma: float = 1.5, part="centre"):
    xs, ys, ws = [], [], []
    for tr in tracks:
        px, py = tr.bodypart(part)
        ok = np.isfinite(px) & np.isfinite(py)
        xs.append(px[ok])
        ys.append(py[ok])
        ws.append(tr.frame_durations()[ok])
    x = np.concatenate(xs) if xs else np.zeros(0)
    y = np.concatenate(ys) if ys else np.zeros(0)
    w = np.concatenate(ws) if ws else np.zeros(0)
    try:
        x0, y0, x1, y1 = app.arena_or_bounds().bounds()
    except Exception:
        x0, y0, x1, y1 = (x.min(), y.min(), x.max(), y.max()) if len(x) else (0, 0, 1, 1)
    span = max(x1 - x0, y1 - y0, 1e-6)
    nx = max(2, int(round(bins * (x1 - x0) / span)))
    ny = max(2, int(round(bins * (y1 - y0) / span)))
    H, xe, ye = np.histogram2d(x, y, bins=[nx, ny], range=[[x0, x1], [y0, y1]], weights=w)
    if sigma > 0:
        H = gaussian_filter(H, sigma)
    if len(tracks) > 0:
        H = H / len(tracks)
    return H.T, (x0, x1, y1, y0)


def heatmap(tracks: list[Track], app: Apparatus | None = None, title: str = "", bins: int = 60, sigma: float = 1.5,
            ax=None, size=(4, 4), cmap="inferno", vmax=None) -> Figure:
    fig = None
    if ax is None:
        fig = Figure(figsize=size, dpi=100)
        ax = fig.add_subplot(111)
    H, extent = occupancy(tracks, app, bins, sigma)
    im = ax.imshow(H, extent=extent, cmap=cmap, origin="upper", interpolation="bilinear", vmin=0, vmax=vmax)
    if app is not None:
        arena = app.arena_or_bounds()
        clip = MplPolygon(arena.polygon(), closed=True, transform=ax.transData)
        im.set_clip_path(clip)
        _draw_apparatus(ax, app, zones=True)
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    if title:
        ax.set_title(title, fontsize=9)
    if fig is not None:
        cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cb.set_label("time (s)", fontsize=8)
        cb.ax.tick_params(labelsize=7)
        fig.tight_layout()
    return fig


def speed_trace(track: Track, app: Apparatus, freezing: np.ndarray | None = None, size=(7, 2.2)) -> Figure:
    from .measures import AnalysisSettings, kinematics

    k = kinematics(track, app, AnalysisSettings())
    fig = Figure(figsize=size, dpi=100)
    ax = fig.add_subplot(111)
    ax.plot(k.t, k.speed, lw=0.7, color="#2563eb")
    fr = k.freezing if freezing is None else freezing
    if fr is not None and fr.any():
        ax.fill_between(k.t, 0, 1, where=fr, transform=ax.get_xaxis_transform(), color="#f97316", alpha=0.25,
                        label="freezing")
        ax.legend(fontsize=7, loc="upper right")
    ax.set_xlabel("time (s)")
    ax.set_ylabel(f"speed ({app.unit}/s)")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def group_plot(groups: dict, measure: str, colors: dict | None = None, kind: str = "bar", posthoc=None,
               p_value: float | None = None, size=(4.2, 3.6)) -> Figure:
    """Bar (mean ± SEM) or box plot with individual points."""
    fig = Figure(figsize=size, dpi=100)
    ax = fig.add_subplot(111)
    names = list(groups)
    rng = np.random.default_rng(0)
    for i, g in enumerate(names):
        v = np.asarray(groups[g], float)
        v = v[np.isfinite(v)]
        c = (colors or {}).get(g, f"C{i}")
        d = descriptive(v)
        if kind == "box":
            ax.boxplot([v], positions=[i], widths=0.5, patch_artist=True,
                       boxprops=dict(facecolor=c, alpha=0.35), medianprops=dict(color="black"), showfliers=False)
        else:
            ax.bar(i, d["mean"], color=c, alpha=0.45, width=0.6, edgecolor=c)
            if d["n"] > 1:
                ax.errorbar(i, d["mean"], yerr=d["sem"], color="black", capsize=4, lw=1)
        ax.scatter(i + rng.uniform(-0.12, 0.12, len(v)), v, s=14, color=c, edgecolor="white", lw=0.5, zorder=3)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=20 if len(names) > 3 else 0, fontsize=8)
    ax.set_ylabel(measure, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    # significance brackets
    if posthoc:
        top = max((np.nanmax(groups[g]) for g in names if len(groups[g])), default=1)
        step = 0.08 * (abs(top) if top else 1)
        level = top + step
        for ph in posthoc:
            if ph.get("p", 1) >= 0.05 or ph["a"] not in names or ph["b"] not in names:
                continue
            i, j = names.index(ph["a"]), names.index(ph["b"])
            ax.plot([i, i, j, j], [level, level + step / 3, level + step / 3, level], color="black", lw=0.8)
            ax.text((i + j) / 2, level + step / 3, stars(ph["p"]), ha="center", va="bottom", fontsize=8)
            level += step * 1.3
    elif p_value is not None and len(names) == 2 and p_value < 0.05:
        top = max(np.nanmax(groups[g]) for g in names if len(groups[g]))
        step = 0.08 * (abs(top) if top else 1)
        ax.plot([0, 0, 1, 1], [top + step, top + 1.33 * step, top + 1.33 * step, top + step], color="black", lw=0.8)
        ax.text(0.5, top + 1.33 * step, stars(p_value), ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    return fig


def time_course(rows: list[dict], measure: str, x: str = "Stage", by: str = "Group", colors: dict | None = None,
                size=(5, 3.4)) -> Figure:
    """Mean ± SEM of a measure across stages/trials/periods for each group (e.g. learning curves)."""
    fig = Figure(figsize=size, dpi=100)
    ax = fig.add_subplot(111)
    xs = []
    for r in rows:
        if r.get(x) not in xs:
            xs.append(r.get(x))
    try:
        xs = sorted(xs, key=lambda v: float(str(v).split()[-1].split("-")[0]))
    except (ValueError, IndexError):
        pass
    groups = []
    for r in rows:
        if r.get(by) not in groups:
            groups.append(r.get(by))
    for gi, g in enumerate(groups):
        means, sems = [], []
        for xv in xs:
            v = [r.get(measure) for r in rows if r.get(by) == g and r.get(x) == xv]
            d = descriptive(v)
            means.append(d["mean"])
            sems.append(d["sem"] if d["n"] > 1 else 0)
        c = (colors or {}).get(g, f"C{gi}")
        ax.errorbar(range(len(xs)), means, yerr=sems, marker="o", ms=4, capsize=3, color=c, label=str(g) or "All")
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels([str(v) for v in xs], rotation=30 if len(xs) > 5 else 0, fontsize=8)
    ax.set_ylabel(measure, fontsize=8)
    ax.set_xlabel(x, fontsize=8)
    ax.legend(fontsize=7, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def fig_to_png(fig: Figure, dpi: int = 120) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    return buf.getvalue()
