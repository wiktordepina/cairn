"""SSRF defence for URL-fetching tools.

Implements security doc §7 requirements for V1:

- **Scheme allowlist** — only `http` and `https`; reject
  `file://`, `gopher://`, `data:`, etc.
- **Pre-flight DNS validation** — resolve the hostname, reject any
  resolved IP in a blocked network (loopback, RFC1918 private,
  link-local incl. cloud metadata, IPv6 ULA/link-local).
- **No automatic redirects** — `safe_fetch` sets
  `follow_redirects=False` on the httpx client. Redirect responses
  surface to the caller; if followed, the caller re-enters
  `safe_fetch` with the target URL and gets a fresh validation.
- **Size and timeout caps** — streaming read with a hard size limit;
  request timeout enforced via httpx's own mechanism.

**V1 limitation:** IP pinning (connecting to the validated IP while
preserving Host/SNI) is deferred. Over HTTPS, proper IP-pinning
conflicts with SNI and needs a custom SSL context; the cost/benefit
is poor for V1 when the practical threat (DNS rebinding) requires
attacker-controlled DNS infrastructure. See ADR 0013.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from cairn.tools._errors import SSRFBlocked

# ---------------------------------------------------------------------------
# Blocked networks
# ---------------------------------------------------------------------------


BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    # IPv4
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("10.0.0.0/8"),  # RFC1918 private
    ipaddress.ip_network("172.16.0.0/12"),  # RFC1918 private
    ipaddress.ip_network("192.168.0.0/16"),  # RFC1918 private
    ipaddress.ip_network("169.254.0.0/16"),  # link-local incl. 169.254.169.254
    ipaddress.ip_network("0.0.0.0/8"),  # "this network"
    ipaddress.ip_network("100.64.0.0/10"),  # carrier-grade NAT
    # IPv6
    ipaddress.ip_network("::1/128"),  # loopback
    ipaddress.ip_network("fc00::/7"),  # unique-local
    ipaddress.ip_network("fe80::/10"),  # link-local
    ipaddress.ip_network("::/128"),  # unspecified
)


ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_url(url: str) -> tuple[str, str]:
    """Parse and validate the scheme + hostname.

    Returns `(hostname, normalised_url)`.

    Raises `SSRFBlocked` on disallowed schemes, missing hostnames,
    or URLs that fail to parse.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise SSRFBlocked(f"Could not parse URL {url!r}: {exc}") from exc

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise SSRFBlocked(
            f"Scheme {parsed.scheme!r} is not allowed (allowed: {sorted(ALLOWED_SCHEMES)})"
        )
    if not parsed.hostname:
        raise SSRFBlocked(f"URL {url!r} has no hostname")

    # Reject explicit userinfo (someone@host) — at best surprising,
    # at worst a phishing vector with credentials in the URL.
    if parsed.username or parsed.password:
        raise SSRFBlocked("URLs with embedded credentials are not allowed")

    return parsed.hostname.lower(), url


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    for net in BLOCKED_NETWORKS:
        try:
            if ip in net:
                return True
        except TypeError:
            # Version mismatch (IPv4 addr vs IPv6 net or vice versa)
            continue
    return False


def resolve_hostname(hostname: str) -> list[str]:
    """Resolve `hostname` via `getaddrinfo` and return every IP.

    Every resolved IP must fall outside `BLOCKED_NETWORKS`. If any
    blocked IP appears, raises `SSRFBlocked` — even one blocked IP
    means the hostname could rebind to it.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SSRFBlocked(f"Hostname {hostname!r} did not resolve: {exc}") from exc

    ips: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        host = sockaddr[0]
        if isinstance(host, str):
            ips.add(host)

    if not ips:
        raise SSRFBlocked(f"Hostname {hostname!r} resolved to no addresses")

    for ip_str in ips:
        ip = ipaddress.ip_address(ip_str)
        if _is_blocked_ip(ip):
            raise SSRFBlocked(f"Hostname {hostname!r} resolved to blocked IP {ip_str}")

    return sorted(ips)


# ---------------------------------------------------------------------------
# safe_fetch
# ---------------------------------------------------------------------------


async def safe_fetch(
    url: str,
    *,
    max_size: int = 5_000_000,
    timeout: float = 30.0,
    user_agent: str = "cairn/1.0",
) -> bytes:
    """Fetch `url` with the full SSRF defence applied.

    - Scheme + hostname validated.
    - Hostname resolved; every resolved IP must be public.
    - Automatic redirects disabled — 3xx responses return as-is;
      caller decides whether to re-enter `safe_fetch` for the target.
    - Response body size capped at `max_size` bytes (streamed read).
    - Request timeout enforced by httpx.

    Returns the raw response body bytes.

    Raises:
        SSRFBlocked — validation failure or oversize response.
        httpx.HTTPError — transport errors surface through.
    """
    hostname, normalised = validate_url(url)
    resolve_hostname(hostname)  # discards the IP list; just validates

    async with (
        httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": user_agent},
        ) as client,
        client.stream("GET", normalised) as response,
    ):
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > max_size:
                raise SSRFBlocked(f"Response exceeds max_size={max_size} bytes")
        if response.status_code >= 400:
            # Non-2xx / non-3xx → still return the body the caller can
            # surface as an error, but carry the status in an exception
            # attribute so the caller can differentiate.
            exc = SSRFBlocked(f"Request returned status {response.status_code}")
            exc.status_code = response.status_code  # type: ignore[attr-defined]
            exc.body = bytes(content)  # type: ignore[attr-defined]
            raise exc
        return bytes(content)
