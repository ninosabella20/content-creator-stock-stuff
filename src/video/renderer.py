"""Render the episode video: Pillow frames piped straight into FFmpeg (H.264 + AAC).

Layers per frame (back to front): drifting background, scene (with cross-fade
transitions), top bar + progress line, subtitles, scrolling sector ticker.
"""
from __future__ import annotations

import logging
import math
import subprocess
import time
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw

from ..config import Settings
from . import graphics as g
from .scenes import H, W, make_scene
from .subtitles import build_cues, split_lines

log = logging.getLogger(__name__)

SEG_LABEL = {"intro": "TODAY", "market_overview": "MARKET OVERVIEW", "geopolitics": "GEOPOLITICS",
             "closing": "THE TAKEAWAY"}


class Chrome:
    """Top bar, progress line and ticker tape."""

    def __init__(self, episode: dict):
        self.ep = episode
        self._top: dict[str, Image.Image] = {}
        self.ticker = self._ticker_strip()

    def top_bar(self, seg: dict, label: str) -> Image.Image:
        key = seg["id"]
        if key in self._top:
            return self._top[key]
        im = Image.new("RGBA", (W, 96), (0, 0, 0, 0))
        d = g.Draw(im)
        d.rectangle((60, 34, 72, 62), fill=g.ACCENT)
        d.text((86, 48), "MARKET BRIEF DAILY", font=g.font("Bold", 26), fill=g.TEXT, anchor="lm")
        dt = date.fromisoformat(self.ep["date"])
        right = f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b %Y')}"
        d.text((W - 60, 48), right, font=g.font("Medium", 24), fill=g.MUTED, anchor="rm")
        rw = g.text_w(d, right, g.font("Medium", 24))
        if label:
            f = g.font("SemiBold", 22)
            lw = g.text_w(d, label, f)
            g.chip(d, (W - 60 - rw - 30 - lw - 32, 30), label, f, fg=g.TEXT, bg=(56, 189, 248, 45))
        self._top[key] = im
        return im

    def _ticker_strip(self) -> Image.Image:
        mk = next((s for s in self.ep["segments"] if s["type"] == "market_overview"), {})
        data = mk.get("data") or {}
        items = []
        sp = data.get("sp500") or {}
        if sp.get("day") is not None:
            items.append(("S&P 500", sp["day"]))
        for tk, r in (data.get("sectors") or {}).items():
            if r and r.get("day") is not None:
                items.append((f"{tk}  {r.get('name', '')}", r["day"]))
        f1, f2 = g.font("SemiBold", 24), g.font("Bold", 24)
        tmp = g.Draw(Image.new("RGBA", (10, 10)))
        widths = [g.text_w(tmp, a, f1) + 14 + g.text_w(tmp, f"{v:+.2f}%", f2) + 70 for a, v in items]
        unit = max(W, sum(widths) or W)
        im = Image.new("RGBA", (unit * 2, 56), (6, 10, 22, 235))
        d = g.Draw(im)
        for rep in range(2):
            x = rep * unit + 30
            for (a, v), w in zip(items, widths):
                d.text((x, 28), a, font=f1, fill=g.MUTED, anchor="lm")
                xa = x + g.text_w(d, a, f1) + 14
                d.text((xa, 28), f"{v:+.2f}%", font=f2, fill=g.move_color(v), anchor="lm")
                d.ellipse((x + w - 38, 25, x + w - 32, 31), fill=g.DIM)
                x += w
        d.line([(0, 0), (unit * 2, 0)], fill=(56, 189, 248, 120), width=2)
        im.info["unit"] = unit
        return im


class SubtitleCache:
    def __init__(self, cues: list[dict]):
        self.cues = cues
        self._i = 0
        self._cache: dict[int, Image.Image] = {}

    def at(self, t: float) -> Image.Image | None:
        cues = self.cues
        while self._i < len(cues) - 1 and t >= cues[self._i + 1]["start"]:
            self._i += 1
        while self._i > 0 and t < cues[self._i]["start"]:
            self._i -= 1
        if not cues:
            return None
        c = cues[self._i]
        if not c["start"] <= t < c["end"] + 0.15:
            return None
        if self._i not in self._cache:
            self._cache = {self._i: self._render(c["text"])}
        return self._cache[self._i]

    @staticmethod
    def _render(text: str) -> Image.Image:
        f = g.font("SemiBold", 40)
        lines = split_lines(text, 52)
        tmp = g.Draw(Image.new("RGBA", (10, 10)))
        tw = max(g.text_w(tmp, ln, f) for ln in lines)
        lh = 52
        im = Image.new("RGBA", (tw + 56, lh * len(lines) + 28), (0, 0, 0, 0))
        d = g.Draw(im)
        g.rounded(d, (0, 0, im.width - 1, im.height - 1), 14, fill=(4, 8, 18, 200))
        for i, ln in enumerate(lines):
            d.text((im.width / 2, 14 + lh * i + lh / 2), ln, font=f, fill=(255, 255, 255, 255), anchor="mm")
        return im


def plan_scenes(episode: dict, timeline: dict) -> list[tuple[float, float, object]]:
    by_id = {s["id"]: s for s in episode["segments"]}
    segs = timeline["segments"]
    out = []
    for i, ts in enumerate(segs):
        start = 0.0 if i == 0 else ts["start"]
        end = segs[i + 1]["start"] if i + 1 < len(segs) else timeline["duration"]
        out.append((start, end, make_scene(by_id[ts["id"]], episode, end - start)))
    return out


class FrameMaker:
    """Builds every frame of the episode; deterministic, so slices can render in parallel."""

    def __init__(self, episode: dict, timeline: dict, settings: Settings, duration: float):
        s = settings.get_path
        self.tr = float(s("video.transition_seconds", 0.5))
        self.duration = duration
        self.scenes = plan_scenes(episode, timeline)
        self.chrome = Chrome(episode)
        self.subs = SubtitleCache(build_cues(timeline)) if bool(s("video.burn_subtitles", True)) else None
        self.bg_big = g.background(W + 160, H + 90)
        self.unit = self.chrome.ticker.info["unit"]
        self.si = 0
        self.labels: dict[str, str] = {}

    def frame(self, t: float) -> Image.Image:
        scenes = self.scenes
        while self.si < len(scenes) - 1 and t >= scenes[self.si + 1][0]:
            self.si += 1
        while self.si > 0 and t < scenes[self.si][0]:
            self.si -= 1
        start, _, scene = scenes[self.si]
        lt = t - start
        ox = int(80 + 70 * math.sin(t / 23.0))          # slow background drift
        oy = int(45 + 38 * math.cos(t / 31.0))
        frame = self.bg_big.crop((ox, oy, ox + W, oy + H))
        layer = scene.render(lt)
        if self.si > 0 and lt < self.tr:                 # cross-fade from the previous scene
            prev = scenes[self.si - 1][2]
            layer = Image.blend(prev.render(prev.duration), layer, g.ease_in_out(lt / self.tr))
        frame.alpha_composite(layer)
        seg = scene.seg
        if seg["id"] not in self.labels:
            self.labels[seg["id"]] = SEG_LABEL.get(seg["type"]) or scene.index_label
        frame.alpha_composite(self.chrome.top_bar(seg, self.labels[seg["id"]]), (0, 0))
        d = g.Draw(frame)
        d.rectangle((0, 96, W, 99), fill=(30, 40, 66, 255))
        d.rectangle((0, 96, int(W * min(1.0, t / self.duration)), 99), fill=g.ACCENT)
        if self.subs is not None:
            im = self.subs.at(t)
            if im is not None:
                frame.alpha_composite(im, ((W - im.width) // 2, 1004 - im.height))
        off = int(t * 70) % self.unit
        frame.alpha_composite(self.chrome.ticker.crop((off, 0, off + W, 56)), (0, H - 56))
        return frame


def _encode_cmd(settings: Settings, fps: int, out: Path) -> list[str]:
    s = settings.get_path
    out_w, out_h = int(s("video.width", 1920)), int(s("video.height", 1080))
    vf = [] if (out_w, out_h) == (W, H) else ["-vf", f"scale={out_w}:{out_h}:flags=lanczos"]
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(fps), "-i", "pipe:0", *vf,
            "-c:v", "libx264", "-preset", s("video.preset", "veryfast"), "-crf", str(s("video.crf", 21)),
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-g", str(fps * 2), "-threads", "2",
            "-an", str(out)]


def _render_slice(args) -> tuple[int, float]:
    episode, timeline, settings, duration, fps, f0, f1, out = args
    fm = FrameMaker(episode, timeline, settings, duration)
    proc = subprocess.Popen(_encode_cmd(settings, fps, Path(out)), stdin=subprocess.PIPE)
    t0 = time.time()
    try:
        for fi in range(f0, f1):
            proc.stdin.write(fm.frame(fi / fps).tobytes())
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg exited with {rc} for frames {f0}-{f1}")
    return f1 - f0, time.time() - t0


def render_video(episode: dict, timeline: dict, audio: Path, out_mp4: Path, settings: Settings,
                 max_seconds: float | None = None, workers: int | None = None) -> dict:
    import multiprocessing as mp
    import os
    s = settings.get_path
    fps = int(s("video.fps", 30))
    duration = timeline["duration"] if not max_seconds else min(max_seconds, timeline["duration"])
    nframes = int(math.ceil(duration * fps))
    workers = workers or max(1, min(int(os.environ.get("RENDER_WORKERS", 0)) or (os.cpu_count() or 2), 8))
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    work = out_mp4.parent / "_video_parts"
    work.mkdir(exist_ok=True)
    # slices aligned to 2-second GOPs so the parts join cleanly
    step = fps * 2
    per = max(step, -(-nframes // workers) // step * step)
    jobs, f0 = [], 0
    while f0 < nframes:
        f1 = min(nframes, f0 + per)
        jobs.append((episode, timeline, settings, duration, fps, f0, f1, str(work / f"part_{len(jobs):03d}.mp4")))
        f0 = f1
    t0 = time.time()
    log.info("rendering %d frames in %d parallel slices", nframes, len(jobs))
    if len(jobs) == 1:
        _render_slice(jobs[0])
    else:
        with mp.get_context("fork").Pool(len(jobs)) as pool:
            for n, sec in pool.imap_unordered(_render_slice, jobs):
                log.info("slice done: %d frames in %.0fs (%.1f fps)", n, sec, n / max(sec, 0.01))
    lst = work / "parts.txt"
    lst.write_text("".join(f"file '{Path(j[-1]).name}'\n" for j in jobs))
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{duration:.3f}",
                    "-movflags", "+faststart", str(out_mp4)], check=True)
    for j in jobs:
        Path(j[-1]).unlink(missing_ok=True)
    lst.unlink(missing_ok=True)
    work.rmdir()
    el = time.time() - t0
    log.info("video done: %.0f s of video in %.0f s", duration, el)
    return {"file": out_mp4.name, "seconds": round(duration, 2), "fps": fps,
            "resolution": f"{s('video.width', 1920)}x{s('video.height', 1080)}",
            "render_seconds": round(el, 1), "workers": len(jobs)}


def render_preview_frames(episode: dict, timeline: dict, out_dir: Path, times: list[float]) -> list[Path]:
    """Still frames for review (e.g. contact sheet), without encoding the whole video."""
    scenes = plan_scenes(episode, timeline)
    chrome = Chrome(episode)
    subs = SubtitleCache(build_cues(timeline))
    bg = g.background(W, H)
    paths = []
    for t in times:
        si = max(i for i, sc in enumerate(scenes) if sc[0] <= t)
        start, _, scene = scenes[si]
        frame = bg.copy()
        frame.alpha_composite(scene.render(t - start))
        frame.alpha_composite(chrome.top_bar(scene.seg, SEG_LABEL.get(scene.seg["type"]) or scene.index_label))
        im = subs.at(t)
        if im is not None:
            frame.alpha_composite(im, ((W - im.width) // 2, 1004 - im.height))
        frame.alpha_composite(chrome.ticker.crop((0, 0, W, 56)), (0, H - 56))
        p = out_dir / f"frame_{int(t):04d}.png"
        frame.convert("RGB").save(p)
        paths.append(p)
    return paths
