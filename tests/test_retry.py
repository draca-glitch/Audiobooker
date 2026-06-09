"""Retry helper regressions."""

from unittest.mock import MagicMock

import httpx
import pytest

from audiobooker.retry import with_retry


def test_immediate_success():
    fn = MagicMock(return_value="ok")
    assert with_retry(fn, tries=3, base_delay=0) == "ok"
    assert fn.call_count == 1


def test_retries_on_network_error():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectError("boom")
        return "ok"

    assert with_retry(fn, tries=3, base_delay=0) == "ok"
    assert calls["n"] == 2


def test_retries_on_timeout():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("slow")
        return "ok"

    assert with_retry(fn, tries=3, base_delay=0) == "ok"
    assert calls["n"] == 3


def test_no_retry_on_4xx():
    req = httpx.Request("GET", "http://x")
    resp = httpx.Response(404, request=req)

    def fn():
        raise httpx.HTTPStatusError("nope", request=req, response=resp)

    with pytest.raises(httpx.HTTPStatusError):
        with_retry(fn, tries=3, base_delay=0)


def test_retries_on_5xx():
    req = httpx.Request("GET", "http://x")
    resp_5xx = httpx.Response(502, request=req)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.HTTPStatusError("bad gateway", request=req, response=resp_5xx)
        return "ok"

    assert with_retry(fn, tries=3, base_delay=0) == "ok"
    assert calls["n"] == 2


def test_exhausted_retries_reraises():
    def fn():
        raise httpx.ConnectError("always down")

    with pytest.raises(httpx.ConnectError):
        with_retry(fn, tries=2, base_delay=0)


def _status_error(code: int, headers: dict | None = None):
    resp = httpx.Response(code, headers=headers or {}, request=httpx.Request("POST", "http://x"))
    return httpx.HTTPStatusError("err", request=resp.request, response=resp)


def test_429_is_retried():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _status_error(429)
        return "ok"

    assert with_retry(fn, tries=3, base_delay=0) == "ok"
    assert calls["n"] == 3


def test_429_honors_retry_after(monkeypatch):
    import audiobooker.retry as retry_mod
    sleeps = []
    monkeypatch.setattr(retry_mod.time, "sleep", sleeps.append)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _status_error(429, {"Retry-After": "7"})
        return "ok"

    assert with_retry(fn, tries=3, base_delay=0.1) == "ok"
    assert sleeps == [7.0]


def test_other_4xx_not_retried():
    def fn():
        raise _status_error(403)

    with pytest.raises(httpx.HTTPStatusError):
        with_retry(fn, tries=3, base_delay=0)
