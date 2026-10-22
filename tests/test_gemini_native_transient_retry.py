"""gemini_native embedding retries a dropped connection instead of failing at once."""

import asyncio

import httpx
import pytest

import embedding_engine as ee


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"embedding": {"values": [0.1, 0.2, 0.3]}}


def _fake_client_factory(outcomes, calls):
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            calls.append(1)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    return _FakeClient


@pytest.fixture
def no_sleep(monkeypatch):
    async def _instant(_delay):
        return None

    monkeypatch.setattr(ee.asyncio, "sleep", _instant)


def _engine():
    return ee.GeminiNativeEmbeddingEngine(api_key="k", model="gemini-embedding-001", dim=3)


def test_recovers_after_proxy_drops_connection(monkeypatch, no_sleep):
    calls, recorded = [], []
    outcomes = [httpx.RemoteProtocolError("Server disconnected without sending a response."), _FakeResponse()]
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client_factory(outcomes, calls))
    monkeypatch.setattr(ee.GeminiNativeEmbeddingEngine, "_record_e001", staticmethod(recorded.append))

    assert asyncio.run(_engine().generate_async("你好")) == [0.1, 0.2, 0.3]
    assert len(calls) == 2
    assert recorded == []


def test_gives_up_and_records_after_retries(monkeypatch, no_sleep):
    calls, recorded = [], []
    outcomes = [httpx.ConnectError("boom") for _ in range(3)]
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client_factory(outcomes, calls))
    monkeypatch.setattr(ee.GeminiNativeEmbeddingEngine, "_record_e001", staticmethod(recorded.append))

    assert asyncio.run(_engine().generate_async("你好")) == []
    assert len(calls) == 3
    assert len(recorded) == 1 and "ConnectError" in recorded[0]


def test_http_errors_are_not_retried(monkeypatch, no_sleep):
    calls, recorded = [], []
    request = httpx.Request("POST", "https://example.invalid")
    error = httpx.HTTPStatusError("400", request=request, response=httpx.Response(400, request=request))
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client_factory([error], calls))
    monkeypatch.setattr(ee.GeminiNativeEmbeddingEngine, "_record_e001", staticmethod(recorded.append))

    assert asyncio.run(_engine().generate_async("你好")) == []
    assert len(calls) == 1
    assert len(recorded) == 1
