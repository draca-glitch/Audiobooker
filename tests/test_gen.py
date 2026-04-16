"""End-to-end gen.py tests with mocked TTS + LLM."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import pytest

from audiobooker.config import load_cast
from audiobooker.effects import EffectRegistry
from audiobooker.gen import (
    _plan_segment,
    _segment_cache_key,
    is_kokoro_segment,
    render_segments,
)


class FakeKokoro:
    """Kokoro stand-in: returns deterministic silence at 24 kHz."""

    def __init__(self, sr: int = 24000):
        self.sr = sr
        self.calls = 0

    def synth(self, text: str, voice: str, speed: float = 1.0):
        self.calls += 1
        samples = np.zeros(self.sr // 10, dtype=np.float32)  # 100 ms
        return samples, self.sr


class FakeElevenLabs:
    """ElevenLabs stand-in at a DIFFERENT sample rate, to expose the
    sample-rate bug that was fixed."""

    def __init__(self, sr: int = 22050):
        self.sr = sr
        self.calls = 0

    def synth(self, text: str, voice_id: str, settings: dict):
        self.calls += 1
        samples = np.zeros(self.sr // 10, dtype=np.float32)
        return samples, self.sr


@pytest.fixture
def cast_cfg(tmp_path):
    p = tmp_path / "cast.yaml"
    p.write_text("""
cast:
  narrator:
    kokoro_voice: am_michael
    elevenlabs_voice: voice_narr
  Alice:
    kokoro_voice: af_bella
    elevenlabs_voice: voice_alice
""")
    return load_cast(p)


def _segments():
    return [
        {"type": "narration", "text": "The sky was dark."},
        {"type": "dialogue", "character": "Alice", "text": "Hello."},
        {"type": "narration", "text": "She walked away."},
    ]


def test_plan_segment_routes_hybrid(cast_cfg):
    narr = {"type": "narration", "text": "X"}
    dial = {"type": "dialogue", "character": "Alice", "text": "Y"}
    assert _plan_segment(narr, cast_cfg, "hybrid")["engine"] == "kokoro"
    assert _plan_segment(dial, cast_cfg, "hybrid")["engine"] == "elevenlabs"


def test_is_kokoro_segment():
    assert is_kokoro_segment({"type": "narration"})
    assert is_kokoro_segment({"type": "hud"})
    assert is_kokoro_segment({"type": "dialogue", "character": "Entity"})
    assert not is_kokoro_segment({"type": "dialogue", "character": "Alice"})


def test_cache_key_stable(cast_cfg):
    seg = {"type": "narration", "text": "X"}
    plan = _plan_segment(seg, cast_cfg, "kokoro")
    k1 = _segment_cache_key(seg, plan, None, cast_cfg.pronunciations)
    k2 = _segment_cache_key(seg, plan, None, cast_cfg.pronunciations)
    assert k1 == k2


def test_cache_key_changes_with_text(cast_cfg):
    plan = _plan_segment({"type": "narration", "text": "X"}, cast_cfg, "kokoro")
    k1 = _segment_cache_key({"type": "narration", "text": "X"}, plan, None, {})
    k2 = _segment_cache_key({"type": "narration", "text": "Y"}, plan, None, {})
    assert k1 != k2


def test_render_and_cache_hit(tmp_path, cast_cfg):
    kokoro = FakeKokoro()
    seg_dir = tmp_path / "segs"
    effects = EffectRegistry(cast_cfg.effects)

    paths = render_segments(
        _segments(), seg_dir, cast_cfg, "kokoro",
        kokoro, None, effects, force=False, workers=1,
    )
    assert len(paths) == 3
    assert kokoro.calls == 3
    for p in paths:
        assert Path(p).exists()
        meta = Path(p).parent / (Path(p).stem + ".meta.json")
        assert meta.exists()

    # Second pass: all cached, no new synth calls.
    kokoro2 = FakeKokoro()
    paths2 = render_segments(
        _segments(), seg_dir, cast_cfg, "kokoro",
        kokoro2, None, effects, force=False, workers=1,
    )
    assert kokoro2.calls == 0
    assert paths == paths2


def test_cache_invalidated_on_text_change(tmp_path, cast_cfg):
    kokoro = FakeKokoro()
    seg_dir = tmp_path / "segs"
    effects = EffectRegistry(cast_cfg.effects)

    render_segments(
        _segments(), seg_dir, cast_cfg, "kokoro",
        kokoro, None, effects, force=False, workers=1,
    )
    assert kokoro.calls == 3

    kokoro2 = FakeKokoro()
    changed = _segments()
    changed[1]["text"] = "Hello, world."
    render_segments(
        changed, seg_dir, cast_cfg, "kokoro",
        kokoro2, None, effects, force=False, workers=1,
    )
    # Only the changed segment re-renders.
    assert kokoro2.calls == 1


def test_hybrid_mixed_engine_rates_unified_on_write(tmp_path, cast_cfg):
    """Regression: when Kokoro returns 24 kHz and ElevenLabs returns 22050,
    every written segment must be resampled to cfg.sample_rate (24 kHz here).
    Without this, ffmpeg's concat demuxer silently retimes the 22050 WAV as
    if it were 24000, pitch-shifting ElevenLabs output ~9% up."""
    seg_dir = tmp_path / "segs"
    effects = EffectRegistry(cast_cfg.effects)
    kokoro = FakeKokoro(sr=24000)
    el = FakeElevenLabs(sr=22050)

    render_segments(
        _segments(), seg_dir, cast_cfg, "hybrid",
        kokoro, el, effects, force=False, workers=1,
    )

    import soundfile as sf
    for i in range(3):
        info = sf.info(str(seg_dir / f"{i:04d}.wav"))
        assert info.samplerate == cast_cfg.sample_rate, (
            f"segment {i}: expected {cast_cfg.sample_rate}, got {info.samplerate}"
        )


def test_empty_segments_skipped(tmp_path, cast_cfg):
    kokoro = FakeKokoro()
    seg_dir = tmp_path / "segs"
    effects = EffectRegistry(cast_cfg.effects)
    segs = [
        {"type": "narration", "text": "Real text."},
        {"type": "narration", "text": ""},
        {"type": "narration", "text": "   "},
        {"type": "narration", "text": "Also real."},
    ]
    paths = render_segments(
        segs, seg_dir, cast_cfg, "kokoro",
        kokoro, None, effects, force=False, workers=1,
    )
    assert kokoro.calls == 2
    assert len(paths) == 2


def test_parallel_elevenlabs(tmp_path, cast_cfg):
    seg_dir = tmp_path / "segs"
    effects = EffectRegistry(cast_cfg.effects)
    el = FakeElevenLabs()
    # Many segments so parallelism has something to chew on.
    segs = [
        {"type": "dialogue", "character": "Alice", "text": f"Line {i}."}
        for i in range(12)
    ]
    paths = render_segments(
        segs, seg_dir, cast_cfg, "elevenlabs",
        None, el, effects, force=False, workers=4,
    )
    assert len(paths) == 12
    assert el.calls == 12
    # Order preserved: file 0000 corresponds to segment 0, etc.
    for i, p in enumerate(paths):
        assert Path(p).name == f"{i:04d}.wav"
