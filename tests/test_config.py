"""cast.yaml loader regressions."""

import os

import pytest

from audiobooker.config import load_cast


def _write_cast(tmp_path, text):
    p = tmp_path / "cast.yaml"
    p.write_text(text)
    return p


def test_minimal_cast(tmp_path):
    p = _write_cast(tmp_path, """
cast:
  narrator:
    kokoro_voice: am_michael
""")
    cfg = load_cast(p)
    assert "narrator" in cfg.cast
    assert cfg.cast["narrator"].kokoro_voice == "am_michael"
    assert cfg.cast["narrator"].speed == 1.0


def test_missing_narrator_rejected(tmp_path):
    p = _write_cast(tmp_path, """
cast:
  SomeOther:
    kokoro_voice: af_bella
""")
    with pytest.raises(ValueError, match="narrator"):
        load_cast(p)


def test_empty_cast_rejected(tmp_path):
    p = _write_cast(tmp_path, "cast: {}\n")
    with pytest.raises(ValueError):
        load_cast(p)


def test_voice_resolution_gender_fallback(tmp_path):
    p = _write_cast(tmp_path, """
cast:
  narrator:
    kokoro_voice: am_michael
  _female_fallback:
    kokoro_voice: af_jessica
""")
    cfg = load_cast(p)
    seg = {"type": "dialogue", "character": "Unknown", "gender": "female"}
    assert cfg.resolve_voice(seg, "kokoro") == "af_jessica"


def test_voice_resolution_falls_back_to_narrator(tmp_path):
    p = _write_cast(tmp_path, """
cast:
  narrator:
    kokoro_voice: am_michael
""")
    cfg = load_cast(p)
    seg = {"type": "dialogue", "character": "Unknown", "gender": "male"}
    assert cfg.resolve_voice(seg, "kokoro") == "am_michael"


def test_api_key_from_env(tmp_path, monkeypatch):
    p = _write_cast(tmp_path, """
cast:
  narrator:
    kokoro_voice: am_michael
""")
    cfg = load_cast(p)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    assert cfg.get_api_key("llm") == "sk-test-123"


def test_api_key_missing_raises(tmp_path, monkeypatch):
    p = _write_cast(tmp_path, """
cast:
  narrator:
    kokoro_voice: am_michael
""")
    cfg = load_cast(p)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Point HOME somewhere empty so the .audiobooker.env fallback is absent.
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        cfg.get_api_key("llm")
