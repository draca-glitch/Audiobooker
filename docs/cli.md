# CLI reference

> All five commands installed by audiobooker.

## `audiobooker`

The main render command. Takes a chapter file plus a cast, produces an M4B (or WAV).

```
audiobooker <chapter_file> --cast <cast.yaml> [options]

  --output PATH            Override output path
  --force                  Regenerate even if cached
  --segments-only          Only run the parser, skip TTS rendering
  --engine {kokoro,elevenlabs,hybrid}
                           TTS engine (default: kokoro)
  --format {aac,wav}       Output format (default: aac, produces .m4b audiobook container)
```

`--segments-only` is handy when iterating: parse the chapter once, inspect the segment JSON, fix any miss-attributed dialogue in the cast, then run the full render. The segment JSON is cached so the LLM only gets called once per chapter regardless.

## `audiobooker-assemble`

Assemble per-chapter M4B files into a single book-length M4B with chapter markers. No re-encoding; just remuxes the existing AAC streams into one container.

```
audiobooker-assemble <book.yaml> --output <book.m4b>
```

The manifest YAML lists chapters in order with titles and file paths:

```yaml
title: "My Book Title"
author: "Author Name"
chapters:
  - title: "Prologue"
    file: out/abc123.m4b
  - title: "Chapter 1"
    file: out/def456.m4b
  - title: "Chapter 2"
    file: out/ghi789.m4b
```

The assembler probes each file for duration and generates ffmpeg chapter metadata automatically. Audiobook players (Apple Books, Prologue, Smart Audiobook Player, etc.) can navigate the resulting M4B by chapter.

## `audiobooker-mix-sfx`

Layer ambient beds and one-shot SFX over an already-rendered chapter. See [sfx.md](sfx.md) for the JSON config format.

```
audiobooker-mix-sfx <segments_dir> <segments_json> <sfx_dir> <output.wav>
                    --config <sfx-config.json>
                    [--sample-rate 24000]
```

## `audiobooker-tts`

Standalone Kokoro CLI for quick smoke tests of your install. No parsing, no cast file required.

```
audiobooker-tts "text to speak" out.wav [--voice af_heart] [--speed 1.0]
```

Useful when you want to audition a Kokoro voice without setting up a chapter / cast workflow.

## `audiobooker-sfx-gen`

Generate a sound effect from a text prompt using ElevenLabs Sound Generation. Useful for filling in spot SFX or ambient beds without hunting through sample libraries.

```
audiobooker-sfx-gen "<prompt>" <output.wav>
                    [--duration N]            # 0.5 to 22.0 seconds
                    [--prompt-influence 0.3]  # 0.0 = creative, 1.0 = literal
                    [--format pcm_24000]      # default matches audiobooker pipeline
                    [--cast cast.yaml]        # optional, only its api_key_env is used
```
