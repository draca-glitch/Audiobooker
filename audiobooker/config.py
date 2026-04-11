"""Cast configuration loader.

The cast.yaml file is the centerpiece of audiobooker. Everything project-specific
lives there: voice assignments per character, pronunciation overrides, audio
effects, LLM endpoint, output paths, optional features (HUD brackets, Entity).

A minimal cast.yaml only needs a `cast` table with at least one entry named
`narrator`. Everything else has sensible defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# --- Defaults -----------------------------------------------------------

DEFAULT_OUTPUT_DIR = "./out"
DEFAULT_SAMPLE_RATE = 24000

DEFAULT_GAPS = {
    "dialogue_tag": 0.25,
    "dialogue_continuation": 0.35,
    "default": 0.55,
    "speaker_change": 0.75,
    "narration": 1.0,
    "paragraph": 1.3,
}

DEFAULT_LLM = {
    "base_url": "https://api.anthropic.com/v1",
    "model": "claude-opus-4-6",
    "api_key_env": "ANTHROPIC_API_KEY",
    "max_tokens": 16384,
    "compat": "anthropic",  # "anthropic" | "openai"
}

DEFAULT_KOKORO = {
    "model": "/opt/kokoro/kokoro-v1.0.onnx",
    "voices": "/opt/kokoro/voices-v1.0.bin",
    "language": "en-us",
}

DEFAULT_ELEVENLABS = {
    "api_url": "https://api.elevenlabs.io/v1/text-to-speech",
    "model": "eleven_multilingual_v2",
    "api_key_env": "ELEVENLABS_API_KEY",
    "output_format": "pcm_24000",
}

DEFAULT_FEATURES = {
    "hud_brackets": False,
    "entity_italics": False,
}


# --- Dataclasses --------------------------------------------------------


@dataclass
class CharacterVoice:
    """A single cast member's voice configuration."""

    name: str
    kokoro_voice: str | None = None
    elevenlabs_voice: str | None = None
    elevenlabs_settings: dict[str, float] | None = None
    speed: float = 1.0
    effect: str | None = None  # name from `effects` table
    gender: str | None = None  # for fallback resolution


@dataclass
class CastConfig:
    """Parsed cast.yaml. The single source of project-specific configuration."""

    cast: dict[str, CharacterVoice] = field(default_factory=dict)
    pronunciations: dict[str, str] = field(default_factory=dict)
    effects: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    gaps: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_GAPS))
    llm: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_LLM))
    kokoro: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_KOKORO))
    elevenlabs: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_ELEVENLABS))
    features: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_FEATURES))
    output_dir: str = DEFAULT_OUTPUT_DIR
    sample_rate: int = DEFAULT_SAMPLE_RATE

    # ---- Voice resolution ------------------------------------------------

    def resolve_voice(self, segment: dict[str, Any], engine: str) -> str:
        """Look up the voice ID/name for a segment under the given engine.

        engine: "kokoro" or "elevenlabs"
        """
        attr = "kokoro_voice" if engine == "kokoro" else "elevenlabs_voice"

        # HUD segments use the special _hud entry
        if segment.get("type") == "hud":
            char = self.cast.get("_hud")
            if char:
                v = getattr(char, attr)
                if v:
                    return v

        # Narration uses narrator
        if segment.get("type") == "narration":
            char = self.cast.get("narrator")
            if char:
                v = getattr(char, attr)
                if v:
                    return v
            raise ValueError("cast.yaml must define a 'narrator' entry")

        # Dialogue: by character name, then by gender fallback
        name = segment.get("character", "")
        if name and name in self.cast:
            v = getattr(self.cast[name], attr)
            if v:
                return v

        gender = segment.get("gender", "female")
        fallback = self.cast.get(f"_{gender}_fallback") or self.cast.get(f"_{gender}")
        if fallback:
            v = getattr(fallback, attr)
            if v:
                return v

        # Last resort: narrator
        char = self.cast["narrator"]
        v = getattr(char, attr)
        if not v:
            raise ValueError(
                f"No {engine} voice resolvable for segment {segment!r}"
            )
        return v

    def resolve_speed(self, segment: dict[str, Any]) -> float:
        if segment.get("type") == "hud":
            char = self.cast.get("_hud")
            return char.speed if char else 1.1
        if segment.get("type") == "narration":
            char = self.cast.get("narrator")
            return char.speed if char else 1.0
        name = segment.get("character", "")
        if name in self.cast:
            return self.cast[name].speed
        return 1.0

    def resolve_effect_name(self, segment: dict[str, Any]) -> str | None:
        if segment.get("type") == "hud":
            char = self.cast.get("_hud")
            return char.effect if char else None
        if segment.get("type") != "dialogue":
            return None
        name = segment.get("character", "")
        if name in self.cast:
            return self.cast[name].effect
        return None

    def resolve_elevenlabs_settings(self, segment: dict[str, Any]) -> dict[str, float]:
        default = {"stability": 0.55, "similarity_boost": 0.75, "style": 0.20}
        if segment.get("type") == "hud":
            char = self.cast.get("_hud")
            if char and char.elevenlabs_settings:
                return dict(char.elevenlabs_settings)
        if segment.get("type") == "narration":
            char = self.cast.get("narrator")
            if char and char.elevenlabs_settings:
                return dict(char.elevenlabs_settings)
        name = segment.get("character", "")
        if name in self.cast and self.cast[name].elevenlabs_settings:
            return dict(self.cast[name].elevenlabs_settings)
        return default

    # ---- Misc helpers ----------------------------------------------------

    def get_api_key(self, service: str) -> str:
        """Look up an API key from env, falling back to ~/.audiobooker.env file."""
        if service == "llm":
            env_var = self.llm.get("api_key_env", "ANTHROPIC_API_KEY")
        elif service == "elevenlabs":
            env_var = self.elevenlabs.get("api_key_env", "ELEVENLABS_API_KEY")
        else:
            raise ValueError(f"unknown service {service!r}")

        key = os.environ.get(env_var)
        if key:
            return key

        env_file = Path.home() / ".audiobooker.env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith(f"{env_var}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")

        raise RuntimeError(
            f"No {env_var} found in environment or ~/.audiobooker.env"
        )


# --- Loader -------------------------------------------------------------


def _merge(default: dict, override: dict | None) -> dict:
    if not override:
        return dict(default)
    out = dict(default)
    out.update(override)
    return out


def load_cast(path: str | Path) -> CastConfig:
    """Load and validate a cast.yaml file."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"cast file not found: {p}")

    with p.open() as f:
        data = yaml.safe_load(f) or {}

    cfg = CastConfig()

    # Engine defaults
    defaults = data.get("engine_defaults", {}) or {}
    cfg.output_dir = defaults.get("output_dir", DEFAULT_OUTPUT_DIR)
    cfg.sample_rate = int(defaults.get("sample_rate", DEFAULT_SAMPLE_RATE))

    cfg.llm = _merge(DEFAULT_LLM, defaults.get("llm"))
    cfg.kokoro = _merge(DEFAULT_KOKORO, data.get("kokoro"))
    cfg.elevenlabs = _merge(DEFAULT_ELEVENLABS, data.get("elevenlabs"))
    cfg.features = _merge(DEFAULT_FEATURES, data.get("features"))
    cfg.gaps = _merge(DEFAULT_GAPS, data.get("gaps"))
    cfg.pronunciations = data.get("pronunciations", {}) or {}
    cfg.effects = data.get("effects", {}) or {}

    # Cast members
    cast_data = data.get("cast", {}) or {}
    if not cast_data:
        raise ValueError(
            f"{p}: cast.yaml must define a `cast` table with at least a 'narrator' entry"
        )

    for name, voice_data in cast_data.items():
        if not isinstance(voice_data, dict):
            raise ValueError(f"{p}: cast.{name} must be a mapping")
        cfg.cast[name] = CharacterVoice(
            name=name,
            kokoro_voice=voice_data.get("kokoro_voice"),
            elevenlabs_voice=voice_data.get("elevenlabs_voice"),
            elevenlabs_settings=voice_data.get("elevenlabs_settings"),
            speed=float(voice_data.get("speed", 1.0)),
            effect=voice_data.get("effect"),
            gender=voice_data.get("gender"),
        )

    if "narrator" not in cfg.cast:
        raise ValueError(f"{p}: cast.yaml must define a 'narrator' entry")

    return cfg
