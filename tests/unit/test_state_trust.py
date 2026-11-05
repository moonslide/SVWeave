"""SEC-STA: `.svweave/` is trusted only when SVWeave created it for this workspace (its identity is
recorded in the trusted directory, outside the repository) or a person adopted it with
`svweave init --adopt-state` (council review 2; owner decision 2026-11-05). A repository, tarball
or shared directory can ship a `.svweave/` with a fabricated database, snapshots or journal."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from svweave.cli import cli
from svweave.config.trusted import STATE_RECORD
from tests.conftest import POLICY, Ws, profile


def _cli(root: Path, *args: str) -> tuple[int, dict[str, Any]]:
    out = CliRunner().invoke(cli, ["--root", str(root), "--compact", *args])
    assert out.stdout.strip(), out.output
    return out.exit_code, json.loads(out.stdout)


def _fresh(tmp_path: Path, name: str = "ws") -> Ws:
    """A workspace whose trusted policy exists but whose `.svweave/` was never created."""
    w = Ws(tmp_path / name)
    w.write("rtl/a.sv", "module a; endmodule\n")
    w.config({"p": profile(source_inputs=[{"kind": "file", "path": "rtl/a.sv"}],
                           top_modules=["a"])})
    w.policy_path.parent.mkdir(parents=True, exist_ok=True)
    w.policy_path.write_text(json.dumps({**POLICY, "allowed_roots": [str(w.root.resolve())]}))
    return w


def _plant(w: Ws) -> None:
    """As if the repository shipped it: a non-empty `.svweave/` SVWeave never recorded."""
    (w.root / ".svweave" / "snapshots").mkdir(parents=True)
    (w.root / ".svweave" / "svweave.db").write_bytes(b"fabricated")


def test_SEC_STA01_a_shipped_state_dir_is_refused_by_every_command(tmp_path: Path) -> None:
    w = _fresh(tmp_path)
    _plant(w)
    for args in (["manifest", "resolve", "--profile", "p"], ["snapshot", "create", "--profile", "p"],
                 ["report", "--build", "0" * 64]):
        code, env = _cli(w.root, *args)
        assert env["error"]["code"] == "POLICY_DENIED", (args, env["error"])
        assert env["error"]["details"]["reason"] == "state_dir_not_adopted", args
        assert "--adopt-state" in env["error"]["details"]["hint"]
    assert (w.root / ".svweave" / "svweave.db").read_bytes() == b"fabricated"  # untouched
    code, env = _cli(w.root, "init")  # init does not adopt it silently either
    assert env["error"]["details"]["reason"] == "state_dir_not_adopted"


def test_SEC_STA02_adopt_state_is_the_explicit_human_step(tmp_path: Path) -> None:
    w = _fresh(tmp_path)
    (w.root / ".svweave").mkdir()
    (w.root / ".svweave" / "notes.txt").write_text("mine")
    code, env = _cli(w.root, "init", "--adopt-state")
    assert code == 0 and env["data"]["state_dir"]["adopted"] is True, env
    code, env = _cli(w.root, "snapshot", "create", "--profile", "p")
    assert code == 0, env.get("error")


def test_SEC_STA03_a_new_or_empty_state_dir_is_created_or_recorded_without_asking(
        tmp_path: Path) -> None:
    w = _fresh(tmp_path)
    code, env = _cli(w.root, "snapshot", "create", "--profile", "p")  # creates .svweave/
    assert code == 0, env.get("error")
    assert (w.policy_path.parent / STATE_RECORD).is_file()
    assert oct((w.root / ".svweave").stat().st_mode & 0o777) == "0o700"
    e = _fresh(tmp_path, "empty")
    (e.root / ".svweave").mkdir()  # empty: nothing in it to trust
    code, env = _cli(e.root, "snapshot", "create", "--profile", "p")
    assert code == 0, env.get("error")


def test_SEC_STA04_a_copied_or_replaced_state_dir_no_longer_matches_its_record(
        tmp_path: Path) -> None:
    w = _fresh(tmp_path)
    assert _cli(w.root, "snapshot", "create", "--profile", "p")[0] == 0
    state = w.root / ".svweave"
    moved = tmp_path / "elsewhere"
    shutil.copytree(state, moved, symlinks=True)  # same content, another directory
    os.rename(state, tmp_path / "old-state")
    os.rename(moved, state)
    code, env = _cli(w.root, "snapshot", "create", "--profile", "p")
    assert env["error"]["details"]["reason"] == "state_dir_not_adopted", env


def test_SEC_STA05_adopt_state_refuses_what_it_cannot_adopt(tmp_path: Path) -> None:
    w = _fresh(tmp_path)
    code, env = _cli(w.root, "init", "--adopt-state")  # nothing there to adopt
    assert code == 2 and env["error"]["code"] == "INVALID_CONFIG"
    target = tmp_path / "target"
    target.mkdir()
    lw = _fresh(tmp_path, "linked")
    (lw.root / ".svweave").symlink_to(target)
    code, env = _cli(lw.root, "init", "--adopt-state")  # a link is never a state dir
    assert env["error"]["code"] == "POLICY_DENIED"
