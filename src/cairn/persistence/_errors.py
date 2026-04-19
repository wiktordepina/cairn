"""Persistence error hierarchy."""

from __future__ import annotations


class PersistenceError(Exception):
    """Base for all persistence-layer errors."""


class MigrationError(PersistenceError):
    """Raised when a schema migration cannot be applied."""


class InvalidToolCallTransition(PersistenceError):
    """Raised when a tool call lifecycle transition is not allowed."""


class NotFoundError(PersistenceError):
    """Raised when a row is required but not found."""
