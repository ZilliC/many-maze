"""Figures: track plots, occupancy / behaviour heat maps, time courses and group graphs (matplotlib)."""

from __future__ import annotations

import io
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402,F401
import numpy as np  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402
from scipy.ndimage import gaussian_filter  # noqa: E402

from .apparatus import Apparatus  # noqa: E402
from .stats import descriptive, error_value, stars  # noqa: E402
from .track import Track  # noqa: E402

COLOR_BY = {"none": "Single colour", "time": "Time", "speed": "Speed"}
HEATMAP_NORMS = {"auto": "Automatic (each map)", "percent": "% of time", "relative": "Relative (max = 1)"}
TRANSFORMS = {"none": "None", "rot90": "Rotate 90°", "rot180": "Rotate 180°", "rot270": "Rotate 270°",
              "flipx": "Mirror left–right", "flipy": "Mirror top–bottom", "rot90+flipx": "Rotate 90° + mirror",
              "rot270+flipx": "Rotate 270° + mirror"}
ERRORS = {"sem": "SEM", "sd": "SD", "ci": "95% CI"}


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


# ---------------------------------------------------------------- alignment between tests
def _bounds(app: Apparatus | None, track: Track | None = None):
    try:
        return app.arena_or_bounds().bounds()
    except Exception:
        if track is None or not np.isfinite(track.x).any():
            return (0.0, 0.0, 1.0, 1.0)
        return (float(np.nanmin(track.x)), float(np.nanmin(track.y)), float(np.nanmax(track.x)),
                float(np.nanmax(track.y)))


def align_xy(x, y, src_bounds, transform: str = "none", dst_bounds=None):
    """Rotate / mirror points about the centre of src_bounds and map them onto dst_bounds.

    transform: "none", "rot90" / "rot180" / "rot270" (clockwise on screen), "flipx", "flipy", or combinations
    joined with "+" applied left to right (e.g. "rot90+flipx").
    """
    x0, y0, x1, y1 = src_bounds
    dx0, dy0, dx1, dy1 = dst_bounds or src_bounds
    w, h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    u = (np.asarray(x, float) - (x0 + x1) / 2) / w
    v = (np.asarray(y, float) - (y0 + y1) / 2) / h
    for op in (transform or "none").split("+"):
        op = op.strip()
        if op == "rot90":
            u, v = -v, u
        elif op == "rot180":
            u, v = -u, -v
        elif op == "rot270":
            u, v = v, -u
        elif op == "flipx":
            u = -u
        elif op == "flipy":
            v = -v
        elif op not in ("", "none"):
            raise ValueError(f"Unknown transform {op!r}")
    return (dx0 + dx1) / 2 + u * (dx1 - dx0), (dy0 + dy1) / 2 + v * (dy1 - dy0)


def align_track(track: Track, app: Apparatus | None, transform: str = "none", ref_app: Apparatus | None = None
                ) -> Track:
    """Copy of the track rotated / mirrored and mapped onto the reference apparatus (for group heat maps)."""
    if (not transform or transform == "none") and (ref_app is None or ref_app is app):
        return track
    src = _bounds(app, track)
    dst = _bounds(ref_app, None) if ref_app is not None else src
    out = track.copy()
    for a, b in (("x", "y"), ("hx", "hy"), ("tx", "ty")):
        nx, ny = align_xy(getattr(track, a), getattr(track, b), src, transform, dst)
        setattr(out, a, nx)
        setattr(out, b, ny)
    if np.isfinite(out.hx).any():
        out.angle = np.degrees(np.arctan2(out.hy - out.ty, out.hx - out.tx))
    return out


# ---------------------------------------------------------------- track plots
def behaviour_markers(track: Track, app: Apparatus, settings=None, events=None, behaviours=None,
                      freezing: bool = True, immobile: bool = False) -> list[dict]:
    """Markers for track plots: freezing / immobility episodes and manually scored events.

    Returns [{"label", "t", "t_end" (None for point events), "color"}].
    """
    from .measures import AnalysisSettings, kinematics, runs

    out = []
    if len(track) and (freezing or immobile):
        k = kinematics(track, app, settings or AnalysisSettings())
        t, dur = k.t, k.dur
        for flag, mask, label, col in ((freezing, k.freezing, "Freezing", "#0ea5e9"),
                                       (immobile, ~k.mobile, "Immobile", "#a855f7")):
            if flag:
                for a, b in runs(mask):
                    out.append({"label": label, "t": float(t[a]), "t_end": float(t[b - 1] + dur[b - 1]),
                                "color": col})
    kinds = {}
    for b in behaviours or []:
        name = b["name"] if isinstance(b, dict) else b.name
        kinds[name] = b.get("kind", "state") if isinstance(b, dict) else getattr(b, "kind", "state")
    palette = ["#ef4444", "#22c55e", "#eab308", "#ec4899", "#14b8a6", "#f97316", "#6366f1"]
    names = list(dict.fromkeys(e.get("behaviour") for e in events or [] if e.get("behaviour")))
    for e in events or []:
        name = e.get("behaviour")
        if not name:
            continue
        point = kinds.get(name) == "point" or (e.get("t_end") is None and kinds.get(name) != "state")
        out.append({"label": name, "t": float(e["t"]),
                    "t_end": None if point else (float(e["t_end"]) if e.get("t_end") is not None else math.inf),
                    "color": palette[names.index(name) % len(palette)]})
    return out


def _series_for(track: Track, app, color_by: str, settings=None):
    if color_by == "time":
        return track.t, "time (s)"
    if color_by == "speed":
        from .measures import AnalysisSettings, kinematics, moving_average

        k = kinematics(track, app, settings or AnalysisSettings())
        win = max(1, int(round(0.2 / max(track.dt, 1e-6))))
        return moving_average(k.speed, win), f"speed ({app.unit if app else 'px'}/s)"
    from . import charts

    v = charts.compute(track, app, [color_by], settings)[color_by]
    return v, charts.param_info(app, color_by, track).label


def track_plot(track: Track, app: Apparatus | None = None, frame=None, title: str = "", color_by: str = "time",
               ax=None, show_head: bool = False, size=(4, 4), part: str = "centre", values=None,
               value_label: str = "", colorbar: bool = False, markers: list | None = None, cmap: str = "viridis",
               norm: Normalize | None = None, settings=None, legend: bool = True, lw: float = 0.9) -> Figure:
    """Path of the centre (or head) with optional colour coding and behaviour markers.

    color_by: "none", "time", "speed" or any per-frame parameter name of core.charts (or pass `values`).
    markers: from behaviour_markers(): point events are drawn at the animal's position, state events and
    freezing episodes as thick translucent path segments.
    """
    fig = None
    if ax is None:
        fig = Figure(figsize=size, dpi=100)
        ax = fig.add_subplot(111)
    if frame is not None:
        img = frame[..., ::-1] if frame.ndim == 3 else frame
        ax.imshow(img, cmap="gray", alpha=0.6)
    _draw_apparatus(ax, app)
    x, y = track.bodypart(part)
    if part != "centre" and not np.isfinite(x).any():
        x, y = track.x, track.y
    mappable = None
    if values is None and color_by and color_by != "none" and len(x) > 1:
        try:
            values, value_label = _series_for(track, app, color_by, settings)
        except Exception:
            values = None
    if values is not None and len(x) > 1:
        from matplotlib.collections import LineCollection

        pts = np.column_stack([x, y]).reshape(-1, 1, 2)
        segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
        vals = np.asarray(values, float)[:-1]
        if norm is None:
            fin = vals[np.isfinite(vals)]
            norm = Normalize(float(fin.min()) if len(fin) else 0, float(fin.max()) if len(fin) else 1)
        lc = LineCollection(segs, cmap=cmap, norm=norm, lw=lw)
        lc.set_array(vals)
        ax.add_collection(lc)
        mappable = lc
    else:
        ax.plot(x, y, "-", lw=lw * 0.9, color="#2563eb")
    handles = {}
    for m in markers or []:
        idx = np.searchsorted(track.t, m["t"])
        if m.get("t_end") is None:
            if idx >= len(x) or not np.isfinite(x[idx]):
                continue
            h, = ax.plot(x[idx], y[idx], marker="D", ms=5, color=m["color"], mec="white", mew=0.6, ls="none",
                         zorder=5)
        else:
            j = np.searchsorted(track.t, m["t_end"])
            if j <= idx:
                continue
            h, = ax.plot(x[idx:j + 1], y[idx:j + 1], "-", lw=4.5, color=m["color"], alpha=0.45,
                         solid_capstyle="round", zorder=4)
            if np.isfinite(x[idx]):
                ax.plot(x[idx], y[idx], "o", ms=3.5, color=m["color"], zorder=5)
        handles.setdefault(m["label"], h)
    ok = np.flatnonzero(np.isfinite(x))
    if len(ok):
        ax.plot(x[ok[0]], y[ok[0]], "o", color="#16a34a", ms=6, label="start", zorder=6)
        ax.plot(x[ok[-1]], y[ok[-1]], "s", color="#dc2626", ms=6, label="end", zorder=6)
    if show_head and track.has_head():
        ax.plot(track.hx, track.hy, ",", color="#f97316", alpha=0.5)
    _limits(ax, app, track, frame)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    if title:
        ax.set_title(title, fontsize=9)
    if handles and legend:
        ax.legend(list(handles.values()), list(handles), fontsize=6, loc="upper left", bbox_to_anchor=(0, -0.01),
                  ncol=min(4, len(handles)), frameon=False, handlelength=1.2, borderaxespad=0)
    if fig is not None:
        if mappable is not None and colorbar:
            cb = fig.colorbar(mappable, ax=ax, fraction=0.046, pad=0.03)
            cb.set_label(value_label, fontsize=8)
            cb.ax.tick_params(labelsize=7)
        fig.tight_layout()
    return fig


def segmented_track_plot(track: Track, app: Apparatus | None, periods: list[tuple[str, float, float]], frame=None,
                         color_by: str = "time", part: str = "centre", markers: list | None = None,
                         settings=None, ncols: int = 0, size=None, title: str = "") -> Figure:
    """Small multiples: one track plot per time period, on a shared colour scale."""
    periods = list(periods) or [("Whole test", float(track.t[0]) if len(track) else 0.0, math.inf)]
    n = len(periods)
    ncols = ncols or min(n, 4)
    nrows = math.ceil(n / ncols)
    fig = Figure(figsize=size or (2.3 * ncols + 0.8, 2.4 * nrows + 0.3), dpi=100, layout="constrained")
    values = label = None
    norm = None
    if color_by and color_by != "none" and len(track) > 1:
        try:
            values, label = _series_for(track, app, color_by, settings)
            fin = np.asarray(values, float)[np.isfinite(values)]
            norm = Normalize(float(fin.min()), float(fin.max())) if len(fin) else None
        except Exception:
            values = None
    mappable = None
    axes = []
    for i, (lab, a, b) in enumerate(periods):
        ax = fig.add_subplot(nrows, ncols, i + 1)
        axes.append(ax)
        m = (track.t >= a) & (track.t < b)
        sub = track.slice_time(a, b)
        mk = [dict(mm, t=max(mm["t"], a), t_end=None if mm.get("t_end") is None else min(mm["t_end"], b))
              for mm in markers or [] if mm["t"] < b and (mm.get("t_end") if mm.get("t_end") is not None
                                                           else mm["t"]) >= a]
        mk = [mm for mm in mk if mm.get("t_end") is None or mm["t_end"] > mm["t"]]
        track_plot(sub, app, frame=frame, title=lab, ax=ax, part=part, markers=mk,
                   values=None if values is None else np.asarray(values)[m], color_by="none" if values is None
                   else color_by, norm=norm, legend=False)
        if ax.collections:
            mappable = ax.collections[-1] if values is not None else None
    if mappable is not None:
        cb = fig.colorbar(mappable, ax=axes, fraction=0.025, pad=0.02)
        cb.set_label(label or "", fontsize=8)
        cb.ax.tick_params(labelsize=7)
    if markers:
        seen = {}
        for mm in markers:
            seen.setdefault(mm["label"], mm["color"])
        fig.legend([Line2D([], [], color=c, lw=4, alpha=0.6) for c in seen.values()], list(seen), fontsize=7,
                   loc="outside lower center", ncol=min(6, len(seen)), frameon=False)
    if title:
        fig.suptitle(title, fontsize=9)
    return fig


# ---------------------------------------------------------------- heat maps
def occupancy(tracks: list[Track], app: Apparatus | None, bins: int = 60, sigma: float = 1.5, part="centre",
              masks: list | None = None, t_range: tuple | None = None, norm: str = "time"):
    """Smoothed time per spatial bin, averaged over tracks. Returns (H [rows = y], extent for imshow).

    masks: optional boolean per-frame arrays (one per track) restricting the map to frames where a behaviour
    occurred (e.g. freezing).  t_range: only samples with t0 <= t < t1.
    norm: "time" (s per bin, mean over tracks), "percent" (% of the mapped time) or "relative" (max = 1).
    """
    xs, ys, ws = [], [], []
    for i, tr in enumerate(tracks):
        px, py = tr.bodypart(part)
        if part != "centre" and not np.isfinite(px).any():
            px, py = tr.x, tr.y
        ok = np.isfinite(px) & np.isfinite(py)
        if masks is not None and i < len(masks) and masks[i] is not None:
            ok &= np.asarray(masks[i], bool)
        if t_range is not None:
            ok &= (tr.t >= t_range[0]) & (tr.t < t_range[1])
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
    if norm == "percent":
        s = H.sum()
        H = 100.0 * H / s if s > 0 else H
    elif norm == "relative":
        m = H.max() if H.size else 0
        H = H / m if m > 0 else H
    return H.T, (x0, x1, y1, y0)


def _norm_label(norm: str, what: str = "") -> str:
    base = {"percent": "% of time", "relative": "relative occupancy"}.get(norm, "time (s)")
    return f"{base} · {what}" if what and what != "Position" else base


def heatmap(tracks: list[Track], app: Apparatus | None = None, title: str = "", bins: int = 60, sigma: float = 1.5,
            ax=None, size=(4, 4), cmap="inferno", vmax=None, norm: str = "auto", masks: list | None = None,
            t_range: tuple | None = None, part: str = "centre", label: str = "") -> Figure:
    """Occupancy heat map. norm: "auto" (time, own scale) / "percent" / "relative"; vmax fixes the scale."""
    fig = None
    if ax is None:
        fig = Figure(figsize=size, dpi=100)
        ax = fig.add_subplot(111)
    occ_norm = {"auto": "time", "fixed": "time"}.get(norm, norm)
    H, extent = occupancy(tracks, app, bins, sigma, part=part, masks=masks, t_range=t_range, norm=occ_norm)
    im = ax.imshow(H, extent=extent, cmap=cmap, origin="upper", interpolation="bilinear", vmin=0, vmax=vmax)
    if app is not None:
        try:
            arena = app.arena_or_bounds()
            clip = MplPolygon(arena.polygon(), closed=True, transform=ax.transData)
            im.set_clip_path(clip)
        except ValueError:
            pass
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
        cb.set_label(label or _norm_label(occ_norm), fontsize=8)
        cb.ax.tick_params(labelsize=7)
        fig.tight_layout()
    return fig


def group_heatmap_figure(data: list[tuple[str, list, object]], norm: str = "auto", vmax: float | None = None,
                         masks: dict | None = None, t_range: tuple | None = None, part: str = "centre",
                         what: str = "", ncols: int = 3) -> Figure:
    """Grid of average heat maps (one per group/label) on a common colour scale.

    data: [(label, tracks (already aligned), apparatus)]; masks: {label: [mask per track]}.
    """
    n = max(1, len(data))
    ncol = min(n, ncols)
    nrow = math.ceil(n / ncol)
    fig = Figure(figsize=(3.3 * ncol + 0.6, 3.3 * nrow), dpi=100, layout="constrained")
    occ_norm = {"auto": "time", "fixed": "time"}.get(norm, norm)
    if vmax is None:
        vmax = 0.0
        for g, trs, app in data:
            if trs:
                H, _ = occupancy(trs, app, masks=(masks or {}).get(g), t_range=t_range, part=part, norm=occ_norm)
                vmax = max(vmax, float(np.nanmax(H)) if H.size else 0.0)
        vmax = vmax or None
    axes = []
    for i, (g, trs, app) in enumerate(data):
        ax = fig.add_subplot(nrow, ncol, i + 1)
        axes.append(ax)
        heatmap(trs, app, title=f"{g} (n = {len(trs)})", ax=ax, vmax=vmax, norm=norm, masks=(masks or {}).get(g),
                t_range=t_range, part=part)
    if axes and axes[0].images:
        cb = fig.colorbar(axes[0].images[0], ax=axes, fraction=0.03, pad=0.02)
        cb.set_label(("mean " if occ_norm == "time" else "") + _norm_label(occ_norm, what), fontsize=8)
        cb.ax.tick_params(labelsize=7)
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


# ---------------------------------------------------------------- group graphs
def _jitter(n, width=0.12, seed=0):
    return np.random.default_rng(seed).uniform(-width, width, n)


def _draw_dist(ax, pos, v, c, kind, error, width, points, seed=0):
    d = descriptive(v)
    if kind == "box":
        if len(v):
            ax.boxplot([v], positions=[pos], widths=width * 0.85, patch_artist=True,
                       boxprops=dict(facecolor=c, alpha=0.35), medianprops=dict(color="black"), showfliers=False)
    elif kind == "violin":
        if len(v) > 1 and np.ptp(v) > 0:
            parts = ax.violinplot([v], positions=[pos], widths=width, showextrema=False, showmedians=True)
            for b in parts["bodies"]:
                b.set_facecolor(c)
                b.set_alpha(0.35)
            parts["cmedians"].set_color("black")
        elif len(v):
            ax.plot([pos - width / 3, pos + width / 3], [d["median"]] * 2, color="black", lw=1)
    elif kind == "point":
        if d["n"]:
            e = error_value(v, error)
            ax.errorbar(pos, d["mean"], yerr=e if e == e else None, fmt="o", color=c, ms=6, capsize=4, lw=1.2,
                        zorder=4)
    else:
        if d["n"]:
            ax.bar(pos, d["mean"], color=c, alpha=0.45, width=width, edgecolor=c)
            e = error_value(v, error)
            if d["n"] > 1 and e == e:
                ax.errorbar(pos, d["mean"], yerr=e, color="black", capsize=4, lw=1)
    if points and len(v):
        ax.scatter(pos + _jitter(len(v), width * 0.2, seed), v, s=14, color=c, edgecolor="white", lw=0.5, zorder=3)


def group_plot(groups: dict, measure: str, colors: dict | None = None, kind: str = "bar", posthoc=None,
               p_value: float | None = None, size=(4.2, 3.6), error: str = "sem", points: bool = True,
               ref_value: float | None = None) -> Figure:
    """Column (mean ± SEM/SD/CI), point, box or violin graph per group with individual points."""
    fig = Figure(figsize=size, dpi=100)
    ax = fig.add_subplot(111)
    names = list(groups)
    for i, g in enumerate(names):
        v = np.asarray(groups[g], float)
        v = v[np.isfinite(v)]
        c = (colors or {}).get(g, f"C{i}")
        _draw_dist(ax, i, v, c, kind, error, 0.6, points, seed=i)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=20 if len(names) > 3 else 0, fontsize=8)
    lab = measure
    if kind in ("bar", "point"):
        lab += f"\n(mean ± {ERRORS.get(error, error)})"
    ax.set_ylabel(lab, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    if ref_value is not None:
        ax.axhline(ref_value, color="#64748b", lw=0.8, ls="--")
    # significance brackets
    finite = [np.asarray(groups[g], float) for g in names if len(groups[g])]
    top = max((np.nanmax(v) for v in finite if np.isfinite(v).any()), default=1.0)
    step = 0.08 * (abs(top) if top else 1)
    if posthoc:
        level = top + step
        for ph in posthoc:
            if ph.get("p", 1) >= 0.05 or ph["a"] not in names or ph["b"] not in names:
                continue
            i, j = names.index(ph["a"]), names.index(ph["b"])
            ax.plot([i, i, j, j], [level, level + step / 3, level + step / 3, level], color="black", lw=0.8)
            ax.text((i + j) / 2, level + step / 3, stars(ph["p"]), ha="center", va="bottom", fontsize=8)
            level += step * 1.3
    elif p_value is not None and len(names) == 2 and p_value < 0.05:
        ax.plot([0, 0, 1, 1], [top + step, top + 1.33 * step, top + 1.33 * step, top + step], color="black", lw=0.8)
        ax.text(0.5, top + 1.33 * step, stars(p_value), ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    return fig


def _sort_levels(vals):
    try:
        return sorted(vals, key=lambda v: float(str(v).split()[-1].split("-")[0]))
    except (ValueError, IndexError):
        return vals


def time_course(rows: list[dict], measure: str, x: str = "Stage", by: str = "Group", colors: dict | None = None,
                size=(5, 3.4), error: str = "sem", points: bool = False, order: list | None = None) -> Figure:
    """Mean ± error of a measure across stages/trials/periods for each group (e.g. learning curves)."""
    fig = Figure(figsize=size, dpi=100)
    ax = fig.add_subplot(111)
    xs = list(order) if order else []
    if not xs:
        for r in rows:
            if r.get(x) not in xs:
                xs.append(r.get(x))
        xs = _sort_levels(xs)
    xs_s = [str(v) for v in xs]
    groups = []
    for r in rows:
        if r.get(by) not in groups:
            groups.append(r.get(by))
    n = max(1, len(groups))
    for gi, g in enumerate(groups):
        means, errs = [], []
        off = (gi - (n - 1) / 2) * 0.06
        c = (colors or {}).get(g, f"C{gi}")
        for xi, xv in enumerate(xs_s):
            v = [r.get(measure) for r in rows if r.get(by) == g and str(r.get(x)) == xv]
            d = descriptive(v)
            means.append(d["mean"])
            e = error_value(v, error)
            errs.append(e if d["n"] > 1 and e == e else 0)
            if points and d["n"]:
                vv = np.asarray([float(a) for a in v if isinstance(a, (int, float)) and a == a])
                ax.scatter(xi + off + _jitter(len(vv), 0.04, xi), vv, s=9, color=c, alpha=0.5, lw=0, zorder=2)
        ax.errorbar(np.arange(len(xs)) + off, means, yerr=errs, marker="o", ms=4, capsize=3, color=c,
                    label=str(g) or "All", zorder=3)
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels(xs_s, rotation=30 if len(xs) > 5 else 0, fontsize=8)
    ax.set_ylabel(f"{measure}\n(mean ± {ERRORS.get(error, error)})", fontsize=8)
    ax.set_xlabel(x, fontsize=8)
    ax.legend(fontsize=7, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def factor_plot(rows: list[dict], measure: str, factors: list[str], kind: str = "bar", error: str = "sem",
                points: bool = True, colors: dict | None = None, orders: dict | None = None, size=(6, 3.8)) -> Figure:
    """Graph of a measure grouped by up to three factors.

    factors[0] → x axis, factors[1] → colour (clustered bars / separate lines), factors[2] → panels.
    kind: "bar" (column), "line", "point", "box" or "violin".
    """
    factors = [f for f in factors if f][:3]
    orders = orders or {}

    def levels(f, rs):
        vals = list(dict.fromkeys(str(r.get(f, "")) for r in rs))
        if f in orders:
            o = [str(v) for v in orders[f]]
            vals.sort(key=lambda v: (o.index(v) if v in o else len(o), v))
        return vals

    def vals_of(rs):
        out = []
        for r in rs:
            v = r.get(measure)
            if isinstance(v, (int, float, np.number)) and not isinstance(v, bool) and math.isfinite(float(v)):
                out.append(float(v))
        return np.asarray(out)

    panels = levels(factors[2], rows) if len(factors) > 2 else [None]
    fig = Figure(figsize=size, dpi=100, layout="constrained")
    axes = []
    xlv = levels(factors[0], rows) if factors else ["All"]
    hue = levels(factors[1], rows) if len(factors) > 1 else [None]
    for pi, pv in enumerate(panels):
        ax = fig.add_subplot(1, len(panels), pi + 1, sharey=axes[0] if axes else None)
        axes.append(ax)
        prow = [r for r in rows if pv is None or str(r.get(factors[2], "")) == pv]
        nh = len(hue)
        width = 0.8 / nh
        for hi, hv in enumerate(hue):
            c = (colors or {}).get(hv, f"C{hi}") if hv is not None else (colors or {}).get(xlv[0], "C0")
            means, errs, xs = [], [], []
            for xi, xv in enumerate(xlv):
                rs = [r for r in prow if (not factors or str(r.get(factors[0], "")) == xv)
                      and (hv is None or str(r.get(factors[1], "")) == hv)]
                v = vals_of(rs)
                pos = xi + (hi - (nh - 1) / 2) * width if kind != "line" else xi + (hi - (nh - 1) / 2) * 0.05
                if kind == "line":
                    d = descriptive(v)
                    means.append(d["mean"])
                    e = error_value(v, error)
                    errs.append(e if e == e else 0)
                    xs.append(pos)
                    if points and len(v):
                        ax.scatter(pos + _jitter(len(v), 0.03, xi), v, s=9, color=c, alpha=0.5, lw=0, zorder=2)
                else:
                    cc = c if hv is not None else (colors or {}).get(xv, f"C{xi}")
                    _draw_dist(ax, pos, v, cc, kind, error, width * 0.9, points, seed=xi * 10 + hi)
            if kind == "line":
                ax.errorbar(xs, means, yerr=errs, marker="o", ms=4, capsize=3, color=c, zorder=3,
                            label=hv if hv is not None else None)
        if hue[0] is not None and kind != "line":
            for hi, hv in enumerate(hue):
                ax.bar(0, 0, color=(colors or {}).get(hv, f"C{hi}"), alpha=0.6, label=hv, width=0)
        ax.set_xticks(range(len(xlv)))
        ax.set_xticklabels(xlv, rotation=25 if len(xlv) > 4 else 0, fontsize=8)
        if factors:
            ax.set_xlabel(factors[0], fontsize=8)
        if pv is not None:
            ax.set_title(f"{factors[2]}: {pv}", fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(labelsize=8)
    lab = measure + (f"\n(mean ± {ERRORS.get(error, error)})" if kind in ("bar", "line", "point") else "")
    axes[0].set_ylabel(lab, fontsize=8)
    if hue[0] is not None:
        axes[-1].legend(fontsize=7, frameon=False, title=factors[1], title_fontsize=7)
    return fig


def scatter_plot(xs, ys, groups, x_label: str, y_label: str, colors: dict | None = None, res: dict | None = None,
                 regression: dict | None = None, size=(4.6, 3.8)) -> Figure:
    """Scatter plot coloured by group with a least-squares line and its 95 % confidence band."""
    from .stats import format_p

    fig = Figure(figsize=size, dpi=100)
    ax = fig.add_subplot(111)
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    names = list(dict.fromkeys(groups))
    for i, g in enumerate(names):
        m = np.array([gg == g for gg in groups])
        ax.scatter(xs[m], ys[m], s=26, color=(colors or {}).get(g, f"C{i}"), edgecolor="white", lw=0.6, zorder=3,
                   label=g or "All")
    reg = regression
    if reg is None:
        from .stats import regression as _reg

        reg = _reg(xs, ys)
    if reg and reg.get("band_x") is not None and len(reg["band_x"]):
        ax.plot(reg["band_x"], reg["band_y"], "-", color="#334155", lw=1.2, zorder=2)
        ax.fill_between(reg["band_x"], reg["band_lo"], reg["band_hi"], color="#94a3b8", alpha=0.25, lw=0, zorder=1)
    res = res or {}
    sym = {"pearson": "r", "spearman": "ρ", "kendall": "τ"}.get(res.get("method", "pearson"), "r")
    if res.get("r") == res.get("r") and res.get("r") is not None:
        ax.set_title(f"{sym} = {res['r']:.3f}, {format_p(res['p'])}, n = {res['n']}", fontsize=9)
    ax.set_xlabel(x_label, fontsize=8)
    ax.set_ylabel(y_label, fontsize=8)
    ax.tick_params(labelsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    if len(names) > 1:
        ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    return fig


def proportions_figure(rows_l, cols_l, T, size=(5, 3.6)) -> Figure:
    """Stacked bars of the proportion of each category per row level."""
    fig = Figure(figsize=size, dpi=100)
    ax = fig.add_subplot(111)
    T = np.asarray(T, float)
    tot = T.sum(axis=1, keepdims=True)
    P = np.divide(T, tot, out=np.zeros_like(T), where=tot > 0) * 100
    bottom = np.zeros(len(rows_l))
    for j, c in enumerate(cols_l):
        ax.bar(range(len(rows_l)), P[:, j], bottom=bottom, label=c, color=f"C{j}", alpha=0.8, width=0.6)
        bottom += P[:, j]
    ax.set_xticks(range(len(rows_l)))
    ax.set_xticklabels([f"{r}\n(n = {int(n)})" for r, n in zip(rows_l, tot[:, 0])], fontsize=8)
    ax.set_ylabel("% of tests", fontsize=8)
    ax.set_ylim(0, 100)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=7, frameon=False, bbox_to_anchor=(1.0, 1.0), loc="upper left")
    fig.tight_layout()
    return fig


def message_figure(text: str, size=(4.2, 3.6), fig: Figure | None = None, fontsize: float = 10) -> Figure:
    """A figure showing only a grey message (no data, nothing chosen…)."""
    fig = fig or Figure(figsize=size, dpi=100)
    ax = fig.add_subplot(111)
    ax.axis("off")
    ax.text(0.5, 0.5, text, ha="center", va="center", fontsize=fontsize, color="#64748b", wrap=True,
            transform=ax.transAxes)
    return fig


def fig_to_png(fig: Figure, dpi: int = 120) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    return buf.getvalue()
