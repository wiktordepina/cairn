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

__all__ = [
    "FileReadArgs",
    "make_file_read",
]
