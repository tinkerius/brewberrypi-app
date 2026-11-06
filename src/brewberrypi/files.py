"""Files the application writes into its configuration folder (NFR-023, runtime contract §3).

The folder is shared with the brewer, who edits it over the network as another user of the same group, so
everything the application creates there is group-writable: files `0664`, directories `0775` (a set-group-ID
bit inherited from the parent is kept). Files are written atomically: temporary file, fsync, rename.
"""

import os
from pathlib import Path

FILE_MODE = 0o664
DIRECTORY_MODE = 0o775


def write_file(path: Path, text: str) -> None:
    """Write `text` (UTF-8) to `path` atomically; a reader sees the old file or the whole new one."""
    write_bytes(path, text.encode("utf-8"))


def write_bytes(path: Path, content: bytes) -> None:
    """Write `content` to `path` atomically, like `write_file`."""
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        os.fchmod(descriptor, FILE_MODE)  # independent of the umask
        view = memoryview(content)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    sync_directory(path.parent)


def make_directory(path: Path) -> None:
    """Create the directory `path`, group-writable."""
    path.mkdir()
    os.chmod(path, (path.stat().st_mode & 0o7000) | DIRECTORY_MODE)


def sync_directory(path: Path) -> None:
    """Make a rename in the directory `path` durable."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
