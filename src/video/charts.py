"""Data graphics. Every value drawn here comes from Market Brief's data (never the script).

Animated charts are drawn with Pillow (fast enough per frame); the static multi-period
heatmap uses Matplotlib.
"""
from __future__ import annotations

import io
import json
from functools import lru_cache

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from ..config import ROOT  # noqa: E402
from . import graphics as g  # noqa: E402

SHORT = {"XLK": "Info Tech", "XLC": "Comm Services", "XLY": "Cons Discretionary",
         "XLP": "Cons Staples", "XLF": "Financials", "XLV": "Health Care", "XLI": "Industrials",
         "XLB": "Materials", "XLE": "Energy", "XLU": "Utilities", "XLRE": "Real Estate"}


def sector_bars(img: Image.Image, box, rows: list[tuple[str, float | None]], progress: float,
                highlight: set[str] | None = None, title: str | None = None) -> None:
    """Horizontal bars of daily % change, sorted, growing from a zero line."""
    d = g.Draw(img)
    x0, y0, x1, y1 = box
    if title:
        d.text((x0, y0), title, font=g.font("SemiBold", 26), fill=g.MUTED)
        y0 += 50
    rows = sorted([r for r in rows if r[1] is not None], key=lambda r: r[1], reverse=True)
    if not rows:
        return
    span = max(0.5, max(abs(v) for _, v in rows))
    label_w = 270
    zero = x0 + label_w + (x1 - x0 - label_w - 120) * 0.5
    half = (x1 - x0 - label_w - 120) * 0.5
    n = len(rows)
    row_h = (y1 - y0) / n
    bar_h = max(10, int(row_h * 0.56))
    f_lab, f_val = g.font("Medium", 24), g.font("SemiBold", 24)
    d.line([(zero, y0 - 6), (zero, y1)], fill=(255, 255, 255, 60), width=2)
    for i, (tick, v) in enumerate(rows):
        p = g.ease_out((progress * (n + 4) - i) / 4)        # staggered growth
        cy = y0 + row_h * i + row_h / 2
        hl = highlight and tick in highlight
        lab_col = g.TEXT if (not highlight or hl) else g.MUTED
        d.text((x0, cy), SHORT.get(tick, tick), font=f_lab, fill=lab_col, anchor="lm")
        L = half * abs(v) / span * p
        col = g.move_color(v)
        if highlight and not hl:
            col = col[:3] + (110,)
        if v >= 0:
            g.rounded(d, (zero, cy - bar_h / 2, zero + max(L, 2), cy + bar_h / 2), 5, fill=col)
            tx, anchor = zero + L + 12, "lm"
        else:
            g.rounded(d, (zero - max(L, 2), cy - bar_h / 2, zero, cy + bar_h / 2), 5, fill=col)
            tx, anchor = zero + 12, "lm"
        if p > 0.05:
            d.text((tx if v >= 0 else zero - L - 12, cy), f"{v * p:+.2f}%", font=f_val,
                   fill=col[:3] + (int(col[3] * min(1.0, p * 1.5)),),
                   anchor=anchor if v >= 0 else "rm")


def rank_strip(img: Image.Image, box, rows: list[tuple[str, float | None]], highlight: str) -> None:
    """11 mini columns, sorted by day %, the current sector highlighted."""
    d = g.Draw(img)
    x0, y0, x1, y1 = box
    rows = sorted([r for r in rows if r[1] is not None], key=lambda r: r[1], reverse=True)
    if not rows:
        return
    span = max(0.5, max(abs(v) for _, v in rows))
    n = len(rows)
    w = (x1 - x0) / n
    mid = (y0 + y1) / 2
    for i, (t, v) in enumerate(rows):
        h = (y1 - y0) / 2 * abs(v) / span
        col = g.move_color(v)
        if t != highlight:
            col = col[:3] + (85,)
        bx0, bx1 = x0 + i * w + w * 0.18, x0 + (i + 1) * w - w * 0.18
        if v >= 0:
            d.rectangle((bx0, mid - max(h, 2), bx1, mid), fill=col)
        else:
            d.rectangle((bx0, mid, bx1, mid + max(h, 2)), fill=col)
        if t == highlight:
            d.polygon([(bx0 + (bx1 - bx0) / 2 - 8, y1 + 16), (bx0 + (bx1 - bx0) / 2 + 8, y1 + 16),
                       (bx0 + (bx1 - bx0) / 2, y1 + 4)], fill=g.TEXT)
    d.line([(x0, mid), (x1, mid)], fill=(255, 255, 255, 70), width=1)


def returns_rows(img: Image.Image, box, items: list[tuple[str, float | None]], progress: float = 1.0) -> None:
    """Labelled performance rows: '1 week  +3.7%' with a small proportional bar."""
    d = g.Draw(img)
    x0, y0, x1, y1 = box
    items = [(k, v) for k, v in items if v is not None]
    if not items:
        return
    span = max(1.0, max(abs(v) for _, v in items))
    rh = (y1 - y0) / len(items)
    f_lab, f_val = g.font("Medium", 24), g.font("Bold", 26)
    bar_x0 = x0 + 150
    bar_w = x1 - bar_x0 - 130
    for i, (k, v) in enumerate(items):
        p = g.ease_out((progress * (len(items) + 2) - i) / 2)
        cy = y0 + rh * i + rh / 2
        d.text((x0, cy), k, font=f_lab, fill=g.MUTED, anchor="lm")
        col = g.move_color(v)
        L = bar_w * abs(v) / span * p
        g.rounded(d, (bar_x0, cy - 7, bar_x0 + bar_w, cy + 7), 7, fill=(255, 255, 255, 18))
        g.rounded(d, (bar_x0, cy - 7, bar_x0 + max(L, 6), cy + 7), 7, fill=col)
        d.text((x1, cy), f"{v:+.1f}%", font=f_val, fill=col, anchor="rm")


def period_heatmap(sectors: dict[str, dict | None], order: list[str], width: int, height: int) -> Image.Image:
    """Matplotlib heatmap: sectors x (day, week, month, YTD, 1 year)."""
    cols = [("day", "Day"), ("week", "Week"), ("month", "Month"), ("ytd", "YTD"), ("y1", "1 Year")]
    data, labels = [], []
    for t in order:
        r = sectors.get(t) or {}
        labels.append(SHORT.get(t, t))
        data.append([r.get(k) if r.get(k) is not None else float("nan") for k, _ in cols])
    dpi = 100
    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    fig.patch.set_alpha(0)
    ax.set_facecolor((0, 0, 0, 0))
    cmap = LinearSegmentedColormap.from_list("rg", ["#b91c1c", "#1e293b", "#15803d"])
    import numpy as np
    arr = np.array(data, dtype=float)
    # normalise each column separately so day moves and 1-year moves are both readable
    norm = np.zeros_like(arr)
    for j in range(arr.shape[1]):
        col = arr[:, j]
        m = np.nanmax(np.abs(col)) if np.isfinite(col).any() else 1
        norm[:, j] = col / (m or 1)
    ax.imshow(np.nan_to_num(norm), cmap=cmap, vmin=-1, vmax=1, aspect="auto")
    for i in range(arr.shape[0]):
        for j in range(arr.shape[1]):
            v = arr[i, j]
            ax.text(j, i, "n/a" if not np.isfinite(v) else f"{v:+.1f}%", ha="center", va="center",
                    color="white", fontsize=15, fontweight="bold")
    ax.set_xticks(range(len(cols)), [c[1] for c in cols], color="#cbd5e1", fontsize=15)
    ax.set_yticks(range(len(labels)), labels, color="#e2e8f0", fontsize=15)
    ax.xaxis.tick_top()
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    ax.set_xticks(np.arange(-.5, len(cols), 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(labels), 1), minor=True)
    ax.grid(which="minor", color="#0b1020", linewidth=3)
    fig.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert("RGBA").resize((width, height), Image.LANCZOS)


# --------------------------------------------------------------------------- world map
@lru_cache(maxsize=1)
def _world() -> dict:
    return json.loads((ROOT / "assets" / "geo" / "world_110m.json").read_text())["countries"]


LON0, LON1, LAT0, LAT1 = -170.0, 180.0, -56.0, 78.0
CENTROIDS = {"us": (-98, 39), "cn": (104, 35), "eu": (10, 50), "mx": (-102, 23), "ca": (-106, 58),
             "jp": (138, 37), "tw": (121, 23.7), "gb": (-2, 54), "in": (79, 22), "sa": (45, 24),
             "ru": (90, 60), "kr": (128, 36), "de": (10, 51)}


def project(lon: float, lat: float, w: int, h: int) -> tuple[float, float]:
    return (lon - LON0) / (LON1 - LON0) * w, (LAT1 - lat) / (LAT1 - LAT0) * h


def world_map(w: int, h: int, highlight: list[str], home: str = "us") -> Image.Image:
    """Equirectangular world map, highlighted countries filled in accent colours."""
    S = 2
    img = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
    d = g.Draw(img)
    hl = set(c.lower() for c in highlight)
    for code, c in _world().items():
        if code == home:
            fill, line = (56, 189, 248, 235), (125, 211, 252, 255)
        elif code in hl or ("eu" in hl and c.get("eu")):
            fill, line = (245, 189, 65, 225), (253, 224, 140, 255)
        else:
            fill, line = (44, 60, 96, 255), (70, 90, 130, 255)
        for ring in c["rings"]:
            pts = [project(x, y, w * S, h * S) for x, y in ring]
            if len(pts) > 2:
                d.polygon(pts, fill=fill, outline=line)
    return img.resize((w, h), Image.LANCZOS)
