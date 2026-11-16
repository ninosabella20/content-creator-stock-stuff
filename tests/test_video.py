import json
import subprocess

import pytest
from PIL import Image

from src.audio import audio_processor as ap
from src.audio.narration import build_timeline, voice_segments
from src.publishing import metadata as md
from src.video.renderer import render_preview_frames, render_video
from src.video.subtitles import build_cues, write_srt
from src.video.thumbnail import make_cover, make_thumbnail

from .conftest import ToneTTS


@pytest.fixture
def produced(template_episode, settings, tmp_path):
    segs = [s for s in template_episode["segments"]]
    audios = voice_segments(segs, ToneTTS(wps=3), settings)   # fast fake voice
    full, tl = build_timeline(segs, audios, 0.5)
    wav = tmp_path / "n.wav"
    ap.write_wav(wav, full, 24000)
    return template_episode, tl, wav


def test_subtitles_cover_narration_without_overlap(produced, tmp_path):
    ep, tl, _ = produced
    cues = build_cues(tl)
    assert cues[0]["start"] >= 0 and cues[-1]["end"] <= tl["duration"]
    for a, b in zip(cues, cues[1:]):
        assert a["end"] <= b["start"] + 1e-6
    assert all(len(c["text"]) <= 110 for c in cues)
    write_srt(cues, tmp_path / "x.srt")
    txt = (tmp_path / "x.srt").read_text()
    assert txt.startswith("1\n00:00:") and " --> " in txt


def test_every_scene_renders(produced, tmp_path):
    ep, tl, _ = produced
    times = [s["start"] + 1.0 for s in tl["segments"]] + [tl["duration"] - 0.5]
    frames = render_preview_frames(ep, tl, tmp_path, times)
    assert len(frames) == len(times)
    assert Image.open(frames[0]).size == (1920, 1080)


def test_short_video_render(produced, settings, tmp_path):
    ep, tl, wav = produced
    out = tmp_path / "v.mp4"
    info = render_video(ep, tl, wav, out, settings, max_seconds=3, workers=2)
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height",
                        "-of", "json", str(out)], capture_output=True, text=True, check=True)
    streams = {s["codec_name"]: s for s in json.loads(p.stdout)["streams"]}
    assert streams["h264"]["width"] == 1920 and streams["h264"]["height"] == 1080
    assert "aac" in streams and abs(info["seconds"] - 3) < 0.1


def test_thumbnail_and_cover(template_episode, tmp_path):
    t = make_thumbnail(template_episode, tmp_path / "t.jpg")
    c = make_cover(tmp_path / "c.jpg")
    assert Image.open(t).size == (1280, 720) and t.stat().st_size < 2_000_000
    assert Image.open(c).size == (3000, 3000)


def test_metadata_has_sources_disclaimer_and_limits(produced, settings):
    ep, tl, _ = produced
    title = md.youtube_title(ep)
    desc = md.youtube_description(ep, tl, settings)
    assert len(title) <= 100 and title.startswith("Market Brief Daily | 15 Nov 2026")
    assert len(desc.encode()) <= 5000 and "<" not in desc and ">" not in desc
    for s in ep["segments"]:
        if s.get("story"):
            assert s["story"]["url"] in desc
    assert "not constitute professional financial" in desc and ep["episode_id"] in desc
    assert desc.count("\n00:00 ") == 1          # chapters start at 00:00
    pd = md.podcast_description(ep, tl, settings)
    assert len(pd) <= 4000 and "Sources" in pd
