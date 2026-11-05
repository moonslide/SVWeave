"""The gateway exact-range patch inside a candidate (plan 12.2 path 2, ADR-012) and the edit
outcome record every edit path returns. Write-safety code: in ``editing/``, not in a backend
adapter (first council review, architecture finding 4)."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from svweave.domain.errors import ErrorCode, SvwError
from svweave.security import safeio

EditPath = Literal["serena_symbol", "exact_range"]


@dataclass
class EditOutcome:
    """Result of one edit; ``edit_path`` says HOW it was produced and is never inferred later."""

    edit_path: EditPath
    relative_path: str
    changed_files: tuple[str, ...]
    #: Files changed beyond the one the edit was asked to change (must be empty).
    unexpected_files: tuple[str, ...]
    old_span: tuple[int, int] | None  # byte range in the OLD file that was replaced
    new_span: tuple[int, int] | None  # byte range in the NEW file now holding the replacement
    #: True when the change is contained in the declared target span (symbol or explicit range).
    within_target: bool
    #: True only for edits produced by Serena itself (plan 12.2 path 1).
    counted_as_serena_semantic_edit: bool
    target: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.unexpected_files and self.within_target and bool(self.changed_files)

    def raise_if_surprising(self) -> None:
        if self.unexpected_files or not self.within_target:
            raise SvwError(
                ErrorCode.POLICY_DENIED,
                "edit changed files or bytes outside the requested target; candidate rejected",
                {
                    "reason": "unexpected_candidate_changes",
                    "unexpected_files": list(self.unexpected_files),
                    "within_target": self.within_target,
                    "edit_path": self.edit_path,
                },
            )


def changed_region(old: bytes, new: bytes) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Minimal differing region as (old_span, new_span) via common prefix / suffix."""
    if old == new:
        return None
    n = min(len(old), len(new))
    p = 0
    while p < n and old[p] == new[p]:
        p += 1
    s = 0
    while s < n - p and old[len(old) - 1 - s] == new[len(new) - 1 - s]:
        s += 1
    return (p, len(old) - s), (p, len(new) - s)


def safe_candidate_file(candidate: Path, relative_path: str) -> Path:
    """Resolve ``relative_path`` under the candidate; reject escapes, symlinks, non-files."""
    if not relative_path or relative_path.startswith("/") or "\x00" in relative_path:
        raise SvwError(ErrorCode.PATH_NOT_ALLOWED, "relative path required", {"path": relative_path[:200]})
    parts = relative_path.split("/")
    if any(p in ("..", "") for p in parts):
        raise SvwError(ErrorCode.PATH_NOT_ALLOWED, "path escapes the candidate", {"path": relative_path[:200]})
    cur = candidate
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            raise SvwError(ErrorCode.PATH_NOT_ALLOWED, "symlink in candidate path refused",
                           {"path": relative_path[:200]})
    real = Path(os.path.realpath(cur))
    base = Path(os.path.realpath(candidate))
    if base not in real.parents or not real.is_file():
        raise SvwError(ErrorCode.PATH_NOT_ALLOWED, "not a regular file inside the candidate",
                       {"path": relative_path[:200]})
    return real


def apply_exact_range(
    candidate: Path,
    relative_path: str,
    start_byte: int,
    end_byte: int,
    expected_sha256: str,
    replacement: bytes,
) -> EditOutcome:
    """Gateway exact-range patch inside the candidate (plan 12.2 path 2).

    Replaces ``[start_byte, end_byte)`` only if those bytes hash to ``expected_sha256``; labelled
    ``edit_path='exact_range'`` and NEVER counted as a Serena semantic edit.
    """
    target = safe_candidate_file(candidate, relative_path)
    data = target.read_bytes()
    if not (0 <= start_byte <= end_byte <= len(data)):
        raise SvwError(ErrorCode.EDIT_CONFLICT, "range outside the file", {"reason": "range_out_of_bounds"})
    if hashlib.sha256(data[start_byte:end_byte]).hexdigest() != expected_sha256:
        raise SvwError(ErrorCode.EDIT_CONFLICT, "bytes at the range differ from the expected hash",
                       {"reason": "expected_bytes_mismatch"})
    new = data[:start_byte] + replacement + data[end_byte:]
    mode = target.stat().st_mode & 0o777
    # The candidate is backend-writable: write no-follow via a fresh O_EXCL temp + rename.
    safeio.write_under(Path(os.path.realpath(candidate)), relative_path, new, mode)
    return EditOutcome(
        edit_path="exact_range",
        relative_path=relative_path,
        changed_files=(relative_path,),
        unexpected_files=(),
        old_span=(start_byte, end_byte),
        new_span=(start_byte, start_byte + len(replacement)),
        within_target=True,
        counted_as_serena_semantic_edit=False,
        target={"start_byte": start_byte, "end_byte": end_byte},
    )
