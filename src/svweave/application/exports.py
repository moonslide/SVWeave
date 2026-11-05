"""``.svweave/exports/``: where graph pages, atlas directories and rename specs are written.

One owner for the directory, the name rule and the private, no-follow write (first council
review, architecture finding 6; second review: the rule lived in graph's private regex and the
rename spec export borrowed it)."""

from __future__ import annotations

import re
from pathlib import Path

from svweave.domain.errors import ErrorCode, SvwError
from svweave.security.safeio import ensure_private_dir, write_under

EXPORT_DIR = "exports"
_SAFE_NAME = re.compile(r"[A-Za-z0-9._-]{1,120}")


def check_export_name(name: str, field: str) -> str:
    """``name`` is one file or directory name below exports/: no path, no leading dot."""
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name) or name.startswith("."):
        raise SvwError(ErrorCode.INVALID_CONFIG, f"{field} must match [A-Za-z0-9._-]+ (no path)",
                       {"field": field})
    return name


def exports_dir(state_dir: Path) -> Path:
    """The private (0700) exports directory, created if missing."""
    return ensure_private_dir(state_dir, EXPORT_DIR)


def write_export(state_dir: Path, rel: str, data: bytes) -> str:
    """Write ``exports/<rel>`` (0600, atomic, no link followed); returns the shown path."""
    write_under(exports_dir(state_dir), rel, data)
    return f".svweave/{EXPORT_DIR}/{rel}"
