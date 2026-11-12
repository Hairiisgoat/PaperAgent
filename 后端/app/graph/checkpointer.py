"""Explicitly scoped SQLite checkpoint connections for synchronous graphs."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from contextlib import closing
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver


@contextmanager
def sqlite_checkpointer(path: Path, *, read_only: bool = False, require_existing: bool = False) -> Iterator[SqliteSaver]:
    """Open a file-backed saver and close its connection on exit."""
    if str(path) == ":memory:" or str(path).lower().startswith("file:"):
        raise ValueError("A file-backed SQLite path is required")
    if read_only or require_existing:
        # Never silently create a checkpoint file when reading or resuming.
        mode = "ro" if read_only else "rw"
        with closing(sqlite3.connect(path.resolve().as_uri() + f"?mode={mode}", uri=True, check_same_thread=False)) as connection:
            yield SqliteSaver(connection)
    else:
        with SqliteSaver.from_conn_string(str(path)) as saver:
            yield saver
