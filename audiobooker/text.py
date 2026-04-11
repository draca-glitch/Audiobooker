"""Text fixup helpers (pronunciation, punctuation normalization, gap selection)."""

from __future__ import annotations

import re
from typing import Any


def fix_pronunciation(text: str, pronunciations: dict[str, str]) -> str:
    """Apply pronunciation overrides + general TTS-friendly punctuation fixes.

    Kokoro and most TTS engines benefit from:
      - em-dash → ", " (Kokoro doesn't pause on —)
      - "..." instead of "." for slightly longer pauses
      - removed thousands-separators in numbers
      - spelled-out unit abbreviations
    """
    # Em-dash and en-dash → comma pause
    text = text.replace("—", ", ").replace("–", ", ")
    # Lengthen periods slightly for clearer phrasing
    text = re.sub(r"\. ", "... ", text)
    # Symbols
    text = text.replace("~", "approximately ")
    # Common metric units (case-sensitive word boundary)
    text = re.sub(r"\bkm\b", "kilometers", text)
    text = re.sub(r"\bcm\b", "centimeters", text)
    text = re.sub(r"\bmm\b", "millimeters", text)
    text = re.sub(r"\bkg\b", "kilograms", text)
    # Strip thousands separators inside numbers (1,400 → 1400)
    text = re.sub(r"(\d),(\d)", r"\1\2", text)
    # Apply user pronunciation map last (case-insensitive)
    for word, phonetic in pronunciations.items():
        text = re.sub(re.escape(word), phonetic, text, flags=re.IGNORECASE)
    return text


def pick_gap(
    seg: dict[str, Any],
    next_seg: dict[str, Any] | None,
    prev_seg: dict[str, Any] | None,
    gaps: dict[str, float],
) -> float:
    """Choose trailing silence duration based on segment context."""
    text = seg.get("text", "")
    seg_type = seg["type"]
    next_type = next_seg["type"] if next_seg else None
    prev_type = prev_seg["type"] if prev_seg else None

    # Short narration between dialogue = dialogue tag ("he said", "she muttered")
    if seg_type == "narration" and len(text) < 60:
        if next_type == "dialogue" and prev_type == "dialogue":
            return gaps["dialogue_tag"]
        if next_type == "dialogue":
            return gaps["dialogue_tag"]

    # Dialogue followed by short narration tag
    if seg_type == "dialogue" and next_seg:
        next_text = next_seg.get("text", "")
        if next_type == "narration" and len(next_text) < 60:
            return gaps["dialogue_tag"]

    # Same speaker continuing
    if seg_type == "dialogue" and next_type == "dialogue":
        if seg.get("character") == next_seg.get("character"):
            return gaps["dialogue_continuation"]
        return gaps["speaker_change"]

    # Narration → dialogue with different speaker than previous dialogue
    if seg_type == "narration" and next_type == "dialogue" and prev_type == "dialogue":
        if prev_seg.get("character") != next_seg.get("character"):
            return gaps["speaker_change"]

    # Long narration / paragraph-like blocks
    if seg_type == "narration" and len(text) > 200:
        return gaps["paragraph"]
    if seg_type == "narration" and (len(text) > 100 or text.endswith(".")):
        return gaps["narration"]

    return gaps["default"]
