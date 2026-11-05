"""Names inside the state directory (a leaf module: storage.db and execution both import it).

The one name of the semantic database (first council review, architecture finding 7: it was
defined twice and hard-coded in five places)."""

from __future__ import annotations

from pathlib import Path

DB_FILENAME = "svweave.db"


def db_path(state_dir: Path) -> Path:
    return state_dir / DB_FILENAME
