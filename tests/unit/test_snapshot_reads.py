"""SEC-SNR: every reader of snapshot bytes goes through ``read_snapshot_file``: no link followed
below the snapshot directory and the bytes checked against the record's sha256 (third council
review: rename, diff impact, graph per-commit labels and LSP positions followed links and
skipped the hash check; a `.svweave/` is project-writable)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from svweave.application import queries
from svweave.domain.errors import ErrorCode, SvwError
from svweave.snapshot.capture import load_record, read_snapshot_file, snapshots_dir
from tests.conftest import Ws
from tests.unit._gateway_fx import stub_build


def _writable(p: Path) -> None:
    for d in (p, *p.parents):
        if d.name == "snapshots":
            break
        os.chmod(d, 0o700 if d.is_dir() else 0o600)


def test_SEC_SNR01_tampered_or_linked_snapshot_bytes_are_refused(ws: Ws) -> None:
    opts, bid, sid = stub_build(ws)
    snap = snapshots_dir(opts.load()) / sid
    sf = next(f for f in load_record(snap).files if f.logical_path.endswith(".sv"))
    good = read_snapshot_file(snap, sf)
    pos = queries.lsp_positions(opts, bid, sf.logical_path, 0, 1)
    assert pos["file"] == sf.logical_path
    victim = snap / sf.snapshot_path
    _writable(victim)
    victim.write_bytes(good + b"// planted\n")
    for read in (lambda: read_snapshot_file(snap, sf),
                 lambda: queries.lsp_positions(opts, bid, sf.logical_path, 0, 1)):
        with pytest.raises(SvwError) as ei:
            read()
        assert ei.value.code is ErrorCode.MISSING_INPUT
        assert ei.value.details["reason"] == "snapshot_file_invalid"
    # the right bytes reached through a linked directory are refused as well
    victim.write_bytes(good)
    parent = victim.parent
    kept = ws.root.parent / "kept-dir"
    shutil.copytree(parent, kept)
    shutil.rmtree(parent)
    parent.symlink_to(kept, target_is_directory=True)
    with pytest.raises(SvwError) as ei:
        read_snapshot_file(snap, sf)
    assert ei.value.details["reason"] == "snapshot_file_invalid"
