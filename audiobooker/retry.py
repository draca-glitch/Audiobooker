"""Small retry helper for HTTP calls.

All external calls (ElevenLabs TTS, ElevenLabs Sound Generation, LLM parser)
can fail transiently: connection resets, read timeouts, proxy hiccups, 5xx
from the upstream. Without retries, one blip during a 500-segment render
loses the entire run.

This module provides a narrow retry wrapper: retry on network errors,
read/connect timeouts, 5xx responses, and 429 rate limits (honoring
Retry-After); do NOT retry on other 4xx (bad key, invalid voice) because
those will never succeed.
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

    Retries on network exceptions, HTTP 5xx, and HTTP 429 (rate limit,
    honoring Retry-After). Other 4xx responses are raised immediately, no
    point retrying those.
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
            # 5xx is transient upstream trouble. 429 is the one 4xx whose
            # entire meaning is "retry later"; treating it as fatal turns a
            # momentary rate limit into a permanently failed segment. Other
            # 4xx (auth, bad voice id, malformed body) will never succeed.
            code = e.response.status_code
            if (code == 429 or 500 <= code < 600) and attempt < tries - 1:
                delay = base_delay * (2**attempt)
                retry_after = e.response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                print(
                    f"  {what}: HTTP {code} "
                    f"(attempt {attempt + 1}/{tries}), retrying in {delay:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            raise
    raise RuntimeError(f"{what}: exhausted {tries} retries")  # unreachable
