"""Drift check for `docs/gen_ref_pages.py`.

Runs the generator with `--check` against the real repo state. If
anyone changes `__all__` (or its `# Section` comments) without
regenerating, this fails locally before they push.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GENERATOR = REPO_ROOT / "docs" / "gen_ref_pages.py"


def test_reference_pages_in_sync() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "Reference docs are out of date — re-run "
        "`uv run python docs/gen_ref_pages.py`.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
