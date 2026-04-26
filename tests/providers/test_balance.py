"""Tests for ``Provider.balance()`` HTTP implementations.

Each test stubs ``httpx.AsyncClient`` to avoid real network I/O. The
adapter does the work — these tests verify shape mapping and graceful
degradation on transport failure / malformed payloads.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from cairn.config._models import ProviderConfig, SecretRef
from cairn.config._secrets import SecretResolver
from cairn.providers._deepseek import DeepSeekProvider
from cairn.providers._openrouter import OpenRouterProvider


class _FakeResponse:
    def __init__(self, *, status: int = 200, payload: Any = None) -> None:
        self.status_code = status
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "http://x"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        self.calls.append((url, dict(headers or {})))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    """Replace ``httpx.AsyncClient`` with a stub that returns ``client``."""

    def factory(*_: Any, **__: Any) -> _FakeClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _ds_provider() -> DeepSeekProvider:
    return DeepSeekProvider(
        ProviderConfig(name="deepseek", api_key=SecretRef.parse("literal:test-key")),
        SecretResolver(),
    )


def _or_provider() -> OpenRouterProvider:
    return OpenRouterProvider(
        ProviderConfig(name="openrouter", api_key=SecretRef.parse("literal:test-key")),
        SecretResolver(),
    )


class TestDeepSeekBalance:
    @pytest.mark.asyncio
    async def test_happy_path_cny(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(
            _FakeResponse(
                payload={
                    "is_available": True,
                    "balance_infos": [
                        {
                            "currency": "CNY",
                            "total_balance": "110.00",
                            "granted_balance": "10.00",
                            "topped_up_balance": "100.00",
                        }
                    ],
                }
            )
        )
        _patch_httpx(monkeypatch, client)
        info = await _ds_provider().balance()
        assert info is not None
        assert info.currency == "CNY"
        assert info.total == 110.0
        assert info.remaining == 110.0  # endpoint returns current remaining
        assert info.used == 0.0  # endpoint doesn't expose historical spend
        assert info.granted == 10.0
        assert info.source == "/user/balance"

    @pytest.mark.asyncio
    async def test_uses_bearer_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(
            _FakeResponse(payload={"balance_infos": [{"currency": "USD", "total_balance": "1.0"}]})
        )
        _patch_httpx(monkeypatch, client)
        await _ds_provider().balance()
        url, headers = client.calls[0]
        assert url.endswith("/user/balance")
        assert headers["Authorization"] == "Bearer test-key"

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(_FakeResponse(status=401))
        _patch_httpx(monkeypatch, client)
        assert await _ds_provider().balance() is None

    @pytest.mark.asyncio
    async def test_transport_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(httpx.ConnectError("offline"))
        _patch_httpx(monkeypatch, client)
        assert await _ds_provider().balance() is None

    @pytest.mark.asyncio
    async def test_empty_balance_infos_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(_FakeResponse(payload={"balance_infos": []}))
        _patch_httpx(monkeypatch, client)
        assert await _ds_provider().balance() is None


class TestOpenRouterBalance:
    @pytest.mark.asyncio
    async def test_happy_path_with_data_envelope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(
            _FakeResponse(payload={"data": {"total_credits": 5.0, "total_usage": 0.79}})
        )
        _patch_httpx(monkeypatch, client)
        info = await _or_provider().balance()
        assert info is not None
        assert info.currency == "USD"
        assert info.total == pytest.approx(5.0)
        assert info.used == pytest.approx(0.79)
        assert info.remaining == pytest.approx(4.21)
        assert info.source == "/api/v1/credits"

    @pytest.mark.asyncio
    async def test_legacy_top_level_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Older OR responses returned credits at the top level.
        client = _FakeClient(_FakeResponse(payload={"total_credits": 10.0, "total_usage": 3.0}))
        _patch_httpx(monkeypatch, client)
        info = await _or_provider().balance()
        assert info is not None
        assert info.remaining == pytest.approx(7.0)

    @pytest.mark.asyncio
    async def test_remaining_clamped_at_zero_when_overdrawn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(_FakeResponse(payload={"total_credits": 1.0, "total_usage": 5.0}))
        _patch_httpx(monkeypatch, client)
        info = await _or_provider().balance()
        assert info is not None
        assert info.remaining == 0.0

    @pytest.mark.asyncio
    async def test_uses_bearer_auth_and_identity_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(
            _FakeResponse(payload={"data": {"total_credits": 1.0, "total_usage": 0}})
        )
        _patch_httpx(monkeypatch, client)
        await _or_provider().balance()
        url, headers = client.calls[0]
        assert url.endswith("/credits")
        assert headers["Authorization"] == "Bearer test-key"
        # Identity headers ride along too — useful so OR can attribute traffic.
        assert headers.get("X-OpenRouter-Title") == "cairn"
        assert headers.get("HTTP-Referer", "").startswith("https://")

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(_FakeResponse(status=429))
        _patch_httpx(monkeypatch, client)
        assert await _or_provider().balance() is None

    @pytest.mark.asyncio
    async def test_transport_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(httpx.ConnectError("offline"))
        _patch_httpx(monkeypatch, client)
        assert await _or_provider().balance() is None
