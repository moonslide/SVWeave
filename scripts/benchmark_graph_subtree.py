#!/usr/bin/env python3
"""Paired timing of `svweave graph export` on a whole build vs `--instance` (subtree view).

The design is the hand-written fixture ``fixtures/language/grf_subtree`` (grf_soc: CLUSTERS
clusters x CORES cores x 4 stages; 1 + CLUSTERS*(2+5*CORES) + 1 instances), elaborated ONCE by the
real pyslang worker under mandatory bwrap. Every export afterwards reads that same immutable build:

* ``cli``: wall time of the real CLI in a subprocess (Python start-up included), what a user waits;
* ``in_process``: wall time of ``graph.export_graph`` only (no interpreter start-up).

Modes run interleaved (full, cluster subtree, core subtree, diff views; reversed on the next
trial) so a drift of the host affects all of them alike. The two diff views read commit-to-commit
diffs of the workspace's own git repository (branch ``chg-reduce``: one line of grf_reduce, a
localized change; branch ``chg-core``: one line of grf_core, a change that reaches most of the
design) against the build of ``main``. Each subtree page, and one changed instance of each diff
page, is checked against the full page (same nodes, same edges for signals declared inside): a
page that is fast but different is reported as FAIL, not as a speed-up.

There is no speed threshold and no qualification claim: the JSON records host, scale, raw samples
and medians. Nothing is downloaded; the workspace lives in a temporary directory.

  SVWEAVE_REQUIRE_BACKENDS=1 .venv/bin/python scripts/benchmark_graph_subtree.py \\
      --clusters 32 --cores 64 --runs 5 --out .svweave/bench/graph-subtree.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bench_common import source_identity  # noqa: E402
from benchmark import host_info  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "fixtures" / "language" / "grf_subtree"
SVWEAVE = REPO / ".venv" / "bin" / "svweave"
CLI_TIMEOUT_S = 900.0
_DATA = re.compile(r'<script id="data" type="application/json">(.*?)</script>', re.S)

MANIFEST = """\
schema_version: "1.0"
project:
  id: "svweave-graph-subtree-bench"
  root: "."
  default_profile: "bench"
source_roots:
  - id: "project"
    path: "."
    include: ["rtl/**"]
    exclude: ["**/.svweave/**"]
    writable: false
profiles:
  bench:
    cwd: "."
    language_mode: "systemverilog"
    source_inputs:
      - kind: "filelist"
        path: "rtl/files.f"
        dialect: "svweave-filelist-v1"
        path_mode: "filelist"
    compilation_units:
      mode: "single"
    top_modules: ["grf_soc"]
    defines: []
    include_dirs: []
    top_parameters:
      - {{top: "grf_soc", name: "CLUSTERS", value: "{clusters}"}}
      - {{top: "grf_soc", name: "CORES", value: "{cores}"}}
    library_dirs: []
    library_files: []
    library_extensions: [".v", ".sv"]
    environment_allowlist: []
    generated_inputs: []
    diagnostic_policy: "no_errors"
analysis:
  missing_input_policy: "block"
  unsupported_option_policy: "block"
  stub_policy: "explicit_only"
  max_build_seconds: 900
  max_worker_memory_mib: 8192
editing:
  enabled: false
"""


def _cli(root: Path, args: list[str]) -> tuple[float, int, dict[str, Any]]:
    t0 = time.perf_counter()
    p = subprocess.run(
        [str(SVWEAVE), "--root", str(root), "--compact", *args],
        capture_output=True, text=True, timeout=CLI_TIMEOUT_S, env=os.environ.copy(),
    )
    wall = time.perf_counter() - t0
    try:
        env = json.loads(p.stdout)
    except json.JSONDecodeError:
        env = {"stdout_tail": p.stdout[-2000:], "stderr_tail": p.stderr[-2000:]}
    return wall, p.returncode, env


def _page(path: Path) -> dict[str, Any]:
    m = _DATA.search(path.read_text())
    if not m:
        raise SystemExit(f"no data block in {path}")
    data: dict[str, Any] = json.loads(m.group(1))
    return data


def equivalence(full: dict[str, Any], sub: dict[str, Any], root: str) -> list[str]:
    """Differences between a subtree page and the full page restricted to that subtree (empty
    list = same content). Needs a full page that is not truncated."""
    why: list[str] = []
    pre = root + "."
    fnodes = {n["path"]: n for n in full["nodes"] if n["path"] == root or n["path"].startswith(pre)}
    snodes = {n["path"]: n for n in sub["nodes"]}
    if set(fnodes) != set(snodes):
        why.append(f"node sets differ: {len(fnodes)} full vs {len(snodes)} subtree")
        return why
    shift = fnodes[root]["depth"]
    for p, fn in fnodes.items():
        sn = snodes[p]
        for k in ("definition", "params", "ports", "file", "line", "stub", "interface"):
            if fn[k] != sn[k]:
                why.append(f"{p}: field {k} differs")
        if fn["depth"] - shift != sn["depth"]:
            why.append(f"{p}: relative depth differs")
    fpath = {n["id"]: n["path"] for n in full["nodes"]}
    spath = {n["id"]: n["path"] for n in sub["nodes"]}
    fe = {(fpath[e["from"]], fpath[e["to"]], e["signal"], e["port"]) for e in full["edges"]
          if fpath[e["from"]] in fnodes and fpath[e["to"]] in fnodes and e["signal"].startswith(pre)}
    se = {(spath[e["from"]], spath[e["to"]], e["signal"], e["port"]) for e in sub["edges"]}
    if fe != se:
        why.append(f"edges differ: {len(fe - se)} only in full, {len(se - fe)} only in subtree")
    fh = {h["signal"] for h in full["hubs"] if h["signal"].startswith(pre)}
    sh = {h["signal"] for h in sub["hubs"]}
    if fh != sh:
        why.append(f"hubs differ: {sorted(fh ^ sh)[:5]}")
    return why


_KW = {"--instance": "instance", "--diff-base": "diff_base", "--diff-head": "diff_head"}
_GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "bench",
            "GIT_AUTHOR_EMAIL": "bench@invalid", "GIT_COMMITTER_NAME": "bench",
            "GIT_COMMITTER_EMAIL": "bench@invalid"}


def _git_setup(ws: Path) -> None:
    """``main`` = the fixture; ``chg-reduce`` / ``chg-core`` = one changed line each. The working
    tree stays on ``main``, the revision the build is made from."""
    env = {**os.environ, **_GIT_ENV}

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(ws), *args], check=True, env=env, capture_output=True)

    (ws / ".gitignore").write_text(".svweave/\n")
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    for branch, file, old, new in (
        ("chg-reduce", "rtl/grf_reduce.sv", "acc ^= din[i*W+:W];", "acc += din[i*W+:W];"),
        ("chg-core", "rtl/grf_core.sv", ".K(3)) u_exec", ".K(5)) u_exec"),
    ):
        git("checkout", "-q", "-b", branch, "main")
        f = ws / file
        f.write_text(f.read_text().replace(old, new, 1))
        git("commit", "-q", "-am", branch)
    git("checkout", "-q", "main")


def _restrict(page: dict[str, Any], root: str) -> dict[str, Any]:
    """The part of a page at and below ``root``, depth relative to it (for :func:`equivalence`)."""
    pre = root + "."
    nodes = [n for n in page["nodes"] if n["path"] == root or n["path"].startswith(pre)]
    shift = next(n["depth"] for n in nodes if n["path"] == root)
    ids = {n["id"] for n in nodes}
    return {"nodes": [{**n, "depth": n["depth"] - shift} for n in nodes],
            "edges": [e for e in page["edges"] if e["from"] in ids and e["to"] in ids],
            "hubs": [h for h in page["hubs"] if h["signal"].startswith(pre)]}


def _median(xs: list[float]) -> float:
    return statistics.median(xs) if xs else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clusters", type=int, default=32)
    ap.add_argument("--cores", type=int, default=64)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--max-fanout", type=int, default=16)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    if not (1 <= a.clusters <= 512 and 1 <= a.cores <= 512 and 1 <= a.runs <= 50):
        ap.error("--clusters/--cores in 1..512, --runs in 1..50")
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out = a.out or REPO / ".svweave" / "bench" / f"graph-subtree-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="svw-graph-subtree-"))
    ws = work / "ws"
    shutil.copytree(FIXTURE, ws / "rtl")
    (ws / "svweave.yaml").write_text(MANIFEST.format(clusters=a.clusters, cores=a.cores))
    os.environ["XDG_CONFIG_HOME"] = str(work / "xdg")
    from svweave.config.trusted import policy_path_for

    pol = policy_path_for(ws / "svweave.yaml")
    pol.parent.mkdir(parents=True, exist_ok=True)
    pol.write_text(json.dumps({
        "schema_version": "1.0", "allowed_roots": [str(ws.resolve())], "writable_paths": [],
        "approval_required": True, "network_enabled": False, "editing_enabled": False}))

    _git_setup(ws)
    instances = 1 + a.clusters * (2 + 5 * a.cores) + 1
    mid = a.clusters // 2
    modes: dict[str, list[str]] = {
        "full": [],
        "cluster_subtree": ["--instance", f"grf_soc.g_cl[{mid}].u_cl"],
        "core_subtree": ["--instance", f"grf_soc.g_cl[{mid}].u_cl.g_core[{a.cores // 2}].u_core"],
        "diff_reduce": ["--diff-base", "main", "--diff-head", "chg-reduce"],
        "diff_core": ["--diff-base", "main", "--diff-head", "chg-core"],
    }
    expect_changed = {"diff_reduce": a.clusters + 1, "diff_core": a.clusters * a.cores}
    report: dict[str, Any] = {
        "benchmark": "graph-export-subtree-paired",
        "status": "RUNNING",
        "created_at": stamp,
        "source_identity": source_identity(),
        "toolchain_lock_sha256": hashlib.sha256((REPO / "toolchain.lock.json").read_bytes()).hexdigest(),
        "benchmark_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "host": host_info(work),
        "design": {"fixture": "fixtures/language/grf_subtree", "top": "grf_soc",
                   "clusters": a.clusters, "cores": a.cores, "expected_instances": instances},
        "runs": a.runs, "max_fanout": a.max_fanout, "max_instances": 100000,
        "limitations": [
            "one synthetic hand-written design on one host; not the plan 22.3 reference corpus",
            "page cache warm; the export reads one immutable build that was elaborated once",
            "no speed threshold: medians and raw samples only",
        ],
    }
    try:
        wall, code, b = _cli(ws, ["build", "--profile", "bench"])
        report["build"] = {"wall_s": round(wall, 3), "exit": code, "outcome": b.get("outcome"),
                           "build_id": b.get("build_id")}
        if code != 0 or b.get("outcome") != "OK":
            report["status"] = "BLOCKED"
            report["build"]["envelope"] = b
            return 2
        bid = str(b["build_id"])
        common = ["graph", "export", "--build", bid, "--max-instances", "100000",
                  "--max-fanout", str(a.max_fanout)]
        cli: dict[str, list[float]] = {m: [] for m in modes}
        sizes: dict[str, dict[str, Any]] = {}
        order = list(modes)
        for trial in range(a.runs):
            for m in (order if trial % 2 == 0 else order[::-1]):
                wall, code, env = _cli(ws, [*common, *modes[m], "--name", f"bench-{m}"])
                if code != 0:
                    report["status"] = "FAIL"
                    report.setdefault("errors", []).append({"mode": m, "exit": code, "envelope": env})
                    return 1
                cli[m].append(wall)
                d = env["data"]
                sizes[m] = {"bytes": d["bytes"], "sha256": d["sha256"], "stats": d["stats"],
                            "truncated": d["truncated"], "path": d["path"]}

        from svweave.application import graph
        from svweave.application.options import WorkspaceOptions

        opts = WorkspaceOptions(root=ws)
        inproc: dict[str, list[float]] = {m: [] for m in modes}
        for trial in range(a.runs):
            for m in (order if trial % 2 == 0 else order[::-1]):
                kw = {_KW[k]: v for k, v in zip(modes[m][::2], modes[m][1::2], strict=True)}
                t0 = time.perf_counter()
                graph.export_graph(opts, bid, f"bench-{trial}", name=f"inproc-{m}",
                                   max_instances=100000, max_fanout=a.max_fanout, **kw)
                inproc[m].append(time.perf_counter() - t0)

        full_page = _page(ws / sizes["full"]["path"])
        checks: dict[str, Any] = {}
        for m in ("cluster_subtree", "core_subtree"):
            why = equivalence(full_page, _page(ws / sizes[m]["path"]), modes[m][1])
            checks[m] = {"root": modes[m][1], "equal_to_full_restricted": not why, "differences": why}
        for m, want in expect_changed.items():
            page = _page(ws / sizes[m]["path"])
            changed = [n for n in page["nodes"] if n.get("diff") == "changed"]
            sample = changed[len(changed) // 2]["path"] if changed else ""
            why = [] if len(changed) == want else [f"{len(changed)} changed instances, expected {want}"]
            why += equivalence(full_page, _restrict(page, sample), sample) if sample else ["none"]
            checks[m] = {"changed_instances": len(changed), "sample": sample,
                         "equal_to_full_restricted": not why, "differences": why}
        report["content_checks"] = checks
        report["full_instances_total"] = sizes["full"]["stats"]["instances_total"]
        report["results"] = {
            m: {
                "cli_wall_s": {"median": round(_median(cli[m]), 4), "raw": [round(x, 4) for x in cli[m]]},
                "in_process_s": {"median": round(_median(inproc[m]), 4),
                                 "raw": [round(x, 4) for x in inproc[m]]},
                **sizes[m],
            }
            for m in modes
        }
        f_cli, f_in = _median(cli["full"]), _median(inproc["full"])
        report["speedup_vs_full"] = {
            m: {"cli": round(f_cli / _median(cli[m]), 2), "in_process": round(f_in / _median(inproc[m]), 2)}
            for m in modes if m != "full"
        }
        ok = all(c["equal_to_full_restricted"] for c in checks.values()) and \
            report["full_instances_total"] == instances and not sizes["full"]["truncated"]
        report["status"] = "PASS" if ok else "FAIL"
        return 0 if ok else 1
    finally:
        out.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: report.get(k) for k in
                          ("status", "design", "build", "speedup_vs_full", "content_checks")}, indent=2))
        for m, r in (report.get("results") or {}).items():
            print(f"{m:16s} cli median {r['cli_wall_s']['median']:8.3f}s  in-process median "
                  f"{r['in_process_s']['median']:8.3f}s  {r['bytes']:>11,d} bytes  "
                  f"{r['stats']['instances_shown']} instances")
        print(f"report: {out}")
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
