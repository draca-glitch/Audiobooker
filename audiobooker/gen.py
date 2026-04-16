"""Main audiobook generator entry point.

Pipeline:
  1. Read chapter text
  2. Parse into segments via LLM (using cast.yaml features)
  3. Render each segment via Kokoro / ElevenLabs / hybrid, with effects
  4. Concatenate WAVs via ffmpeg
  5. Re-encode to AAC M4B (default) or keep WAV
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from audiobooker import __version__
from audiobooker.api import LLMClient
from audiobooker.config import CastConfig, load_cast
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
    """Concatenate WAV files using ffmpeg.

    All inputs must share a sample rate and channel count. `_render_one`
    guarantees this by keeping track of the per-engine rate and writing
    segments consistently.
    """
    if not wav_paths:
        print("ERROR: No WAV segments to concatenate", file=sys.stderr)
        return False

    list_path = output_path + ".filelist.txt"
    with open(list_path, "w") as f:
        for p in wav_paths:
            # Use absolute paths because ffmpeg concat resolves relative paths
            # relative to the file list location, not the working directory.
            f.write(f"file '{os.path.abspath(p)}'\n")

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_path, "-c:a", "pcm_s16le", output_path,
            ],
            capture_output=True, text=True,
        )
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass

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


# --- Unified per-segment renderer ---------------------------------------


def _plan_segment(seg: dict[str, Any], cfg: CastConfig, engine: str) -> dict[str, Any]:
    """Decide how one segment should be rendered.

    Returns a dict capturing engine + all its inputs so it can be hashed
    into a stable cache key and handed to a worker thread.
    """
    if engine == "hybrid":
        active = "kokoro" if is_kokoro_segment(seg) else "elevenlabs"
    else:
        active = engine

    plan: dict[str, Any] = {"engine": active}
    if active == "kokoro":
        plan["voice"] = cfg.resolve_voice(seg, "kokoro")
        plan["speed"] = cfg.resolve_speed(seg)
    else:
        plan["voice_id"] = cfg.resolve_voice(seg, "elevenlabs")
        base = cfg.resolve_elevenlabs_settings(seg)
        delivery = seg.get("delivery", "")
        plan["settings"] = adjust_settings_for_delivery(base, delivery)
        plan["delivery"] = delivery
    plan["effect"] = cfg.resolve_effect_name(seg)
    return plan


def _segment_cache_key(
    seg: dict[str, Any],
    plan: dict[str, Any],
    effect_spec: Any,
    pronunciations: dict[str, str],
) -> str:
    """Stable hash of everything that goes into one segment's audio output.

    Changing any of: text, engine, voice, speed, settings, effect chain, or
    pronunciation rules → new key → segment re-renders.
    """
    payload = {
        "text": seg["text"],
        "plan": plan,
        "effect_spec": effect_spec,
        "pron": pronunciations,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.md5(blob).hexdigest()[:16]


def _resample(samples: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Resample audio to dst_sr using pedalboard's WindowedSinc resampler.

    pedalboard is already a hard dep. Its StreamResampler is a proper
    low-pass-filtered resampler, not linear interpolation, so it's safe
    for audible-quality audiobook material.
    """
    if src_sr == dst_sr:
        return samples
    from pedalboard.io import StreamResampler

    src_sr_f = float(src_sr)
    dst_sr_f = float(dst_sr)
    # Streamer expects shape (channels, samples) float32.
    buf = samples.astype(np.float32, copy=False).reshape(1, -1)
    resampler = StreamResampler(src_sr_f, dst_sr_f, num_channels=1)
    out = resampler.process(buf)
    tail = resampler.process()  # flush
    if tail.size:
        out = np.concatenate([out, tail], axis=1)
    return out.flatten()


def _render_one(
    text: str,
    plan: dict[str, Any],
    cfg: CastConfig,
    kokoro: KokoroEngine | None,
    el_engine: ElevenLabsEngine | None,
    effects: EffectRegistry,
    kokoro_lock: threading.Lock,
    target_sr: int,
) -> tuple[np.ndarray, int]:
    """Synthesize one segment and return (samples, target_sr).

    Engines may return audio at whatever sample rate they prefer (Kokoro
    is 24 kHz; ElevenLabs follows `output_format`). We resample everything
    to a single `target_sr` so all segment WAVs share parameters — without
    this, ffmpeg's concat demuxer silently reinterprets the second input
    at the first input's rate, pitch-shifting the later engine's output.
    """
    tts_text = fix_pronunciation(text, cfg.pronunciations)
    if plan["engine"] == "kokoro":
        assert kokoro is not None
        # ONNX Runtime sessions are not guaranteed re-entrant across Python
        # threads; serialize Kokoro calls so hybrid mode stays safe.
        with kokoro_lock:
            samples, sr = kokoro.synth(
                tts_text, voice=plan["voice"], speed=plan["speed"]
            )
    else:
        assert el_engine is not None
        samples, sr = el_engine.synth(tts_text, plan["voice_id"], plan["settings"])
    samples = effects.apply(plan["effect"], samples, sr)
    samples = _resample(samples, sr, target_sr)
    return samples, target_sr


def render_segments(
    segments: list[dict[str, Any]],
    seg_dir: Path,
    cfg: CastConfig,
    engine: str,
    kokoro: KokoroEngine | None,
    el_engine: ElevenLabsEngine | None,
    effects: EffectRegistry,
    *,
    force: bool = False,
    workers: int = 1,
) -> list[str]:
    """Render all segments to seg_dir/NNNN.wav, return their paths in order.

    Caching: each segment has a sidecar NNNN.meta.json storing a hash of its
    render inputs. On rerun, segments whose hash matches the sidecar are
    skipped. Changing text / voice / cast / effects invalidates the entry.

    Parallelism: ElevenLabs calls are HTTP-bound and parallelize well. Kokoro
    calls are ONNX CPU work and run under a lock (already multi-threaded
    internally). In hybrid mode both engines share the pool; Kokoro work
    serializes naturally through the lock.
    """
    seg_dir.mkdir(parents=True, exist_ok=True)
    n = len(segments)
    paths: list[str | None] = [None] * n
    target_sr = int(cfg.sample_rate)

    to_render: list[tuple[int, dict[str, Any], dict[str, Any], str]] = []
    cached_count = 0
    skipped_empty = 0

    for i, seg in enumerate(segments):
        text = seg.get("text") or ""  # tolerate text=None from the LLM
        if not text.strip():
            skipped_empty += 1
            continue
        plan = _plan_segment(seg, cfg, engine)

        effect_spec = cfg.effects.get(plan["effect"]) if plan["effect"] else None
        key = _segment_cache_key(seg, plan, effect_spec, cfg.pronunciations)
        seg_path = seg_dir / f"{i:04d}.wav"
        meta_path = seg_dir / f"{i:04d}.meta.json"

        if not force and seg_path.exists() and meta_path.exists():
            try:
                existing = json.loads(meta_path.read_text())
                # Also require the cached file to be at the current target
                # sample rate; changing cfg.sample_rate should invalidate.
                if existing.get("key") == key and existing.get("sr") == target_sr:
                    paths[i] = str(seg_path)
                    cached_count += 1
                    continue
            except (json.JSONDecodeError, OSError):
                pass
        to_render.append((i, seg, plan, key))

    if cached_count:
        print(f"  Cached: {cached_count}/{n} segments reused")
    if skipped_empty:
        print(f"  Skipped: {skipped_empty} empty segments")

    kokoro_lock = threading.Lock()
    stats = {"kokoro_chars": 0, "elevenlabs_chars": 0}
    stats_lock = threading.Lock()

    def _task(i: int, seg: dict[str, Any], plan: dict[str, Any], key: str) -> tuple[int, str]:
        prev_seg = segments[i - 1] if i > 0 else None
        next_seg = segments[i + 1] if i < n - 1 else None
        gap_duration = pick_gap(seg, next_seg, prev_seg, cfg.gaps)

        char = f" — {seg.get('character')}" if seg.get("character") else ""
        if plan["engine"] == "kokoro":
            label = f"KO:{plan['voice']} @{plan['speed']}x"
        else:
            delivery_label = f" [{plan['delivery']}]" if plan.get("delivery") else ""
            label = (
                f"EL:{plan['voice_id'][:8]} "
                f"stab={plan['settings']['stability']:.2f}{delivery_label}"
            )
        print(
            f"  [{i + 1}/{n}] {seg['type']}{char} → {label} "
            f"({len(seg['text'])} chars) gap={gap_duration:.2f}s"
        )

        samples, sr = _render_one(
            seg["text"], plan, cfg, kokoro, el_engine, effects,
            kokoro_lock, target_sr,
        )
        # All segments now share target_sr (resampled in _render_one). Gap
        # is sized from that same rate, so ffmpeg concat stays consistent.
        gap = np.zeros(int(sr * gap_duration), dtype=np.float32)
        samples = np.concatenate([samples, gap])

        seg_path = seg_dir / f"{i:04d}.wav"
        meta_path = seg_dir / f"{i:04d}.meta.json"
        sf.write(str(seg_path), samples, sr)
        meta_path.write_text(json.dumps({"key": key, "sr": sr}))

        with stats_lock:
            stats[f"{plan['engine']}_chars"] += len(seg["text"])
        return i, str(seg_path)

    if workers > 1 and to_render:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_task, i, seg, plan, key): i
                for (i, seg, plan, key) in to_render
            }
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    i, path = fut.result()
                    paths[i] = path
                except Exception as e:  # noqa: BLE001
                    print(
                        f"  WARNING: segment {idx} failed: {e}",
                        file=sys.stderr,
                    )
    else:
        for (i, seg, plan, key) in to_render:
            try:
                _, path = _task(i, seg, plan, key)
                paths[i] = path
            except Exception as e:  # noqa: BLE001
                print(f"  WARNING: segment {i} failed: {e}", file=sys.stderr)

    if engine == "hybrid":
        print(
            f"  Kokoro: {stats['kokoro_chars']} chars | "
            f"ElevenLabs: {stats['elevenlabs_chars']} chars"
        )
    elif engine == "elevenlabs":
        print(f"  Total characters sent to ElevenLabs: {stats['elevenlabs_chars']}")

    return [p for p in paths if p is not None]


# --- CLI ----------------------------------------------------------------


def _validate_output_extension(output: Path, fmt: str) -> None:
    """Warn if --output's extension doesn't match --format."""
    ext = output.suffix.lower()
    if fmt == "aac" and ext not in (".m4b", ".m4a", ".aac"):
        print(
            f"WARNING: --format aac but --output is '{ext}'. The file will be "
            f"AAC-encoded regardless of its extension; pick .m4b for audiobook "
            f"players to recognize it.",
            file=sys.stderr,
        )
    elif fmt == "wav" and ext != ".wav":
        print(
            f"WARNING: --format wav but --output is '{ext}'. The file will be "
            f"PCM WAV regardless of its extension.",
            file=sys.stderr,
        )


def main():
    parser = argparse.ArgumentParser(
        description="Multi-voice audiobook generator (audiobooker)",
    )
    parser.add_argument(
        "--version", action="version", version=f"audiobooker {__version__}"
    )
    parser.add_argument("chapter_file", help="Path to a chapter .txt file")
    parser.add_argument("--cast", required=True, help="Path to cast.yaml")
    parser.add_argument("--output", help="Output path (default: <output_dir>/<hash>.<format>)")
    parser.add_argument("--force", action="store_true", help="Regenerate even if cached")
    parser.add_argument(
        "--format",
        choices=["wav", "aac"],
        default="aac",
        help="Output format: aac (default, 64k stereo M4B audiobook container, "
             "~10-15x smaller) or wav (raw PCM)",
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
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Parallel workers for rendering. Defaults to 4 when ElevenLabs is "
             "in use (HTTP-bound, parallelizes well) and 1 for Kokoro-only "
             "(CPU-bound, already multi-threaded internally).",
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
    if args.output:
        _validate_output_extension(output_path, args.format)

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
    print(f"audiobooker {__version__}")
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
    workers = args.workers
    if workers is None:
        workers = 4 if args.engine in ("elevenlabs", "hybrid") else 1
    print(
        f"Generating audio ({len(segments)} segments) with engine={args.engine}, "
        f"workers={workers}..."
    )
    effects = EffectRegistry(cfg.effects)
    seg_dir = output_dir / f"{content_hash}{suffix}-segments"

    kokoro: KokoroEngine | None = None
    el_engine: ElevenLabsEngine | None = None
    if args.engine in ("kokoro", "hybrid"):
        kokoro = KokoroEngine(
            cfg.kokoro["model"], cfg.kokoro["voices"], cfg.kokoro.get("language", "en-us")
        )
    if args.engine in ("elevenlabs", "hybrid"):
        el_engine = ElevenLabsEngine(
            api_url=cfg.elevenlabs["api_url"],
            model=cfg.elevenlabs["model"],
            api_key=cfg.get_api_key("elevenlabs"),
            output_format=cfg.elevenlabs["output_format"],
        )

    t0 = time.time()
    wav_paths = render_segments(
        segments,
        seg_dir,
        cfg,
        args.engine,
        kokoro,
        el_engine,
        effects,
        force=args.force,
        workers=workers,
    )
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
