"""Tests for the ``web_fetch`` built-in tool."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING

import httpx
import pytest

from cairn.tools._errors import SSRFBlocked, ToolError
from cairn.tools.builtin._web_fetch import (
    DEFAULT_MAX_BYTES,
    make_web_fetch,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from cairn.orchestrator import TurnContext


def _make_getaddrinfo(ips: list[str]):
    def fake(host: str, port: object, *args: object, **kwargs: object):  # noqa: ARG001
        out: list[tuple[int, int, int, str, tuple[str, int]]] = []
        for ip in ips:
            family = socket.AF_INET if ":" not in ip else socket.AF_INET6
            out.append((family, 0, 0, "", (ip, 0)))
        return out

    return fake


def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cairn.tools.security._ssrf.socket.getaddrinfo",
        _make_getaddrinfo(["1.1.1.1"]),
    )


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    real_cls = httpx.AsyncClient

    def patched(**kwargs: object):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("cairn.tools.builtin._web_fetch.httpx.AsyncClient", patched)


class TestMetadata:
    def test_metadata(self) -> None:
        t = make_web_fetch()
        assert t.name == "web_fetch"
        assert t.risk_tier == 2
        assert t.side_effects == "read"
        assert t.approval_required is False
        assert t.tool_kind == "native"


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_fetches_text(
        self,
        monkeypatch: pytest.MonkeyPatch,
        turn_ctx: TurnContext,
    ) -> None:
        _public_dns(monkeypatch)

        def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
            return httpx.Response(
                200,
                content=b"hello",
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )

        _patch_client(monkeypatch, handler)
        t = make_web_fetch()
        result = await t.invoke({"url": "https://example.com/"}, turn_ctx)
        content = result.content
        assert isinstance(content, str)
        assert content.startswith("[200 text/plain]")
        assert "hello" in content

    @pytest.mark.asyncio
    async def test_fetches_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        turn_ctx: TurnContext,
    ) -> None:
        _public_dns(monkeypatch)

        def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
            return httpx.Response(
                200,
                content=b'{"ok":true}',
                headers={"Content-Type": "application/json"},
            )

        _patch_client(monkeypatch, handler)
        t = make_web_fetch()
        result = await t.invoke({"url": "https://api.example.com/ping"}, turn_ctx)
        assert '{"ok":true}' in result.content


class TestHtmlExtraction:
    @pytest.mark.asyncio
    async def test_html_stripped_to_text(
        self,
        monkeypatch: pytest.MonkeyPatch,
        turn_ctx: TurnContext,
    ) -> None:
        _public_dns(monkeypatch)
        html = (
            b"<html><head><title>t</title>"
            b"<style>body{color:red}</style>"
            b"<script>alert('xss')</script></head>"
            b"<body><h1>Headline</h1><p>Para&amp;nthetical</p></body></html>"
        )

        def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
            return httpx.Response(
                200,
                content=html,
                headers={"Content-Type": "text/html; charset=utf-8"},
            )

        _patch_client(monkeypatch, handler)
        t = make_web_fetch()
        result = await t.invoke({"url": "https://example.com/"}, turn_ctx)
        content = result.content
        assert isinstance(content, str)
        assert "Headline" in content
        assert "Para&nthetical" in content
        # Script and style bodies must not leak through.
        assert "alert" not in content
        assert "color:red" not in content


class TestBinaryContent:
    @pytest.mark.asyncio
    async def test_binary_returns_summary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        turn_ctx: TurnContext,
    ) -> None:
        _public_dns(monkeypatch)
        binary = b"\x89PNG\r\n\x1a\nblob"

        def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
            return httpx.Response(
                200,
                content=binary,
                headers={"Content-Type": "image/png"},
            )

        _patch_client(monkeypatch, handler)
        t = make_web_fetch()
        result = await t.invoke({"url": "https://example.com/a.png"}, turn_ctx)
        content = result.content
        assert isinstance(content, str)
        assert content.startswith("[binary: image/png")
        assert f"{len(binary)} bytes" in content
        assert "sha256:" in content


class TestSsrfRejections:
    @pytest.mark.asyncio
    async def test_blocks_file_scheme(self, turn_ctx: TurnContext) -> None:
        t = make_web_fetch()
        with pytest.raises(SSRFBlocked, match="Scheme"):
            await t.invoke({"url": "file:///etc/passwd"}, turn_ctx)

    @pytest.mark.asyncio
    async def test_blocks_gopher(self, turn_ctx: TurnContext) -> None:
        t = make_web_fetch()
        with pytest.raises(SSRFBlocked):
            await t.invoke({"url": "gopher://example.com/x"}, turn_ctx)

    @pytest.mark.asyncio
    async def test_blocks_private_ip(
        self,
        monkeypatch: pytest.MonkeyPatch,
        turn_ctx: TurnContext,
    ) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["127.0.0.1"]),
        )
        t = make_web_fetch()
        with pytest.raises(SSRFBlocked, match="blocked IP"):
            await t.invoke({"url": "http://internal.example/"}, turn_ctx)

    @pytest.mark.asyncio
    async def test_blocks_metadata_endpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        turn_ctx: TurnContext,
    ) -> None:
        monkeypatch.setattr(
            "cairn.tools.security._ssrf.socket.getaddrinfo",
            _make_getaddrinfo(["169.254.169.254"]),
        )
        t = make_web_fetch()
        with pytest.raises(SSRFBlocked):
            await t.invoke({"url": "http://aws.example/"}, turn_ctx)

    @pytest.mark.asyncio
    async def test_blocks_embedded_credentials(self, turn_ctx: TurnContext) -> None:
        t = make_web_fetch()
        with pytest.raises(SSRFBlocked, match="credentials"):
            await t.invoke({"url": "https://user:pass@example.com/"}, turn_ctx)


class TestSizeCap:
    @pytest.mark.asyncio
    async def test_oversize_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        turn_ctx: TurnContext,
    ) -> None:
        _public_dns(monkeypatch)

        def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
            return httpx.Response(
                200,
                content=b"x" * 5000,
                headers={"Content-Type": "text/plain"},
            )

        _patch_client(monkeypatch, handler)
        t = make_web_fetch(max_bytes=100)
        with pytest.raises(ToolError, match="exceeds"):
            await t.invoke({"url": "https://example.com/"}, turn_ctx)

    def test_default_cap(self) -> None:
        assert DEFAULT_MAX_BYTES == 5_000_000


class TestStatusHandling:
    @pytest.mark.asyncio
    async def test_4xx_surfaces_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        turn_ctx: TurnContext,
    ) -> None:
        _public_dns(monkeypatch)

        def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
            return httpx.Response(
                404,
                content=b"not found",
                headers={"Content-Type": "text/plain"},
            )

        _patch_client(monkeypatch, handler)
        t = make_web_fetch()
        with pytest.raises(ToolError, match="status 404"):
            await t.invoke({"url": "https://example.com/missing"}, turn_ctx)


class TestRedirects:
    @pytest.mark.asyncio
    async def test_follows_redirect_chain(
        self,
        monkeypatch: pytest.MonkeyPatch,
        turn_ctx: TurnContext,
    ) -> None:
        _public_dns(monkeypatch)
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url))
            if request.url.path == "/start":
                return httpx.Response(302, headers={"Location": "https://example.com/final"})
            return httpx.Response(
                200,
                content=b"arrived",
                headers={"Content-Type": "text/plain"},
            )

        _patch_client(monkeypatch, handler)
        t = make_web_fetch()
        result = await t.invoke({"url": "https://example.com/start"}, turn_ctx)
        assert len(hits) == 2
        assert "arrived" in result.content
        # Final URL prefix reports the terminal URL, not the start.
        assert "example.com/final" in result.content

    @pytest.mark.asyncio
    async def test_redirect_to_private_ip_blocked(
        self,
        monkeypatch: pytest.MonkeyPatch,
        turn_ctx: TurnContext,
    ) -> None:
        # First hop resolves public; second hop (metadata.example) resolves
        # to a blocked IP.
        call_count = {"n": 0}

        def fake_getaddrinfo(host: str, *args: object, **kwargs: object):  # noqa: ARG001
            call_count["n"] += 1
            if host == "metadata.example":
                return _make_getaddrinfo(["169.254.169.254"])(host, 0)
            return _make_getaddrinfo(["1.1.1.1"])(host, 0)

        monkeypatch.setattr("cairn.tools.security._ssrf.socket.getaddrinfo", fake_getaddrinfo)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": "http://metadata.example/meta"})

        _patch_client(monkeypatch, handler)
        t = make_web_fetch()
        with pytest.raises(SSRFBlocked):
            await t.invoke({"url": "https://example.com/start"}, turn_ctx)

    @pytest.mark.asyncio
    async def test_redirect_hop_cap(
        self,
        monkeypatch: pytest.MonkeyPatch,
        turn_ctx: TurnContext,
    ) -> None:
        _public_dns(monkeypatch)

        def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
            # Always redirect to itself — forces the hop cap.
            return httpx.Response(302, headers={"Location": "https://example.com/loop"})

        _patch_client(monkeypatch, handler)
        t = make_web_fetch(max_redirects=2)
        with pytest.raises(ToolError, match="Too many redirects"):
            await t.invoke({"url": "https://example.com/loop"}, turn_ctx)
