"""``web_fetch`` — Tier 2, SSRF-defended URL fetch.

Reuses ``validate_url`` and ``resolve_hostname`` from
``cairn.tools.security._ssrf`` but drives its own httpx request loop
because it needs access to response status + headers for
Content-Type filtering and manual redirect handling.

Safety rails:

- Scheme allowlist (http/https), blocked-network IP check, and embedded
  credentials rejection run before every hop (including each redirect
  target).
- ``follow_redirects=False``; the tool walks redirects manually with a
  hard hop cap.
- Streaming read with a hard size cap.
- Request timeout enforced via httpx.
- Only text-like Content-Types (``text/*``, ``application/json``,
  ``application/xml``, ``application/javascript``) surface as text;
  everything else returns a binary summary.
- HTML is best-effort stripped to plain text (``<script>`` / ``<style>``
  content removed, remaining tags dropped, whitespace collapsed).
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, Field

from cairn.tools._decorator import tool
from cairn.tools._errors import SSRFBlocked, ToolError
from cairn.tools.security._ssrf import resolve_hostname, validate_url

if TYPE_CHECKING:
    from cairn.orchestrator import TurnContext
    from cairn.orchestrator._protocols import Tool


DEFAULT_MAX_BYTES = 5_000_000
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_REDIRECTS = 5
_USER_AGENT = "cairn/1.0"
_ACCEPT = "text/html;q=0.9,application/json;q=0.8,text/plain;q=0.7"

_TEXT_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-javascript",
        "application/xhtml+xml",
    }
)


class WebFetchArgs(BaseModel):
    url: str = Field(description="Fully-qualified http(s) URL to fetch.")


def _parse_content_type(header: str | None) -> tuple[str, str | None]:
    """Return ``(mime, charset)``. Unknown inputs default to octet-stream."""
    if not header:
        return "application/octet-stream", None
    head, _, params = header.partition(";")
    mime = head.strip().lower() or "application/octet-stream"
    charset: str | None = None
    for part in params.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset":
            charset = value.strip().strip('"')
            break
    return mime, charset


def _is_text_mime(mime: str) -> bool:
    return mime.startswith("text/") or mime in _TEXT_CONTENT_TYPES


_SCRIPT_BLOCK = re.compile(
    r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL
)
_STYLE_BLOCK = re.compile(
    r"<style[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL
)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def _html_to_text(html: str) -> str:
    stripped = _SCRIPT_BLOCK.sub(" ", html)
    stripped = _STYLE_BLOCK.sub(" ", stripped)
    stripped = _TAG.sub(" ", stripped)
    # Minimal entity decoding for the most common HTML entities.
    import html as _html

    stripped = _html.unescape(stripped)
    return _WHITESPACE.sub(" ", stripped).strip()


def _binary_summary(url: str, mime: str, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()[:16]
    return f"[binary: {mime}, {len(body)} bytes, sha256:{digest}] from {url}"


async def _validate_url_or_block(url: str) -> str:
    """Full pre-flight SSRF validation. Returns the normalised URL."""
    hostname, normalised = validate_url(url)
    resolve_hostname(hostname)
    return normalised


async def _fetch_with_redirects(
    start_url: str,
    *,
    max_size: int,
    timeout: float,
    max_redirects: int,
) -> tuple[int, httpx.Headers, bytes, str]:
    """Walk redirects manually, re-validating the SSRF rules at each hop.

    Returns ``(status_code, headers, body, final_url)``.
    """
    current = await _validate_url_or_block(start_url)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": _USER_AGENT, "Accept": _ACCEPT},
    ) as client:
        for _ in range(max_redirects + 1):
            async with client.stream("GET", current) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_size:
                        raise ToolError(
                            f"Response exceeds max_size={max_size} bytes"
                        )

                status = response.status_code
                if 300 <= status < 400:
                    location = response.headers.get("Location")
                    if not location:
                        return status, response.headers, bytes(body), current
                    next_url = str(httpx.URL(current).join(location))
                    current = await _validate_url_or_block(next_url)
                    continue

                return status, response.headers, bytes(body), current

    raise ToolError(f"Too many redirects (limit={max_redirects})")


def _decode_text(body: bytes, charset: str | None) -> str:
    encoding = charset or "utf-8"
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def make_web_fetch(
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> Tool:
    """Build a ``web_fetch`` tool.

    The tool carries no workspace scope — SSRF defence is enforced
    against external hostnames regardless of the caller's session.
    """

    @tool(
        name="web_fetch",
        description=(
            "Fetch an http(s) URL. Enforces SSRF defence (blocks private "
            "networks, disallowed schemes). HTML is reduced to plain text. "
            f"Max response size {max_bytes} bytes; up to {max_redirects} "
            "redirects; binary content types return a summary only."
        ),
        risk_tier=2,
        side_effects="read",
        timeout_s=timeout_s + 5.0,  # runner timeout sits above the httpx one
        args_model=WebFetchArgs,
    )
    async def web_fetch(args: WebFetchArgs, ctx: TurnContext) -> str:  # noqa: ARG001
        try:
            status, headers, body, final_url = await _fetch_with_redirects(
                args.url,
                max_size=max_bytes,
                timeout=timeout_s,
                max_redirects=max_redirects,
            )
        except SSRFBlocked:
            # Re-raise — ToolError subtype, the runner will surface it.
            raise
        except httpx.HTTPError as exc:
            raise ToolError(f"HTTP error fetching {args.url}: {exc}") from exc

        mime, charset = _parse_content_type(headers.get("Content-Type"))

        if status >= 400:
            raise ToolError(
                f"Request returned status {status} for {final_url}"
            )

        if not _is_text_mime(mime):
            return _binary_summary(final_url, mime, body)

        text = _decode_text(body, charset)
        if mime in ("text/html", "application/xhtml+xml"):
            text = _html_to_text(text)

        prefix = f"[{status} {mime}] {final_url}\n"
        return prefix + text

    return web_fetch


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_TIMEOUT_S",
    "WebFetchArgs",
    "make_web_fetch",
]
