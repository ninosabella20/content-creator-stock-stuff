import numpy as np
import pytest

from src.audio import audio_processor as ap
from src.audio.base import TTSError
from src.audio.narration import build_timeline, plan_chunks, voice_segments
from src.audio.text_normalize import normalize_for_tts

from .conftest import ToneTTS


def test_normalisation_for_speech():
    t = normalize_for_tts("The S&P 500 (SPY) fell -0.36%; Nvidia lost $1.6 trillion and C$102M in Q3 FY2026.")
    assert "S and P 500" in t and "fell 0.36 percent" in t
    assert "1.6 trillion dollars" in t and "102 million Canadian dollars" in t
    assert "third quarter fiscal 2026" in t and "(SPY)" not in t


def test_lexicon_override():
    assert "cap-LEE-tah" in normalize_for_tts("Caplyta data", {"Caplyta": "cap-LEE-tah"})


def test_align_boundaries_snaps_to_pauses():
    cuts = ap.align_boundaries([1, 1], 10.0, [(4.6, 5.4)], window=2)
    assert cuts == [pytest.approx(5.0)]
    assert ap.align_boundaries([1, 1, 1], 9.0, []) == [pytest.approx(3.0), pytest.approx(6.0)]


def test_silence_detection_and_trim():
    sr = 24000
    tone = (np.sin(np.arange(sr) * 0.05) * 8000).astype(np.int16)
    a = np.concatenate([np.zeros(sr // 2, np.int16), tone, np.zeros(sr // 2, np.int16), tone])
    sil = ap.find_silences(a, sr, min_len=0.2)
    assert any(abs(s - 1.5) < 0.05 for s, _ in sil)
    assert len(ap.trim_silence(a, sr)) < len(a)


def test_plan_chunks():
    segs = [{"narration": "word " * n} for n in (30, 150, 100, 100, 90)]
    assert plan_chunks(segs, 300) == [[0, 1, 2], [3, 4]]
    assert plan_chunks(segs, 0) == [[0], [1], [2], [3], [4]]


def _segs():
    return [{"id": f"s{i}", "type": "sector", "narration": "One two three four. Five six seven eight nine ten."}
            for i in range(4)]


def test_chunked_voice_is_split_back_into_segments(settings):
    tts = ToneTTS(max_words=20)
    out = voice_segments(_segs(), tts, settings)
    assert len(out) == 4 and all(len(a) > 24000 for a in out)
    assert tts.calls == 2        # 4 segments x 10 words, 20 words per call


def test_voice_change_mid_episode_revoices_everything(settings):
    class Flaky(ToneTTS):
        def synthesize(self, text):
            if self.calls == 1:
                self.sig = "tone:2"      # e.g. provider fell back to another model
            return super().synthesize(text)
    tts = Flaky()
    voice_segments(_segs(), tts, settings)
    assert tts.calls == 2 + 4          # aborted at the 2nd call (voice changed), then a full redo


def test_truncated_audio_is_rejected(settings):
    class Short(ToneTTS):
        def synthesize(self, text):
            return np.zeros(100, np.int16) + 5000, 24000
    with pytest.raises(TTSError):
        voice_segments([{"id": "x", "type": "sector", "narration": "word " * 60}], Short(), settings)


def test_timeline_and_mastering(tmp_path, settings):
    segs = _segs()
    audios = voice_segments(segs, ToneTTS(), settings)
    full, tl = build_timeline(segs, audios, gap=0.7)
    assert [s["id"] for s in tl["segments"]] == ["s0", "s1", "s2", "s3"]
    for a, b in zip(tl["segments"], tl["segments"][1:]):
        assert b["start"] > a["end"]
    assert all(len(s["sentences"]) == 2 for s in tl["segments"])
    raw = tmp_path / "raw.wav"
    ap.write_wav(raw, full, 24000)
    master = tmp_path / "m.wav"
    ap.master(raw, master, -16)
    mp3 = tmp_path / "e.mp3"
    ap.export_mp3(master, mp3, {"title": "T", "artist": "Active Thinking"})
    assert abs(ap.ffprobe_duration(mp3) - tl["duration"]) < 0.3
