"""SEC-SNP: a ``.svweave/`` is project-writable (a repository can ship one), so a snapshot found
on disk is trusted by neither its name nor its record (council review 2). Capture only, no slang."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from svweave.application import services
from svweave.application.options import WorkspaceOptions
from svweave.application.session import Session
from svweave.application.use_cases import create_snapshot
from svweave.domain.errors import ErrorCode, SvwError
from svweave.manifest.resolver import resolve_profile
from svweave.snapshot.capture import capture_snapshot, load_record, snapshots_dir
from tests.conftest import Ws, profile


def _ws(tmp_path: Path) -> tuple[Ws, str]:
    w = Ws(tmp_path / "ws")
    w.write("rtl/a.sv", "module a; endmodule\n")
    w.config({"p": profile(source_inputs=[{"kind": "file", "path": "rtl/a.sv"}],
                           top_modules=["a"])})
    w.policy()
    env = create_snapshot(WorkspaceOptions(root=w.root), "p", "r")
    assert env.error is None, env.error
    return w, str(env.snapshot_id)


def _thaw(d: Path) -> None:
    for dirpath, _dirnames, filenames in os.walk(d):
        os.chmod(dirpath, 0o755)
        for fn in filenames:
            os.chmod(os.path.join(dirpath, fn), 0o644)


def test_SEC_SNP01_text_search_never_walks_a_planted_files_symlink(tmp_path: Path) -> None:
    w, sid = _ws(tmp_path)
    secret = tmp_path / "home" / ".ssh"
    secret.mkdir(parents=True)
    (secret / "id_key").write_text("PRIVATE KEY material\n")
    sdir = snapshots_dir(w.ctx()) / sid
    _thaw(sdir)
    os.rename(sdir / "files", sdir / "files.orig")
    (sdir / "files").symlink_to(tmp_path / "home")
    s = Session(WorkspaceOptions(root=w.root), use_graph=False)
    with pytest.raises(SvwError) as e:
        services.search(s, "r", snapshot_id=sid, query="PRIVATE KEY")
    assert e.value.code == ErrorCode.POLICY_DENIED
    assert "PRIVATE" not in json.dumps(e.value.details or {})


def test_SEC_SNP02_a_record_that_does_not_match_its_identity_is_refused(tmp_path: Path) -> None:
    w, sid = _ws(tmp_path)
    sdir = snapshots_dir(w.ctx()) / sid
    assert load_record(sdir).snapshot_id == sid
    _thaw(sdir)
    raw = json.loads((sdir / "snapshot.json").read_text())
    for bad in ({"files": [{**raw["files"][0], "snapshot_path": "../../../etc/passwd"}]},
                {"files": [{**raw["files"][0], "sha256": "0" * 64}]},
                {"snapshot_id": "f" * 64}):
        (sdir / "snapshot.json").write_text(json.dumps({**raw, **bad}))
        with pytest.raises(SvwError) as e:
            load_record(sdir)
        assert e.value.details["reason"] == "snapshot_metadata_invalid", bad
    (sdir / "snapshot.json").unlink()
    (sdir / "snapshot.json").symlink_to(tmp_path / "elsewhere.json")
    (tmp_path / "elsewhere.json").write_text(json.dumps(raw))
    with pytest.raises(SvwError):
        load_record(sdir)  # never read through a link


def test_SEC_SNP03_capture_does_not_reuse_a_snapshot_whose_files_dir_is_a_link(
        tmp_path: Path) -> None:
    w, sid = _ws(tmp_path)
    ctx = w.ctx()
    sdir = snapshots_dir(ctx) / sid
    _thaw(sdir)
    os.rename(sdir / "files", tmp_path / "moved")
    (sdir / "files").symlink_to(w.root)  # the live tree: same bytes, but not immutable
    rec = capture_snapshot(ctx, resolve_profile(ctx, "p", {}))
    assert rec.snapshot_id == sid
    assert not (sdir / "files").is_symlink() and (sdir / "files").is_dir()


def test_SEC_SNP04_capture_does_not_reuse_a_snapshot_with_a_planted_record(
        tmp_path: Path) -> None:
    w, sid = _ws(tmp_path)
    ctx = w.ctx()
    sdir = snapshots_dir(ctx) / sid
    _thaw(sdir)
    raw = json.loads((sdir / "snapshot.json").read_text())
    raw["spec"]["roots"] = raw["spec"].get("roots", [])  # same id fields ...
    raw["workspace_id"] = "someone-else"  # ... but another record
    (sdir / "snapshot.json").write_text(json.dumps(raw))
    capture_snapshot(ctx, resolve_profile(ctx, "p", {}))
    assert load_record(sdir).workspace_id == ctx.workspace_id


def test_SEC_SNP05_validate_never_reuses_a_planted_candidate_snapshot(tmp_path: Path) -> None:
    """The candidate snapshot's id is computable from the base and the edit: a directory planted
    under it must not make `edit validate` compile other bytes than the candidate's."""
    from svweave.editing.candsnap import derive_candidate_snapshot

    w, sid = _ws(tmp_path)
    ctx = w.ctx()
    base = load_record(snapshots_dir(ctx) / sid)
    new = b"module a; wire w; endmodule\n"
    first = derive_candidate_snapshot(ctx, base, {"rtl/a.sv": new})
    assert first is not None
    cdir = snapshots_dir(ctx) / first.snapshot_id
    f = cdir / first.files[0].snapshot_path
    _thaw(cdir)
    f.write_bytes(b"module a; EVIL; endmodule\n")  # planted bytes under the candidate's id
    again = derive_candidate_snapshot(ctx, base, {"rtl/a.sv": new})
    assert again is not None and again.snapshot_id == first.snapshot_id
    assert f.read_bytes() == new


def test_SEC_SNP06_gc_cannot_run_while_a_candidate_snapshot_is_written(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """First council review: the candidate snapshot was built without the shared workspace lock,
    so a concurrent `svweave gc` (exclusive, removes every .tmp-*) could delete it mid-copy and
    turn validate into a terminal VALIDATION_FAILED."""
    import svweave.editing.candsnap as cs
    from svweave.storage.lock import WorkspaceBusy, workspace_lock

    w, sid = _ws(tmp_path)
    ctx = w.ctx()
    base = load_record(snapshots_dir(ctx) / sid)
    seen: list[str] = []
    real = cs._derive

    def probe(*a: object, **kw: object) -> object:
        try:  # what gc does: exclusive, non-blocking
            with workspace_lock(ctx.state_dir, exclusive=True, blocking=False):
                seen.append("gc could run")
        except WorkspaceBusy:
            seen.append("gc excluded")
        return real(*a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(cs, "_derive", probe)
    assert cs.derive_candidate_snapshot(ctx, base, {"rtl/a.sv": b"module a; wire w; endmodule\n"})
    assert seen == ["gc excluded"]
