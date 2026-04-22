"""Cairn memory — tier-1 observation extraction, storage, and retrieval.

This subpackage sits on top of `cairn.persistence.MemoryRepo` and wires
in:

- JSONL observation log (per-day append-only disk mirror; see
  `ObservationLog`).
- Extraction worker driving the utility model (later phase).
- Retrieval service implementing the orchestrator's `MemoryService`
  protocol (later phase).

The persistence schema lives in `cairn.persistence`; the domain types
(`MemoryEntry`, `MemoryEntryType`, `MemoryClass`) live in
`cairn.domain`. This module owns everything in between.
"""

from cairn.memory._observation_log import Observation, ObservationLog

__all__ = [
    # Observation log
    "Observation",
    "ObservationLog",
]
