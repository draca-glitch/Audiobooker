"""Parser JSON recovery + postprocessing regressions."""

import pytest

from audiobooker.parser import (
    _parse_json_with_recovery,
    postprocess_segments,
    presplit_quotes,
)


def test_recover_clean_json():
    raw = '[{"type": "narration", "text": "hi"}]'
    assert _parse_json_with_recovery(raw) == [{"type": "narration", "text": "hi"}]


def test_recover_with_prose_preamble():
    # LLM prepends "Here is the JSON:" despite being told not to.
    raw = 'Here is the JSON:\n[{"type": "narration", "text": "hi"}]'
    out = _parse_json_with_recovery(raw)
    assert out == [{"type": "narration", "text": "hi"}]


def test_recover_with_fences_and_prose():
    raw = '```json\n[{"type": "narration", "text": "hi"}]\n```'
    out = _parse_json_with_recovery(raw)
    assert out == [{"type": "narration", "text": "hi"}]


def test_recover_truncated():
    raw = '[{"type": "narration", "text": "a"}, {"type": "narration", "text": "b"'
    out = _parse_json_with_recovery(raw)
    assert out == [{"type": "narration", "text": "a"}]


def test_recover_trailing_prose():
    raw = '[{"type": "narration", "text": "hi"}]\n\nThat was the chapter!'
    out = _parse_json_with_recovery(raw)
    assert out == [{"type": "narration", "text": "hi"}]


def test_recover_preamble_with_stray_bracket():
    # LLM puts a [bracketed aside] before the real JSON.
    raw = 'Here is the [parsed] output:\n[{"type": "narration", "text": "hi"}]'
    out = _parse_json_with_recovery(raw)
    assert out == [{"type": "narration", "text": "hi"}]


def test_recover_text_abbreviation_in_payload():
    # Abbreviations with periods inside strings must not trip up recovery.
    raw = '[{"type": "narration", "text": "Mr. Smith spoke."}]'
    out = _parse_json_with_recovery(raw)
    assert out == [{"type": "narration", "text": "Mr. Smith spoke."}]


def test_postprocess_splits_merged_dialogue():
    # LLM produced a single dialogue segment that should have been three.
    merged = [{
        "type": "dialogue",
        "character": "A",
        "text": 'Please,",  he said. "I have nowhere',
    }]
    out = postprocess_segments(merged)
    # Expect at least one narration segment carved out.
    assert any(s["type"] == "narration" for s in out)


def test_postprocess_dedupes_consecutive_duplicate():
    dupes = [
        {"type": "narration", "text": "same line"},
        {"type": "narration", "text": "same line"},
    ]
    out = postprocess_segments(dupes)
    assert len(out) == 1


def test_presplit_quotes():
    line = '"Hello," he said. "Bye."'
    out = presplit_quotes(line)
    lines = [ln for ln in out.split("\n") if ln.strip()]
    assert len(lines) >= 3


class _StubClient:
    """LLM stub: returns queued responses per call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, system_prompt, user_prompt):
        self.calls += 1
        return self.responses.pop(0)


def _seg_json(texts):
    import json as _json
    return _json.dumps([{"type": "narration", "text": t} for t in texts])


def test_coverage_check_catches_dropped_content():
    from audiobooker.parser import _call_parser
    text = "alpha " * 200  # 1000 alnum chars
    client = _StubClient([_seg_json(["alpha " * 40])])  # only 20% covered
    with pytest.raises(ValueError, match="content was dropped"):
        _call_parser(client, text, {}, "kokoro")


def test_parse_chapter_falls_back_to_halves_on_coverage_failure():
    from audiobooker.parser import parse_chapter
    text = ("alpha bravo charlie delta echo " * 40 + "\n\n"
            + "foxtrot golf hotel india juliet " * 40)
    half_a = _seg_json(["alpha bravo charlie delta echo " * 40])
    half_b = _seg_json(["foxtrot golf hotel india juliet " * 40])
    truncated = _seg_json(["alpha bravo charlie delta echo " * 4])  # ~10%
    client = _StubClient([truncated, half_a, half_b])
    segments = parse_chapter(text, client, {}, engine="kokoro")
    assert client.calls == 3  # full attempt + two halves
    joined = " ".join(s["text"] for s in segments)
    assert "foxtrot" in joined and "alpha" in joined


def test_truncated_output_error_exists():
    from audiobooker.api import TruncatedOutputError
    assert issubclass(TruncatedOutputError, RuntimeError)
