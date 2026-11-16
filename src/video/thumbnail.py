"""YouTube thumbnail (1280x720 JPEG < 2 MB) and podcast cover art (3000x3000 JPEG)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PIL import Image

from . import graphics as g

SHORT = {"XLK": "Tech", "XLC": "Comms", "XLY": "Discr.", "XLP": "Staples", "XLF": "Financials",
         "XLV": "Health", "XLI": "Industrials", "XLB": "Materials", "XLE": "Energy", "XLU": "Utilities",
         "XLRE": "Real Est."}


def _market(episode: dict) -> dict:
    return next((s for s in episode["segments"] if s["type"] == "market_overview"), {}).get("data") or {}


def make_thumbnail(episode: dict, out: Path) -> Path:
    W, H = 1280, 720
    img = g.background(W, H)
    d = g.Draw(img)
    data = _market(episode)
    sp = data.get("sp500") or {}
    dt = date.fromisoformat(episode["date"])
    # brand
    d.rectangle((48, 44, 60, 92), fill=g.ACCENT)
    d.text((76, 40), "MARKET BRIEF", font=g.font("Black", 48, True), fill=g.TEXT)
    d.text((78, 96), f"DAILY  ·  {dt.strftime('%a')} {dt.day} {dt.strftime('%b %Y').upper()}",
           font=g.font("Bold", 26), fill=g.ACCENT)
    # S&P badge
    day = sp.get("day")
    if day is not None:
        col = g.move_color(day)
        g.rounded(d, (W - 360, 40, W - 48, 150), 20, fill=col[:3] + (48,), outline=col, width=3)
        d.text((W - 334, 56), "S&P 500", font=g.font("Bold", 26), fill=g.TEXT)
        d.text((W - 334, 88), f"{day:+.2f}%", font=g.font("Black", 54, True), fill=col)
    # headline
    hook = episode.get("title_hook") or "11 sectors + markets & geopolitics"
    f = g.fit_font(d, hook, "Black", W - 96, 82, 46, max_lines=3)
    y = 210
    for line in g.wrap(d, hook, f, W - 96, 3):
        d.text((48, y), line, font=f, fill=g.TEXT)
        y += f.size + 8
    # sector heat strip
    sectors = data.get("sectors") or {}
    n = max(1, len(sectors))
    sw = (W - 96 - (n - 1) * 8) / n
    x = 48
    for tk, r in sectors.items():
        v = (r or {}).get("day")
        col = g.move_color(v)
        g.rounded(d, (x, H - 130, x + sw, H - 44), 10, fill=col[:3] + (210,))
        d.text((x + sw / 2, H - 108), SHORT.get(tk, tk), font=g.font("Bold", 17), fill=(255, 255, 255, 255), anchor="mt")
        if v is not None:
            d.text((x + sw / 2, H - 80), f"{v:+.1f}%", font=g.font("Black", 22, True), fill=(255, 255, 255, 255), anchor="mt")
        x += sw + 8
    g.save_jpeg(img, out)
    return out


def make_cover(out: Path, size: int = 3000, subtitle: str | None = None) -> Path:
    """Square show/episode artwork (Spotify/Apple: 1400-3000 px, RGB JPEG)."""
    S = size
    img = g.background(S, S)
    d = g.Draw(img)
    k = S / 3000
    # rising bars motif
    bars = [0.30, 0.42, 0.36, 0.55, 0.48, 0.66, 0.60, 0.78]
    bw = 190 * k
    x0 = S / 2 - (len(bars) * bw + (len(bars) - 1) * 50 * k) / 2
    for i, h in enumerate(bars):
        x = x0 + i * (bw + 50 * k)
        col = g.UP if i in (3, 5, 7) else g.ACCENT
        g.rounded(d, (x, 2350 * k - h * 1100 * k, x + bw, 2350 * k), int(28 * k), fill=col[:3] + (200,))
    d.text((S / 2, 420 * k), "DAILY", font=g.font("Bold", int(190 * k), True), fill=g.ACCENT, anchor="mt")
    d.text((S / 2, 640 * k), "MARKET", font=g.font("Black", int(430 * k), True), fill=g.TEXT, anchor="mt")
    d.text((S / 2, 1060 * k), "BRIEF", font=g.font("Black", int(430 * k), True), fill=g.TEXT, anchor="mt")
    d.text((S / 2, 2520 * k), subtitle or "11 sectors · markets · geopolitics", font=g.font("SemiBold", int(120 * k)),
           fill=g.MUTED, anchor="mt")
    d.text((S / 2, 2720 * k), "ACTIVE THINKING", font=g.font("Bold", int(110 * k)), fill=g.TEXT, anchor="mt")
    g.save_jpeg(img, out, max_bytes=1_800_000)
    return out
