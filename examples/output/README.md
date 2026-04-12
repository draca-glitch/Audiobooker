# Example output

These M4B files were generated from the example chapters in this directory using audiobooker with the default Kokoro engine and the hybrid (Kokoro + ElevenLabs) engine.

The only thing done to produce these beyond running the default pipeline was a short five-minute session picking which ElevenLabs voices sounded best for each character in hybrid mode, and a small text edit to the corridor example to add narrator lines introducing each HUD readout (so a listener knows they are hearing a system display, not dialogue). The voice picks are in the example cast.yaml files (the `elevenlabs_voice` field on each character). No post-processing, no manual editing of the audio, no cleanup of any kind. The files are the raw output of the pipeline as-is.

Each example is rendered twice:
- **Kokoro only**: local, free, no API key needed for TTS
- **Hybrid**: Kokoro for narration + ElevenLabs for character dialogue (shows the per-character voice differentiation that hybrid mode is designed for)

## Park bench (chapter 1)

A short scene with two characters (Anna and Marcus) meeting in a park. Demonstrates basic narrator + multi-character dialogue.

- `parkbench-kokoro-ch1.m4b` — Kokoro only
- `parkbench-hybrid-ch1.m4b` — Hybrid (Anna = Jessica, Marcus = Charlie on ElevenLabs)

## Sci-fi corridor (chapter 2)

A short scene with two operatives (Vance and Rell), a HUD system readout, and an AI entity (Aurora) that speaks in italics. Demonstrates the optional HUD bracket parsing, Entity italics feature, and per-character pedalboard effects (robot effect on HUD, entity effect on Aurora).

- `corridor-kokoro-ch2.m4b` — Kokoro only
- `corridor-hybrid-ch2.m4b` — Hybrid (Vance and Rell on ElevenLabs, HUD and Aurora on Kokoro with effects)
