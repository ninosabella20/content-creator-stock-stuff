"""Animated scenes, one per script segment. Coordinates are designed for 1920x1080.

A scene draws onto a transparent layer for a local time t (seconds since the scene
started). Frames whose animation state is identical are cached, so long static
stretches cost almost nothing to render.
"""
from __future__ import annotations

from datetime import date

from PIL import Image, ImageDraw

from ..data.market_brief import SECTOR_NAMES
from . import charts
from . import graphics as g

W, H = 1920, 1080
TOP, BOTTOM = 130, 840          # content area (chrome + subtitles live outside it)


class Scene:
    label = ""

    def __init__(self, seg: dict, episode: dict, duration: float, index_label: str = ""):
        self.seg = seg
        self.ep = episode
        self.duration = max(0.5, duration)
        self.index_label = index_label
        self._cache_key = None
        self._cache_img: Image.Image | None = None
        self.points = (seg.get("on_screen") or {}).get("key_points") or []

    # timings of staggered key points: spread across the first ~70% of the scene
    def point_time(self, i: int, n: int) -> float:
        return min(self.duration * (0.22 + 0.5 * i / max(1, n)), 2.5 + 4.0 * i)

    def state(self, t: float) -> tuple:
        """Animation progress values; equal state => identical frame."""
        return (round(min(t, self.anim_end()), 2) if t < self.anim_end() else -1,
                tuple(round(g.clamp01((t - self.point_time(i, len(self.points))) / 0.6), 2)
                      for i in range(len(self.points))))

    def anim_end(self) -> float:
        return 3.2

    def render(self, t: float) -> Image.Image:
        key = self.state(t)
        if key == self._cache_key and self._cache_img is not None:
            return self._cache_img
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        self.draw(img, t)
        self._cache_key, self._cache_img = key, img
        return img

    def draw(self, img: Image.Image, t: float) -> None:
        raise NotImplementedError

    # -- shared pieces ------------------------------------------------------------------
    def draw_points(self, img: Image.Image, t: float, box, size: int = 30, bullet=g.ACCENT,
                    max_lines: int = 2) -> None:
        d = g.Draw(img)
        x0, y0, x1, _ = box
        f = g.font("Medium", size)
        y = y0
        n = len(self.points)
        for i, p in enumerate(self.points):
            a = g.appear(t, self.point_time(i, n), 0.6)
            if a <= 0:
                continue
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ld = g.Draw(layer)
            dx = int((1 - a) * 30)
            ld.rounded_rectangle((x0 + dx, y + 10, x0 + dx + 10, y + 10 + size * 0.9), 4, fill=bullet)
            for k, line in enumerate(g.wrap(ld, p, f, x1 - x0 - 40, max_lines)):
                ld.text((x0 + 30 + dx, y + k * (size + 10)), line, font=f, fill=g.TEXT)
            img.alpha_composite(g.with_alpha(layer, a))
            y += (size + 10) * len(g.wrap(d, p, f, x1 - x0 - 40, max_lines)) + 22


# ===================================================================================== intro
class IntroScene(Scene):
    def anim_end(self) -> float:
        return 3.4

    def draw(self, img, t):
        d = g.Draw(img)
        a = g.appear(t, 0.0, 0.9)
        f_big = g.font("Black", 150, True)
        brand = "MARKET BRIEF"
        bw = g.text_w(d, brand, f_big)
        y = 230 + int((1 - a) * 40)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = g.Draw(layer)
        ld.text(((W - bw) / 2, y), brand, font=f_big, fill=g.TEXT)
        f_daily = g.font("Bold", 54, True)
        dw = g.text_w(ld, "D A I L Y", f_daily)
        ld.text(((W - dw) / 2, y + 185), "D A I L Y", font=f_daily, fill=g.ACCENT)
        img.alpha_composite(g.with_alpha(layer, a))
        line = g.appear(t, 0.3, 0.9)
        d.rectangle((W / 2 - 260 * line, y + 268, W / 2 + 260 * line, y + 272), fill=g.ACCENT)
        ad = g.appear(t, 0.7, 0.7)
        if ad > 0:
            dt = date.fromisoformat(self.ep["date"])
            txt = dt.strftime("%A, %B ") + str(dt.day) + dt.strftime(", %Y")
            f = g.font("Medium", 40)
            d.text((W / 2, y + 320), txt, font=f, fill=g.MUTED[:3] + (int(255 * ad),), anchor="mt")
        # stat tiles
        mk = next((s for s in self.ep["segments"] if s["type"] == "market_overview"), {})
        sp = (mk.get("data") or {}).get("sp500") or {}
        tiles = [("S&P 500 · day", sp.get("day")), ("Week", sp.get("week")), ("Year to date", sp.get("ytd"))]
        tw, th, gap = 360, 130, 40
        x = (W - (3 * tw + 2 * gap)) / 2
        for i, (lab, v) in enumerate(tiles):
            p = g.appear(t, 1.2 + 0.2 * i, 0.7)
            if p <= 0 or v is None:
                x += tw + gap
                continue
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            box = (x, 700 + (1 - p) * 30, x + tw, 700 + th + (1 - p) * 30)
            g.panel(layer, box, 18, shadow=False)
            ld = g.Draw(layer)
            ld.text((box[0] + 26, box[1] + 22), lab.upper(), font=g.font("SemiBold", 22), fill=g.MUTED)
            ld.text((box[0] + 26, box[1] + 58), f"{v * g.ease_out(p):+.2f}%", font=g.font("Bold", 52, True),
                    fill=g.move_color(v))
            img.alpha_composite(g.with_alpha(layer, p))
            x += tw + gap


# ============================================================================ market overview
class MarketScene(Scene):
    def anim_end(self) -> float:
        return 3.5

    def heat_time(self) -> float:
        return self.duration * 0.55 if self.duration > 25 else 1e9

    def state(self, t):
        base = super().state(t)
        ht = self.heat_time()
        return base + (round(min(1.0, max(0.0, (t - ht) / 0.8)), 2),)

    def draw(self, img, t):
        d = g.Draw(img)
        data = self.seg.get("data") or {}
        sp = data.get("sp500") or {}
        head = (self.seg.get("on_screen") or {}).get("headline") or "Market Overview"
        a = g.appear(t, 0.0, 0.6)
        d.text((80, TOP + 10 - (1 - a) * 20), head, font=g.font("Bold", 50, True), fill=g.TEXT[:3] + (int(255 * a),))
        d.text((80, TOP + 78), f"Session: {data.get('session_label', '')}", font=g.font("Medium", 26),
               fill=g.MUTED[:3] + (int(255 * a),))
        # left: S&P 500 card
        p = g.appear(t, 0.3, 0.8)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        box = (80, TOP + 140, 640, BOTTOM)
        g.panel(layer, box)
        ld = g.Draw(layer)
        ld.text((box[0] + 34, box[1] + 30), "S&P 500", font=g.font("Bold", 34, True), fill=g.TEXT)
        if sp.get("last"):
            ld.text((box[2] - 34, box[1] + 36), f"SPY {sp['last']:,.2f}", font=g.font("Medium", 24),
                    fill=g.MUTED, anchor="ra")
        day = sp.get("day")
        if day is not None:
            ld.text((box[0] + 34, box[1] + 90), f"{day * g.ease_out(p):+.2f}%", font=g.font("Black", 96, True),
                    fill=g.move_color(day))
            ld.text((box[0] + 38, box[1] + 205), "on the day", font=g.font("Medium", 26), fill=g.MUTED)
        charts.returns_rows(layer, (box[0] + 34, box[1] + 260, box[2] - 34, box[3] - 30),
                            [("Week", sp.get("week")), ("Month", sp.get("month")),
                             ("YTD", sp.get("ytd")), ("1 Year", sp.get("y1"))], g.appear(t, 0.8, 1.6))
        img.alpha_composite(g.with_alpha(layer, p))
        # right: sector bars, then the multi-period heatmap
        rows = [(tk, (r or {}).get("day")) for tk, r in (data.get("sectors") or {}).items()]
        hp = min(1.0, max(0.0, (t - self.heat_time()) / 0.8))
        rbox = (700, TOP + 140, 1840, BOTTOM)
        if hp < 1:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            g.panel(layer, rbox)
            charts.sector_bars(layer, (rbox[0] + 40, rbox[1] + 30, rbox[2] - 40, rbox[3] - 30), rows,
                               g.appear(t, 0.6, 2.4),
                               highlight=set(data.get("leaders", [])[:1] + data.get("laggards", [])[:1]),
                               title="SECTORS · DAILY CHANGE")
            img.alpha_composite(g.with_alpha(layer, g.appear(t, 0.4, 0.7) * (1 - hp)))
        if hp > 0:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            g.panel(layer, rbox)
            g.Draw(layer).text((rbox[0] + 40, rbox[1] + 30), "PERFORMANCE BY PERIOD",
                                       font=g.font("SemiBold", 26), fill=g.MUTED)
            layer.alpha_composite(self._heat(rbox), (rbox[0] + 30, rbox[1] + 75))
            img.alpha_composite(g.with_alpha(layer, hp))

    _heat_img = None

    def _heat(self, rbox):
        if self._heat_img is None:
            data = self.seg.get("data") or {}
            self._heat_img = charts.period_heatmap(data.get("sectors") or {}, list(SECTOR_NAMES),
                                                   rbox[2] - rbox[0] - 60, rbox[3] - rbox[1] - 100)
        return self._heat_img


# ===================================================================================== sector
class SectorScene(Scene):
    def anim_end(self) -> float:
        return 3.0

    def draw(self, img, t):
        d = g.Draw(img)
        seg, data = self.seg, self.seg.get("data") or {}
        perf = data.get("perf") or {}
        mk = next((s for s in self.ep["segments"] if s["type"] == "market_overview"), {})
        all_rows = [(tk, (r or {}).get("day")) for tk, r in ((mk.get("data") or {}).get("sectors") or {}).items()]

        # ---- left: sector performance card
        a = g.appear(t, 0.0, 0.7)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        box = (70, TOP, 690, BOTTOM)
        g.panel(layer, box)
        ld = g.Draw(layer)
        ld.text((box[0] + 36, box[1] + 30), self.index_label, font=g.font("SemiBold", 24), fill=g.ACCENT)
        f_name = g.fit_font(ld, seg["title"], "Bold", box[2] - box[0] - 72, 60, 36, max_lines=2)
        y = box[1] + 72
        for line in g.wrap(ld, seg["title"], f_name, box[2] - box[0] - 72, 2):
            ld.text((box[0] + 36, y), line, font=f_name, fill=g.TEXT)
            y += f_name.size + 8
        g.chip(ld, (box[0] + 36, y + 10), seg["sector"] + " ETF", g.font("SemiBold", 22), fg=g.TEXT)
        day = perf.get("day")
        if day is not None:
            p = g.appear(t, 0.3, 1.0)
            ld.text((box[0] + 34, y + 70), f"{day * p:+.2f}%", font=g.font("Black", 88, True), fill=g.move_color(day))
            rank = data.get("rank_today")
            if rank:
                ld.text((box[0] + 38, y + 178), f"{g.ordinal(rank)} of 11 sectors today",
                        font=g.font("Medium", 24), fill=g.MUTED)
        charts.rank_strip(layer, (box[0] + 36, y + 225, box[2] - 36, y + 320), all_rows, seg["sector"])
        charts.returns_rows(layer, (box[0] + 36, y + 350, box[2] - 36, box[3] - 24),
                            [("Week", perf.get("week")), ("Month", perf.get("month")),
                             ("YTD", perf.get("ytd")), ("1 Year", perf.get("y1"))], g.appear(t, 0.8, 1.6))
        img.alpha_composite(g.with_alpha(layer, a))

        # ---- right: story card
        b = g.appear(t, 0.25, 0.7)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        rb = (730 + int((1 - b) * 40), TOP, 1850 + int((1 - b) * 40), BOTTOM)
        g.panel(layer, rb)
        ld = g.Draw(layer)
        st = seg.get("story")
        x, y = rb[0] + 44, rb[1] + 34
        if st:
            ld.text((x, y), "TOP STORY", font=g.font("Bold", 24), fill=g.GOLD)
            nx = g.chip(ld, (x + 160, y - 6), st.get("company") or st.get("ticker") or "", g.font("SemiBold", 22),
                        fg=g.TEXT, bg=(255, 255, 255, 30))
            if st.get("ticker"):
                g.chip(ld, (nx + 12, y - 6), st["ticker"], g.font("Bold", 22), fg=g.ACCENT,
                       bg=(56, 189, 248, 40))
            head = (seg.get("on_screen") or {}).get("headline") or st.get("headline", "")
            f_h = g.fit_font(ld, head, "Bold", rb[2] - rb[0] - 88, 52, 34, max_lines=3)
            y += 64
            for line in g.wrap(ld, head, f_h, rb[2] - rb[0] - 88, 3):
                ld.text((x, y), line, font=f_h, fill=g.TEXT)
                y += f_h.size + 12
            y += 14
            ld.line([(x, y), (rb[2] - 44, y)], fill=g.PANEL_LINE, width=2)
            pts_y = y + 28
            # footer: source + data chips (from Market Brief data only)
            fy = rb[3] - 64
            src = st.get("source") or ""
            pub = st.get("published") or ""
            if pub:
                try:
                    pd = date.fromisoformat(pub)
                    pub = f"{pd.day} {pd.strftime('%b %Y')}"
                except ValueError:
                    pass
            ld.text((x, fy + 10), f"Source: {src}" + (f" · {pub}" if pub else ""), font=g.font("Medium", 24),
                    fill=g.MUTED)
            cx = rb[2] - 44
            chips = []
            if st.get("today_return") is not None:
                chips.append((f"{st.get('ticker') or 'Stock'} today {st['today_return']:+.1f}%",
                              g.move_color(st["today_return"])))
            elif st.get("week_return") is not None:
                chips.append((f"1W {st['week_return']:+.1f}%", g.move_color(st["week_return"])))
            cons = st.get("consensus") or {}
            if cons.get("rating"):
                chips.append((f"Analysts: {cons['rating']}" + (f" ({cons['analysts']})" if cons.get("analysts") else ""),
                              g.TEXT))
            f_c = g.font("SemiBold", 22)
            for txt, col in reversed(chips):
                w = g.text_w(ld, txt, f_c) + 32
                g.chip(ld, (cx - w, fy), txt, f_c, fg=col, bg=(255, 255, 255, 22))
                cx -= w + 12
        else:
            ld.text((x, y), "SECTOR CONTEXT", font=g.font("Bold", 24), fill=g.MUTED)
            f_h = g.font("Bold", 48, True)
            y += 64
            for line in g.wrap(ld, "No standout verified development today", f_h, rb[2] - rb[0] - 88, 2):
                ld.text((x, y), line, font=f_h, fill=g.TEXT)
                y += f_h.size + 12
            pts_y = y + 30
        img.alpha_composite(g.with_alpha(layer, b))
        self.draw_points(img, t, (x, pts_y, rb[2] - 44, rb[3] - 90), size=34, max_lines=3)


# ================================================================================ geopolitics
class GeoScene(Scene):
    def anim_end(self) -> float:
        return 4.0

    _map = None

    def draw(self, img, t):
        d = g.Draw(img)
        countries = self.seg.get("countries") or []
        head = (self.seg.get("on_screen") or {}).get("headline") or "Geopolitical Intelligence"
        a = g.appear(t, 0.0, 0.6)
        d.text((80, TOP - 4), "GEOPOLITICS", font=g.font("Bold", 24), fill=g.GOLD[:3] + (int(255 * a),))
        d.text((80, TOP + 28), head, font=g.font("Bold", 44, True), fill=g.TEXT[:3] + (int(255 * a),))
        mw, mh = 1040, 540
        mx, my = 60, TOP + 110
        if self._map is None:
            self._map = charts.world_map(mw, mh, [c["code"] for c in countries])
        img.alpha_composite(g.with_alpha(self._map, g.appear(t, 0.2, 0.9)), (mx, my))
        us = charts.project(*charts.CENTROIDS["us"], mw, mh)
        for i, c in enumerate(countries):
            if c["code"] not in charts.CENTROIDS:
                continue
            p = g.appear(t, 0.9 + 0.35 * i, 1.2)
            if p <= 0:
                continue
            dst = charts.project(*charts.CENTROIDS[c["code"]], mw, mh)
            pts = g.arc_points((mx + us[0], my + us[1]), (mx + dst[0], my + dst[1]), 0.22)
            k = max(2, int(len(pts) * p))
            d.line(pts[:k], fill=g.GOLD, width=4, joint="curve")
            if p > 0.95:
                ex, ey = pts[-1]
                d.ellipse((ex - 9, ey - 9, ex + 9, ey + 9), fill=g.GOLD, outline=(255, 255, 255, 230), width=2)
        ux, uy = mx + us[0], my + us[1]
        d.ellipse((ux - 10, uy - 10, ux + 10, uy + 10), fill=g.ACCENT, outline=(255, 255, 255, 230), width=2)
        # country cards
        cx0, cx1 = 1130, 1860
        n = max(1, min(3, len(countries)))
        ch = min(220, (BOTTOM - TOP - 20) // n - 16)
        y = TOP + 10
        for i, c in enumerate(countries[:3]):
            p = g.appear(t, 0.6 + 0.35 * i, 0.7)
            if p <= 0:
                y += ch + 16
                continue
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            box = (cx0 + int((1 - p) * 40), y, cx1 + int((1 - p) * 40), y + ch)
            g.panel(layer, box, 18)
            ld = g.Draw(layer)
            fl = g.flag(c["code"], 48)
            tx = box[0] + 26
            if fl:
                layer.alpha_composite(fl, (box[0] + 26, box[1] + 24))
                tx += fl.width + 18
            ld.text((tx, box[1] + 26), c["name"], font=g.font("Bold", 34, True), fill=g.TEXT)
            if c.get("relation"):
                rel = c["relation"]
                col = {"Ally": g.UP, "Rival": g.DOWN, "Adversary": g.DOWN}.get(rel, g.GOLD)
                g.chip(ld, (box[2] - 26 - g.text_w(ld, rel, g.font("Bold", 20)) - 28, box[1] + 26), rel,
                       g.font("Bold", 20), fg=col, bg=col[:3] + (40,))
            body = c.get("week_line") or c.get("lever") or ""
            fy = box[1] + 88
            for line in g.wrap(ld, body, g.font("Regular", 24), box[2] - box[0] - 52, max(1, (ch - 100) // 32)):
                ld.text((box[0] + 26, fy), line, font=g.font("Regular", 24), fill=g.MUTED)
                fy += 32
            img.alpha_composite(g.with_alpha(layer, p))
            y += ch + 16
        if not countries:
            self.draw_points(img, t, (1140, TOP + 20, 1850, BOTTOM), size=30)


# ==================================================================================== closing
class ClosingScene(Scene):
    END_CARD = 6.0

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.points = (self.seg.get("on_screen") or {}).get("watch") or []

    def anim_end(self) -> float:
        return 2.5

    def state(self, t):
        ec = self.duration - self.END_CARD
        return super().state(t) + (round(min(1.0, max(0.0, (t - ec) / 0.8)), 2),)

    def draw(self, img, t):
        d = g.Draw(img)
        os_ = self.seg.get("on_screen") or {}
        ec = max(self.duration * 0.5, self.duration - self.END_CARD)
        e = min(1.0, max(0.0, (t - ec) / 0.8))
        if e < 1:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ld = g.Draw(layer)
            a = g.appear(t, 0, 0.7)
            ld.text((120, TOP + 20), "THE TAKEAWAY", font=g.font("Bold", 28), fill=g.GOLD)
            f = g.fit_font(ld, os_.get("takeaway") or "", "Bold", 1680, 64, 40, max_lines=3)
            y = TOP + 80
            for line in g.wrap(ld, os_.get("takeaway") or "", f, 1680, 3):
                ld.text((120, y + (1 - a) * 20), line, font=f, fill=g.TEXT)
                y += f.size + 14
            watch = os_.get("watch") or []
            if watch:
                y += 40
                ld.text((120, y), "WHAT TO WATCH", font=g.font("Bold", 28), fill=g.ACCENT)
                layer2 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                self.draw_points(layer2, t, (120, y + 60, 1800, BOTTOM), size=34, max_lines=1)
                layer.alpha_composite(layer2)
            img.alpha_composite(g.with_alpha(layer, a * (1 - e)))
        if e > 0:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ld = g.Draw(layer)
            f_big = g.font("Black", 130, True)
            ld.text((W / 2, 300), "MARKET BRIEF", font=f_big, fill=g.TEXT, anchor="mt")
            ld.text((W / 2, 470), "D A I L Y", font=g.font("Bold", 50, True), fill=g.ACCENT, anchor="mt")
            ld.text((W / 2, 580), "Back after the next market close", font=g.font("Medium", 40),
                    fill=g.TEXT, anchor="mt")
            ld.text((W / 2, 650), "Sources for every story are in the description", font=g.font("Regular", 30),
                    fill=g.MUTED, anchor="mt")
            ld.text((W / 2, 760), "News and commentary for information only. Not investment advice.",
                    font=g.font("Regular", 26), fill=g.DIM, anchor="mt")
            img.alpha_composite(g.with_alpha(layer, e))


def make_scene(seg: dict, episode: dict, duration: float) -> Scene:
    typ = seg["type"]
    if typ == "intro":
        return IntroScene(seg, episode, duration)
    if typ == "market_overview":
        return MarketScene(seg, episode, duration)
    if typ == "sector":
        order = [s["sector"] for s in episode["segments"] if s["type"] == "sector"]
        i = order.index(seg["sector"]) + 1
        return SectorScene(seg, episode, duration, f"SECTOR {i:02d} / {len(order)}")
    if typ == "geopolitics":
        return GeoScene(seg, episode, duration)
    if typ == "closing":
        return ClosingScene(seg, episode, duration)
    raise ValueError(f"unknown segment type {typ}")
