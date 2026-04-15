# audiobooker

**Multi-voice audiobook generator.** Take a chapter of prose, get back a single M4B file with a different voice for the narrator and each character, optional sound effects per character, and optional ambient SFX layered underneath.

The whole project is driven by **one configuration file**: `cast.yaml`. You define which voices speak which characters, what pronunciation to override, and what audio effects to apply. Everything else is mechanical.

```
chapter.txt + cast.yaml ─┐
                         │
                         ▼
                  ┌─────────────┐
                  │  LLM parser │   split into segments per character,
                  │  (Claude /  │   dialogue tags detected and attributed
                  │   any LLM)  │
                  └──────┬──────┘
                         │
                         ▼
                  ┌─────────────┐
                  │     TTS     │   each segment rendered to a small WAV
                  │   render    │   (Kokoro local / ElevenLabs cloud /
                  │             │   hybrid + per-segment pedalboard effects)
                  └──────┬──────┘
                         │
                     many small
                     WAV files
                         │
                         ▼
                  ┌─────────────┐
                  │   ffmpeg    │   concat all segment WAVs into one file
                  │   concat    │   with context-aware silence gaps
                  └──────┬──────┘
                         │
                         ▼
                  ┌─────────────┐
                  │  AAC encode │   re-encode to 64k stereo M4B audiobook
                  │  (default)  │   container (--format wav to skip this)
                  └──────┬──────┘
                         │
                         ▼
                   chapter.m4b
                   (+ optional SFX mix before encode)
                   (+ optional book assembly via audiobooker-assemble
                      with chapter markers for the full book)
```

## Status

Beta. The pipeline works end-to-end and has been used in private to render real fiction at chapter scale, but the public API surface is still stabilizing. Expect minor breaking changes until 1.0.

## Requirements

- **Python 3.10+** and **ffmpeg**
- **[Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx)** model files (`kokoro-v1.0.onnx` and `voices-v1.0.bin`) for the default local TTS
- **An LLM API key** for the chapter parser. Anthropic native is the default; any OpenAI-compatible endpoint works (Ollama for local/free, OpenRouter / DigitalOcean Gradient / vLLM / LM Studio for serverless). Full backend options in [docs/llm-backends.md](docs/llm-backends.md).
- **[ElevenLabs](https://elevenlabs.io/)** account (optional, only for `--engine elevenlabs` / `--engine hybrid`)
- **SFX WAVs** (optional, only for ambient beds and one-shots via `audiobooker-mix-sfx`)

Python deps (`kokoro-onnx`, `soundfile`, `numpy`, `httpx`, `pedalboard`, `pyyaml`) install automatically via pip.

## Install

```bash
git clone https://github.com/draca-glitch/audiobooker.git
cd audiobooker
pip install -e .
```

Then download the Kokoro model files (instructions in the [kokoro-onnx README](https://github.com/thewh1teagle/kokoro-onnx)) and put them somewhere readable. The defaults assume `/opt/kokoro/kokoro-v1.0.onnx` and `/opt/kokoro/voices-v1.0.bin`, but you can change that in `cast.yaml`.

Set your LLM key:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

(Or put it in `~/.audiobooker.env` as `ANTHROPIC_API_KEY=sk-ant-...`. audiobooker reads that file as a fallback.)

## Quickstart

```bash
# 1. Render the bundled park bench example with the default Kokoro engine
audiobooker examples/chapter.example.txt --cast examples/cast.example.yaml

# Output → ./out/<hash>.m4b (AAC 64k stereo, audiobook format with bookmark support)
```

That's it. The first run will:
1. Call your LLM to parse the chapter into segments (cached, won't re-call unless the chapter changes)
2. Render each segment with Kokoro
3. ffmpeg-concat the segments with context-aware silence gaps
4. Re-encode to AAC in an M4B audiobook container (64k stereo, ~10-15x smaller than raw WAV)

Subsequent runs of the same chapter file are cached and return instantly. Pass `--format wav` if you want raw PCM instead of M4B.

**Recommended approach: render one chapter at a time.** audiobooker is designed around per-chapter rendering. Each chapter gets its own M4B file, its own cached segment JSON, and its own per-segment WAVs. This means you can iterate on a single chapter (fix a pronunciation, swap a voice, re-parse after an edit) without touching the rest of the book. Once all chapters are rendered, assemble them into a single book-length M4B with chapter markers using `audiobooker-assemble`.

To use ElevenLabs for character dialogue and Kokoro for narration:

```bash
export ELEVENLABS_API_KEY="..."
audiobooker examples/chapter.example.txt --cast examples/cast.example.yaml --engine hybrid
```

To assemble per-chapter M4B files into a single audiobook with chapter markers:

```yaml
# book.yaml
title: "My Book Title"
author: "Author Name"
chapters:
  - title: "Prologue"
    file: out/abc123.m4b
  - title: "Chapter 1"
    file: out/def456.m4b
```

```bash
audiobooker-assemble book.yaml --output my-book.m4b
```

To layer ambient SFX and one-shots after rendering:

```bash
audiobooker-mix-sfx \
    out/<hash>-segments \
    out/<hash>-segments.json \
    /path/to/sfx-wavs \
    out/chapter-mixed.wav \
    --config examples/chapter-hud-sfx-config.example.json
```

## Recommended workflow: drive it from an AI assistant

The cleanest way to use audiobooker is **through an AI coding assistant** (Claude Code, Cursor, Aider, Continue, etc.) rather than by hand. The workflow is naturally collaborative and the tooling rewards an agent that can read your prose, edit `cast.yaml`, run renders, and iterate on the result.

A typical loop with an AI assistant looks like:

1. Drop your chapter file in the project, ask the AI to read it and identify every speaking character.
2. The AI drafts a `cast.yaml` for you with sensible voice picks, gender fallbacks, and any pronunciations it spots in unusual names.
3. The AI runs `audiobooker --segments-only` to verify the parser tagged everything correctly. If a line ended up on the wrong character, the AI can re-run with a smaller prompt window or fix the cast entry.
4. The AI runs a full render, listens to the result, and either iterates on voice picks / speeds / pronunciations, or moves on to the next chapter.
5. For SFX-heavy scenes, the AI drafts the `sfx-config.json` and uses `audiobooker-sfx-gen` to generate any missing one-shots from text prompts.

You can absolutely run audiobooker by hand, but the configuration files are exactly the kind of thing an LLM is good at producing and refining from natural-language descriptions of what you want each character to sound like. If you have access to a frontier coding assistant, use it.

## Documentation

- **[docs/cast-yaml.md](docs/cast-yaml.md)**: full `cast.yaml` schema reference (engine_defaults, kokoro, elevenlabs, cast, pronunciations, effects, gaps)
- **[docs/llm-backends.md](docs/llm-backends.md)**: chapter-parser LLM options (Anthropic, Ollama, serverless gateways)
- **[docs/engines.md](docs/engines.md)**: TTS engines (kokoro, elevenlabs, hybrid), the cost-per-book table, caching
- **[docs/sfx.md](docs/sfx.md)**: SFX layering, ambient/oneshot config, where to get SFX files, on-demand generation
- **[docs/cli.md](docs/cli.md)**: full CLI reference for all five commands

## Supporting audiobooker

audiobooker is MIT-licensed and developed by one person on a home server, in spare time, mostly because I wanted my own books read aloud with a real cast and could not find a tool that did it the way I wanted. There is no company behind it, no venture-capital pressure, and no plans to add a paid tier. If audiobooker saves you time, helps your fiction sound the way it sounds in your head, or just makes you smile, you can chip in:

- ⭐ **Star the repo**. Costs nothing, helps people find it.
- ☕ **Buy me a coffee**: [buymeacoffee.com/dracaglitch](https://buymeacoffee.com/dracaglitch)
- 💖 **GitHub Sponsors**: [github.com/sponsors/draca-glitch](https://github.com/sponsors/draca-glitch) *(page set up, pending GitHub approval)*
- 🐛 **Open issues** for bugs, broken extractors, weird edge cases you hit on real chapters
- 🛠️ **Send PRs**: extra cast.yaml examples, better pronunciations, integrations with other TTS engines, anything

Sponsorship goes directly toward keeping the development server running and toward more time spent improving audiobooker instead of doing my day job. Nothing you sponsor unlocks paid features. audiobooker stays one piece of software, fully open source, with everything in the public repo.

## License

MIT. See [LICENSE](LICENSE).

## Credits

Built on top of:

- [Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx): local TTS engine
- [pedalboard](https://github.com/spotify/pedalboard): audio effect chains
- [ElevenLabs](https://elevenlabs.io/): optional cloud TTS for premium dialogue
- [Anthropic Claude](https://www.anthropic.com/): default LLM for the chapter parser
- [ffmpeg](https://ffmpeg.org/): segment concatenation
- [soundfile](https://github.com/bastibe/python-soundfile) / [numpy](https://numpy.org/) / [httpx](https://www.python-httpx.org/): Python plumbing
