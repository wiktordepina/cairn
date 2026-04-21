"""Built-in tools that ship with cairn V1.

Each ``make_<tool>`` factory builds a decorated ``Tool`` against the
collaborators it needs — typically a ``WorkspaceSandbox`` for filesystem
tools. The CLI calls these factories once at startup and hands the
resulting ``Tool`` list to ``DefaultToolRegistry``.
"""

from cairn.tools.builtin._file_read import (
    FileReadArgs,
    make_file_read,
)
from cairn.tools.builtin._file_write import (
    FileWriteArgs,
    make_file_write,
)
from cairn.tools.builtin._grep import GrepArgs, make_grep

__all__ = [
    "FileReadArgs",
    "FileWriteArgs",
    "GrepArgs",
    "make_file_read",
    "make_file_write",
    "make_grep",
]
