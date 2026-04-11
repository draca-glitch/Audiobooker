"""Thin wrappers around Kokoro and ElevenLabs TTS engines."""

from __future__ import annotations

import io
from typing import Any

import httpx
import numpy as np


# --- Kokoro -------------------------------------------------------------


class KokoroEngine:
    """Lazy-loaded Kokoro ONNX TTS engine."""

    def __init__(self, model_path: str, voices_path: str, language: str = "en-us"):
        self.model_path = model_path
        self.voices_path = voices_path
        self.language = language
        self._kokoro = None

    def _load(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro

            self._kokoro = Kokoro(self.model_path, self.voices_path)
        return self._kokoro

    def synth(self, text: str, voice: str, speed: float = 1.0) -> tuple[np.ndarray, int]:
        kokoro = self._load()
        samples, sample_rate = kokoro.create(
            text, voice=voice, speed=speed, lang=self.language
        )
        return samples, sample_rate


# --- ElevenLabs ---------------------------------------------------------


class ElevenLabsEngine:
    """Stateless ElevenLabs HTTP client."""

    def __init__(
        self,
        api_url: str,
        model: str,
        api_key: str,
        output_format: str = "pcm_24000",
    ):
        self.api_url = api_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.output_format = output_format
        # pcm_24000 → 24kHz, pcm_16000 → 16kHz, etc.
        self.sample_rate = int(output_format.split("_")[-1]) if "_" in output_format else 24000

    def synth(
        self,
        text: str,
        voice_id: str,
        settings: dict[str, float],
    ) -> tuple[np.ndarray, int]:
        url = f"{self.api_url}/{voice_id}"
        resp = httpx.post(
            url,
            params={"output_format": self.output_format},
            headers={
                "xi-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "model_id": self.model,
                "voice_settings": {
                    "stability": settings.get("stability", 0.55),
                    "similarity_boost": settings.get("similarity_boost", 0.75),
                    "style": settings.get("style", 0.20),
                    "use_speaker_boost": True,
                },
            },
            timeout=httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0),
        )
        if resp.status_code == 401:
            raise RuntimeError("ElevenLabs: invalid API key")
        if resp.status_code == 429:
            raise RuntimeError("ElevenLabs: rate limited or character quota exceeded")
        resp.raise_for_status()

        pcm_bytes = resp.content
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, self.sample_rate


# --- Delivery → settings adjuster ---------------------------------------


_INTENSE_WORDS = (
    "shout", "scream", "angry", "furious", "desperate",
    "panicked", "terrified", "anguished", "explosive",
)
_SOFT_WORDS = (
    "whisper", "quiet", "gentle", "hushed", "barely audible",
    "murmur", "soft", "tender",
)
_COLD_WORDS = ("cold", "threatening", "menacing", "ominous", "sinister")
_SARCASTIC_WORDS = ("sarcastic", "teasing", "mocking", "dry", "ironic")


def adjust_settings_for_delivery(
    base: dict[str, float],
    delivery: str | None,
) -> dict[str, float]:
    """Nudge ElevenLabs voice_settings based on the parser-supplied delivery hint."""
    if not delivery:
        return dict(base)

    settings = dict(base)
    d = delivery.lower()

    if any(w in d for w in _INTENSE_WORDS):
        settings["stability"] = max(0.20, settings["stability"] - 0.20)
        settings["style"] = min(1.0, settings["style"] + 0.25)
    if any(w in d for w in _SOFT_WORDS):
        settings["stability"] = min(0.85, settings["stability"] + 0.15)
        settings["style"] = max(0.0, settings["style"] - 0.10)
    if any(w in d for w in _COLD_WORDS):
        settings["stability"] = max(0.35, settings["stability"] - 0.10)
        settings["style"] = min(1.0, settings["style"] + 0.15)
    if any(w in d for w in _SARCASTIC_WORDS):
        settings["stability"] = max(0.30, settings["stability"] - 0.15)
        settings["style"] = min(1.0, settings["style"] + 0.20)

    return settings
