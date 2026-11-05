"""ARC: architecture findings of both council reviews, pinned so they do not creep back: layer
directions, one validated lock reader, bounded caches, one post-build step for CLI and MCP."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from svweave.domain.envelope import Envelope, Outcome
from svweave.domain.errors import ErrorCode, SvwError

REPO = Path(__file__).resolve().parents[2]


def _loaded_after(module: str) -> set[str]:
    code = f"import sys, {module}; print('\\n'.join(sorted(sys.modules)))"
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                         check=True, env={"PYTHONPATH": str(REPO / "src")}).stdout
    return set(out.split())


@pytest.mark.parametrize("module,forbidden", [
    # editing/ and config/ never import the application layer (cycles finding 5)
    ("svweave.editing.confirm", "svweave.application"),
    ("svweave.editing.journal", "svweave.application"),
    ("svweave.editing.tree", "svweave.adapters"),
    ("svweave.editing.exact_range", "svweave.adapters"),
    ("svweave.config.context", "svweave.application"),
    # adapters never import orchestration (it composes them)
    ("svweave.adapters.serena.adapter", "svweave.orchestration"),
    ("svweave.adapters.codebase_memory.adapter", "svweave.orchestration"),
    # orchestration's doctor checks never import the application layer
    ("svweave.orchestration.doctor_checks", "svweave.application"),
    # redaction is shared by CLI and MCP without loading the gateway
    ("svweave.security.redaction", "svweave.gateway"),
])
def test_ARC_01_layers_import_one_way(module: str, forbidden: str) -> None:
    bad = sorted(m for m in _loaded_after(module) if m == forbidden or m.startswith(forbidden + "."))
    assert not bad, f"{module} loads {bad}"


def test_ARC_02_a_lock_that_does_not_match_its_schema_fails_where_it_is_read(
        tmp_path: Path) -> None:
    from svweave.adapters.toolchain import Toolchain
    from svweave.config.toolchain_lock import read_lock

    lock = json.loads((REPO / "toolchain.lock.json").read_text())
    del lock["components"]
    (tmp_path / "toolchain.lock.json").write_text(json.dumps(lock))
    (tmp_path / "capability_report.json").write_text("{}")
    with pytest.raises(SvwError) as e:
        Toolchain.load(tmp_path)
    assert e.value.code is ErrorCode.INVALID_CONFIG and e.value.details["reason"] == "lock_schema"
    assert read_lock(REPO / "toolchain.lock.json")["components"]  # the real lock is valid


def test_ARC_03_freshness_caches_are_bounded_and_resettable(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from svweave.application import semantic_common as sc

    monkeypatch.setattr(sc, "CACHE_ENTRIES", 3)
    sc.reset_caches()
    for i in range(10):
        sc._put(sc._VERDICTS, ("s", str(i)), ("fp", True))
    assert list(sc._VERDICTS) == [("s", "7"), ("s", "8"), ("s", "9")]  # oldest dropped
    sc.reset_caches()
    assert not sc._VERDICTS and not sc._RECORDS


def test_ARC_04_cli_build_and_update_take_the_one_post_build_step(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from svweave.application import build, evidence, update
    from svweave.cli import cli

    seen: list[str | None] = []
    monkeypatch.setattr(evidence, "post_build", lambda opts, bid: seen.append(bid))
    monkeypatch.setattr(build, "run_build", lambda opts, profile, rid, **kw: Envelope(
        request_id=rid, outcome=Outcome.OK, build_id="a" * 64))
    monkeypatch.setattr(update, "run_update", lambda opts, profile, rid, **kw: Envelope(
        request_id=rid, outcome=Outcome.OK, build_id="b" * 64))
    for cmd in (["build"], ["update", "--no-git"]):
        r = CliRunner().invoke(cli, ["--root", str(tmp_path), "--compact", *cmd])
        assert r.exit_code == 0, r.output
    assert seen == ["a" * 64, "b" * 64]


def test_ARC_05_write_safety_code_is_in_editing_not_in_the_serena_adapter() -> None:
    from svweave.adapters.serena import guard
    from svweave.editing import exact_range, tree

    assert guard.apply_exact_range is exact_range.apply_exact_range
    assert guard.tree_hashes is tree.tree_hashes
    src = (REPO / "src/svweave/adapters/serena/guard.py").read_text()
    assert "def apply_exact_range" not in src and "def tree_hashes" not in src
