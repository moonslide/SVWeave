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
from svweave.config.trusted import (
    ADOPT_HINT,
    check_state_dir,
    ensure_outside_tree,
    policy_path_for,
    register_state_dir,
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


def init_workspace(
    root: Path, request_id: str, policy_path: Path | None = None, *, adopt_state: bool = False
) -> Envelope:
    root = root.resolve()
    created: list[str] = []
    skipped: list[str] = []
    state = root / STATE_DIRNAME
    check_state_dir(state)  # symlink / foreign owner => POLICY_DENIED
    trusted = trusted_dir(root)
    existed = state.exists()
    if existed and any(state.iterdir()) and not state_dir_registered(trusted, state):
        if not adopt_state:
            raise SvwError(
                ErrorCode.POLICY_DENIED,
                f"{state} exists and was not created by SVWeave for this workspace",
                {"path": str(state), "reason": "state_dir_not_adopted", "hint": ADOPT_HINT},
            )
    elif adopt_state and not existed:
        raise SvwError(ErrorCode.INVALID_CONFIG, f"--adopt-state: {state} does not exist",
                       {"path": str(state)})
    for sub in ("", "manifests", "snapshots"):
        d = state / sub if sub else state
        if not d.exists():
            d.mkdir(parents=True, mode=0o700)
            created.append(str(d.relative_to(root)) + "/")
    register_state_dir(trusted, state)  # created here, or adopted by this explicit request
    mpath = root / MANIFEST_FILENAME
    rel = MANIFEST_FILENAME
    (created if _write_new(mpath, manifest_text(root.name)) else skipped).append(rel)
    ppath = policy_path_for(mpath, policy_path)
    ensure_outside_tree(ppath, root)
    ppath.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    policy_created = _write_new(ppath, policy_text(root), 0o600)
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
            "state_dir": {"path": str(state), "adopted": bool(adopt_state and existed)},
        },
    )
