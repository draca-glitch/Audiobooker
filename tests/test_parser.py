"""Parser JSON recovery + postprocessing regressions."""

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
