"""SSL trust-store bootstrap.

Injects the operating system's trust store into Python's `ssl` module
via the `truststore` package. Needed when running behind a corporate
MITM proxy (Netskope / Zscaler) that rewrites provider HTTPS endpoints
with a private CA — Python's default `certifi` bundle won't trust that
CA, so provider SDK calls fail cert validation.

Silent on failure: if `truststore` is unavailable or injection raises,
`setup_ssl()` logs at `DEBUG` and returns `False`. Callers should not
crash the application over an optional bootstrap.
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

_injected = False


def setup_ssl() -> bool:
    """Inject the OS trust store into the `ssl` module.

    Idempotent: safe to call multiple times. A second call after a
    successful injection returns `True` without re-invoking
    `truststore`.

    Returns:
        `True` if the OS trust store is now in effect (including on
        repeat calls after a successful injection). `False` if
        `truststore` is not installed or injection raised — in which
        case the default `ssl` context is left untouched.
    """

    global _injected
    if _injected:
        return True

    try:
        import truststore
    except ImportError:
        _logger.debug("truststore not installed; leaving default ssl context in place")
        return False

    try:
        truststore.inject_into_ssl()
    except Exception:
        _logger.debug("truststore.inject_into_ssl() failed", exc_info=True)
        return False

    _injected = True
    return True
