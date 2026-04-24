"""Orchestrator + Textual app assembly.

Wires every collaborator needed to drive a real session:

- config resolution (profile selection, secret resolver, model registry)
- persistence (database, repositories)
- providers
- memory services + extraction queue
- tool system (registry, runner, delegation tool, approvers, transformers)
- orchestrator with the above
- Textual app + observer + approval gateway

Tranche 1 ships the entry-point shape so the CLI compiles and the
`cairn` command responds; the full collaborator graph lands
iteratively in follow-up commits on this branch (the bootstrap is
a ~200 LOC wiring exercise with no new business logic, just the
constructors already shipped by earlier bricks).
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def launch(*, profile_name: str | None) -> int:
    """Assemble the orchestrator + Textual app and run it.

    Returns a process exit code.

    Tranche 1 stub: reports that full wiring lands in a follow-up
    commit and exits with a non-zero code. Keeping the stub callable
    means `cairn --help` / `cairn --version` work today and the CLI
    test harness has a stable target.
    """
    del profile_name  # unused until full wiring lands
    message = (
        "cairn: the Textual UI is still being wired up — "
        "`cairn` launch is not yet functional on feat/ui.\n"
        "Track progress: https://github.com/wiktordepina/cairn/pull/…\n"
    )
    sys.stderr.write(message)
    log.info("cairn launch invoked but bootstrap is stubbed (tranche 1)")
    return 2
