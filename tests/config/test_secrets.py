"""Tests for SecretResolver."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cairn.config._models import SecretRef
from cairn.config._secrets import MissingSecretError, SecretResolver


class TestSecretResolver:
    def test_env_resolves(self) -> None:
        resolver = SecretResolver()
        ref = SecretRef.parse("env:TEST_CAIRN_KEY")
        with patch.dict("os.environ", {"TEST_CAIRN_KEY": "my-api-key"}):
            assert resolver.resolve(ref) == "my-api-key"

    def test_env_missing_raises(self) -> None:
        resolver = SecretResolver()
        ref = SecretRef.parse("env:NONEXISTENT_VAR_XYZ")
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(MissingSecretError, match="NONEXISTENT_VAR_XYZ"),
        ):
            resolver.resolve(ref)

    def test_keyring_resolves(self) -> None:
        resolver = SecretResolver()
        ref = SecretRef.parse("keyring:cairn:api-key")
        with patch("keyring.get_password", return_value="secret-from-keyring"):
            assert resolver.resolve(ref) == "secret-from-keyring"

    def test_keyring_missing_raises(self) -> None:
        resolver = SecretResolver()
        ref = SecretRef.parse("keyring:cairn:missing-key")
        with (
            patch("keyring.get_password", return_value=None),
            pytest.raises(MissingSecretError, match="cairn/missing-key"),
        ):
            resolver.resolve(ref)

    def test_prompt_resolves(self) -> None:
        resolver = SecretResolver()
        ref = SecretRef.parse("prompt:Enter API key")
        with patch("getpass.getpass", return_value="prompted-secret"):
            assert resolver.resolve(ref) == "prompted-secret"

    def test_prompt_caches(self) -> None:
        resolver = SecretResolver()
        ref = SecretRef.parse("prompt:Enter API key")
        with patch("getpass.getpass", return_value="first-call") as mock_getpass:
            assert resolver.resolve(ref) == "first-call"
            assert resolver.resolve(ref) == "first-call"
            # Should only prompt once
            mock_getpass.assert_called_once()

    def test_literal_resolves(self) -> None:
        resolver = SecretResolver()
        ref = SecretRef.parse("literal:plaintext-value")
        assert resolver.resolve(ref) == "plaintext-value"

    def test_unknown_scheme_raises(self) -> None:
        resolver = SecretResolver()
        ref = SecretRef(scheme="unknown", params=("x",))
        with pytest.raises(MissingSecretError, match="Unknown secret scheme"):
            resolver.resolve(ref)
