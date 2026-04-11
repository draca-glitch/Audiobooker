"""Pedalboard audio effect chain builder.

Effect chains are defined declaratively in cast.yaml under the `effects` table:

    effects:
      dark:
        - pitch_shift: { semitones: -2 }
        - reverb: { room_size: 0.6, wet_level: 0.25 }
      demon:
        - pitch_shift: { semitones: -5 }
        - distortion: { drive_db: 15 }
        - reverb: { room_size: 0.6, wet_level: 0.3 }

Each chain is a list of single-key mappings; the key is the effect name and
the value is the kwargs dict passed to the pedalboard plugin.

Supported plugin names: pitch_shift, reverb, distortion, bitcrush, chorus,
compressor, delay, gain, highpass, lowpass.
"""

from __future__ import annotations

from typing import Any

import numpy as np

try:
    from pedalboard import (
        Bitcrush,
        Chorus,
        Compressor,
        Delay,
        Distortion,
        Gain,
        HighpassFilter,
        LowpassFilter,
        Pedalboard,
        PitchShift,
        Reverb,
    )
except ImportError as e:
    raise ImportError(
        "pedalboard is required for audio effects. Install with: pip install pedalboard"
    ) from e


_PLUGIN_MAP = {
    "pitch_shift": PitchShift,
    "reverb": Reverb,
    "distortion": Distortion,
    "bitcrush": Bitcrush,
    "chorus": Chorus,
    "compressor": Compressor,
    "delay": Delay,
    "gain": Gain,
    "highpass": HighpassFilter,
    "lowpass": LowpassFilter,
}


def build_effect(chain_spec: list[dict[str, Any]]) -> Pedalboard:
    """Build a Pedalboard from a list of single-key mappings."""
    plugins = []
    for entry in chain_spec:
        if not isinstance(entry, dict) or len(entry) != 1:
            raise ValueError(
                f"effect chain entries must be single-key mappings, got: {entry!r}"
            )
        name, kwargs = next(iter(entry.items()))
        if name not in _PLUGIN_MAP:
            raise ValueError(
                f"unknown effect plugin {name!r}. Supported: {sorted(_PLUGIN_MAP)}"
            )
        plugins.append(_PLUGIN_MAP[name](**(kwargs or {})))
    return Pedalboard(plugins)


class EffectRegistry:
    """Lazy-instantiated effect chains keyed by name."""

    def __init__(self, effects_table: dict[str, list[dict[str, Any]]]):
        self._specs = effects_table
        self._cache: dict[str, Pedalboard] = {}

    def get(self, name: str | None) -> Pedalboard | None:
        if not name:
            return None
        if name not in self._specs:
            return None
        if name not in self._cache:
            self._cache[name] = build_effect(self._specs[name])
        return self._cache[name]

    def apply(self, name: str | None, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        board = self.get(name)
        if board is None:
            return samples
        # pedalboard wants (channels, samples) float32
        return board(samples.reshape(1, -1), sample_rate).flatten()
