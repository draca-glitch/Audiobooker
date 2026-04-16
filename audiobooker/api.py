"""LLM client for the chapter parser.

Supports two API shapes:

  - "anthropic"  → POST {base_url}/messages (Anthropic native API)
  - "openai"     → POST {base_url}/chat/completions (OpenAI / DigitalOcean
                   Gradient / vLLM / LM Studio / OpenRouter / Ollama)

Both shapes return a flat string from `parse_chapter_call`.
"""

from __future__ import annotations

import httpx

from audiobooker.retry import with_retry


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        compat: str = "anthropic",
        max_tokens: int = 16384,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.compat = compat
        self.max_tokens = max_tokens

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """Send a single-turn system+user prompt and return the assistant text."""
        if self.compat == "anthropic":
            return self._chat_anthropic(system_prompt, user_prompt)
        if self.compat == "openai":
            return self._chat_openai(system_prompt, user_prompt)
        raise ValueError(f"unknown compat mode {self.compat!r}")

    def _chat_anthropic(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url}/messages"

        def _call():
            resp = httpx.post(
                url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": self.max_tokens,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
                timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0),
            )
            resp.raise_for_status()
            return resp.json()

        data = with_retry(_call, what="anthropic parser")
        # Anthropic returns content as a list of blocks
        blocks = data.get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    def _chat_openai(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url}/chat/completions"

        def _call():
            resp = httpx.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": self.max_tokens,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0),
            )
            resp.raise_for_status()
            return resp.json()

        data = with_retry(_call, what="openai parser")
        return data["choices"][0]["message"]["content"]
