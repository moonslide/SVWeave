"""SEC-EXT: commit / recovery writes into an EXTERNAL root follow no link in any component below
that root (first council review, security finding 4). Before the fix only the last directory of
`@id/a/b/c.sv` was opened no-follow: a parent swapped for a symlink between the compare-and-set
check and the rename redirected the approved bytes into a directory someone else chose."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from svweave.domain.errors import ErrorCode, SvwError
from svweave.domain.ids import sha256_hex
from svweave.editing import journal as jr
from tests.conftest import Ws, profile


def _ext_ws(tmp_path: Path) -> tuple[Ws, Path]:
    ext = tmp_path / "ext"
    (ext / "a" / "b").mkdir(parents=True)
    (ext / "a" / "b" / "c.sv").write_bytes(b"module c; endmodule\n")
    w = Ws(tmp_path / "ws")
    w.write("rtl/t.sv", "module t; endmodule\n")
    w.config({"p": profile()}, source_roots=[
        {"id": "project", "path": ".", "include": ["**"], "exclude": []},
        {"id": "ext", "include": ["**"], "exclude": []},
    ])
    w.local([{"id": "ext", "path": str(ext)}])
    w.policy(allowed_roots=[str(w.root.resolve()), str(ext.resolve())])
    return w, ext


def _journal(before: bytes, after: bytes) -> jr.Journal:
    return jr.Journal("txn-" + "a" * 20, "0" * 64, "1" * 64, "2026-11-05T00:00:00+00:00",
                      files=[jr.JournalFile("@ext/a/b/c.sv", sha256_hex(before), sha256_hex(after))])


def test_SEC_EXT01_an_external_root_write_lands_in_that_root(tmp_path: Path) -> None:
    w, ext = _ext_ws(tmp_path)
    ctx = w.ctx()
    new = b"module c; wire w; endmodule\n"
    jr.apply_files(ctx, _journal(b"module c; endmodule\n", new), {"@ext/a/b/c.sv": new})
    assert (ext / "a" / "b" / "c.sv").read_bytes() == new


def test_SEC_EXT02_a_parent_swapped_for_a_link_is_refused_not_followed(tmp_path: Path) -> None:
    w, ext = _ext_ws(tmp_path)
    ctx = w.ctx()
    elsewhere = tmp_path / "attacker"
    (elsewhere / "b").mkdir(parents=True)
    old = b"module c; endmodule\n"
    (elsewhere / "b" / "c.sv").write_bytes(old)  # same bytes: the CAS alone would pass
    os.rename(ext / "a", tmp_path / "a-moved")
    (ext / "a").symlink_to(elsewhere)
    new = b"module c; wire w; endmodule\n"
    with pytest.raises(SvwError) as e:
        jr.apply_files(ctx, _journal(old, new), {"@ext/a/b/c.sv": new})
    assert e.value.code is ErrorCode.POLICY_DENIED
    assert (elsewhere / "b" / "c.sv").read_bytes() == old, "the write followed the swapped parent"
    assert jr.target_sha(ctx, "@ext/a/b/c.sv") == "non-regular"
