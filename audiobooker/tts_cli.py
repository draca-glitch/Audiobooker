"""Standalone Kokoro CLI: text → WAV. No parsing, no cast, no LLM.

Useful as a quick smoke test of your Kokoro install, or for non-audiobook
TTS calls from other tools.

Usage:
    audiobooker-tts "text to speak" output.wav [--voice af_heart] [--cast cast.yaml]
"""

from __future__ import annotations

import argparse
import sys

import soundfile as sf

from audiobooker.config import DEFAULT_KOKORO, load_cast
from audiobooker.tts import KokoroEngine


def main():
    parser = argparse.ArgumentParser(description="Standalone Kokoro TTS CLI")
    parser.add_argument("text", help="Text to speak")
    parser.add_argument("output", help="Output WAV path")
    parser.add_argument("--voice", default="af_heart", help="Kokoro voice name")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument(
        "--cast",
        help="Optional cast.yaml — only its kokoro.{model,voices,language} block is used",
    )
    args = parser.parse_args()

    if args.cast:
        cfg = load_cast(args.cast)
        model = cfg.kokoro["model"]
        voices = cfg.kokoro["voices"]
        language = cfg.kokoro.get("language", "en-us")
    else:
        model = DEFAULT_KOKORO["model"]
        voices = DEFAULT_KOKORO["voices"]
        language = DEFAULT_KOKORO["language"]

    import os
    if not os.path.exists(model) or not os.path.exists(voices):
        print(f"ERROR: Kokoro model files not found at {model} / {voices}", file=sys.stderr)
        print("Download from https://github.com/thewh1teagle/kokoro-onnx", file=sys.stderr)
        sys.exit(1)

    engine = KokoroEngine(model, voices, language)
    samples, sr = engine.synth(args.text, voice=args.voice, speed=args.speed)
    sf.write(args.output, samples, sr)
    print(f"Wrote {args.output} ({len(samples)/sr:.2f}s)")


if __name__ == "__main__":
    main()
