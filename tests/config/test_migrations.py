"""Tests for schema migration infrastructure."""

from __future__ import annotations

from typing import Any

import pytest

from cairn.config._migrations import _MIGRATIONS, MigrationError, migrate, migration


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    """Clear the migration registry before and after each test."""
    saved = dict(_MIGRATIONS)
    _MIGRATIONS.clear()
    yield
    _MIGRATIONS.clear()
    _MIGRATIONS.update(saved)


class TestMigration:
    def test_no_op_at_current_version(self) -> None:
        raw = {"schema_version": 1}
        assert migrate(raw, target_version=1) is raw

    def test_single_migration(self) -> None:
        @migration(from_version=1, to_version=2)
        def _v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
            raw = dict(raw)
            raw["new_field"] = True
            raw["schema_version"] = 2
            return raw

        result = migrate({"schema_version": 1}, target_version=2)
        assert result["schema_version"] == 2
        assert result["new_field"] is True

    def test_chain_of_migrations(self) -> None:
        @migration(from_version=1, to_version=2)
        def _v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
            raw = dict(raw)
            raw["step1"] = True
            raw["schema_version"] = 2
            return raw

        @migration(from_version=2, to_version=3)
        def _v2_to_v3(raw: dict[str, Any]) -> dict[str, Any]:
            raw = dict(raw)
            raw["step2"] = True
            raw["schema_version"] = 3
            return raw

        result = migrate({"schema_version": 1}, target_version=3)
        assert result["schema_version"] == 3
        assert result["step1"] is True
        assert result["step2"] is True

    def test_gap_in_chain_raises(self) -> None:
        # Register only v2->v3, no v1->v2
        @migration(from_version=2, to_version=3)
        def _v2_to_v3(raw: dict[str, Any]) -> dict[str, Any]:
            raw["schema_version"] = 3
            return raw

        with pytest.raises(MigrationError, match="No migration registered"):
            migrate({"schema_version": 1}, target_version=3)

    def test_missing_schema_version_raises(self) -> None:
        with pytest.raises(MigrationError, match="no schema_version"):
            migrate({})

    def test_downgrade_raises(self) -> None:
        with pytest.raises(MigrationError, match="cannot downgrade"):
            migrate({"schema_version": 5}, target_version=1)

    def test_migration_must_update_version(self) -> None:
        @migration(from_version=1, to_version=2)
        def _broken(raw: dict[str, Any]) -> dict[str, Any]:
            return dict(raw)  # doesn't update schema_version

        with pytest.raises(MigrationError, match="did not update schema_version"):
            migrate({"schema_version": 1}, target_version=2)

    def test_duplicate_registration_raises(self) -> None:
        @migration(from_version=1, to_version=2)
        def _first(raw: dict[str, Any]) -> dict[str, Any]:
            return raw

        with pytest.raises(MigrationError, match="Duplicate"):

            @migration(from_version=1, to_version=2)
            def _second(raw: dict[str, Any]) -> dict[str, Any]:
                return raw
