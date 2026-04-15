# cast.yaml reference

> `cast.yaml` is the single source of project-specific configuration. Everything that varies from book to book lives here. The Python code itself is generic; if your audiobook sounds wrong, it is almost always something to fix in `cast.yaml`, not the code.

An example file ships with the project at **`examples/cast.example.yaml`**: a minimal cast with a narrator, two characters, and gender fallbacks. Look in the `examples/` directory for additional cast files showing more advanced use (effect chains, pronunciation overrides, opt-in parser features).

Below is the full schema with every field documented.

## Top-level structure

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

## `engine_defaults`

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

For the full LLM-backend matrix (Anthropic, Ollama, serverless), see [llm-backends.md](llm-backends.md).

## `kokoro`

```yaml
kokoro:
  model: /opt/kokoro/kokoro-v1.0.onnx
  voices: /opt/kokoro/voices-v1.0.bin
  language: en-us               # see kokoro-onnx for supported languages
```

Where to find your downloaded Kokoro model files. The defaults assume `/opt/kokoro/`, but any readable path works. Kokoro ships ~54 voices in `voices-v1.0.bin` covering American/British male/female and a few European voices (`af_*`, `am_*`, `bf_*`, `bm_*`, `ef_*`, `em_*`).

## `elevenlabs` (optional)

```yaml
elevenlabs:
  api_url: https://api.elevenlabs.io/v1/text-to-speech
  model: eleven_multilingual_v2
  api_key_env: ELEVENLABS_API_KEY
  output_format: pcm_24000
```

Only needed for `--engine elevenlabs` or `--engine hybrid`. You can omit this whole block if you only ever use Kokoro.

## `cast`: the centerpiece

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

### Per-character fields

| Field | Required | Default | Description |
|---|---|---|---|
| `kokoro_voice` | for `--engine kokoro/hybrid` | _none_ | Kokoro voice name like `am_michael`, `af_bella`, `bm_george`. See [kokoro-onnx voices](https://github.com/thewh1teagle/kokoro-onnx) for the full list. |
| `elevenlabs_voice` | for `--engine elevenlabs/hybrid` | _none_ | ElevenLabs voice ID. Use a public preset from your ElevenLabs voice library or your own custom voice. |
| `elevenlabs_settings` | no | `{stability: 0.55, similarity_boost: 0.75, style: 0.20}` | Per-character voice settings. Higher stability = more consistent / less expressive. Lower stability + higher style = more dramatic delivery. |
| `speed` | no | `1.0` | Playback speed multiplier passed to Kokoro. Roughly 0.85 to 1.15 is the useful range. |
| `effect` | no | none | Name of an effect chain in the `effects` table. Applied after TTS render. |
| `gender` | no | none | Optional. Used internally if the parser tags an unnamed character with this name and you want to influence which fallback gets used. Most casts won't need this. |

### How voice resolution works

For each segment the parser produces, audiobooker walks the cast table in this order:

1. If the segment is `type: narration`, use `narrator`.
2. If the segment is `type: dialogue` and the parser identified a character name that exists in `cast`, use that entry.
3. Otherwise, use `_female_fallback` or `_male_fallback` based on the parser's gender guess.
4. Last resort: fall back to `narrator`.

If you see the wrong voice on a line, the cause is almost always that the parser tagged a character name that does not exist in `cast`. Add the entry and re-run with `--force` to clear the segment cache.

## `pronunciations` (optional)

```yaml
pronunciations:
  Aeon: ay-on
  Anna: ah-nah
  Vagra: way-grah
```

Case-insensitive substring replacement applied to every segment before TTS. Use this to fix fantasy names, place names, technical terms, anything Kokoro mispronounces. Built-in fixes already handle: em-dash to comma pause, period to "..." for slightly longer pauses, `kg` / `cm` / `km` / `mm` spelled out, and thousands separators stripped from numbers (`1,400` → `1400`).

## `effects` (optional)

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

## `gaps` (optional)

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
