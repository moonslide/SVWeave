"""Trusted (out-of-repo) state: policy location, ownership checks.

The trusted policy must never be readable from, or writable through, the repository tree:
a committed `policy.json` could otherwise grant itself roots, editing and network access.
Layout: `$XDG_CONFIG_HOME` (fallback `~/.config`) `/svweave/workspaces/<workspace_key>/`
where `workspace_key = sha256(realpath(dir containing svweave.yaml))[:16]`. M4 will keep the
approval store in the same directory (`trusted_dir`).
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from svweave.config.loader import load_policy
from svweave.config.models import Manifest, Policy
from svweave.config.paths import is_within, lexical_join
from svweave.domain.errors import ErrorCode, SvwError
from svweave.domain.ids import sha256_hex
from svweave.security.safeio import write_under


def config_home() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg and os.path.isabs(xdg):
        return Path(xdg)
    return Path.home() / ".config"


def workspace_key(manifest_dir: Path) -> str:
    return sha256_hex(os.path.realpath(manifest_dir))[:16]


def trusted_dir(manifest_dir: Path) -> Path:
    """Directory holding trusted per-workspace state (policy now, approval store in M4)."""
    return config_home() / "svweave" / "workspaces" / workspace_key(manifest_dir)


def policy_path_for(manifest_path: Path, override: Path | None = None) -> Path:
    return override if override is not None else trusted_dir(manifest_path.parent) / "policy.json"


def ensure_outside_tree(path: Path, *trees: Path) -> None:
    real = Path(os.path.realpath(path))
    for t in trees:
        if is_within(real, Path(os.path.realpath(t))):
            raise SvwError(
                ErrorCode.POLICY_DENIED,
                f"trusted policy path {path} is inside the project tree; policy must live outside "
                "the repository",
                {"path": str(path), "tree": str(t)},
            )


#: ``details.reason`` of every refusal of the state directory itself
STATE_DIR_REASONS = frozenset({
    "state_dir_symlink", "state_dir_foreign_owner", "state_dir_not_directory",
    "state_dir_writable_by_others", "state_dir_not_adopted",
})


def check_state_dir(path: Path) -> None:
    """The runtime state dir must be a real directory owned by the current user."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if os.path.islink(path):
        raise SvwError(
            ErrorCode.POLICY_DENIED,
            f"{path} is a symlink; refusing to use it as state dir",
            {"path": str(path), "reason": "state_dir_symlink"},
        )
    if st.st_uid != os.geteuid():
        raise SvwError(
            ErrorCode.POLICY_DENIED,
            f"{path} is not owned by the current user",
            {"path": str(path), "uid": st.st_uid, "reason": "state_dir_foreign_owner"},
        )
    if not stat.S_ISDIR(st.st_mode):
        raise SvwError(ErrorCode.POLICY_DENIED, f"{path} is not a directory",
                       {"path": str(path), "reason": "state_dir_not_directory"})
    if st.st_mode & 0o022:  # others could plant builds, snapshots or journals in it
        raise SvwError(
            ErrorCode.POLICY_DENIED,
            f"{path} is writable by group or others; refusing to use it as state dir "
            f"(chmod go-w {path})",
            {"path": str(path), "reason": "state_dir_writable_by_others"},
        )


#: in the trusted directory: which `.svweave/` directories SVWeave created (or a person adopted)
STATE_RECORD = "state_dir.json"
#: inside `.svweave/`: a random token whose sha256 the record holds. A repository, tarball or copy
#: cannot ship the right token (it never leaves this machine's state); no device or inode number
#: is compared (unstable on NFS and across remounts; an inode can be reused: third council review)
STATE_TOKEN = ".svweave-id"
_RECORD_SCHEMA = 2
_MAX_RECORDED = 256
ADOPT_HINT = (
    "SVWeave trusts only a .svweave/ it recorded for this workspace: a repository, tarball, copy or "
    "shared directory can ship one with fabricated builds, snapshots or journals. If you created "
    "this one (an earlier SVWeave version, or the project was moved or copied), run "
    "`svweave init --adopt-state` once; otherwise move it aside (it may hold an interrupted "
    "commit's journal: do not delete it unread)"
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _read_token(state: Path) -> str | None:
    """The token inside ``state``, read without following any link (None: absent or unusable)."""
    try:
        dfd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        fd = os.open(STATE_TOKEN, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                     dir_fd=dfd)
    except OSError:
        return None
    finally:
        os.close(dfd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = os.read(fd, 256).decode("ascii", "replace").strip()
    except OSError:
        return None
    finally:
        os.close(fd)
    return raw if re.fullmatch(r"[0-9a-f]{64}", raw) else None


def _read_record(trusted: Path) -> dict[str, Any]:
    try:
        fd = os.open(trusted / STATE_RECORD, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return {}
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return {}
        with os.fdopen(os.dup(fd), "rb") as f:
            rec = json.loads(f.read(1 << 20) or b"null")
    except (OSError, ValueError):
        return {}
    finally:
        os.close(fd)
    return rec if isinstance(rec, dict) else {}


def _recorded(trusted: Path) -> dict[str, str]:
    rec = _read_record(trusted)
    dirs = rec.get("dirs") if rec.get("schema") == _RECORD_SCHEMA else None
    if not isinstance(dirs, dict):
        return {}
    return {k: v for k, v in dirs.items() if isinstance(k, str) and isinstance(v, str)}


_held = threading.local()


@contextmanager
def state_dir_lock(trusted: Path) -> Iterator[None]:
    """One registration decision at a time per workspace (re-entrant within a thread). Without it
    two first commands each wrote their own token, and the token left in the directory need not be
    the one the record holds: a spurious ``state_dir_not_adopted``."""
    if getattr(_held, "dirs", None) is None:
        _held.dirs = set()
    key = os.path.realpath(trusted)
    if key in _held.dirs:
        yield
        return
    trusted.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(trusted / ".state_dir.lock",
                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _held.dirs.add(key)
        try:
            yield
        finally:
            _held.dirs.discard(key)
    finally:
        os.close(fd)


def register_state_dir(trusted: Path, state: Path) -> None:
    """Record ``state`` (an existing real directory) as a trusted state dir of this workspace: a
    fresh token inside it, its sha256 in the trusted record, keyed by the real path."""
    with state_dir_lock(trusted):
        _register(trusted, state)


def _register(trusted: Path, state: Path) -> None:
    check_state_dir(state)
    os.chmod(state, 0o700, follow_symlinks=False)  # private, whatever umask made it
    token = secrets.token_hex(32)
    write_under(state, STATE_TOKEN, (token + "\n").encode("ascii"), 0o600)
    trusted.mkdir(parents=True, exist_ok=True, mode=0o700)
    dirs = _recorded(trusted)
    key = os.path.realpath(state)
    dirs.pop(key, None)
    dirs[key] = _sha(token)
    while len(dirs) > _MAX_RECORDED:
        dirs.pop(next(iter(dirs)))
    tmp = trusted / f".{STATE_RECORD}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"schema": _RECORD_SCHEMA, "dirs": dirs}, f)
    os.replace(tmp, trusted / STATE_RECORD)


def state_dir_registered(trusted: Path, state: Path) -> bool:
    want = _recorded(trusted).get(os.path.realpath(state))
    token = _read_token(state)
    return want is not None and token is not None and hmac.compare_digest(_sha(token), want)


def state_dir_has_content(state: Path) -> bool:
    """Anything in ``state`` besides a token (a token alone vouches for nothing)."""
    return any(name != STATE_TOKEN for name in os.listdir(state))


def verify_state_dir(trusted: Path, state: Path) -> None:
    """``state`` is a `.svweave/` SVWeave created for this workspace (or a person adopted).

    Missing: created now (0700) and recorded. Recorded (its token matches the record for its real
    path): trusted. Present, empty, unrecorded: nothing in it to trust, recorded. Present,
    unrecorded and not empty: ``POLICY_DENIED`` (reason ``state_dir_not_adopted``)."""
    check_state_dir(state)
    if not os.path.lexists(state):
        try:
            os.mkdir(state, 0o700)
        except FileExistsError:
            check_state_dir(state)  # a concurrent SVWeave process created it
    if state_dir_registered(trusted, state):
        return
    with state_dir_lock(trusted):  # decide once the other registrations are done
        if state_dir_registered(trusted, state):
            return
        if not state_dir_has_content(state):
            _register(trusted, state)
            return
    raise SvwError(
        ErrorCode.POLICY_DENIED,
        f"{state} is not recorded as this workspace's SVWeave state; refusing to use it. If you "
        "created it (an earlier SVWeave version, or the project was moved or copied), run "
        "`svweave init --adopt-state` once",
        {"path": str(state), "reason": "state_dir_not_adopted", "hint": ADOPT_HINT},
    )


def load_trusted_policy(
    manifest_path: Path, manifest: Manifest, override: Path | None
) -> tuple[Path, Policy]:
    """The ONE way to find and load the policy (used by WorkspaceContext and doctor)."""
    mdir = manifest_path.parent
    project_root = lexical_join(mdir, manifest.project.root)
    ppath = policy_path_for(manifest_path, override)
    ensure_outside_tree(ppath, mdir, project_root)
    if not ppath.exists():
        raise SvwError(
            ErrorCode.INVALID_CONFIG,
            f"trusted policy not found at {ppath}; run `svweave init`",
            {"file": str(ppath)},
        )
    return ppath, load_policy(ppath)
