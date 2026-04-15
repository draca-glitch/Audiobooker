# TTS engines

> Three render modes: local Kokoro, cloud ElevenLabs, or hybrid (free narration + premium dialogue). Switch with `--engine`.

## `--engine kokoro` (default)

Local, free, ~real-time on a modern CPU, no API key. Kokoro's narrator voices (`am_michael`, `bm_george`, `af_bella`, etc.) are good. Clear, well-paced, and pleasant for long-form listening. Use this for everything by default.

The honest weakness of Kokoro is **per-character differentiation**. The voice library has range, but when you map five or six characters to it, the results tend to converge in tone and listeners start to lose track of who's speaking. For a single-narrator book or a story with one or two characters, Kokoro alone is excellent. For an ensemble cast, see hybrid mode below.

## `--engine elevenlabs`

Full cloud render. Premium voices throughout, the strongest per-character differentiation, and emotion-driven delivery via the parser-supplied `delivery` field. Costs characters from your ElevenLabs quota for **every** segment, including narration, which is the bulk of the text. Expensive on long books, and usually overkill. Most users want hybrid instead.

## `--engine hybrid` (recommended for ensemble fiction)

This is the mode that exists specifically because **Kokoro is great at narration but weaker at distinguishing many character voices**, while **ElevenLabs is great at character voices but expensive at narration scale**. Hybrid lets you use each engine for what it is good at:

- narration → **Kokoro** (free, plentiful, listeners spend most of their time here)
- named character dialogue → **ElevenLabs** (where the per-character differentiation matters)

### Why this is dramatically cheaper than pure ElevenLabs

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

## Caching

audiobooker caches aggressively, keyed on the MD5 of the chapter text:

- The parsed segment JSON is cached so you only pay for one LLM call per chapter, even across many re-renders.
- The final concatenated WAV is cached so re-running on an unchanged chapter file is instant.
- The per-segment WAVs are kept in `<hash>-segments/` so SFX mixing can re-read them without re-rendering.

If you edit the chapter file by even one character, the hash changes and everything re-runs. To force re-render of an unchanged chapter, pass `--force`.

Each engine has its own cache suffix (`-el` for ElevenLabs, `-hy` for hybrid) so the three engines do not collide and you can render the same chapter in different engines for comparison.
