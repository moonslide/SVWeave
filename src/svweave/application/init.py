"""`svweave init`: create svweave.yaml template, .svweave/ and the trusted policy.

Never overwrites an existing file; never touches global MCP client config or git hooks.
The policy is written OUTSIDE the repository (see config/trusted.py) and a repo-local
`.svweave/policy.json` is never adopted.
"""

from __future__ import annotations

import os
from pathlib import Path

from svweave.application.templates import manifest_text, policy_text
from svweave.config.context import MANIFEST_FILENAME, STATE_DIRNAME
from svweave.config.loader import load_manifest
from svweave.config.paths import is_within, lexical_join
from svweave.config.trusted import (
    ADOPT_HINT,
    check_state_dir,
    ensure_outside_tree,
    policy_path_for,
    register_state_dir,
    state_dir_has_content,
    state_dir_lock,
    state_dir_registered,
    trusted_dir,
)
from svweave.domain.envelope import AnalysisCoverage, Envelope, Outcome
from svweave.domain.errors import ErrorCode, SvwError
from svweave.domain.states import Completeness


def _write_new(path: Path, text: str, mode: int = 0o666) -> bool:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)  # 'x': never clobber
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def _state_location(root: Path, manifest_path: Path | None) -> tuple[Path, Path, Path]:
    """(manifest file, manifest directory, project root) exactly as WorkspaceContext.load
    resolves them (third council review H2: init recorded ``<--root>/.svweave`` while every other
    command used ``<project.root>/.svweave``, so ``--adopt-state`` adopted the wrong directory)."""
    mpath = Path(os.path.abspath(manifest_path)) if manifest_path is not None else (
        root / MANIFEST_FILENAME)
    mdir = Path(os.path.realpath(mpath.parent))
    project_root = mdir  # the template init writes has project.root "."
    if mpath.is_file():
        manifest = load_manifest(mpath)
        project_root = Path(os.path.realpath(lexical_join(mdir, manifest.project.root)))
    return mpath, mdir, project_root


def _prepare_state(state: Path, trusted: Path, root: Path, adopt_state: bool) -> list[str]:
    """Create or adopt ``state`` and record it; returns the directories created."""
    with state_dir_lock(trusted):  # no other registration decides in between
        return _prepare_state_locked(state, trusted, root, adopt_state)


def _prepare_state_locked(state: Path, trusted: Path, root: Path, adopt_state: bool) -> list[str]:
    check_state_dir(state)  # symlink / foreign owner / not a directory => POLICY_DENIED
    existed = state.exists()
    if existed and state_dir_has_content(state) and not state_dir_registered(trusted, state):
        if not adopt_state:
            raise SvwError(
                ErrorCode.POLICY_DENIED,
                f"{state} exists and is not recorded as this workspace's SVWeave state; if you "
                "created it, run `svweave init --adopt-state` once",
                {"path": str(state), "reason": "state_dir_not_adopted", "hint": ADOPT_HINT},
            )
    elif adopt_state and not existed:
        raise SvwError(ErrorCode.INVALID_CONFIG, f"--adopt-state: {state} does not exist",
                       {"path": str(state)})
    created: list[str] = []
    for sub in ("", "manifests", "snapshots"):
        d = state / sub if sub else state
        if not d.exists():
            d.mkdir(parents=True, mode=0o700)
            created.append(os.path.relpath(d, root) + "/")
    if not state_dir_registered(trusted, state):  # created here, or adopted by this request
        register_state_dir(trusted, state)
    return created


def init_workspace(
    root: Path, request_id: str, policy_path: Path | None = None, *, adopt_state: bool = False,
    manifest_path: Path | None = None,
) -> Envelope:
    root = root.resolve()
    skipped: list[str] = []
    mpath, mdir, project_root = _state_location(root, manifest_path)
    state = project_root / STATE_DIRNAME
    trusted = trusted_dir(mdir)
    ensure_outside_tree(trusted, mdir, project_root)
    state_info: dict[str, object] = {"path": str(state), "adopted": False}
    if is_within(project_root, mdir):
        existed = os.path.lexists(state)
        created = _prepare_state(state, trusted, root, adopt_state)
        state_info["adopted"] = bool(adopt_state and existed)
    elif adopt_state:
        raise SvwError(
            ErrorCode.POLICY_DENIED,
            "--adopt-state: project.root leaves the manifest directory; list it in the trusted "
            "policy's allowed_roots first, then adopt",
            {"project_root": str(project_root), "reason": "project_root_not_allowed"},
        )
    else:  # the first command creates it, once the trusted policy allows that root
        created = []
        state_info["deferred"] = "project_root_outside_manifest_dir"
    rel = os.path.relpath(mpath, root)
    (created if _write_new(mpath, manifest_text(root.name)) else skipped).append(rel)
    ppath = policy_path_for(mpath, policy_path)
    ensure_outside_tree(ppath, root, mdir, project_root)
    ppath.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    policy_created = _write_new(ppath, policy_text(mdir), 0o600)
    return Envelope(
        request_id=request_id,
        outcome=Outcome.OK,
        analysis_coverage=AnalysisCoverage(
            completeness=Completeness.COMPLETE,
            scope="init",
            basis="filesystem",
            limitations=["Global MCP client config and git hooks are never modified."],
        ),
        data={
            "root": str(root),
            "created": created,
            "skipped_existing": skipped,
            "trusted_policy": {"path": str(ppath), "created": policy_created},
            "state_dir": state_info,
        },
    )
