"""QUAL: policy leftovers of the first council review in scripts/qualify.py and the mutation
suite: G0 never verifies with --skip-uvm, the EDA "Coverage" slot is RTL coverage, a dirty tree
blocks the release decision, the report lists only existing evidence, --no-baseline never
counts a mutation as killed."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import qualify  # noqa: E402


def test_QUAL_01_G0_lock_verification_never_skips_uvm(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """test-evidence finding 14: `--verify-only --skip-uvm` exits 0 without UVM; nothing pinned
    qualify's G0 to call it without the flag."""
    seen: list[list[str]] = []

    def sh(argv: list[str], log: Path, **kw: Any) -> dict[str, Any]:
        seen.append([str(a) for a in argv])
        return {"rc": 0}

    monkeypatch.setattr(qualify, "sh", sh)
    (tmp_path / "logs").mkdir()
    qualify.step_lock(tmp_path)
    assert seen and "--verify-only" in seen[0] and not any("skip" in a for a in seen[0])


def test_QUAL_02_the_EDA_coverage_slot_is_rtl_coverage_only() -> None:
    """policy finding 2 (rule 11): the slot showed MEASURED for SVWeave's own pytest coverage."""
    eda = qualify.eda_states()
    assert eda["coverage"]["state"] in ("NOT_RUN", "EXTERNAL_DECLARED")
    src = Path(qualify.__file__).read_text()
    assert 'line("Coverage:   ", eda["coverage"]["state"])' in src
    assert "MEASURED (SVWeave software QA only" not in src


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True,
                          timeout=900, check=False)


def test_QUAL_03_a_dirty_tree_blocks_and_only_existing_evidence_is_listed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """policy findings 3 and 9."""
    out = tmp_path / "q"
    monkeypatch.setattr(qualify, "git", lambda *a: " M tests/unit/x.py" if a[0] == "status"
                        else "0" * 40)
    rc = qualify.main(["--out-dir", str(out), "--skip", ",".join(qualify.STEPS)])
    rep = json.loads((out / "report.json").read_text())
    assert rc == 1 and rep["release_decision"] == "BLOCKED"
    assert any("uncommitted change" in r for r in rep["decision_reasons"])
    for p in rep["evidence_paths"]:
        assert (ROOT / p).exists(), p  # absolute paths ignore ROOT
    assert "evidence/m0/" not in rep["evidence_paths"]


def test_QUAL_03b_an_unknown_tree_state_blocks_like_a_dirty_tree(tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Third council review M3: when `git status` failed the uncommitted list was empty and
    nothing blocked."""
    out = tmp_path / "q"
    monkeypatch.setattr(qualify, "git", lambda *a: None)
    rc = qualify.main(["--out-dir", str(out), "--skip", ",".join(qualify.STEPS)])
    rep = json.loads((out / "report.json").read_text())
    assert rc == 1 and rep["release_decision"] == "BLOCKED" and rep["tree_state"] == "unknown"
    assert any("state is unknown" in r for r in rep["decision_reasons"])
    assert rep["source_commit"].endswith("+unknown-tree")


def test_QUAL_04_no_baseline_never_counts_a_mutation_as_killed(tmp_path: Path) -> None:
    """policy finding 8: with --no-baseline a test that was already red counted as KILLED."""
    dest = tmp_path / "m.json"
    r = _cli("scripts/mutation_suite.py", "--only", "M32", "--no-baseline", "--out", str(dest),
             "--scratch", str(tmp_path / "scratch"))
    res = json.loads(dest.read_text())
    assert res["baseline_checked"] is False and res["all_killed"] is False, r.stdout[-800:]
    assert [m["status"] for m in res["mutations"]] == ["UNVERIFIED"] and res["killed"] == 0


def test_QUAL_05_the_lock_refresh_tool_refuses_without_an_explicit_online_flag() -> None:
    """policy finding 4: a maintainer tool that downloads and rewrites the frozen lock runs only
    on an explicit --online step (AGENTS rule 9 keeps product downloads to the installer)."""
    lock = (ROOT / "toolchain.lock.json").read_bytes()
    r = _cli("scripts/gateway_lock_refresh.py")
    assert r.returncode == 2 and "--online" in r.stderr
    assert (ROOT / "toolchain.lock.json").read_bytes() == lock
    src = (ROOT / "scripts" / "gateway_lock_refresh.py").read_text()
    assert "assert want ==" not in src  # a digest check `python -O` would strip
