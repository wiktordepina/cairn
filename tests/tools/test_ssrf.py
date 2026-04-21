"""Tests for SSRF defence."""

from __future__ import annotations

import httpx
import pytest

from cairn.tools.security import (
    SSRFBlocked,
    resolve_hostname,
    safe_fetch,
    validate_url,
)

# ---------------------------------------------------------------------------
# validate_url
# ---------------------------------------------------------------------------


class TestValidateUrl:
    def test_accepts_http(self) -> None:
        host, _ = validate_url("http://example.com/path")
        assert host == "example.com"

    def test_accepts_https(self) -> None:
        host, _ = validate_url("https://example.com/")
        assert host == "example.com"

    def test_lowercases_hostname(self) -> None:
        host, _ = validate_url("https://EXAMPLE.COM/")
        assert host == "example.com"

    def test_rejects_file_scheme(self) -> None:
        with pytest.raises(SSRFBlocked, match="Scheme"):
            validate_url("file:///etc/passwd")

    def test_rejects_gopher(self) -> None:
        with pytest.raises(SSRFBlocked, match="Scheme"):
            validate_url("gopher://example.com/x")

    def test_rejects_data(self) -> None:
        with pytest.raises(SSRFBlocked, match="Scheme"):
            validate_url("data:text/plain,hello")

    def test_rejects_javascript(self) -> None:
        with pytest.raises(SSRFBlocked, match="Scheme"):
            validate_url("javascript:alert(1)")

    def test_rejects_empty_hostname(self) -> None:
        with pytest.raises(SSRFBlocked, match="hostname"):
            validate_url("http:///path")

    def test_rejects_embedded_credentials(self) -> None:
        with pytest.raises(SSRFBlocked, match="credentials"):
            validate_url("https://user:pass@example.com/")


# ---------------------------------------------------------------------------
# resolve_hostname — uses monkeypatched getaddrinfo
# ---------------------------------------------------------------------------


def _make_getaddrinfo(ips: list[str]):
    """Build a getaddrinfo stub that returns ``ips`` for any hostname."""

    def fake_getaddrinfo(host: str, port: object, *args: object, **kwargs: object):  # noqa: ARG001
        out = []
        for ip in ips:
            family = (
                __import__("socket").AF_INET if ":" not in ip else __import__("socket").AF_INET6
            )
            out.append((family, 0, 0, "", (ip, 0)))
        return out

    return fake_getaddrinfo


class TestResolveHostname:
    def test_rejects_loopback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["127.0.0.1"]),
        )
        with pytest.raises(SSRFBlocked, match="blocked IP"):
            resolve_hostname("example.com")

    def test_rejects_metadata_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["169.254.169.254"]),
        )
        with pytest.raises(SSRFBlocked, match="blocked IP"):
            resolve_hostname("metadata.example")

    def test_rejects_rfc1918_10(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["10.0.0.5"]),
        )
        with pytest.raises(SSRFBlocked):
            resolve_hostname("internal.example")

    def test_rejects_rfc1918_192(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["192.168.1.100"]),
        )
        with pytest.raises(SSRFBlocked):
            resolve_hostname("router.local")

    def test_rejects_ipv6_loopback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["::1"]),
        )
        with pytest.raises(SSRFBlocked):
            resolve_hostname("ipv6.local")

    def test_rejects_ipv6_link_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["fe80::1"]),
        )
        with pytest.raises(SSRFBlocked):
            resolve_hostname("ipv6.example")

    def test_rejects_when_any_ip_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Attacker-controlled hostname returning both a public IP
        # and a blocked one — must reject.
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["1.1.1.1", "127.0.0.1"]),
        )
        with pytest.raises(SSRFBlocked):
            resolve_hostname("evil.example")

    def test_accepts_public_ipv4(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["1.1.1.1"]),
        )
        ips = resolve_hostname("one.one.one.one")
        assert ips == ["1.1.1.1"]

    def test_accepts_public_ipv6(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["2001:db8::1"]),
        )
        ips = resolve_hostname("ipv6.example")
        assert ips == ["2001:db8::1"]


# ---------------------------------------------------------------------------
# safe_fetch — uses MockTransport for deterministic HTTP
# ---------------------------------------------------------------------------


class TestSafeFetch:
    @pytest.mark.asyncio
    async def test_fetches_with_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["1.1.1.1"]),
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"hello world")

        # Patch the AsyncClient class to inject a mock transport.
        real_cls = httpx.AsyncClient

        def patched_client(**kwargs: object):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_cls(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("cairn.tools.security._ssrf.httpx.AsyncClient", patched_client)
        body = await safe_fetch("https://example.com/")
        assert body == b"hello world"

    @pytest.mark.asyncio
    async def test_blocks_disallowed_scheme_before_fetching(self) -> None:
        with pytest.raises(SSRFBlocked):
            await safe_fetch("file:///etc/passwd")

    @pytest.mark.asyncio
    async def test_blocks_private_ip_before_fetching(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["127.0.0.1"]),
        )
        with pytest.raises(SSRFBlocked, match="blocked IP"):
            await safe_fetch("http://myservice.local/")

    @pytest.mark.asyncio
    async def test_enforces_size_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["1.1.1.1"]),
        )
        big = b"x" * 10_000

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=big)

        real_cls = httpx.AsyncClient

        def patched_client(**kwargs: object):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_cls(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("cairn.tools.security._ssrf.httpx.AsyncClient", patched_client)
        with pytest.raises(SSRFBlocked, match="exceeds"):
            await safe_fetch("https://example.com/", max_size=1000)

    @pytest.mark.asyncio
    async def test_surfaces_4xx_with_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["1.1.1.1"]),
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, content=b"not found")

        real_cls = httpx.AsyncClient

        def patched_client(**kwargs: object):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_cls(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("cairn.tools.security._ssrf.httpx.AsyncClient", patched_client)
        with pytest.raises(SSRFBlocked) as exc_info:
            await safe_fetch("https://example.com/missing")
        assert exc_info.value.status_code == 404  # type: ignore[attr-defined]
