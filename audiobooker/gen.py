"""Main audiobook generator entry point.

Pipeline:
  1. Read chapter text
  2. Parse into segments via LLM (using cast.yaml features)
  3. Render each segment via Kokoro / ElevenLabs / hybrid, with effects
  4. Concatenate WAVs via ffmpeg
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from audiobooker.config import CastConfig, load_cast
from audiobooker.api import LLMClient
from audiobooker.effects import EffectRegistry
from audiobooker.parser import parse_chapter
from audiobooker.text import fix_pronunciation, pick_gap
from audiobooker.tts import (
    ElevenLabsEngine,
    KokoroEngine,
    adjust_settings_for_delivery,
)


# --- Helpers ------------------------------------------------------------


def hash_content(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]


def make_llm_client(cfg: CastConfig) -> LLMClient:
    return LLMClient(
        base_url=cfg.llm["base_url"],
        model=cfg.llm["model"],
        api_key=cfg.get_api_key("llm"),
        compat=cfg.llm.get("compat", "anthropic"),
        max_tokens=int(cfg.llm.get("max_tokens", 16384)),
    )


def is_kokoro_segment(seg: dict) -> bool:
    """In hybrid mode: Kokoro renders narration, HUD, and Entity dialogue."""
    if seg["type"] in ("narration", "hud"):
        return True
    if seg["type"] == "dialogue" and seg.get("character") == "Entity":
        return True
    return False


def concatenate_wavs(wav_paths: list[str], output_path: str) -> bool:
    """Concatenate WAV files using ffmpeg."""
    if not wav_paths:
        print("ERROR: No WAV segments to concatenate", file=sys.stderr)
        return False

    list_path = output_path + ".filelist.txt"
    with open(list_path, "w") as f:
        for p in wav_paths:
            # Use absolute paths because ffmpeg concat resolves relative paths
            # relative to the file list location, not the working directory.
            f.write(f"file '{os.path.abspath(p)}'\n")

    result = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_path, "-c:a", "pcm_s16le", output_path,
        ],
        capture_output=True, text=True,
    )
    os.unlink(list_path)

    if result.returncode != 0:
        print(f"ERROR: ffmpeg failed: {result.stderr}", file=sys.stderr)
        return False
    return True


def reencode_to_aac(wav_path: str, output_path: str, bitrate: str = "64k") -> bool:
    """Re-encode a WAV file to AAC in an M4B (audiobook) container via ffmpeg.

    M4B is the standard audiobook format: same AAC codec and MP4 container as
    M4A, but the .m4b extension tells media players to treat it as an audiobook
    with bookmark support, position resume, and library categorization.

    Produces a stereo AAC file at the specified bitrate. For speech/audiobook
    content, 64k stereo is more than sufficient and produces files roughly
    10-15x smaller than the source WAV.
    """
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", wav_path,
            "-c:a", "aac",
            "-b:a", bitrate,
            "-ac", "2",       # stereo
            output_path,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: AAC encode failed: {result.stderr}", file=sys.stderr)
        return False
    return True


# --- Per-engine renderers -----------------------------------------------


def render_kokoro(
    segments: list[dict],
    seg_dir: Path,
    cfg: CastConfig,
    kokoro: KokoroEngine,
    effects: EffectRegistry,
) -> list[str]:
    seg_dir.mkdir(parents=True, exist_ok=True)
    wav_paths: list[str] = []
    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        if not text:
            continue
        voice = cfg.resolve_voice(seg, "kokoro")
        speed = cfg.resolve_speed(seg)
        seg_path = seg_dir / f"{i:04d}.wav"

        prev_seg = segments[i - 1] if i > 0 else None
        next_seg = segments[i + 1] if i < len(segments) - 1 else None
        gap_duration = pick_gap(seg, next_seg, prev_seg, cfg.gaps)

        char = f" — {seg.get('character')}" if seg.get("character") else ""
        print(
            f"  [{i+1}/{len(segments)}] {seg['type']}{char} → {voice} @{speed}x "
            f"({len(text)} chars) gap={gap_duration:.2f}s"
        )

        try:
            tts_text = fix_pronunciation(text, cfg.pronunciations)
            samples, sr = kokoro.synth(tts_text, voice=voice, speed=speed)
            samples = effects.apply(cfg.resolve_effect_name(seg), samples, sr)
            gap = np.zeros(int(cfg.sample_rate * gap_duration), dtype=np.float32)
            samples = np.concatenate([samples, gap])
            sf.write(str(seg_path), samples, sr)
            wav_paths.append(str(seg_path))
        except Exception as e:  # noqa: BLE001
            print(f"  WARNING: Failed segment {i}: {e}", file=sys.stderr)
    return wav_paths


def render_elevenlabs(
    segments: list[dict],
    seg_dir: Path,
    cfg: CastConfig,
    el_engine: ElevenLabsEngine,
    effects: EffectRegistry,
) -> list[str]:
    seg_dir.mkdir(parents=True, exist_ok=True)
    wav_paths: list[str] = []
    total_chars = 0
    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        if not text:
            continue
        voice_id = cfg.resolve_voice(seg, "elevenlabs")
        base_settings = cfg.resolve_elevenlabs_settings(seg)
        delivery = seg.get("delivery", "")
        settings = adjust_settings_for_delivery(base_settings, delivery)
        seg_path = seg_dir / f"{i:04d}.wav"

        prev_seg = segments[i - 1] if i > 0 else None
        next_seg = segments[i + 1] if i < len(segments) - 1 else None
        gap_duration = pick_gap(seg, next_seg, prev_seg, cfg.gaps)

        char = f" — {seg.get('character')}" if seg.get("character") else ""
        delivery_label = f" [{delivery}]" if delivery else ""
        print(
            f"  [{i+1}/{len(segments)}] {seg['type']}{char}{delivery_label} → "
            f"EL:{voice_id[:8]} stab={settings['stability']:.2f} "
            f"({len(text)} chars) gap={gap_duration:.2f}s"
        )

        try:
            tts_text = fix_pronunciation(text, cfg.pronunciations)
            total_chars += len(tts_text)
            samples, sr = el_engine.synth(tts_text, voice_id, settings)
            samples = effects.apply(cfg.resolve_effect_name(seg), samples, sr)
            gap = np.zeros(int(sr * gap_duration), dtype=np.float32)
            samples = np.concatenate([samples, gap])
            sf.write(str(seg_path), samples, sr)
            wav_paths.append(str(seg_path))
        except Exception as e:  # noqa: BLE001
            print(f"  WARNING: Failed segment {i}: {e}", file=sys.stderr)
    print(f"  Total characters sent to ElevenLabs: {total_chars}")
    return wav_paths


def render_hybrid(
    segments: list[dict],
    seg_dir: Path,
    cfg: CastConfig,
    kokoro: KokoroEngine,
    el_engine: ElevenLabsEngine,
    effects: EffectRegistry,
) -> list[str]:
    seg_dir.mkdir(parents=True, exist_ok=True)
    wav_paths: list[str] = []
    el_chars = 0
    kokoro_chars = 0
    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        if not text:
            continue
        use_kokoro = is_kokoro_segment(seg)
        seg_path = seg_dir / f"{i:04d}.wav"

        prev_seg = segments[i - 1] if i > 0 else None
        next_seg = segments[i + 1] if i < len(segments) - 1 else None
        gap_duration = pick_gap(seg, next_seg, prev_seg, cfg.gaps)

        char = f" — {seg.get('character')}" if seg.get("character") else ""
        delivery = seg.get("delivery", "")

        try:
            tts_text = fix_pronunciation(text, cfg.pronunciations)
            if use_kokoro:
                voice = cfg.resolve_voice(seg, "kokoro")
                speed = cfg.resolve_speed(seg)
                kokoro_chars += len(tts_text)
                print(
                    f"  [{i+1}/{len(segments)}] {seg['type']}{char} → "
                    f"KO:{voice} @{speed}x ({len(text)} chars) gap={gap_duration:.2f}s"
                )
                samples, sr = kokoro.synth(tts_text, voice=voice, speed=speed)
            else:
                voice_id = cfg.resolve_voice(seg, "elevenlabs")
                base_settings = cfg.resolve_elevenlabs_settings(seg)
                settings = adjust_settings_for_delivery(base_settings, delivery)
                el_chars += len(tts_text)
                delivery_label = f" [{delivery}]" if delivery else ""
                print(
                    f"  [{i+1}/{len(segments)}] {seg['type']}{char}{delivery_label} → "
                    f"EL:{voice_id[:8]} stab={settings['stability']:.2f} "
                    f"({len(text)} chars) gap={gap_duration:.2f}s"
                )
                samples, sr = el_engine.synth(tts_text, voice_id, settings)

            samples = effects.apply(cfg.resolve_effect_name(seg), samples, sr)
            gap = np.zeros(int(cfg.sample_rate * gap_duration), dtype=np.float32)
            samples = np.concatenate([samples, gap])
            sf.write(str(seg_path), samples, sr)
            wav_paths.append(str(seg_path))
        except Exception as e:  # noqa: BLE001
            print(f"  WARNING: Failed segment {i}: {e}", file=sys.stderr)
    print(f"  Kokoro: {kokoro_chars} chars | ElevenLabs: {el_chars} chars")
    return wav_paths


# --- CLI ----------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Multi-voice audiobook generator (audiobooker)",
    )
    parser.add_argument("chapter_file", help="Path to a chapter .txt file")
    parser.add_argument("--cast", required=True, help="Path to cast.yaml")
    parser.add_argument("--output", help="Output path (default: <output_dir>/<hash>.<format>)")
    parser.add_argument("--force", action="store_true", help="Regenerate even if cached")
    parser.add_argument(
        "--format",
        choices=["wav", "aac"],
        default="aac",
        help="Output format: aac (default, 64k stereo M4B audiobook container, ~10-15x smaller) or wav (raw PCM)",
    )
    parser.add_argument(
        "--segments-only",
        action="store_true",
        help="Only generate segment JSON, skip audio rendering",
    )
    parser.add_argument(
        "--engine",
        choices=["kokoro", "elevenlabs", "hybrid"],
        default="kokoro",
        help="TTS engine: kokoro (local, free), elevenlabs (cloud, premium), "
             "hybrid (kokoro for narration/HUD/Entity + elevenlabs for character dialogue)",
    )
    args = parser.parse_args()

    chapter_path = Path(args.chapter_file)
    if not chapter_path.is_file():
        print(f"ERROR: chapter file not found: {chapter_path}", file=sys.stderr)
        sys.exit(1)

    cfg = load_cast(args.cast)
    chapter_text = chapter_path.read_text()
    content_hash = hash_content(chapter_text)

    output_dir = Path(cfg.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    suffix = {"kokoro": "", "elevenlabs": "-el", "hybrid": "-hy"}[args.engine]
    ext = "m4b" if args.format == "aac" else "wav"
    output_path = (
        Path(args.output) if args.output
        else output_dir / f"{content_hash}{suffix}.{ext}"
    )
    segments_path = output_dir / f"{content_hash}-segments.json"
    el_segments_path = output_dir / f"{content_hash}-el-segments.json"
    active_segments_path = (
        el_segments_path if args.engine in ("elevenlabs", "hybrid") else segments_path
    )

    # Cache hit?
    if output_path.exists() and not args.force:
        print(f"CACHED: {output_path}")
        print(output_path)
        return

    # Step 1: parse chapter
    print(f"Parsing chapter: {chapter_path.name} (hash: {content_hash})")
    if active_segments_path.exists() and not args.force:
        print(f"Using cached segments: {active_segments_path}")
        segments = json.loads(active_segments_path.read_text())
    else:
        client = make_llm_client(cfg)
        parse_engine = "elevenlabs" if args.engine in ("elevenlabs", "hybrid") else "kokoro"
        t0 = time.time()
        segments = parse_chapter(chapter_text, client, cfg.features, engine=parse_engine)
        print(f"Parsed {len(segments)} segments in {time.time()-t0:.1f}s")
        active_segments_path.write_text(
            json.dumps(segments, indent=2, ensure_ascii=False)
        )
        print(f"Segments saved: {active_segments_path}")

    if args.segments_only:
        print(json.dumps(segments, indent=2, ensure_ascii=False))
        return

    # Step 2: render audio
    print(f"Generating audio ({len(segments)} segments) with engine={args.engine}...")
    effects = EffectRegistry(cfg.effects)
    seg_dir = output_dir / f"{content_hash}{suffix}-segments"

    t0 = time.time()
    if args.engine == "elevenlabs":
        el_engine = ElevenLabsEngine(
            api_url=cfg.elevenlabs["api_url"],
            model=cfg.elevenlabs["model"],
            api_key=cfg.get_api_key("elevenlabs"),
            output_format=cfg.elevenlabs["output_format"],
        )
        wav_paths = render_elevenlabs(segments, seg_dir, cfg, el_engine, effects)
    elif args.engine == "hybrid":
        kokoro = KokoroEngine(
            cfg.kokoro["model"], cfg.kokoro["voices"], cfg.kokoro.get("language", "en-us")
        )
        el_engine = ElevenLabsEngine(
            api_url=cfg.elevenlabs["api_url"],
            model=cfg.elevenlabs["model"],
            api_key=cfg.get_api_key("elevenlabs"),
            output_format=cfg.elevenlabs["output_format"],
        )
        wav_paths = render_hybrid(segments, seg_dir, cfg, kokoro, el_engine, effects)
    else:
        kokoro = KokoroEngine(
            cfg.kokoro["model"], cfg.kokoro["voices"], cfg.kokoro.get("language", "en-us")
        )
        wav_paths = render_kokoro(segments, seg_dir, cfg, kokoro, effects)
    print(f"Rendered {len(wav_paths)} WAVs in {time.time()-t0:.1f}s")

    # Step 3: concatenate
    wav_output = output_dir / f"{content_hash}{suffix}.wav"
    print("Concatenating...")
    if not concatenate_wavs(wav_paths, str(wav_output)):
        sys.exit(1)

    # Step 4: re-encode if requested (default: AAC in M4B audiobook container)
    if args.format == "aac":
        print("Re-encoding to AAC M4B (64k stereo)...")
        if not reencode_to_aac(str(wav_output), str(output_path)):
            sys.exit(1)
        # Remove the intermediate WAV to save disk
        wav_output.unlink(missing_ok=True)
    else:
        # WAV output: the concat result is already the final file
        if wav_output != output_path:
            wav_output.rename(output_path)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Done: {output_path} ({size_mb:.1f} MB)")
    print(output_path)


if __name__ == "__main__":
    main()
