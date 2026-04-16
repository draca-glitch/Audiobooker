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
