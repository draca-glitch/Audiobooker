"""Small retry helper for HTTP calls.

All external calls (ElevenLabs TTS, ElevenLabs Sound Generation, LLM parser)
can fail transiently: connection resets, read timeouts, proxy hiccups, 5xx
from the upstream. Without retries, one blip during a 500-segment render
loses the entire run.

This module provides a narrow retry wrapper: retry on network errors,
read/connect timeouts, and 5xx responses; do NOT retry on 4xx (bad key,
invalid voice, quota exceeded) because those will never succeed.
"""

from __future__ import annotations

import sys
import time
from typing import Callable, TypeVar

import httpx

T = TypeVar("T")

_RETRIABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


def with_retry(
    fn: Callable[[], T],
    *,
    tries: int = 3,
    base_delay: float = 1.0,
    what: str = "request",
) -> T:
    """Call fn() with exponential backoff on transient errors.

    Retries on network exceptions and HTTP 5xx. 4xx responses (including 401
    auth and 429 quota) are raised immediately — no point retrying those.
    """
    for attempt in range(tries):
        try:
            return fn()
        except _RETRIABLE_EXCEPTIONS as e:
            if attempt == tries - 1:
                raise
            delay = base_delay * (2**attempt)
            print(
                f"  {what}: {e.__class__.__name__} (attempt {attempt + 1}/{tries}), "
                f"retrying in {delay:.1f}s",
                file=sys.stderr,
            )
            time.sleep(delay)
        except httpx.HTTPStatusError as e:
            # Only 5xx is worth retrying. 4xx means the request itself is
            # wrong (auth, bad voice id, malformed body, quota).
            if 500 <= e.response.status_code < 600 and attempt < tries - 1:
                delay = base_delay * (2**attempt)
                print(
                    f"  {what}: HTTP {e.response.status_code} "
                    f"(attempt {attempt + 1}/{tries}), retrying in {delay:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            raise
    raise RuntimeError(f"{what}: exhausted {tries} retries")  # unreachable
