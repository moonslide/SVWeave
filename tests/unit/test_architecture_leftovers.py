"""ARC: architecture findings of both council reviews, pinned so they do not creep back: layer
directions, one validated lock reader, bounded caches, one post-build step for CLI and MCP."""

from __future__ import annotations

import ast
import graphlib
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
    # `-I` ignores PYTHONPATH (third council review): the source tree goes on sys.path in code
    code = (f"import sys; sys.path.insert(0, {str(REPO / 'src')!r}); import {module}; "
            "print('\\n'.join(sorted(sys.modules)))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                         check=True).stdout
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


# ------------------------------------------------------------------ static import matrix (M7)
# Every import of every module, lazy ones inside functions included (third council review M7:
# the runtime check above saw nine pairs and no lazy import). docs/architecture.md states the
# same table; a new direction has to change both.
SRC = REPO / "src" / "svweave"
#: leaf modules any layer may use (they import only domain/): the database file name, the locks,
#: and the execution attempt records storage/db.py keeps
LEAVES = {
    "svweave.storage.paths": "storage_leaf",
    "svweave.storage.lock": "storage_leaf",
    "svweave.execution.ledger": "execution_records",
    "svweave.execution.models": "execution_records",
}
ALLOWED: dict[str, set[str]] = {
    "domain": set(),
    "security": {"domain"},
    "storage_leaf": {"domain"},
    "execution_records": {"domain", "storage_leaf"},
    "config": {"domain", "security"},
    "manifest": {"config", "domain"},
    "snapshot": {"config", "domain", "manifest", "security", "storage_leaf"},
    "storage": {"domain", "snapshot", "execution_records", "storage_leaf"},
    "editing": {"config", "domain", "security", "snapshot", "storage", "storage_leaf"},
    "adapters": {"config", "domain", "editing", "manifest", "security", "snapshot", "storage",
                 "storage_leaf"},
    "orchestration": {"adapters", "domain", "security"},
    "execution": {"adapters", "config", "domain", "manifest", "security", "snapshot",
                  "storage_leaf", "execution_records"},
    "application": {"adapters", "config", "domain", "editing", "execution", "execution_records",
                    "manifest", "orchestration", "security", "snapshot", "storage",
                    "storage_leaf"},
    "gateway": {"application", "domain", "security"},
    "server": {"application", "gateway", "security"},
    "launcher": {"application", "config", "domain", "orchestration", "server"},
    "cli": {"application", "config", "domain", "editing", "gateway", "security", "server"},
}
#: the only import cycles between modules, both through a lazy import: doctor's plugin
#: registration and the Slurm control record (docs/architecture.md, "Known cycles")
KNOWN_MODULE_CYCLES = [
    ["svweave.application.doctor", "svweave.application.doctor_wiring"],
    ["svweave.execution.control", "svweave.execution.remote_bootstrap"],
]


def _modules() -> dict[str, Path]:
    return {".".join(f.relative_to(SRC.parent).with_suffix("").parts).removesuffix(".__init__"): f
            for f in SRC.rglob("*.py")}


def _imports(mods: dict[str, Path]) -> dict[str, set[tuple[str, int]]]:
    """module -> {(imported svweave module, line)}; ``from pkg import sub`` names the submodule."""
    out: dict[str, set[tuple[str, int]]] = {}
    for m, f in mods.items():
        found: set[tuple[str, int]] = set()
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Import):
                found |= {(a.name, n.lineno) for a in n.names if a.name.startswith("svweave")}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0 and (
                    n.module.startswith("svweave")):
                subs = {f"{n.module}.{a.name}" for a in n.names} & set(mods)
                found |= {(x, n.lineno) for x in subs} or {(n.module, n.lineno)}
        out[m] = {(t, ln) for t, ln in found if t != m}
    return out


def _group(mod: str) -> str:
    if mod in LEAVES:
        return LEAVES[mod]
    parts = mod.split(".")
    return "svweave" if len(parts) == 1 else parts[1]


def test_ARC_01_every_import_follows_the_documented_matrix() -> None:
    mods = _modules()
    bad = []
    for m, targets in _imports(mods).items():
        src = _group(m)
        assert src in ALLOWED or src == "svweave", f"{m}: no layer in the matrix"
        for t, line in targets:
            dst = _group(t)
            if dst not in (src, "svweave") and dst not in ALLOWED.get(src, set()):
                bad.append(f"{mods[m].relative_to(REPO)}:{line} {src} -> {dst} ({t})")
    assert not bad, "imports outside docs/architecture.md:\n" + "\n".join(sorted(bad))


def test_ARC_01_the_layer_matrix_has_no_cycle() -> None:
    graph = {k: {d for d in v if d != k} for k, v in ALLOWED.items()}
    list(graphlib.TopologicalSorter(graph).static_order())  # CycleError otherwise


def test_ARC_01_module_import_cycles_are_only_the_known_ones() -> None:
    mods = _modules()
    edges = {m: {t for t, _ in ts} for m, ts in _imports(mods).items()}
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    cycles: list[list[str]] = []

    def visit(v: str) -> None:  # Tarjan's strongly connected components
        index[v] = low[v] = len(index)
        stack.append(v)
        on.add(v)
        for w in edges.get(v, ()):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                cycles.append(sorted(comp))

    for v in sorted(mods):
        if v not in index:
            visit(v)
    assert sorted(cycles) == KNOWN_MODULE_CYCLES


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
