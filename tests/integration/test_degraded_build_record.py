"""SEC-ISO: a build produced by a worker without bwrap isolation says so in every later answer,
not only in the envelope of the build call (council review). Real pyslang worker; only the
isolation label of its outcome is replaced, to stand in for SVWEAVE_ALLOW_DEGRADED_WORKER=1 on a
host without bwrap (which a CI host with bwrap cannot otherwise produce)."""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from svweave.application import build as B
from tests.integration.helpers import lang_ws
from tests.integration.test_cli_semantic import run

pytestmark = pytest.mark.integration

NOTE = "ran WITHOUT bwrap isolation"


def _degraded(monkeypatch: pytest.MonkeyPatch) -> Any:
    real = B.run_worker

    def worker(*a: Any, **kw: Any) -> Any:
        out = real(*a, **kw)
        return dataclasses.replace(out, isolation="degraded_no_isolation")

    monkeypatch.setattr(B, "run_worker", worker)
    return real


def test_SEC_ISO01_degraded_isolation_is_stored_and_repeated_by_later_queries(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = lang_ws(tmp_path, "sem01_scopes", "sem01_top")
    real = _degraded(monkeypatch)
    code, b = run(w.root, "build", "--profile", "p")
    assert code == 0 and b["data"]["worker_isolation"] == "degraded_no_isolation"
    bid = b["build_id"]
    monkeypatch.setattr(B, "run_worker", real)  # later calls: a normal, sandboxed process
    db = sqlite3.connect(w.root / ".svweave" / "svweave.db")
    stats, limits = db.execute(
        "SELECT stats, limitations FROM build_reports WHERE build_id=?", (bid,)).fetchone()
    assert '"worker_isolation":"degraded_no_isolation"' in stats and NOTE in limits
    code, h = run(w.root, "query", "hierarchy", "--build", bid)
    assert code == 0 and any(NOTE in x for x in h["analysis_coverage"]["limitations"])
    code, again = run(w.root, "build", "--profile", "p")  # reused, still labelled
    assert again["build_id"] == bid and again["data"].get("reused") is True
    assert any(NOTE in x for x in again["analysis_coverage"]["limitations"])


def test_SEC_ISO02_a_sandboxed_build_carries_no_such_limitation(tmp_path: Path) -> None:
    w = lang_ws(tmp_path, "sem01_scopes", "sem01_top")
    code, b = run(w.root, "build", "--profile", "p")
    assert code == 0 and b["data"]["worker_isolation"] == "bwrap"
    assert not any(NOTE in x for x in b["analysis_coverage"]["limitations"])
    db = sqlite3.connect(w.root / ".svweave" / "svweave.db")
    (stats,) = db.execute("SELECT stats FROM build_reports WHERE build_id=?",
                          (b["build_id"],)).fetchone()
    assert '"worker_isolation":"bwrap"' in stats


def test_SEC_ISO03_a_blocked_build_of_a_degraded_worker_is_stored_with_its_isolation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Council review 2: the dependency-leak (BLOCKED) path stored no isolation record."""
    visible = Path("/etc/nsswitch.conf")  # outside the snapshot but exposed by the worker sandbox
    if not visible.is_file():
        pytest.skip("no /etc/nsswitch.conf on this host")
    w = lang_ws(tmp_path, "sem01_scopes", "sem01_top")
    top = w.root / "top.sv"
    top.write_text(f'`include "{visible}"\n' + top.read_text())
    _degraded(monkeypatch)
    code, b = run(w.root, "build", "--profile", "p")
    assert code == 3 and b["outcome"] == "BLOCKED"
    db = sqlite3.connect(w.root / ".svweave" / "svweave.db")
    stats, limits = db.execute(
        "SELECT stats, limitations FROM build_reports WHERE build_id=?", (b["build_id"],)
    ).fetchone()
    assert '"worker_isolation":"degraded_no_isolation"' in stats and NOTE in limits
