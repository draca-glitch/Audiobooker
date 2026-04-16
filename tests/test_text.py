"""Text fix-up regressions."""

from audiobooker.text import fix_pronunciation, pick_gap
from audiobooker.config import DEFAULT_GAPS


def test_abbreviation_not_expanded():
    assert fix_pronunciation("Mr. Smith walked in.", {}) == "Mr. Smith walked in."
    assert fix_pronunciation("Dr. Jones said hi.", {}) == "Dr. Jones said hi."
    assert fix_pronunciation("Mrs. Park and Mr. Lee met.", {}) == "Mrs. Park and Mr. Lee met."


def test_latin_abbreviations_not_expanded():
    assert fix_pronunciation("Many things, i.e. a list.", {}) == "Many things, i.e. a list."
    assert fix_pronunciation("Fruit, e.g. apples.", {}) == "Fruit, e.g. apples."
    assert fix_pronunciation("The U.S. market.", {}) == "The U.S. market."


def test_sentence_period_expanded():
    assert fix_pronunciation("Hello. World.", {}) == "Hello... World."


def test_em_dash_to_comma():
    assert fix_pronunciation("Stop—then pause.", {}) == "Stop, then pause."


def test_pronunciation_word_boundary():
    # A "Ra" rule must NOT corrupt "parapet" or "pirate".
    out = fix_pronunciation("The pirate is apart from parapet.", {"Ra": "Rah"})
    assert "Rah" not in out
    assert out == "The pirate is apart from parapet."


def test_pronunciation_applies_to_whole_word():
    out = fix_pronunciation("Ra woke up.", {"Ra": "Rah"})
    assert out == "Rah woke up."


def test_units_expanded():
    assert fix_pronunciation("run 5 km today", {}) == "run 5 kilometers today"
    assert fix_pronunciation("weigh 2 kg", {}) == "weigh 2 kilograms"


def test_thousands_separators_stripped():
    assert fix_pronunciation("The price was 1,400 dollars.", {}) == (
        "The price was 1400 dollars."
    )


def test_pick_gap_dialogue_tag():
    gaps = dict(DEFAULT_GAPS)
    prev = {"type": "dialogue", "text": "Hi.", "character": "A"}
    seg = {"type": "narration", "text": "he said."}
    nxt = {"type": "dialogue", "text": "Bye.", "character": "A"}
    assert pick_gap(seg, nxt, prev, gaps) == gaps["dialogue_tag"]


def test_pick_gap_paragraph():
    gaps = dict(DEFAULT_GAPS)
    seg = {"type": "narration", "text": "X" * 250}
    assert pick_gap(seg, None, None, gaps) == gaps["paragraph"]
