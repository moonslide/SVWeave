"""Trusted (out-of-repo) state: policy location, ownership checks.

The trusted policy must never be readable from, or writable through, the repository tree:
a committed `policy.json` could otherwise grant itself roots, editing and network access.
Layout: `$XDG_CONFIG_HOME` (fallback `~/.config`) `/svweave/workspaces/<workspace_key>/`
where `workspace_key = sha256(realpath(dir containing svweave.yaml))[:16]`. M4 will keep the
approval store in the same directory (`trusted_dir`).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from svweave.config.loader import load_policy
from svweave.config.models import Manifest, Policy
from svweave.config.paths import is_within, lexical_join
from svweave.domain.errors import ErrorCode, SvwError
from svweave.domain.ids import sha256_hex


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
            {"path": str(path)},
        )
    if st.st_uid != os.geteuid():
        raise SvwError(
            ErrorCode.POLICY_DENIED,
            f"{path} is not owned by the current user",
            {"path": str(path), "uid": st.st_uid},
        )


#: in the trusted directory: the identity of the `.svweave/` SVWeave created (or a person adopted)
STATE_RECORD = "state_dir.json"
ADOPT_HINT = (
    "a .svweave/ that SVWeave did not create here is not trusted (a repository, tarball or shared "
    "directory can ship one, with fabricated builds and snapshots). If you created it (e.g. the "
    "project was moved or copied), run `svweave init --adopt-state`; otherwise delete it"
)


def _identity(state: Path) -> dict[str, Any]:
    st = os.lstat(state)
    return {"path": os.path.realpath(state), "dev": st.st_dev, "ino": st.st_ino}


def _read_record(trusted: Path) -> dict[str, Any] | None:
    try:
        fd = os.open(trusted / STATE_RECORD, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(os.dup(fd), "rb") as f:
            rec = json.loads(f.read(1 << 16) or b"null")
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)
    return rec if isinstance(rec, dict) else None


def register_state_dir(trusted: Path, state: Path) -> None:
    """Record ``state`` (an existing real directory) as this workspace's trusted state dir."""
    check_state_dir(state)
    trusted.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = trusted / f".{STATE_RECORD}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(_identity(state), f)
    os.replace(tmp, trusted / STATE_RECORD)


def state_dir_registered(trusted: Path, state: Path) -> bool:
    rec = _read_record(trusted)
    try:
        return rec is not None and rec == _identity(state)
    except OSError:
        return False


def verify_state_dir(trusted: Path, state: Path) -> None:
    """``state`` is the `.svweave/` SVWeave created for this workspace (or a person adopted).

    Missing: created now (0700) and recorded. Present and recorded (same real path, device and
    inode): trusted. Present, empty, unrecorded: nothing in it to trust, recorded. Present,
    unrecorded and not empty: ``POLICY_DENIED`` (reason ``state_dir_not_adopted``)."""
    check_state_dir(state)
    if not os.path.lexists(state):
        try:
            os.mkdir(state, 0o700)
        except FileExistsError:
            pass  # a concurrent SVWeave process created it: checked below like any other
        else:
            register_state_dir(trusted, state)
            return
        check_state_dir(state)
    if state_dir_registered(trusted, state):
        return
    if not os.listdir(state):
        register_state_dir(trusted, state)
        return
    raise SvwError(
        ErrorCode.POLICY_DENIED,
        f"{state} was not created by SVWeave for this workspace; refusing to use it",
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
