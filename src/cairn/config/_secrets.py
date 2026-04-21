"""Secret resolution — resolve SecretRef values to actual secrets on demand."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cairn.config._models import SecretRef


class MissingSecretError(Exception):
    """Raised when a secret cannot be resolved from its backing store."""


class SecretResolver:
    """Resolves `SecretRef` instances to their actual secret values.

    The resolver is stateful: `prompt:` references are cached for the
    lifetime of the process. All other schemes are resolved fresh on each call
    (so a rotated keyring entry is picked up without restart).
    """

    def __init__(self) -> None:
        self._prompt_cache: dict[str, str] = {}

    def resolve(self, ref: SecretRef) -> str:
        """Resolve a secret reference to its value."""
        match ref.scheme:
            case "keyring":
                return self._resolve_keyring(ref)
            case "env":
                return self._resolve_env(ref)
            case "prompt":
                return self._resolve_prompt(ref)
            case "literal":
                return self._resolve_literal(ref)
            case _:
                raise MissingSecretError(f"Unknown secret scheme: {ref.scheme!r}")

    def _resolve_keyring(self, ref: SecretRef) -> str:
        import keyring as kr

        service, key = ref.params
        value = kr.get_password(service, key)
        if value is None:
            raise MissingSecretError(
                f"Keyring has no entry for {service}/{key}. "
                f"Run `cairn config secret set {service}:{key}` to store it."
            )
        return value

    def _resolve_env(self, ref: SecretRef) -> str:
        import os

        (name,) = ref.params
        value = os.environ.get(name)
        if value is None:
            raise MissingSecretError(f"Environment variable {name} is not set.")
        return value

    def _resolve_prompt(self, ref: SecretRef) -> str:
        import getpass

        (message,) = ref.params
        if message not in self._prompt_cache:
            self._prompt_cache[message] = getpass.getpass(f"{message}: ")
        return self._prompt_cache[message]

    def _resolve_literal(self, ref: SecretRef) -> str:
        (value,) = ref.params
        return value
