# 0017 — SSRF defence: IP pinning deferred in V1

**Date:** 2026-04-21
**Status:** accepted

## Context

`web_fetch` and any future URL-fetching tool run through the SSRF
defence in `cairn.tools.security._ssrf`. The V1 implementation:

1. Validates the scheme (http/https only), hostname present, no
   embedded credentials.
2. Resolves the hostname via `getaddrinfo` and rejects if any
   resolved IP falls in a blocked network (loopback, RFC1918
   private, link-local — including cloud metadata 169.254.169.254,
   IPv6 ULA/link-local/unspecified, carrier-grade NAT).
3. Lets the HTTP client connect using the hostname (so DNS is
   consulted again at connection time).

Step 3 is the gap. Between the validation `getaddrinfo` and the
client's actual connection, DNS could return a different address.
A "textbook" DNS-rebinding attack works by:

1. Attacker controls `evil.example.com`.
2. First DNS query (validation) returns `1.2.3.4` — public IP,
   passes.
3. Second DNS query (connection) returns `127.0.0.1` or
   `169.254.169.254` — the attacker has swapped records.
4. Client connects to the internal address while thinking it's
   fetching a public URL.

The textbook mitigation is **IP pinning**: resolve the hostname,
verify every IP is safe, then force the HTTP client to connect to
one of those specific IPs while preserving the Host/SNI header.

IP pinning on HTTPS is not difficult in principle but non-trivial
in practice:

- The TCP layer must connect to the pinned IP.
- The TLS layer must use the original hostname for SNI.
- The HTTP layer must use the original hostname for the `Host`
  header.
- The certificate validation must still check the original
  hostname.

httpx supports some of this via custom `Transport` instances, but
the clean shape needs a bespoke SSL context and a transport that
overrides connection address without leaking the pinned IP to TLS.
Doing it right is a careful piece of code; doing it wrong is a
worse security posture than not trying (the pinned IP leaking
into SNI would itself be a vulnerability).

The practical threat picture also matters. For DNS rebinding to
fire:

- The attacker must control DNS for the hostname in question.
- The attacker must time the rebinding precisely around our
  validation and connection.
- The target must be reachable from the attacker's chosen victim
  address (internal network addressable from the machine running
  cairn).

That's a narrow attacker profile for a single-user desktop
application. A server-side cairn deployment with an accessible
internal network (AWS instance metadata, corporate intranet) would
be the worst case, and the answer there is both IP pinning *and*
outbound-firewall rules.

## Decision

V1 SSRF defence validates the hostname and its resolved IPs
pre-flight but does not pin the connection IP. The `safe_fetch`
helper (and `web_fetch`'s manual redirect walk) call
`resolve_hostname` before every HTTP request, including every hop,
so the attack window is bounded to "rebinds between
`getaddrinfo` and `connect`" — typically milliseconds.

The gap is documented:

- In this ADR.
- In `cairn.tools.security._ssrf`'s module docstring ("V1
  limitation").
- In `docs/tools.md` under the SSRF defence section.

When we ship IP pinning (V2), the plan is:

1. Resolve the hostname, filter to safe IPs, pin one.
2. Custom httpx `AsyncHTTPTransport` that overrides
   connection address to the pinned IP.
3. SSL context with the original hostname in SNI and
   certificate verification.
4. Tests that exercise the rebinding scenario end-to-end against
   a controllable DNS stub.

Deployments with harder SSRF requirements (cloud hosting,
multi-tenant) should pair cairn's pre-flight validation with an
outbound firewall rule — a belt-and-braces boundary at the
network layer that's simpler and more robust than application-level
pinning.

## Consequences

**Easier:**

- V1 SSRF defence is understandable and reviewable in one module.
  The blocked-networks list, the resolver check, and the
  streaming-size cap are all visible in under 200 lines.
- Tests mock `socket.getaddrinfo` and `httpx.MockTransport` — no
  bespoke TLS machinery needed.

**Harder:**

- Shipping on a server with an accessible internal network
  (metadata service, intranet) without adding a firewall rule is
  unsafe. The docs call this out, but a user who misses the
  warning is exposed.
- A V2 upgrade path requires replacing the httpx client plumbing,
  which will touch every URL-fetching tool. The current
  `safe_fetch` + per-hop `resolve_hostname` structure already
  centralises this, so the lift is bounded.

**Ongoing cost:**

- Every release revisits this decision against the current threat
  picture. If cairn grows a server-side deployment, the upgrade
  becomes load-bearing.
- The module docstring and this ADR must stay in sync with the
  implementation. When IP pinning lands, this ADR becomes
  superseded, not edited.
