"""Chapter → segments parser.

Calls an LLM to split a chapter into a list of typed segments:

    [
        {"type": "narration", "text": "..."},
        {"type": "dialogue", "character": "Holmes", "text": "..."},
        {"type": "hud", "text": "..."},
        ...
    ]

Two opt-in features driven by cast.yaml `features` table:

  - hud_brackets: parse [bracketed] system messages as type=hud
  - entity_italics: parse <!-- voice: NAME --> *italic* as dialogue with that character

Includes robust JSON recovery for truncated/malformed model output, and a
half-and-half fallback for chapters that exceed the model's output budget.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from audiobooker.api import LLMClient


# --- System prompts -----------------------------------------------------

_BASE_RULES = """You are a text parser for an audiobook system. Your job is to split a chapter into segments for text-to-speech with different voices.

Rules:
- Split the text into segments of type "narration" or "dialogue"{extra_types}.
- For dialogue, identify which character is speaking and set the "character" field.
- For dialogue, include ONLY the spoken words (strip quotation marks).
- CRITICAL: When a line contains multiple quoted strings with non-quoted text between them, you MUST split them into separate segments. The text between quotes is narration. Examples:
  Input: "Please," John called after her again, his voice cracking. "I have nowhere to go."
  Output: [dialogue "Please,"], [narration "John called after her again, his voice cracking."], [dialogue "I have nowhere to go."]
  Input: "We will begin." His voice echoed against the stone. "Who are you?"
  Output: [dialogue "We will begin."], [narration "His voice echoed against the stone."], [dialogue "Who are you?"]
  NEVER merge two separate quoted strings into one dialogue segment. Each "..." is its own dialogue segment. Any text between closing " and opening " is always narration. This is the most important rule.
- For narration, include everything that isn't inside quotation marks (descriptions, action, dialogue tags, internal thoughts).
- Preserve ALL text — every word from the chapter must appear in exactly one segment. Nothing gets dropped.
- Keep segments in strict source order. Don't reorder or skip anything.
- For unknown characters, add a "gender" field ("male" or "female") for voice assignment.
- Merge very short consecutive narration segments (under 10 words) with the nearest adjacent narration segment.
{extra_rules}
Return ONLY a JSON array, no markdown fences, no explanation."""

_HUD_RULES = """- HUD segments: Any text in [square brackets] that represents a system/interface readout (e.g. [Item Detected], [Contact: Holmes]). These are computer/HUD messages and get a special voice. Extract just the bracketed text (without the brackets). Use type "hud".
- IMPORTANT: Text surrounding HUD brackets must NOT be dropped. If a line reads: 'The HUD blinked quietly: [Item: Coin Pouch — Not Detected]', output TWO segments: [narration "The HUD blinked quietly."], [hud "Item, Coin Pouch, Not Detected"]. The prose before/after brackets is narration and must be preserved."""

_ENTITY_RULES = """- Entity segments: Text in *italics* immediately preceded by a <!-- voice: NAME --> comment is telepathic/internal speech from the named character. Treat these as dialogue with character=NAME (strip the asterisks and the comment). Example: <!-- voice: Entity --> *Remain still.* → [dialogue, character: "Entity", text: "Remain still."]"""

_DELIVERY_RULES = """
EMOTION/DELIVERY TAGS:
- For EVERY segment, add a "delivery" field describing HOW this line should be performed.
- Use short acting directions: "whispered, fearful", "angry shout", "calm and measured", "sarcastic", "exhausted, barely audible", "urgent, desperate", "cold and threatening", "warm, teasing", "monotone, robotic", "awed, breathless", etc.
- For narration: match the tone of the scene (tense, peaceful, ominous, etc.).
- For HUD (if present): always use "flat, synthetic, clinical".
- Read the surrounding context to infer the right emotional tone — don't just say "neutral".
- This field drives voice modulation in expressive engines like ElevenLabs."""


def build_system_prompt(features: dict[str, bool], engine: str) -> str:
    """Build the parser system prompt based on enabled features and target engine."""
    extra_types = ""
    extra_rules_parts = []

    if features.get("hud_brackets"):
        extra_types += ', "hud"'
        extra_rules_parts.append(_HUD_RULES)
    if features.get("entity_italics"):
        extra_rules_parts.append(_ENTITY_RULES)

    extra_rules = "\n".join(extra_rules_parts)
    if extra_rules:
        extra_rules = "\n" + extra_rules + "\n"

    prompt = _BASE_RULES.format(extra_types=extra_types, extra_rules=extra_rules)
    if engine == "elevenlabs":
        prompt += _DELIVERY_RULES
    return prompt


# --- Pre-processing -----------------------------------------------------


def presplit_quotes(text: str) -> str:
    """Pre-split lines so each quoted string and its narration beat are on its own line.

    Reduces the LLM's chance of merging dialogue segments or reordering attribution.
    """
    result = []
    for line in text.split("\n"):
        parts = re.split(r'("(?:[^"\\]|\\.)*")', line)
        if len(parts) < 2:
            result.append(line)
            continue
        for part in parts:
            stripped = part.strip()
            if stripped:
                result.append(stripped)
    return "\n".join(result)


def split_chapter(chapter_text: str) -> tuple[str, str]:
    """Split chapter at a paragraph break near the midpoint."""
    lines = chapter_text.split("\n")
    mid = len(lines) // 2
    best = None
    for offset in range(len(lines) // 2):
        for candidate in (mid + offset, mid - offset):
            if 0 <= candidate < len(lines) and lines[candidate].strip() == "":
                best = candidate
                break
        if best is not None:
            break
    if best is None:
        best = mid
    first = "\n".join(lines[:best]).rstrip()
    second = "\n".join(lines[best:]).lstrip()
    return first, second


# --- LLM call + JSON recovery -------------------------------------------


def _strip_fences(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]
    return content.strip()


def _parse_json_with_recovery(content: str) -> list[dict[str, Any]]:
    """Parse a JSON array, with recovery for preamble/suffix prose and truncation."""
    content = _strip_fences(content)

    # Try each `[` position as the start of the array. This tolerates
    # prose preamble, including prose that itself contains a stray `[`
    # ("Here is the [first] JSON array: [...]"). Also try trimming to
    # the last `]` in case there's trailing commentary.
    candidates: list[str] = []
    start = 0
    while True:
        pos = content.find("[", start)
        if pos < 0:
            break
        chunk = content[pos:]
        candidates.append(chunk)
        rbracket = chunk.rfind("]")
        if rbracket > 0:
            candidates.append(chunk[: rbracket + 1])
        start = pos + 1

    # If there were no `[` at all, still try the raw content once so the
    # original JSONDecodeError surfaces with its real context.
    if not candidates:
        candidates.append(content)

    last_err: json.JSONDecodeError | None = None
    for cand in candidates:
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError as e:
            last_err = e

    # Truncation recovery: walk back through `}` in the last viable candidate
    # (the one starting at the first `[`), trying to close the array early.
    tail = candidates[0]
    search_from = len(tail)
    for _ in range(50):
        last_brace = tail.rfind("}", 0, search_from)
        if last_brace <= 0:
            break
        truncated = tail[: last_brace + 1].rstrip().rstrip(",") + "\n]"
        try:
            segments = json.loads(truncated)
            count = sum(1 for s in segments if isinstance(s, dict) and "type" in s)
            print(
                f"  WARNING: parser JSON truncated, recovered {count} segments",
                file=sys.stderr,
            )
            return segments
        except json.JSONDecodeError:
            search_from = last_brace
    if last_err is not None:
        raise last_err
    raise json.JSONDecodeError("no JSON array found in LLM output", content, 0)


def _call_parser(
    client: LLMClient,
    text: str,
    features: dict[str, bool],
    engine: str,
) -> list[dict[str, Any]]:
    system_prompt = build_system_prompt(features, engine)
    extra_reminder = ""
    if engine == "elevenlabs":
        extra_reminder = (
            '\n\nREMINDER: Include a "delivery" field on every segment '
            "describing the emotional tone/acting direction."
        )
    user_prompt = (
        "Parse this chapter into narration/dialogue segments.\n\n"
        'REMINDER: Every time you see text between a closing " and an opening ", '
        "that text is NARRATION — a separate narration segment. Never skip it, "
        "never merge the two quoted sections into one dialogue segment."
        f"{extra_reminder}\n\n{text}"
    )
    raw = client.chat(system_prompt, user_prompt)
    return _parse_json_with_recovery(raw)


# --- Post-processing ----------------------------------------------------


def postprocess_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fix common LLM mistakes: split merged dialogue, recover dropped narration, dedupe."""
    fixed: list[dict[str, Any]] = []
    for seg in segments:
        if not isinstance(seg, dict) or "type" not in seg or "text" not in seg:
            continue
        if seg["type"] == "dialogue":
            text = seg["text"]
            quote_parts = re.split(r'("(?:[^"\\]|\\.)*")', text)
            if len(quote_parts) >= 3 and any(p.startswith('"') for p in quote_parts):
                # Recovery: an LLM merge mistake stripped the OUTER quotes but
                # kept the INNER ones, e.g. source `"A," she said. "B"` arrived
                # as a single dialogue segment with text `A," she said. "B`.
                # The regex matches the embedded `"..."` spans, which were the
                # narration tag that originally lived BETWEEN two source quote
                # pairs. The text outside those matches is the actual dialogue
                # continuation from the same character.
                for part in quote_parts:
                    part = part.strip()
                    if not part:
                        continue
                    if part.startswith('"') and part.endswith('"'):
                        # Bracketed-by-quotes → was the narration tag.
                        fixed.append({"type": "narration", "text": part[1:-1]})
                    else:
                        # Outside the quote spans → original dialogue, same character.
                        fixed.append({**seg, "text": part})
            else:
                fixed.append(seg)
        elif seg["type"] == "narration":
            parts = re.split(r'(".*?")', seg["text"])
            if len(parts) > 1 and any(p.startswith('"') for p in parts):
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    if part.startswith('"') and part.endswith('"'):
                        fixed.append(
                            {
                                "type": "dialogue",
                                "character": seg.get("character", "Unknown"),
                                "gender": seg.get("gender", "male"),
                                "text": part[1:-1],
                            }
                        )
                    else:
                        fixed.append({"type": "narration", "text": part})
            else:
                fixed.append(seg)
        else:
            fixed.append(seg)

    deduped: list[dict[str, Any]] = []
    for seg in fixed:
        if deduped and seg["text"].strip():
            prev = deduped[-1]
            if prev["text"].strip() == seg["text"].strip():
                if seg["type"] == "dialogue" and prev["type"] == "narration":
                    deduped[-1] = seg
                continue
            prev_clean = prev["text"].strip().strip('",.')
            seg_clean = seg["text"].strip().strip('",.')
            if prev_clean and seg_clean:
                if (
                    prev["type"] == "narration"
                    and seg["type"] == "dialogue"
                    and prev_clean == seg_clean
                ):
                    deduped[-1] = seg
                    continue
                if (
                    prev["type"] == "dialogue"
                    and seg["type"] == "narration"
                    and prev_clean == seg_clean
                ):
                    continue
        deduped.append(seg)

    return deduped


# --- Public entry point -------------------------------------------------


def parse_chapter(
    chapter_text: str,
    client: LLMClient,
    features: dict[str, bool],
    engine: str = "kokoro",
) -> list[dict[str, Any]]:
    """Parse chapter into segments. Falls back to half-and-half on failure."""
    chapter_text = presplit_quotes(chapter_text)
    try:
        segments = _call_parser(client, chapter_text, features, engine)
    except Exception as e:  # noqa: BLE001
        print(
            f"  Full-chapter parse failed ({e.__class__.__name__}), splitting in half...",
            file=sys.stderr,
        )
        first_half, second_half = split_chapter(chapter_text)
        print(f"  Parsing first half ({len(first_half)} chars)...")
        seg_a = _call_parser(client, first_half, features, engine)
        print(f"  Parsing second half ({len(second_half)} chars)...")
        seg_b = _call_parser(client, second_half, features, engine)
        segments = seg_a + seg_b
        print(f"  Merged: {len(seg_a)} + {len(seg_b)} = {len(segments)} segments")
    return postprocess_segments(segments)
