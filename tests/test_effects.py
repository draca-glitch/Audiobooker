"""Effect registry regressions."""

import numpy as np
import pytest

from audiobooker.effects import EffectRegistry, build_effect


def test_empty_registry_is_identity():
    reg = EffectRegistry({})
    sig = np.ones(1000, dtype=np.float32)
    out = reg.apply(None, sig, 24000)
    assert np.array_equal(out, sig)


def test_unknown_effect_name_is_identity():
    reg = EffectRegistry({"known": [{"gain": {"gain_db": 0}}]})
    sig = np.ones(1000, dtype=np.float32)
    out = reg.apply("unknown-name", sig, 24000)
    assert np.array_equal(out, sig)


def test_gain_effect_scales_amplitude():
    reg = EffectRegistry({"loud": [{"gain": {"gain_db": 6.0}}]})
    sig = np.ones(1000, dtype=np.float32) * 0.1
    out = reg.apply("loud", sig, 24000)
    # +6 dB is roughly ×2.0
    assert out.mean() > 0.15


def test_unknown_plugin_raises():
    with pytest.raises(ValueError, match="unknown effect plugin"):
        build_effect([{"no_such_thing": {}}])


def test_malformed_chain_entry_raises():
    with pytest.raises(ValueError, match="single-key"):
        build_effect([{"gain": {}, "extra": {}}])


def test_effect_chain_is_cached():
    reg = EffectRegistry({"x": [{"gain": {"gain_db": 0}}]})
    b1 = reg.get("x")
    b2 = reg.get("x")
    assert b1 is b2  # same cached instance
