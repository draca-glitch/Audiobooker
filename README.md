# audiobooker

**Multi-voice audiobook generator.** Take a chapter of prose, get back a single WAV file with a different voice for the narrator and each character, optional sound effects per character, and optional ambient SFX layered underneath.

The whole project is driven by **one configuration file**: `cast.yaml`. You define which voices speak which characters, what pronunciation to override, and what audio effects to apply. Everything else is mechanical.

```
chapter.txt + cast.yaml ─┐
                         │
                         ▼
                  ┌─────────────┐
                  │  LLM parser │   (split into segments per
                  │  (Claude /  │    character, dialogue tags
                  │   any LLM)  │    detected and attributed)
                  └──────┬──────┘
                         │
                         ▼
                  ┌─────────────┐
                  │     TTS     │   (Kokoro local / ElevenLabs cloud /
                  │   render    │    hybrid; per-segment effects via
                  │             │    pedalboard)
                  └──────┬──────┘
                         │
                         ▼
                  ┌─────────────┐
                  │   ffmpeg    │   (concat segments with
                  │   concat    │    context-aware silence gaps)
                  └──────┬──────┘
                         │
                         ▼
                   chapter.wav
                   (+ optional SFX mix in a second pass)
```

## Status

Beta. The pipeline works end-to-end and has been used in private to render real fiction at chapter scale, but the public API surface is still stabilizing. Expect minor breaking changes until 1.0.

---

## Requirements

audiobooker is a thin orchestration layer over a few external pieces. You will need most or all of the following installed before it does anything useful.

### Mandatory

- **Python 3.10+**
- **[Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx)**. Local CPU-only TTS engine. The default voice provider. Free, fast, decent quality, no API key. Download two model files (`kokoro-v1.0.onnx` and `voices-v1.0.bin`) and point `cast.yaml` at them.
- **[ffmpeg](https://ffmpeg.org/)**. Used to concatenate the per-segment WAVs into a single output file. Any modern build works. Install via your package manager (`apt install ffmpeg`, `brew install ffmpeg`, `choco install ffmpeg`).
- **An LLM API for the chapter parser.** audiobooker calls a frontier LLM to split prose into typed segments (narration and dialogue, with each line attributed to a character). Two API shapes are supported:
  - **Anthropic native**: `https://api.anthropic.com/v1` with an `ANTHROPIC_API_KEY`. The default. Recommended.
  - **OpenAI-compatible**: anything that speaks `/chat/completions`: OpenAI itself, DigitalOcean Gradient, vLLM, LM Studio, Ollama with its OpenAI bridge, OpenRouter, etc. Set `compat: openai` in `cast.yaml` and point `base_url` at it.

  Claude Opus or equivalent gives the best parse quality. Smaller models (Sonnet, GPT-4o-mini, Qwen2.5-32B+) work but produce more dialogue-merging errors. The post-processor catches most of them, but quality scales with model.

### Optional

- **[ElevenLabs](https://elevenlabs.io/)** account. Only needed if you want to use `--engine elevenlabs` (full cloud render) or `--engine hybrid` (cloud for character dialogue, local Kokoro for narration). The free tier of 10k chars/month is enough to test. The Creator tier (100k chars/month) typically covers a 100k-word book in hybrid mode.
- **Sound effect WAV files**. Only needed if you want ambient beds or one-shot SFX layered under the audio in a second pass with `audiobooker-mix-sfx`. You provide your own files; freesound.org has a large CC-licensed library, or generate them on demand with `audiobooker-sfx-gen` (see below).

### Python dependencies

Installed automatically via `pip install audiobooker` (or `pip install -e .` from a local clone):

| Package | What it does |
|---|---|
| `kokoro-onnx` | local TTS inference |
| `soundfile` | WAV read/write |
| `numpy` | audio sample arrays, mixing |
| `httpx` | LLM and ElevenLabs API calls |
| `pedalboard` | audio effect chains (pitch shift, reverb, distortion, bitcrush, chorus, etc.) |
| `pyyaml` | reading `cast.yaml` |

---

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

# Output → ./out/<hash>.wav
```

That's it. The first run will:
1. Call your LLM to parse the chapter into segments (cached, won't re-call unless the chapter changes)
2. Render each segment with Kokoro
3. ffmpeg-concat the segments with context-aware silence gaps

Subsequent runs of the same chapter file are cached and return instantly.

To use ElevenLabs for character dialogue and Kokoro for narration:

```bash
export ELEVENLABS_API_KEY="..."
audiobooker examples/chapter.example.txt --cast examples/cast.example.yaml --engine hybrid
```

To layer ambient SFX and one-shots after rendering:

```bash
audiobooker-mix-sfx \
    out/<hash>-segments \
    out/<hash>-segments.json \
    /path/to/sfx-wavs \
    out/chapter-mixed.wav \
    --config examples/sfx-config.example.json
```

---

## Recommended workflow: drive it from an AI assistant

The cleanest way to use audiobooker is **through an AI coding assistant** (Claude Code, Cursor, Aider, Continue, etc.) rather than by hand. The workflow is naturally collaborative and the tooling rewards an agent that can read your prose, edit `cast.yaml`, run renders, and iterate on the result.

A typical loop with an AI assistant looks like:

1. Drop your chapter file in the project, ask the AI to read it and identify every speaking character.
2. The AI drafts a `cast.yaml` for you with sensible voice picks, gender fallbacks, and any pronunciations it spots in unusual names.
3. The AI runs `audiobooker --segments-only` to verify the parser tagged everything correctly. If a line ended up on the wrong character, the AI can re-run with a smaller prompt window or fix the cast entry.
4. The AI runs a full render, listens to the result, and either iterates on voice picks / speeds / pronunciations, or moves on to the next chapter.
5. For SFX-heavy scenes, the AI drafts the `sfx-config.json` and uses `audiobooker-sfx-gen` to generate any missing one-shots from text prompts.

You can absolutely run audiobooker by hand, but the configuration files are exactly the kind of thing an LLM is good at producing and refining from natural-language descriptions of what you want each character to sound like. If you have access to a frontier coding assistant, use it.

---

## LLM backend

The chapter parser is the only piece of audiobooker that talks to an LLM. Three backends are supported and you switch between them by editing four lines in `cast.yaml`. No code changes, no separate install paths.

### Option A: Anthropic native (default, recommended)

```yaml
engine_defaults:
  llm:
    base_url: https://api.anthropic.com/v1
    model: claude-opus-4-6
    api_key_env: ANTHROPIC_API_KEY
    compat: anthropic
    max_tokens: 16384
```

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Best parse quality. Around $3-5 to parse a 100k-word book on Opus, around $0.15 on Sonnet 4.6. Recommended for most users.

### Option B: Ollama (local, free, no content filter)

```yaml
engine_defaults:
  llm:
    base_url: http://localhost:11434/v1
    model: qwen2.5:32b
    api_key_env: OLLAMA_API_KEY      # Ollama ignores the key, but the env var must exist
    compat: openai
    max_tokens: 16384
```

```bash
ollama pull qwen2.5:32b
export OLLAMA_API_KEY="dummy"        # any non-empty value
```

100% local, zero cost, **no content filtering**. The last point matters for audiobooks: cloud LLMs will sometimes refuse to parse fiction containing violence, sexual content, or even certain literary classics (it can happen with Sherlock Holmes). Local models have no such restriction. Quality is somewhat below frontier models, so use 32B or larger if you can afford the RAM. Llama 3.3 70B is excellent if you have a beefy machine. Ollama exposes an OpenAI-compatible endpoint at `/v1`, which is why the `compat` field is `openai`.

### Option C: Serverless / proxied inference (DigitalOcean Gradient, OpenRouter, Together, vLLM, LM Studio, etc.)

Anything that speaks the OpenAI `/chat/completions` shape works:

```yaml
engine_defaults:
  llm:
    base_url: https://inference.do-ai.run/v1     # or https://openrouter.ai/api/v1, etc.
    model: anthropic-claude-opus-4.6              # whatever the provider exposes
    api_key_env: DO_GRADIENT_API_KEY              # or OPENROUTER_API_KEY, etc.
    compat: openai
    max_tokens: 16384
```

```bash
export DO_GRADIENT_API_KEY="..."
```

Useful when you want frontier model quality without managing per-vendor billing relationships, when your preferred provider isn't Anthropic directly, or when you're already paying for inference through a unified API gateway.

---

## The cast.yaml file

`cast.yaml` is the single source of project-specific configuration. Everything that varies from book to book lives here. The Python code itself is generic; if your audiobook sounds wrong, it is almost always something to fix in `cast.yaml`, not the code.

An example file ships with the project at **`examples/cast.example.yaml`**: a minimal cast with a narrator, two characters, and gender fallbacks. Look in the `examples/` directory for additional cast files showing more advanced use (effect chains, pronunciation overrides, opt-in parser features).

Below is the full schema with every field documented.

### Top-level structure

```yaml
engine_defaults:    # output dir, sample rate, LLM endpoint
  ...
kokoro:             # Kokoro model paths and language
  ...
elevenlabs:         # ElevenLabs API config (only needed for --engine elevenlabs/hybrid)
  ...
gaps:               # optional override of context-aware silence durations
  ...
cast:               # the heart of the file: character → voice mappings
  ...
pronunciations:     # optional: phonetic spelling overrides for tricky words
  ...
effects:            # optional: pedalboard effect chains by name
  ...
```

Only `cast` is mandatory, and within `cast` only the `narrator` entry is required. Everything else has sensible defaults.

### `engine_defaults`

```yaml
engine_defaults:
  output_dir: ./out               # where rendered WAVs and segment caches go
  sample_rate: 24000              # Kokoro is 24kHz native; leave this unless you know why
  llm:
    base_url: https://api.anthropic.com/v1
    model: claude-opus-4-6
    api_key_env: ANTHROPIC_API_KEY    # only the env var NAME, never put a key here
    compat: anthropic                  # "anthropic" or "openai"
    max_tokens: 16384
```

`api_key_env` is the **name** of the environment variable to read. audiobooker never stores actual key values in `cast.yaml`. The key is loaded from the env at runtime, with a fallback to `~/.audiobooker.env` if the env var is unset.

### `kokoro`

```yaml
kokoro:
  model: /opt/kokoro/kokoro-v1.0.onnx
  voices: /opt/kokoro/voices-v1.0.bin
  language: en-us               # see kokoro-onnx for supported languages
```

Where to find your downloaded Kokoro model files. The defaults assume `/opt/kokoro/`, but any readable path works. Kokoro ships ~54 voices in `voices-v1.0.bin` covering American/British male/female and a few European voices (`af_*`, `am_*`, `bf_*`, `bm_*`, `ef_*`, `em_*`).

### `elevenlabs` (optional)

```yaml
elevenlabs:
  api_url: https://api.elevenlabs.io/v1/text-to-speech
  model: eleven_multilingual_v2
  api_key_env: ELEVENLABS_API_KEY
  output_format: pcm_24000
```

Only needed for `--engine elevenlabs` or `--engine hybrid`. You can omit this whole block if you only ever use Kokoro.

### `cast`: the centerpiece

This is where you define who speaks each line.

```yaml
cast:
  narrator:                       # REQUIRED
    kokoro_voice: am_michael
    speed: 1.0
    elevenlabs_voice: JBFqnCBsd6RMkjVDRZzb     # optional, only for --engine elevenlabs/hybrid
    elevenlabs_settings:                       # optional
      stability: 0.65
      similarity_boost: 0.75
      style: 0.15

  Anna:                           # any character name as it appears in the chapter
    kokoro_voice: af_bella
    speed: 0.95

  Marcus:
    kokoro_voice: bm_lewis
    speed: 1.0
    effect: dark                  # optional, references the `effects` table

  # FALLBACK ENTRIES (optional, used when the parser tags an unnamed character by gender):

  _female_fallback:
    kokoro_voice: af_jessica
  _male_fallback:
    kokoro_voice: am_liam
```

#### Per-character fields

| Field | Required | Default | Description |
|---|---|---|---|
| `kokoro_voice` | for `--engine kokoro/hybrid` | _none_ | Kokoro voice name like `am_michael`, `af_bella`, `bm_george`. See [kokoro-onnx voices](https://github.com/thewh1teagle/kokoro-onnx) for the full list. |
| `elevenlabs_voice` | for `--engine elevenlabs/hybrid` | _none_ | ElevenLabs voice ID. Use a public preset from your ElevenLabs voice library or your own custom voice. |
| `elevenlabs_settings` | no | `{stability: 0.55, similarity_boost: 0.75, style: 0.20}` | Per-character voice settings. Higher stability = more consistent / less expressive. Lower stability + higher style = more dramatic delivery. |
| `speed` | no | `1.0` | Playback speed multiplier passed to Kokoro. Roughly 0.85 to 1.15 is the useful range. |
| `effect` | no | none | Name of an effect chain in the `effects` table. Applied after TTS render. |
| `gender` | no | none | Optional. Used internally if the parser tags an unnamed character with this name and you want to influence which fallback gets used. Most casts won't need this. |

#### How voice resolution works

For each segment the parser produces, audiobooker walks the cast table in this order:

1. If the segment is `type: narration`, use `narrator`.
2. If the segment is `type: dialogue` and the parser identified a character name that exists in `cast`, use that entry.
3. Otherwise, use `_female_fallback` or `_male_fallback` based on the parser's gender guess.
4. Last resort: fall back to `narrator`.

If you see the wrong voice on a line, the cause is almost always that the parser tagged a character name that does not exist in `cast`. Add the entry and re-run with `--force` to clear the segment cache.

### `pronunciations` (optional)

```yaml
pronunciations:
  Aeon: ay-on
  Anna: ah-nah
  Vagra: way-grah
```

Case-insensitive substring replacement applied to every segment before TTS. Use this to fix fantasy names, place names, technical terms, anything Kokoro mispronounces. Built-in fixes already handle: em-dash to comma pause, period to "..." for slightly longer pauses, `kg` / `cm` / `km` / `mm` spelled out, and thousands separators stripped from numbers (`1,400` → `1400`).

### `effects` (optional)

```yaml
effects:
  robot:
    - bitcrush: { bit_depth: 8 }
    - chorus: { rate_hz: 0.5, depth: 0.2 }

  demon:
    - pitch_shift: { semitones: -5 }
    - distortion: { drive_db: 15 }
    - reverb: { room_size: 0.6, wet_level: 0.3 }
```

Each named effect is a list of pedalboard plugins, applied in order. Each list item is a single-key mapping where the key is the plugin name and the value is the kwargs passed to it.

Supported plugin names: `pitch_shift`, `reverb`, `distortion`, `bitcrush`, `chorus`, `compressor`, `delay`, `gain`, `highpass`, `lowpass`. See the [pedalboard docs](https://github.com/spotify/pedalboard) for parameter reference.

To use an effect, set `effect: <name>` on the relevant cast entry.

### `gaps` (optional)

```yaml
gaps:
  dialogue_tag: 0.25            # after a short narration between two quotes ("he said")
  dialogue_continuation: 0.35   # dialogue → dialogue, same speaker
  default: 0.55                 # generic between-segment gap
  speaker_change: 0.75          # dialogue → dialogue, different speaker
  narration: 1.0                # after a long narration block
  paragraph: 1.3                # after a very long narration / paragraph break
```

Trailing silence (in seconds) inserted after each segment before concatenation. The defaults work for most prose; only override these if your specific narration style needs different pacing.

---

## Engines

### `--engine kokoro` (default)

Local, free, ~real-time on a modern CPU, no API key. Kokoro's narrator voices (`am_michael`, `bm_george`, `af_bella`, etc.) are good. Clear, well-paced, and pleasant for long-form listening. Use this for everything by default.

The honest weakness of Kokoro is **per-character differentiation**. The voice library has range, but when you map five or six characters to it, the results tend to converge in tone and listeners start to lose track of who's speaking. For a single-narrator book or a story with one or two characters, Kokoro alone is excellent. For an ensemble cast, see hybrid mode below.

### `--engine elevenlabs`

Full cloud render. Premium voices throughout, the strongest per-character differentiation, and emotion-driven delivery via the parser-supplied `delivery` field. Costs characters from your ElevenLabs quota for **every** segment, including narration, which is the bulk of the text. Expensive on long books, and usually overkill. Most users want hybrid instead.

### `--engine hybrid` (recommended for ensemble fiction)

This is the mode that exists specifically because **Kokoro is great at narration but weaker at distinguishing many character voices**, while **ElevenLabs is great at character voices but expensive at narration scale**. Hybrid lets you use each engine for what it is good at:

- narration → **Kokoro** (free, plentiful, listeners spend most of their time here)
- named character dialogue → **ElevenLabs** (where the per-character differentiation matters)

#### Why this is dramatically cheaper than pure ElevenLabs

**Most book text is narration, not dialogue.** This is the key insight that makes hybrid affordable. Across mainstream commercial fiction, dialogue typically accounts for **only 20 to 30 percent of total characters**. Literary fiction often runs lower (15 to 20 percent).

That means in hybrid mode you are only sending the small minority of the book, the spoken character lines, to the paid engine. Everything else (the bulk of the text) renders for free on Kokoro.

A worked example for a 100,000-word novel (≈ 550,000 characters):

| Mode | Chars billed to ElevenLabs | Approx ElevenLabs cost | Plan needed |
|---|---|---|---|
| **Pure ElevenLabs** | ~550,000 (100%) | ~$11 to $13 per book | Creator tier (100k/mo) used up in ~6 days |
| **Hybrid, dialogue-heavy fiction** (~30% dialogue) | ~165,000 | ~$3 to $4 per book | Creator tier covers it cleanly |
| **Hybrid, average commercial fiction** (~25% dialogue) | ~140,000 | ~$2.50 to $3 per book | Creator tier with margin to spare |
| **Hybrid, literary fiction** (~15% dialogue) | ~85,000 | ~$1.70 per book | Creator tier with lots of margin |

In other words, **hybrid is roughly 3 to 6 times cheaper than pure ElevenLabs**, depending on how much of your book is dialogue. The narrator is local and free. The character voices are still premium. The audiobook still sounds like it has a real cast.

Concrete example with the bundled park bench chapter (~320 words, mostly dialogue): hybrid mode sends ~180 characters to ElevenLabs, well under one cent.

---

## SFX layering (`audiobooker-mix-sfx`)

A separate, optional second pass that layers ambient beds and one-shot sound effects under an already-rendered audiobook. It reads the per-segment WAVs that audiobooker writes during render, plus a JSON config describing what to play where.

```bash
audiobooker-mix-sfx \
    out/<hash>-segments \
    out/<hash>-segments.json \
    /path/to/sfx-wavs \
    out/chapter-mixed.wav \
    --config sfx-config.json
```

Two kinds of layers are supported:

**Ambients**: looped, faded background beds tied to a segment range:

```json
"ambients": [
  {
    "file": "corridor-hum.wav",
    "start_seg": 0,
    "end_seg": 30,
    "volume": 0.06
  }
]
```

**One-shots**: single SFX hits aligned to a specific segment:

```json
"oneshots": [
  {
    "file": "light-flicker.wav",
    "segment": 0,
    "offset_ms": 200,
    "volume": 0.18,
    "max_duration_ms": 1500,
    "trim_ms": 0
  }
]
```

See `examples/sfx-config.example.json` for a full annotated example.

### Where to get the SFX files

Two paths:

**1. Bring your own.** [freesound.org](https://freesound.org/) has a large Creative Commons library; the BBC Sound Effects archive is free for personal use; many game-audio bundles on itch.io are royalty-free. Drop the WAVs in a folder, name them sensibly, reference them from your `sfx-config.json`.

**2. Generate them on demand with ElevenLabs Sound Generation.** ElevenLabs has a separate `/v1/sound-generation` API that takes a text prompt and returns a custom SFX. audiobooker ships a thin CLI for it:

```bash
audiobooker-sfx-gen "metallic clang in a long corridor" sfx/clang.wav --duration 2
audiobooker-sfx-gen "low ominous rumble"                sfx/rumble.wav --duration 5
audiobooker-sfx-gen "soft electronic chirp"             sfx/chirp.wav  --duration 1.2
```

Each call uses your `ELEVENLABS_API_KEY` and counts against your ElevenLabs character/SFX quota. Generated WAVs are 24kHz mono PCM by default, which matches the audiobooker pipeline natively. Drop them in your `sfx_dir` and reference them from `sfx-config.json` exactly like a downloaded file. Useful for scenes where you can't find exactly the right sound, or when you want a custom-tuned ambient bed for a specific chapter.

Flags:
- `--duration N`: clip length in seconds (0.5 to 22.0). Omit to let the API choose.
- `--prompt-influence 0.0 to 1.0`: 0 is more creative, 1 is more literal. Default 0.3.
- `--format pcm_24000`: output format. Default matches audiobooker; `mp3_44100_128` also works.

---

## Caching

audiobooker caches aggressively, keyed on the MD5 of the chapter text:

- The parsed segment JSON is cached so you only pay for one LLM call per chapter, even across many re-renders.
- The final concatenated WAV is cached so re-running on an unchanged chapter file is instant.
- The per-segment WAVs are kept in `<hash>-segments/` so SFX mixing can re-read them without re-rendering.

If you edit the chapter file by even one character, the hash changes and everything re-runs. To force re-render of an unchanged chapter, pass `--force`.

Each engine has its own cache suffix (`-el` for ElevenLabs, `-hy` for hybrid) so the three engines do not collide and you can render the same chapter in different engines for comparison.

---

## CLI reference

### `audiobooker`

```
audiobooker <chapter_file> --cast <cast.yaml> [options]

  --output PATH            Override output WAV path
  --force                  Regenerate even if cached
  --segments-only          Only run the parser, skip TTS rendering
  --engine {kokoro,elevenlabs,hybrid}
                           TTS engine (default: kokoro)
```

### `audiobooker-mix-sfx`

```
audiobooker-mix-sfx <segments_dir> <segments_json> <sfx_dir> <output.wav>
                    --config <sfx-config.json>
                    [--sample-rate 24000]
```

### `audiobooker-tts`

Standalone Kokoro CLI for quick smoke tests of your install. No parsing, no cast file required.

```
audiobooker-tts "text to speak" out.wav [--voice af_heart] [--speed 1.0]
```

### `audiobooker-sfx-gen`

Generate a sound effect from a text prompt using ElevenLabs Sound Generation. Useful for filling in spot SFX or ambient beds without hunting through sample libraries.

```
audiobooker-sfx-gen "<prompt>" <output.wav>
                    [--duration N]            # 0.5 to 22.0 seconds
                    [--prompt-influence 0.3]  # 0.0 = creative, 1.0 = literal
                    [--format pcm_24000]      # default matches audiobooker pipeline
                    [--cast cast.yaml]        # optional, only its api_key_env is used
```

---

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
