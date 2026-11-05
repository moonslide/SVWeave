"""Serena-specific candidate change tracking (EDT-06 hook). The hashing, change sets and the
exact-range patch it builds on are write-path code in ``svweave.editing`` (re-exported here for
the adapter API)."""

from __future__ import annotations

from pathlib import Path

from svweave.editing.exact_range import (
    EditOutcome,
    EditPath,
    apply_exact_range,
    changed_region,
    safe_candidate_file,
)
from svweave.editing.tree import ChangeSet, diff_hashes, tree_hashes

__all__ = [
    "CandidateGuard",
    "ChangeSet",
    "EditOutcome",
    "EditPath",
    "apply_exact_range",
    "changed_region",
    "diff_hashes",
    "safe_candidate_file",
    "tree_hashes",
]


class CandidateGuard:
    """Hash baseline of a candidate directory; ``changed()`` lists what changed since."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._baseline: dict[str, str] = {}

    def baseline(self) -> dict[str, str]:
        self._baseline = tree_hashes(self.root)
        return self._baseline

    def changed(self) -> ChangeSet:
        return diff_hashes(self._baseline, tree_hashes(self.root))
