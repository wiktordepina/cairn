"""Tests for convention-file envelope rendering."""

from __future__ import annotations

from pathlib import Path

from cairn.conventions import (
    ConventionFile,
    ConventionSource,
    render_conventions,
    wrap_one,
)


def _make(
    filename: str = "AGENTS.md",
    path: str = "/repo/AGENTS.md",
    content: str = "hello",
) -> ConventionFile:
    return ConventionFile(
        filename=filename,
        path=Path(path),
        content=content,
        truncated_from=None,
        source=ConventionSource.PROJECT,
    )


def test_wrap_one_format() -> None:
    envelope = wrap_one(_make())
    assert envelope == (
        '<project_conventions source="AGENTS.md" path="/repo/AGENTS.md">\n'
        "hello\n"
        "</project_conventions>"
    )


def test_render_conventions_joins_with_blank_line() -> None:
    out = render_conventions(
        [
            _make(filename="CAIRN.md", path="/repo/CAIRN.md", content="c"),
            _make(filename="AGENTS.md", path="/repo/AGENTS.md", content="a"),
        ],
    )
    assert "</project_conventions>\n\n<project_conventions" in out
    assert out.startswith('<project_conventions source="CAIRN.md"')
    assert out.endswith("</project_conventions>")


def test_render_empty_list_yields_empty_string() -> None:
    assert render_conventions([]) == ""
