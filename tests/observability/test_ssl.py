"""Tests for `cairn.ssl.setup_ssl`."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

import cairn.ssl as ssl_module
from cairn.ssl import setup_ssl


@pytest.fixture(autouse=True)
def _reset_injection_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ssl_module, "_injected", False)
    yield


def _install_fake_truststore(
    monkeypatch: pytest.MonkeyPatch, *, raises: Exception | None = None
) -> MagicMock:
    fake = ModuleType("truststore")
    inject = MagicMock()
    if raises is not None:
        inject.side_effect = raises
    fake.inject_into_ssl = inject  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "truststore", fake)
    return inject


def test_setup_ssl_injects_when_truststore_available(monkeypatch: pytest.MonkeyPatch) -> None:
    inject = _install_fake_truststore(monkeypatch)

    assert setup_ssl() is True
    inject.assert_called_once_with()


def test_setup_ssl_returns_false_when_truststore_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def blocked(name: str, *args: object, **kwargs: object):
        if name == "truststore":
            raise ImportError("no truststore")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    monkeypatch.delitem(sys.modules, "truststore", raising=False)

    assert setup_ssl() is False


def test_setup_ssl_silent_on_injection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_truststore(monkeypatch, raises=RuntimeError("boom"))

    assert setup_ssl() is False


def test_setup_ssl_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    inject = _install_fake_truststore(monkeypatch)

    assert setup_ssl() is True
    assert setup_ssl() is True
    inject.assert_called_once_with()
