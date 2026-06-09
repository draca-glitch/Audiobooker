"""audiobooker — multi-voice audiobook generator driven by a cast.yaml file."""

__version__ = "0.3.0"

from audiobooker.config import CastConfig, load_cast

__all__ = ["CastConfig", "load_cast", "__version__"]
