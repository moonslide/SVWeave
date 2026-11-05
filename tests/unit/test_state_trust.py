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
from svweave.config.trusted import STATE_RECORD, STATE_TOKEN
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


def test_SEC_STA04_a_replaced_or_forged_state_dir_does_not_match_its_record(
        tmp_path: Path) -> None:
    """The record holds the sha256 of a random token inside the directory SVWeave created: a
    directory shipped in its place (``rm -rf && clone``, an extracted tarball; the same path, maybe
    the same inode) lacks the token, and a forged token does not match. No device or inode number
    is compared (third council review M2)."""
    w = _fresh(tmp_path)
    assert _cli(w.root, "snapshot", "create", "--profile", "p")[0] == 0
    state = w.root / ".svweave"
    token = (state / STATE_TOKEN).read_text()
    os.rename(state, tmp_path / "old-state")
    _plant(w)  # shipped in its place: no token
    code, env = _cli(w.root, "snapshot", "create", "--profile", "p")
    assert env["error"]["details"]["reason"] == "state_dir_not_adopted", env
    (state / STATE_TOKEN).write_text("0" * 64 + "\n")  # a forged token
    code, env = _cli(w.root, "snapshot", "create", "--profile", "p")
    assert env["error"]["details"]["reason"] == "state_dir_not_adopted", env
    (state / STATE_TOKEN).unlink()
    (tmp_path / "token").write_text(token)  # the right token, reached through a link
    (state / STATE_TOKEN).symlink_to(tmp_path / "token")
    code, env = _cli(w.root, "snapshot", "create", "--profile", "p")
    assert env["error"]["details"]["reason"] == "state_dir_not_adopted", env
    assert (state / "svweave.db").read_bytes() == b"fabricated"  # untouched
    # the person's own backup restored in place carries the token: still the same state
    shutil.rmtree(state)
    shutil.copytree(tmp_path / "old-state", state, symlinks=True)
    code, env = _cli(w.root, "snapshot", "create", "--profile", "p")
    assert code == 0, env.get("error")


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


def _sub_root(tmp_path: Path, name: str = "ws") -> Ws:
    """A workspace whose manifest says ``project.root: sub``: its state dir is ``sub/.svweave``."""
    w = Ws(tmp_path / name)
    w.write("sub/rtl/a.sv", "module a; endmodule\n")
    w.config({"p": profile(source_inputs=[{"kind": "file", "path": "rtl/a.sv"}],
                           top_modules=["a"])},
             project={"id": "t", "root": "sub", "default_profile": "p"})
    w.policy_path.parent.mkdir(parents=True, exist_ok=True)
    w.policy_path.write_text(json.dumps({**POLICY, "allowed_roots": [str(w.root.resolve())]}))
    return w


def test_SEC_STA06_init_records_and_adopts_the_state_dir_every_command_uses(
        tmp_path: Path) -> None:
    """Third council review H2: with ``project.root`` other than ``.`` init recorded
    ``<--root>/.svweave`` while every other command checks ``<project.root>/.svweave``."""
    w = _sub_root(tmp_path)
    code, env = _cli(w.root, "init")
    assert code == 0, env.get("error")
    assert env["data"]["state_dir"]["path"] == str((w.root / "sub" / ".svweave").resolve())
    assert not (w.root / ".svweave").exists()
    code, env = _cli(w.root, "snapshot", "create", "--profile", "p")
    assert code == 0, env.get("error")
    a = _sub_root(tmp_path, "adopt")
    (a.root / "sub" / ".svweave").mkdir()
    (a.root / "sub" / ".svweave" / "notes.txt").write_text("mine")
    code, env = _cli(a.root, "snapshot", "create", "--profile", "p")
    assert env["error"]["details"]["reason"] == "state_dir_not_adopted", env
    code, env = _cli(a.root, "init")
    assert env["error"]["details"]["reason"] == "state_dir_not_adopted", env
    code, env = _cli(a.root, "init", "--adopt-state")
    assert code == 0 and env["data"]["state_dir"]["adopted"] is True, env
    code, env = _cli(a.root, "snapshot", "create", "--profile", "p")
    assert code == 0, env.get("error")
    assert not (a.root / ".svweave").exists()


def test_SEC_STA07_a_first_format_record_admits_its_directory_once_and_is_rewritten(
        tmp_path: Path) -> None:
    w = _fresh(tmp_path)
    state = w.root / ".svweave"
    state.mkdir(mode=0o700)
    (state / "notes.txt").write_text("made by the 2026-11-05 version")
    st = os.lstat(state)
    record = w.policy_path.parent / STATE_RECORD
    record.write_text(json.dumps({"path": os.path.realpath(state), "dev": st.st_dev,
                                  "ino": st.st_ino}))
    code, env = _cli(w.root, "snapshot", "create", "--profile", "p")
    assert code == 0, env.get("error")
    rec = json.loads(record.read_text())
    assert rec["schema"] == 2 and list(rec["dirs"]) == [os.path.realpath(state)]
    assert (state / STATE_TOKEN).is_file()


def test_SEC_STA08_a_state_dir_others_can_write_or_a_file_is_refused(tmp_path: Path) -> None:
    w = _fresh(tmp_path)
    assert _cli(w.root, "snapshot", "create", "--profile", "p")[0] == 0
    state = w.root / ".svweave"
    os.chmod(state, 0o775)
    code, env = _cli(w.root, "snapshot", "create", "--profile", "p")
    assert env["error"]["details"]["reason"] == "state_dir_writable_by_others", env
    f = _fresh(tmp_path, "file")
    (f.root / ".svweave").write_text("not a directory")
    code, env = _cli(f.root, "snapshot", "create", "--profile", "p")
    assert env["error"]["code"] == "POLICY_DENIED", env
    assert env["error"]["details"]["reason"] == "state_dir_not_directory", env


def test_SEC_STA09_the_mcp_launcher_and_doctor_name_the_refused_state_dir_and_the_fix(
        tmp_path: Path) -> None:
    """Third council review M5: on the MCP path only the message was printed (no
    `--adopt-state` hint) and doctor labelled the refusal ``policy_tightening``."""
    from svweave.launcher import launcher

    w = _fresh(tmp_path)
    _plant(w)
    res = CliRunner().invoke(launcher, ["--repo", str(w.root)])
    assert res.exit_code == 6 and res.stdout == ""
    assert "POLICY_DENIED" in res.stderr and "svweave-mcp: hint:" in res.stderr
    assert "svweave init --adopt-state" in res.stderr
    code, env = _cli(w.root, "doctor")
    names = {c["name"]: c for c in env["data"]["checks"]}
    assert names["state_dir"]["status"] == "FAIL", names.keys()
    assert "policy_tightening" not in names


def _first_command_a(trusted: Path, state: Path, a_wrote: Any, b_done: Any) -> None:
    from svweave.config import trusted as T

    real = T.write_under

    def token_then_wait(*args: Any, **kw: Any) -> None:
        real(*args, **kw)
        a_wrote.set()
        b_done.wait(2)  # B may run in between, unless the registration lock holds it

    T.write_under = token_then_wait  # type: ignore[assignment]  # this child process only
    T.verify_state_dir(trusted, state)


def _first_command_b(trusted: Path, state: Path, a_wrote: Any, b_done: Any) -> None:
    from svweave.config import trusted as T

    a_wrote.wait(10)
    T.verify_state_dir(trusted, state)
    b_done.set()


def test_SEC_STA10_two_first_commands_leave_one_token_and_its_record(tmp_path: Path) -> None:
    """Two SVWeave processes starting on a fresh workspace each wrote their own token: B wrote
    token and record between A's token and A's record, leaving B's token next to A's record, so
    every later command refused the directory (state_dir_not_adopted). Registration decisions
    are now serialized by a lock in the trusted directory."""
    import multiprocessing as mp

    from svweave.config.trusted import state_dir_registered

    trusted, state = tmp_path / "trusted", tmp_path / "proj" / ".svweave"
    state.parent.mkdir()
    ctx = mp.get_context("spawn")  # no fork of the multi-threaded test process
    a_wrote, b_done = ctx.Event(), ctx.Event()
    procs = [ctx.Process(target=f, args=(trusted, state, a_wrote, b_done))
             for f in (_first_command_a, _first_command_b)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
    assert [p.exitcode for p in procs] == [0, 0]
    assert state_dir_registered(trusted, state), "the token left behind is not the recorded one"
