"""Mix ambient beds and one-shot SFX under audiobook segments.

Takes the segment WAVs from `audiobooker` and layers ambient backgrounds
and spot sound effects at specified positions. Produces a single mixed WAV.

Usage:
    audiobooker-mix-sfx <segments_dir> <segments_json> <sfx_dir> <output.wav> --config sfx.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf


DEFAULT_SAMPLE_RATE = 24000


def load_wav_mono(path: str, sample_rate: int) -> np.ndarray:
    """Load a WAV file as mono float32 at the target sample rate."""
    data, sr = sf.read(path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != sample_rate:
        ratio = sample_rate / sr
        new_len = int(len(data) * ratio)
        x_old = np.linspace(0, 1, len(data))
        x_new = np.linspace(0, 1, new_len)
        data = np.interp(x_new, x_old, data)
    return data


def fade(audio: np.ndarray, sample_rate: int, fade_in_ms: int = 50, fade_out_ms: int = 50) -> np.ndarray:
    """Apply fade in/out to avoid clicks."""
    samples_in = int(sample_rate * fade_in_ms / 1000)
    samples_out = int(sample_rate * fade_out_ms / 1000)
    if samples_in > 0 and len(audio) > samples_in:
        audio[:samples_in] *= np.linspace(0, 1, samples_in)
    if samples_out > 0 and len(audio) > samples_out:
        audio[-samples_out:] *= np.linspace(1, 0, samples_out)
    return audio


def loop_to_length(audio: np.ndarray, target_len: int, sample_rate: int, crossfade_ms: int = 500) -> np.ndarray:
    """Loop an audio clip to fill target_len samples, with crossfade at loop points."""
    if len(audio) >= target_len:
        return audio[:target_len]

    xf_samples = int(sample_rate * crossfade_ms / 1000)
    xf_samples = min(xf_samples, len(audio) // 4)

    result = np.zeros(target_len, dtype=np.float32)
    pos = 0

    chunk_len = min(len(audio), target_len - pos)
    result[pos:pos + chunk_len] = audio[:chunk_len]
    pos += len(audio) - xf_samples

    while pos < target_len:
        chunk_len = min(len(audio), target_len - pos)
        chunk = audio[:chunk_len].copy()
        if xf_samples > 0 and pos > 0:
            overlap = min(xf_samples, chunk_len, target_len - pos)
            fade_out = np.linspace(1, 0, overlap)
            fade_in = np.linspace(0, 1, overlap)
            result[pos:pos + overlap] *= fade_out
            chunk[:overlap] *= fade_in
        end = min(pos + chunk_len, target_len)
        result[pos:end] += chunk[:end - pos]
        pos += len(audio) - xf_samples

    return result[:target_len]


def build_segment_timeline(seg_dir: Path, num_segments: int, sample_rate: int):
    """Build a timeline mapping segment index to (start_sample, end_sample)."""
    timeline = []
    pos = 0
    for i in range(num_segments):
        wav_path = seg_dir / f"{i:04d}.wav"
        if wav_path.exists():
            info = sf.info(str(wav_path))
            n_samples = int(info.frames * sample_rate / info.samplerate)
            timeline.append((pos, pos + n_samples))
            pos += n_samples
        else:
            timeline.append((pos, pos))
    return timeline, pos


def mix_chapter(seg_dir: Path, segments_json: Path, sfx_dir: Path, config: dict, sample_rate: int) -> np.ndarray:
    with open(segments_json) as f:
        segments = json.load(f)

    num_segs = len(segments)
    print(f"Loaded {num_segs} segments from {segments_json.name}")

    timeline, total_samples = build_segment_timeline(seg_dir, num_segs, sample_rate)
    print(f"Total duration: {total_samples / sample_rate:.1f}s ({total_samples} samples)")

    dry = np.zeros(total_samples, dtype=np.float32)
    for i in range(num_segs):
        wav_path = seg_dir / f"{i:04d}.wav"
        if wav_path.exists():
            audio = load_wav_mono(str(wav_path), sample_rate)
            start, end = timeline[i]
            actual_len = min(len(audio), end - start)
            dry[start:start + actual_len] = audio[:actual_len]

    sfx_layer = np.zeros(total_samples, dtype=np.float32)

    # Ambient beds (looped, faded)
    for amb in config.get("ambients", []):
        sfx_path = sfx_dir / amb["file"]
        if not sfx_path.exists():
            print(f"  WARNING: {sfx_path} not found, skipping")
            continue

        start_seg = amb["start_seg"]
        end_seg = min(amb["end_seg"], num_segs - 1)
        volume = amb.get("volume", 0.08)
        start_sample = timeline[start_seg][0]
        end_sample = timeline[end_seg][1]
        duration = end_sample - start_sample
        if duration <= 0:
            continue

        print(f"  Ambient: {amb['file']} segs {start_seg}-{end_seg} vol={volume:.2f} ({duration / sample_rate:.1f}s)")
        audio = load_wav_mono(str(sfx_path), sample_rate)
        looped = loop_to_length(audio, duration, sample_rate)
        looped = fade(looped, sample_rate, fade_in_ms=1000, fade_out_ms=1500)
        sfx_layer[start_sample:end_sample] += looped * volume

    # One-shot SFX
    for shot in config.get("oneshots", []):
        sfx_path = sfx_dir / shot["file"]
        if not sfx_path.exists():
            print(f"  WARNING: {sfx_path} not found, skipping")
            continue

        seg_idx = shot["segment"]
        if seg_idx >= num_segs:
            continue

        volume = shot.get("volume", 0.15)
        offset_ms = shot.get("offset_ms", 0)
        trim_ms = shot.get("trim_ms", 0)

        start_sample = timeline[seg_idx][0] + int(sample_rate * offset_ms / 1000)
        start_sample = max(0, min(start_sample, total_samples - 1))

        print(f"  One-shot: {shot['file']} @ seg {seg_idx} vol={volume:.2f}")
        audio = load_wav_mono(str(sfx_path), sample_rate)

        trim_samples = int(sample_rate * trim_ms / 1000)
        if 0 < trim_samples < len(audio):
            audio = audio[trim_samples:]

        max_dur = shot.get("max_duration_ms", 5000)
        max_samples = int(sample_rate * max_dur / 1000)
        if len(audio) > max_samples:
            audio = audio[:max_samples]

        audio = fade(audio, sample_rate, fade_in_ms=20, fade_out_ms=100)
        end_sample = min(start_sample + len(audio), total_samples)
        actual_len = end_sample - start_sample
        sfx_layer[start_sample:end_sample] += audio[:actual_len] * volume

    mixed = dry + sfx_layer

    # Limiter: instead of scaling the entire file down (which makes voices
    # quiet when SFX peaks are loud), we apply a lookahead limiter that
    # only attenuates samples near loud peaks. Voices stay at full volume,
    # SFX peaks get gently tamed.
    mixed = _limit(mixed, sample_rate, ceiling=0.95, release_ms=50)

    return mixed


def _limit(audio: np.ndarray, sample_rate: int, ceiling: float = 0.95,
           release_ms: float = 50) -> np.ndarray:
    """Simple lookahead peak limiter.

    Instead of scaling the entire file by a single global factor (which
    makes voices quiet when SFX peaks are loud), this applies per-sample
    gain reduction only around loud peaks, with a smooth release so the
    limiting doesn't click.

    For audiobook SFX mixing this means: voices stay at full volume, SFX
    peaks above the ceiling get gently pushed down, and the overall output
    sounds as loud as the voice track rather than as quiet as the
    normalization factor would make it.
    """
    abs_audio = np.abs(audio)
    peak = np.max(abs_audio)

    if peak <= ceiling:
        return audio  # nothing to do

    # Build a per-sample gain envelope
    gain = np.ones_like(audio)
    over = abs_audio > ceiling
    gain[over] = ceiling / abs_audio[over]

    # Smooth the gain envelope with a release filter so transitions
    # don't click. Simple one-pole lowpass on the gain signal.
    release_samples = int(sample_rate * release_ms / 1000)
    if release_samples > 0:
        coeff = np.exp(-1.0 / release_samples)
        smoothed = np.copy(gain)
        for i in range(1, len(smoothed)):
            # Gain can drop instantly (attack=0) but recovers slowly (release)
            if smoothed[i] > smoothed[i - 1]:
                smoothed[i] = coeff * smoothed[i - 1] + (1 - coeff) * smoothed[i]
        gain = smoothed

    limited = audio * gain
    reduction_db = 20 * np.log10(peak / ceiling)
    print(f"  Limiter: peak {peak:.3f} → {ceiling} ({reduction_db:.1f} dB reduction on peaks only)")

    return limited


def main():
    parser = argparse.ArgumentParser(description="Mix SFX into audiobooker segments")
    parser.add_argument("segments_dir", help="Directory with segment WAV files (NNNN.wav)")
    parser.add_argument("segments_json", help="Segments JSON metadata file")
    parser.add_argument("sfx_dir", help="Directory containing SFX WAV files")
    parser.add_argument("output", help="Output WAV path")
    parser.add_argument("--config", required=True, help="SFX config JSON file")
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    mixed = mix_chapter(
        Path(args.segments_dir),
        Path(args.segments_json),
        Path(args.sfx_dir),
        config,
        args.sample_rate,
    )

    sf.write(args.output, mixed, args.sample_rate)
    print(f"\nOutput: {args.output} ({len(mixed) / args.sample_rate:.1f}s)")


if __name__ == "__main__":
    main()
