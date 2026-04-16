"""ElevenLabs Sound Generation CLI.

Generates a sound effect WAV from a text prompt using the ElevenLabs
Sound Generation API. Drop the resulting file in your sfx_dir and
reference it from sfx-config.json.

Usage:
    audiobooker-sfx-gen "metallic clang in a long corridor" out/clang.wav
    audiobooker-sfx-gen "low ominous rumble" out/rumble.wav --duration 5
    audiobooker-sfx-gen "soft electronic chirp" out/chirp.wav --duration 1.5 --prompt-influence 0.5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from audiobooker.config import DEFAULT_ELEVENLABS, load_cast
from audiobooker.retry import with_retry


SOUND_GEN_URL = "https://api.elevenlabs.io/v1/sound-generation"


def generate_sfx(
    prompt: str,
    output_path: Path,
    api_key: str,
    duration_seconds: float | None = None,
    prompt_influence: float = 0.3,
    output_format: str = "pcm_24000",
) -> None:
    """Call ElevenLabs Sound Generation API and save the result.

    duration_seconds: 0.5 to 22.0. None lets the API choose based on the prompt.
    prompt_influence: 0.0 (more creative) to 1.0 (more literal). Default 0.3.
    output_format: pcm_24000 → 24kHz mono PCM (matches audiobooker default).
    """
    body: dict = {
        "text": prompt,
        "prompt_influence": prompt_influence,
    }
    if duration_seconds is not None:
        body["duration_seconds"] = duration_seconds

    def _call() -> bytes:
        resp = httpx.post(
            SOUND_GEN_URL,
            params={"output_format": output_format},
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0),
        )
        if resp.status_code == 401:
            raise RuntimeError("ElevenLabs: invalid API key")
        if resp.status_code == 429:
            raise RuntimeError("ElevenLabs: rate limited or character quota exceeded")
        resp.raise_for_status()
        return resp.content

    content = with_retry(_call, what="elevenlabs sfx-gen")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_format.startswith("pcm_"):
        # Wrap raw PCM in a WAV header so the file is playable.
        import numpy as np
        import soundfile as sf

        sample_rate = int(output_format.split("_")[-1])
        samples = np.frombuffer(content, dtype=np.int16).astype(np.float32) / 32768.0
        sf.write(str(output_path), samples, sample_rate)
    else:
        # mp3, etc. — write the bytes verbatim.
        output_path.write_bytes(content)


def main():
    parser = argparse.ArgumentParser(
        description="Generate sound effects with ElevenLabs Sound Generation API"
    )
    parser.add_argument("prompt", help="Text prompt describing the sound effect")
    parser.add_argument("output", help="Output audio file path")
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Duration in seconds (0.5 to 22.0). Omit to let the API choose.",
    )
    parser.add_argument(
        "--prompt-influence",
        type=float,
        default=0.3,
        help="0.0 (more creative) to 1.0 (more literal). Default 0.3.",
    )
    parser.add_argument(
        "--format",
        default="pcm_24000",
        help="Output format (pcm_24000 default; also mp3_44100_128, etc.)",
    )
    parser.add_argument(
        "--cast",
        help="Optional cast.yaml — only its elevenlabs.api_key_env is used",
    )
    args = parser.parse_args()

    # Resolve API key
    if args.cast:
        cfg = load_cast(args.cast)
        api_key = cfg.get_api_key("elevenlabs")
    else:
        import os

        env_var = DEFAULT_ELEVENLABS["api_key_env"]
        api_key = os.environ.get(env_var)
        if not api_key:
            env_file = Path.home() / ".audiobooker.env"
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if line.startswith(f"{env_var}="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        if not api_key:
            print(
                f"ERROR: No {env_var} found in environment or ~/.audiobooker.env",
                file=sys.stderr,
            )
            sys.exit(1)

    output_path = Path(args.output)
    print(f"Generating: {args.prompt!r}")
    if args.duration:
        print(f"  Duration: {args.duration}s")
    print(f"  Prompt influence: {args.prompt_influence}")
    print(f"  Output: {output_path}")

    try:
        generate_sfx(
            prompt=args.prompt,
            output_path=output_path,
            api_key=api_key,
            duration_seconds=args.duration,
            prompt_influence=args.prompt_influence,
            output_format=args.format,
        )
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    size_kb = output_path.stat().st_size / 1024
    print(f"Wrote {output_path} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
