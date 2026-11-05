"""Candidate tree hashing and change sets: the write path's view of what a backend changed in a
candidate (EDT-06). Write-safety code lives here in ``editing/``, not in a backend adapter
(first council review, architecture finding 4)."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from svweave.domain.errors import ErrorCode, SvwError

#: Files a backend adapter writes into a candidate itself (Serena's verible filelist); the
#: gateway excludes them from edit diffs unless they also exist in the base.
GENERATED_FILES: frozenset[str] = frozenset({"verible.filelist"})


def _hash_file(p: Path) -> str:
    """sha256 of a regular file opened without following a link and without blocking (a FIFO
    planted by the backend must never hang the gateway)."""
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as f:
        h = hashlib.sha256()
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _unreadable(e: OSError) -> None:
    raise SvwError(
        ErrorCode.POLICY_DENIED,
        "candidate tree has an unreadable directory: its changes cannot be checked",
        {"reason": "candidate_unreadable", "path": getattr(e, "filename", None)},
    )


def tree_hashes(root: Path) -> dict[str, str]:
    """relative POSIX path -> sha256, ``symlink:<target>`` or ``special:<type>`` for every entry
    under ``root``. The tree is backend-writable: non-regular files are never opened, and an
    unreadable directory is an error, not a silently skipped subtree."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=_unreadable):
        dirnames.sort()
        for dn in dirnames:  # os.walk lists symlinks to directories here and never descends
            dp = Path(dirpath) / dn
            if dp.is_symlink():
                out[dp.relative_to(root).as_posix()] = "symlink:" + os.readlink(dp)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            mode = os.lstat(p).st_mode
            if stat.S_ISLNK(mode):
                out[rel] = "symlink:" + os.readlink(p)
            elif not stat.S_ISREG(mode):
                out[rel] = f"special:{stat.S_IFMT(mode):o}"  # fifo, socket, device: never opened
            else:
                out[rel] = _hash_file(p)
    return out


@dataclass(frozen=True)
class ChangeSet:
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(sorted({*self.added, *self.removed, *self.modified}))

    def __bool__(self) -> bool:
        return bool(self.files)


def diff_hashes(before: dict[str, str], after: dict[str, str]) -> ChangeSet:
    return ChangeSet(
        added=tuple(sorted(set(after) - set(before))),
        removed=tuple(sorted(set(before) - set(after))),
        modified=tuple(sorted(k for k in set(before) & set(after) if before[k] != after[k])),
    )
