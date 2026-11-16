"""Visual theme and Pillow drawing primitives shared by video, thumbnail and cover art."""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import ROOT

FONTS = ROOT / "assets" / "fonts"
FLAGS = ROOT / "assets" / "flags"

# Palette (dark financial-news look)
BG_TOP = (9, 14, 28)
BG_BOTTOM = (16, 26, 50)
PANEL = (20, 30, 56, 235)
PANEL_LINE = (52, 70, 110, 255)
TEXT = (236, 241, 250, 255)
MUTED = (148, 163, 190, 255)
DIM = (96, 112, 140, 255)
ACCENT = (56, 189, 248, 255)      # sky blue
GOLD = (245, 189, 65, 255)
UP = (34, 197, 94, 255)
DOWN = (239, 68, 68, 255)
FLAT = (148, 163, 184, 255)


class Draw(ImageDraw.ImageDraw):
    """ImageDraw that alpha-BLENDS semi-transparent fills on RGBA images.

    Pillow's ImageDraw replaces pixels (including alpha) when the fill colour is
    translucent, which punches see-through holes into panels. Translucent operations
    are drawn on a scratch layer and composited instead."""

    def __init__(self, im: Image.Image):
        super().__init__(im)
        self._target = im

    def _translucent(self, kw) -> bool:
        for k in ("fill", "outline"):
            c = kw.get(k)
            if isinstance(c, tuple) and len(c) == 4 and c[3] < 255:
                return True
        return False

    def _blend(self, name, args, kw):
        layer = Image.new("RGBA", self._target.size, (0, 0, 0, 0))
        getattr(ImageDraw.ImageDraw(layer), name)(*args, **kw)
        self._target.alpha_composite(layer)

    def rectangle(self, *a, **k):
        return self._blend("rectangle", a, k) if self._translucent(k) else super().rectangle(*a, **k)

    def rounded_rectangle(self, *a, **k):
        return self._blend("rounded_rectangle", a, k) if self._translucent(k) else super().rounded_rectangle(*a, **k)

    def ellipse(self, *a, **k):
        return self._blend("ellipse", a, k) if self._translucent(k) else super().ellipse(*a, **k)

    def polygon(self, *a, **k):
        return self._blend("polygon", a, k) if self._translucent(k) else super().polygon(*a, **k)

    def line(self, *a, **k):
        return self._blend("line", a, k) if self._translucent(k) else super().line(*a, **k)

    def text(self, *a, **k):
        return self._blend("text", a, k) if self._translucent(k) else super().text(*a, **k)


def move_color(x: float | None) -> tuple:
    if x is None or abs(x) < 0.05:
        return FLAT
    return UP if x > 0 else DOWN


@lru_cache(maxsize=128)
def font(weight: str = "Regular", size: int = 32, display: bool = False) -> ImageFont.FreeTypeFont:
    name = f"InterDisplay-{weight}.otf" if display else f"Inter-{weight}.otf"
    path = FONTS / name
    if not path.exists():
        path = FONTS / "Inter-Regular.otf"
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default(size)


def text_w(draw: ImageDraw.ImageDraw, text: str, f) -> int:
    return int(draw.textlength(text, font=f))


def wrap(draw: ImageDraw.ImageDraw, text: str, f, max_w: int, max_lines: int = 3) -> list[str]:
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if text_w(draw, t, f) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and " ".join(lines) != " ".join(words):
        last = lines[-1]
        while last and text_w(draw, last + "…", f) > max_w:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        lines[-1] = last + "…"
    return lines


def fit_font(draw, text: str, weight: str, max_w: int, start: int, min_size: int = 20,
             display: bool = True, max_lines: int = 1):
    size = start
    while size > min_size:
        f = font(weight, size, display)
        if len(wrap(draw, text, f, max_w, 99)) <= max_lines:
            return f
        size -= 2
    return font(weight, min_size, display)


def rounded(draw: ImageDraw.ImageDraw, box, radius: int, fill=None, outline=None, width: int = 1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def chip(draw, xy, text: str, f, fg=TEXT, bg=(255, 255, 255, 28), pad=(16, 8), radius=10) -> int:
    x, y = xy
    w = text_w(draw, text, f)
    bb = draw.textbbox((0, 0), text, font=f)
    h = bb[3] - bb[1]
    rounded(draw, (x, y, x + w + 2 * pad[0], y + h + 2 * pad[1] + 4), radius, fill=bg)
    draw.text((x + pad[0], y + pad[1] - bb[1] + 2), text, font=f, fill=fg)
    return x + w + 2 * pad[0]


def shadow_layer(size, box, radius=24, blur=18, alpha=120) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = box
    d.rounded_rectangle((x0 + 6, y0 + 10, x1 + 6, y1 + 10), radius, fill=(0, 0, 0, alpha))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def panel(img: Image.Image, box, radius: int = 22, fill=PANEL, outline=PANEL_LINE, shadow=True):
    if shadow:
        img.alpha_composite(shadow_layer(img.size, box, radius))
    d = Draw(img)
    rounded(d, box, radius, fill=fill, outline=outline, width=2)


def background(w: int, h: int, grid: bool = True) -> Image.Image:
    """Dark vertical gradient with a faint chart grid and a soft glow."""
    img = Image.new("RGB", (w, h), BG_TOP)
    top, bot = BG_TOP, BG_BOTTOM
    grad = Image.linear_gradient("L").resize((w, h))
    img = Image.composite(Image.new("RGB", (w, h), bot), Image.new("RGB", (w, h), top), grad)
    img = img.convert("RGBA")
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((int(w * 0.55), int(-h * 0.35), int(w * 1.25), int(h * 0.55)), fill=(56, 120, 248, 60))
    gd.ellipse((int(-w * 0.2), int(h * 0.55), int(w * 0.35), int(h * 1.3)), fill=(20, 184, 166, 38))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(min(w, h) // 6)))
    if grid:
        d = Draw(img)
        step = max(40, w // 32)
        for x in range(0, w, step):
            d.line([(x, 0), (x, h)], fill=(255, 255, 255, 9))
        for y in range(0, h, step):
            d.line([(0, y), (w, y)], fill=(255, 255, 255, 9))
    return img


@lru_cache(maxsize=64)
def flag(code: str, height: int) -> Image.Image | None:
    p = FLAGS / f"{code.lower()}.png"
    if not p.exists():
        return None
    im = Image.open(p).convert("RGBA")
    w = int(im.width * height / im.height)
    im = im.resize((w, height), Image.LANCZOS)
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, height - 1), radius=max(3, height // 8), fill=255)
    im.putalpha(Image.fromarray(__import__("numpy").minimum(
        __import__("numpy").array(im.getchannel("A")), __import__("numpy").array(mask))))
    return im


# ----------------------------------------------------------------------- animation easing
def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_out(x: float) -> float:
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_in_out(x: float) -> float:
    x = clamp01(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def appear(t: float, start: float, dur: float = 0.6) -> float:
    """0..1 progress of an element that appears at `start` (seconds)."""
    return ease_out((t - start) / dur) if dur > 0 else float(t >= start)


def with_alpha(im: Image.Image, a: float) -> Image.Image:
    if a >= 0.999:
        return im
    out = im.copy()
    alpha = out.getchannel("A").point(lambda v: int(v * max(0.0, a)))
    out.putalpha(alpha)
    return out


def ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def arc_points(p0, p1, bend: float = 0.25, n: int = 48) -> list[tuple[float, float]]:
    (x0, y0), (x1, y1) = p0, p1
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy) or 1
    cx, cy = mx - dy / dist * dist * bend, my + dx / dist * dist * bend
    if cy > my:  # always bow upwards
        cx, cy = mx + dy / dist * dist * bend, my - dx / dist * dist * bend
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t ** 2 * x1
        y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t ** 2 * y1
        pts.append((x, y))
    return pts


def save_jpeg(img: Image.Image, path: Path, max_bytes: int = 2_000_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rgb = img.convert("RGB")
    for q in (92, 88, 84, 80, 75, 70):
        rgb.save(path, "JPEG", quality=q, optimize=True, progressive=True)
        if path.stat().st_size <= max_bytes:
            return
