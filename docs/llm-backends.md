# LLM backends

> The chapter parser is the only piece of audiobooker that talks to an LLM. Three backends are supported and you switch between them by editing four lines in `cast.yaml`. No code changes, no separate install paths.

## Option A: Anthropic native (default, recommended)

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

## Option B: Ollama (local, free, no content filter)

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

## Option C: Serverless / proxied inference (DigitalOcean Gradient, OpenRouter, Together, vLLM, LM Studio, etc.)

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

## Choosing parse quality

Claude Opus or equivalent gives the best parse quality. Smaller models (Sonnet, GPT-4o-mini, Qwen2.5-32B+) work but produce more dialogue-merging errors. The post-processor catches most of them, but quality scales with model. If you find the parser is mis-attributing dialogue to the wrong character on a chapter, the cheapest fix is usually to swap to a stronger model for that one parse, since the segment JSON is cached and you only pay once per chapter.
