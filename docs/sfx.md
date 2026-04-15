# SFX layering

> A separate, optional second pass that layers ambient beds and one-shot sound effects under an already-rendered audiobook.

It reads the per-segment WAVs that audiobooker writes during render, plus a JSON config describing what to play where.

```bash
audiobooker-mix-sfx \
    out/<hash>-segments \
    out/<hash>-segments.json \
    /path/to/sfx-wavs \
    out/chapter-mixed.wav \
    --config sfx-config.json
```

## Layer types

Two kinds of layers are supported.

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

See `examples/chapter-hud-sfx-config.example.json` for a full annotated example.

## Where to get the SFX files

Two paths.

### 1. Bring your own

[freesound.org](https://freesound.org/) has a large Creative Commons library; the BBC Sound Effects archive is free for personal use; many game-audio bundles on itch.io are royalty-free. Drop the WAVs in a folder, name them sensibly, reference them from your `sfx-config.json`.

### 2. Generate them on demand with ElevenLabs Sound Generation

ElevenLabs has a separate `/v1/sound-generation` API that takes a text prompt and returns a custom SFX. audiobooker ships a thin CLI for it:

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
