"""GRF-I: `svweave graph export` on the real demo design (real pyslang, no backend servers)."""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from typing import Any

import pytest

from tests.integration.demo_support import demo_ws
from tests.integration.test_cli_semantic import run

pytestmark = pytest.mark.integration


def _page_data(path: Path) -> dict[str, Any]:
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', path.read_text(), re.S)
    assert m
    from svweave.application.graph_codec import FORMAT, decode

    raw = json.loads(m.group(1))
    assert raw.get("format") == FORMAT  # pages are written compact (svweave-graph/2)
    data: dict[str, Any] = decode(raw)
    return data


def _built(tmp_path: Path, **kw: Any) -> tuple[Path, str]:
    w = demo_ws(tmp_path, **kw)
    code, b = run(w.root, "build", "--profile", "p")
    assert code == 0 and b["outcome"] == "OK"
    return w.root, str(b["build_id"])


def test_GRF_I01_export_writes_one_private_self_contained_file(tmp_path: Path) -> None:
    root, bid = _built(tmp_path)
    code, env = run(root, "graph", "export", "--build", bid)
    assert code == 0 and env["outcome"] == "OK" and env["freshness"] == "CURRENT"
    d = env["data"]
    assert d["served"] is False and d["path"] == f".svweave/exports/graph-{bid[:12]}.html"
    f = root / d["path"]
    assert f.is_file() and stat.S_IMODE(f.stat().st_mode) == 0o600
    assert f.stat().st_size == d["bytes"]
    assert any("not a netlist" in x or "netlist" in x for x in env["analysis_coverage"]["limitations"])
    data = _page_data(f)
    paths = [n["path"] for n in data["nodes"]]
    assert paths[0] == "demo_top" and len(paths) == 5 and data["stats"]["instances_total"] == 5
    assert data["meta"]["build_id"] == bid and data["meta"]["evidence_kind"] == "HDL_STATIC_RESOLVED"
    by_path = {n["path"]: n["id"] for n in data["nodes"]}
    edges = {(e["from"], e["to"], e["signal"]) for e in data["edges"]}
    top, lane0 = by_path["demo_top"], by_path["demo_top.g_lane[0].u_lane"]
    assert (top, lane0, "demo_top.clk") in edges  # top port -> child input
    assert (lane0, top, "demo_top.data_o") in edges  # child output -> top port
    assert not any(e["signal"].endswith(".lane") for e in data["edges"])  # genvar is not a signal


def test_GRF_I02_graph_follows_the_named_build_configuration(tmp_path: Path) -> None:
    r2, b2 = _built(tmp_path / "a", lanes=2)
    r3, b3 = _built(tmp_path / "b", lanes=3)
    n2 = _page_data(r2 / run(r2, "graph", "export", "--build", b2)[1]["data"]["path"])["nodes"]
    n3 = _page_data(r3 / run(r3, "graph", "export", "--build", b3)[1]["data"]["path"])["nodes"]
    assert len(n2) == 5 and len(n3) == 7  # LANES=3 elaborates one more lane (+ its SVA bind)


def test_GRF_I03_limits_hubs_and_refusals(tmp_path: Path) -> None:
    root, bid = _built(tmp_path, lanes=4)
    code, env = run(root, "graph", "export", "--build", bid, "--max-fanout", "2", "--name", "hubs")
    assert code == 0 and any("listed as hubs" in w for w in env["warnings"])
    data = _page_data(root / env["data"]["path"])
    assert "demo_top.clk" in [h["signal"] for h in data["hubs"]]
    assert not any(e["signal"] == "demo_top.clk" for e in data["edges"])
    code, env = run(root, "graph", "export", "--build", bid, "--max-instances", "3", "--name", "cut")
    assert code == 0 and env["data"]["truncated"] is True
    assert any(w.startswith("truncated:") for w in env["warnings"])
    cut = _page_data(root / env["data"]["path"])
    assert [n["depth"] for n in cut["nodes"]] == sorted(n["depth"] for n in cut["nodes"])[: len(cut["nodes"])]
    for bad in (["--name", "../x"], ["--name", "a/b"], ["--name", ".hidden"], ["--max-fanout", "1"],
                ["--max-instances", "0"]):
        code, e = run(root, "graph", "export", "--build", bid, *bad)
        assert code == 2 and e["error"]["code"] == "INVALID_CONFIG", bad
    code, e = run(root, "graph", "export", "--build", "0" * 64)
    assert code == 3 and e["error"]["code"] == "MISSING_INPUT"
    assert not (root.parent / "x.html").exists()


# ------------------------------------------------------------------ subtree view (--instance)
# fixtures/language/grf_subtree: grf_soc -> g_cl[c].u_cl -> g_core[i].u_core -> 4 grf_stage, plus
# one grf_reduce per cluster and one at the top. Expectations below are derived from that RTL.
def _grf(tmp_path: Path, clusters: int = 2, cores: int = 2) -> tuple[Path, str]:
    from tests.integration.helpers import lang_ws

    w = lang_ws(tmp_path, "grf_subtree", "grf_soc", top_parameters=[
        {"top": "grf_soc", "name": "CLUSTERS", "value": str(clusters)},
        {"top": "grf_soc", "name": "CORES", "value": str(cores)},
    ])
    code, b = run(w.root, "build", "--profile", "p")
    assert code == 0 and b["outcome"] == "OK", b
    return w.root, str(b["build_id"])


def _edges(data: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    p = {n["id"]: n["path"] for n in data["nodes"]}
    return {(p[e["from"]], p[e["to"]], e["signal"], e["port"]) for e in data["edges"]}


def test_GRF_I04_subtree_draws_exactly_the_rtl_below_the_root(tmp_path: Path) -> None:
    root, bid = _grf(tmp_path)
    cl = "grf_soc.g_cl[1].u_cl"
    code, env = run(root, "graph", "export", "--build", bid, "--instance", cl)
    assert code == 0 and env["outcome"] == "OK" and env["data"]["root"] == cl
    assert env["data"]["path"].startswith(f".svweave/exports/graph-{bid[:12]}-sub-")
    assert any("subtree view" in x for x in env["analysis_coverage"]["limitations"])
    assert any("boundary signals" in w for w in env["warnings"])
    data = _page_data(root / env["data"]["path"])
    assert data["meta"]["root"] == cl and data["root"] == cl
    core0, core1 = f"{cl}.g_core[0].u_core", f"{cl}.g_core[1].u_core"
    stages = ("u_fetch", "u_decode", "u_exec", "u_wb")
    expect = {cl: 0, core0: 1, core1: 1, f"{cl}.u_red": 1}
    expect.update({f"{c}.{s}": 2 for c in (core0, core1) for s in stages})
    assert {n["path"]: n["depth"] for n in data["nodes"]} == expect  # 2 + 5*CORES, depth from root
    assert [n["path"] for n in data["nodes"] if n["parent"] is None] == [cl]
    assert data["stats"]["instances_total"] == 12 and data["truncated"] is False
    assert not any(n["path"].startswith("grf_soc.g_cl[0]") for n in data["nodes"])
    e = _edges(data)
    # the core pipeline: fetch -> decode -> exec -> wb through the core's named signals
    for a, b, sig in (("u_fetch", "u_decode", "f2d"), ("u_decode", "u_exec", "d2e"),
                      ("u_exec", "u_wb", "e2w")):
        assert (f"{core0}.{a}", f"{core0}.{b}", f"{core0}.{sig}", "q -> d") in e
    # fan-out of the cluster input to every core, fan-in of every core into the reducer
    for c in (core0, core1):
        assert (cl, c, f"{cl}.cin", "din") in e
        assert (c, f"{cl}.u_red", f"{cl}.core_bus", "dout -> din") in e
    assert (f"{cl}.u_red", cl, f"{cl}.cout", "dout") in e
    assert all(s.startswith(cl + ".") for _, _, s, _ in e)  # nothing drawn from outside
    # what the root's own ports connect to in grf_soc is listed, never drawn
    assert {(x["signal"], x["instances"]) for x in data["boundary"]} == {
        ("grf_soc.clk", 1), ("grf_soc.rst_n", 1), ("grf_soc.din", 1), ("grf_soc.cluster_bus", 1)}
    # a core: 4 stages, the f2d/d2e/e2w chain, and its port signals from the cluster as boundary
    code, env = run(root, "graph", "export", "--build", bid, "--instance", core0, "--name", "core")
    core = _page_data(root / env["data"]["path"])
    assert [n["path"] for n in core["nodes"]] == [core0] + [f"{core0}.{s}" for s in stages]
    assert {x["signal"] for x in core["boundary"]} == {
        f"{cl}.clk", f"{cl}.rst_n", f"{cl}.cin", f"{cl}.core_bus"}


def test_GRF_I05_every_subtree_equals_the_full_page_restricted_to_it(tmp_path: Path) -> None:
    """Reading only the subtree must not change what is drawn: for every instance as root, nodes,
    their ports/params, edges and hubs of signals declared inside equal the full export's."""
    root, bid = _grf(tmp_path, clusters=2, cores=3)
    for fanout in ("1000", "3"):  # no hubs; then cluster/core clk & rst_n become hubs
        code, env = run(root, "graph", "export", "--build", bid, "--max-fanout", fanout,
                        "--name", f"full{fanout}")
        full = _page_data(root / env["data"]["path"])
        assert len(full["nodes"]) == 1 + 2 * (2 + 5 * 3) + 1 and "boundary" not in full
        by_path = {n["path"]: n for n in full["nodes"]}
        kids: dict[str | None, list[str]] = {}
        for n in full["nodes"]:
            kids.setdefault(n["parent"], []).append(n["id"])
        for i, r in enumerate(full["nodes"]):
            code, env = run(root, "graph", "export", "--build", bid, "--instance", r["path"],
                            "--max-fanout", fanout, "--name", f"s{fanout}-{i}")
            assert code == 0, env
            sub = _page_data(root / env["data"]["path"])
            below, todo = set(), [r["id"]]  # closure of the parent links in the FULL page
            while todo:
                x = todo.pop()
                below.add(x)
                todo.extend(kids.get(x, []))
            assert {n["id"] for n in sub["nodes"]} == below, r["path"]
            for n in sub["nodes"]:
                f = by_path[n["path"]]
                assert {k: v for k, v in n.items() if k not in ("depth", "parent", "collapsed")} == \
                    {k: v for k, v in f.items() if k not in ("depth", "parent", "collapsed")}
                assert n["depth"] == f["depth"] - r["depth"]
            pre = r["path"] + "."
            inside = {x for x in _edges(full) if x[2].startswith(pre)
                      and by_path[x[0]]["id"] in below and by_path[x[1]]["id"] in below}
            assert _edges(sub) == inside, r["path"]
            assert {h["signal"] for h in sub["hubs"]} == \
                {h["signal"] for h in full["hubs"] if h["signal"].startswith(pre)}, r["path"]
            if fanout == "3" and r["path"].endswith("u_cl"):
                assert {h["signal"] for h in sub["hubs"]} >= {pre + "clk", pre + "rst_n"}


def test_GRF_I06_subtree_root_resolution_limits_and_refusals(tmp_path: Path) -> None:
    root, bid = _grf(tmp_path)
    cl = "grf_soc.g_cl[0].u_cl"
    code, env = run(root, "graph", "export", "--build", bid, "--instance", cl, "--name", "by-path")
    iid = next(n["id"] for n in _page_data(root / env["data"]["path"])["nodes"] if n["path"] == cl)
    code, env = run(root, "graph", "export", "--build", bid, "--instance", iid, "--name", "by-id")
    assert code == 0 and env["data"]["root"] == cl  # an instance id names the same subtree
    code, env = run(root, "graph", "export", "--build", bid, "--instance", "grf_soc", "--name", "top")
    top = _page_data(root / env["data"]["path"])
    assert len(top["nodes"]) == 26 and top["boundary"] == []  # the top's ports are its own signals
    code, env = run(root, "graph", "export", "--build", bid, "--instance", cl, "--max-instances", "3",
                    "--name", "cut")
    assert code == 0 and env["data"]["truncated"] is True
    cut = _page_data(root / env["data"]["path"])
    assert [n["path"] for n in cut["nodes"]][0] == cl and [n["depth"] for n in cut["nodes"]] == [0, 1, 1]
    code, e = run(root, "graph", "export", "--build", bid, "--instance", "grf_soc.g_cl[9].u_cl")
    assert code == 3 and e["error"]["code"] == "MISSING_INPUT"
    for bad in ("", "  ", "grf_soc\nx"):
        code, e = run(root, "graph", "export", "--build", bid, "--instance", bad)
        assert code == 2 and e["error"]["code"] == "INVALID_CONFIG", repr(bad)
    pages = list((root / ".svweave" / "exports").iterdir())
    assert pages and all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in pages)


# ------------------------------------------------------------------ diff view (--diff-base)
# git diff -> changed module/interface definitions -> their instances with subtrees, plus the
# ancestors that connect them (context). Expectations derived from the grf_subtree RTL.
def _git_grf(tmp_path: Path, cores: int = 2) -> Path:
    from tests.integration.helpers import lang_ws
    from tests.integration.test_diff_impact import git

    w = lang_ws(tmp_path, "grf_subtree", "grf_soc", top_parameters=[
        {"top": "grf_soc", "name": "CLUSTERS", "value": "2"},
        {"top": "grf_soc", "name": "CORES", "value": str(cores)},
    ])
    (w.root / ".gitignore").write_text(".svweave/\n")
    git(w.root, "init", "-q", "-b", "main")
    git(w.root, "add", "-A")
    git(w.root, "commit", "-q", "-m", "base")
    return w.root


def _build(root: Path) -> str:
    code, b = run(root, "build", "--profile", "p")
    assert code == 0 and b["outcome"] == "OK", b
    return str(b["build_id"])


def _edit(root: Path, file: str, old: str, new: str) -> None:
    p = root / file
    text = p.read_text()
    assert text.count(old) == 1
    p.write_text(text.replace(old, new))


def _marks(data: dict[str, Any]) -> dict[str, str]:
    return {n["path"]: n["diff"] for n in data["nodes"]}


@pytest.mark.usefixtures("isolated_git")
def test_GRF_I07_diff_after_the_build_draws_changed_instances_and_their_ancestors(
        tmp_path: Path) -> None:
    root = _git_grf(tmp_path)
    bid = _build(root)  # build of the base, then the change: old side is exact
    _edit(root, "grf_reduce.sv", "acc ^= din[i*W+:W];", "acc += din[i*W+:W];")
    code, env = run(root, "graph", "export", "--build", bid, "--diff-base", "HEAD")
    assert code == 0 and env["outcome"] == "OK" and env["freshness"] == "STALE"  # STALE expected
    assert env["data"]["path"].startswith(f".svweave/exports/graph-{bid[:12]}-diff-")
    assert any("diff view" in x for x in env["analysis_coverage"]["limitations"])
    d = env["data"]["diff"]
    assert [(x["qualified_name"], x["instances"], x["instances_drawn"])
            for x in d["changed_definitions"]] == [("grf_reduce", 3, 3)]
    # the envelope carries what the README promises: files with the side used
    assert d["files"] == [{"path": "grf_reduce.sv", "side": "old", "aligned": True}]
    assert env["data"]["written"] is True and env["pagination"]["truncated"] is False
    data = _page_data(root / env["data"]["path"])
    assert data["meta"]["diff"] == "HEAD -> working tree" and data["root"] == "grf_soc"
    assert data["diff"]["files"] == [{"path": "grf_reduce.sv", "side": "old", "aligned": True}]
    cl0, cl1 = "grf_soc.g_cl[0].u_cl", "grf_soc.g_cl[1].u_cl"
    assert _marks(data) == {"grf_soc": "context", cl0: "context", cl1: "context",
                            f"{cl0}.u_red": "changed", f"{cl1}.u_red": "changed",
                            "grf_soc.u_red": "changed"}  # no core, no stage was read
    by = {n["path"]: n for n in data["nodes"]}
    assert by[cl0]["children_total"] == 3 and data["stats"]["changed_instances"] == 3
    e = _edges(data)
    assert (cl0, f"{cl0}.u_red", f"{cl0}.core_bus", "din") in e  # context -> changed reducer
    assert (f"{cl0}.u_red", cl0, f"{cl0}.cout", "dout") in e
    assert ("grf_soc", "grf_soc.u_red", "grf_soc.cluster_bus", "din") in e


@pytest.mark.usefixtures("isolated_git")
def test_GRF_I08_build_after_the_change_maps_the_new_side_and_matches_the_full_page(
        tmp_path: Path) -> None:
    root = _git_grf(tmp_path)
    _edit(root, "grf_core.sv", "grf_stage #(.W(W), .K(3)) u_exec", "grf_stage #(.W(W), .K(5)) u_exec")
    bid = _build(root)  # build of the CHANGED tree: the new side is exact
    code, env = run(root, "graph", "export", "--build", bid, "--diff-base", "HEAD",
                    "--max-fanout", "1000")
    assert code == 0 and env["freshness"] == "CURRENT", env.get("error")
    data = _page_data(root / env["data"]["path"])
    assert data["diff"]["files"] == [{"path": "grf_core.sv", "side": "new", "aligned": True}]
    assert [x["qualified_name"] for x in data["diff"]["changed_definitions"]] == ["grf_core"]
    marks = _marks(data)
    cores = sorted(p for p, m in marks.items() if m == "changed")
    assert cores == [f"grf_soc.g_cl[{c}].u_cl.g_core[{i}].u_core" for c in (0, 1) for i in (0, 1)]
    assert sorted(p for p, m in marks.items() if m == "context") == [
        "grf_soc", "grf_soc.g_cl[0].u_cl", "grf_soc.g_cl[1].u_cl"]
    assert sum(1 for m in marks.values() if m == "inside") == 16  # 4 stages per changed core
    assert not any(p.endswith("u_red") for p in marks)
    # inside each changed core the page equals the full export restricted to it
    code, env = run(root, "graph", "export", "--build", bid, "--max-fanout", "1000",
                    "--name", "full")
    full = _page_data(root / env["data"]["path"])
    fby = {n["path"]: n for n in full["nodes"]}
    for c in cores:
        pre = c + "."
        mine = {x for x in _edges(data) if x[2].startswith(pre)}
        theirs = {x for x in _edges(full) if x[2].startswith(pre)}
        assert mine == theirs and len(mine) == 4 * 2 + 3 * 3 + 2, c
        for n in data["nodes"]:
            if n["path"].startswith(pre):
                assert n["ports"] == fby[n["path"]]["ports"]


@pytest.mark.usefixtures("isolated_git")
def test_GRF_I09_root_limits_the_diff_and_commit_to_commit_diffs(tmp_path: Path) -> None:
    from tests.integration.test_diff_impact import git

    root = _git_grf(tmp_path)
    bid = _build(root)
    _edit(root, "grf_core.sv", "grf_stage #(.W(W), .K(3)) u_exec", "grf_stage #(.W(W), .K(5)) u_exec")
    cl1 = "grf_soc.g_cl[1].u_cl"
    code, env = run(root, "graph", "export", "--build", bid, "--diff-base", "HEAD", "--instance", cl1)
    assert code == 0 and env["data"]["diff"]["outside_root"] == 2
    assert any("outside the instance" in w for w in env["warnings"])
    data = _page_data(root / env["data"]["path"])
    assert data["root"] == cl1 and [n["path"] for n in data["nodes"]][0] == cl1
    assert sorted(p for p, m in _marks(data).items() if m == "changed") == [
        f"{cl1}.g_core[0].u_core", f"{cl1}.g_core[1].u_core"]
    git(root, "commit", "-q", "-am", "change")  # commit to commit: blobs only, old side exact
    code, env = run(root, "graph", "export", "--build", bid, "--diff-base", "HEAD~1",
                    "--diff-head", "HEAD", "--name", "c2c")
    assert code == 0, env.get("error")
    data = _page_data(root / env["data"]["path"])
    assert data["meta"]["diff"] == "HEAD~1 -> HEAD"
    assert data["diff"]["files"] == [{"path": "grf_core.sv", "side": "old", "aligned": True}]
    assert sum(1 for m in _marks(data).values() if m == "changed") == 4


@pytest.mark.usefixtures("isolated_git")
def test_GRF_I10_nothing_to_draw_is_an_answer_and_bad_input_is_refused(tmp_path: Path) -> None:
    root = _git_grf(tmp_path)
    bid = _build(root)
    exports = root / ".svweave" / "exports"

    def nothing(*extra: str) -> dict[str, Any]:
        code, env = run(root, "graph", "export", "--build", bid, "--diff-base", "HEAD", *extra)
        # like `query diff-impact` on an empty diff: OK, the build is not BLOCKED, no page
        assert code == 0 and env["outcome"] == "OK", env.get("error")
        assert env["analysis_coverage"]["completeness"] == "COMPLETE"
        assert env["data"]["path"] is None and env["data"]["written"] is False
        assert any(w.startswith("nothing to draw") for w in env["warnings"])
        d: dict[str, Any] = env["data"]["diff"]
        return d

    assert "no changed line" in nothing()["nothing_to_draw"]  # clean tree
    (root / "notes.txt").write_text("not RTL\n")
    from tests.integration.test_diff_impact import git

    git(root, "add", "notes.txt")
    d = nothing()
    assert d["unmapped_files"] == ["notes.txt"] and d["summary"]["files_not_in_build"] == 1
    _edit(root, "grf_reduce.sv", "acc ^= din[i*W+:W];", "acc += din[i*W+:W];")
    core = "grf_soc.g_cl[0].u_cl.g_core[0].u_core"  # a reducer change does not reach a core
    d = nothing("--instance", core)
    assert "under the instance" in d["nothing_to_draw"] and d["outside_root"] == 3
    assert not exports.exists() or not list(exports.glob("*-diff-*"))
    for bad in (["--diff-head", "HEAD"], ["--diff-base", "-x"], ["--diff-base", "a..b"],
                ["--diff-base", "HEAD", "--diff-head", "--x"]):
        code, e = run(root, "graph", "export", "--build", bid, *bad)
        assert code == 2 and e["error"]["code"] == "INVALID_CONFIG", bad
    code, e = run(root, "graph", "export", "--build", bid, "--diff-base", "nosuchref")
    assert code == 3 and e["error"]["code"] == "MISSING_INPUT"
    assert e["error"]["details"]["reason"] == "git_failed"


@pytest.mark.usefixtures("isolated_git")
def test_GRF_I11_truncated_diff_view_reports_what_is_really_drawn(tmp_path: Path) -> None:
    root = _git_grf(tmp_path)
    bid = _build(root)
    _edit(root, "grf_reduce.sv", "acc ^= din[i*W+:W];", "acc += din[i*W+:W];")
    code, env = run(root, "graph", "export", "--build", bid, "--diff-base", "HEAD",
                    "--max-instances", "3")
    assert code == 0 and env["data"]["truncated"] is True and env["pagination"]["truncated"]
    assert env["pagination"]["limit"] == 3
    data = _page_data(root / env["data"]["path"])
    assert [n["diff"] for n in data["nodes"]] == ["context"] * 3  # soc and both clusters
    x = env["data"]["diff"]["changed_definitions"][0]
    assert (x["instances"], x["instances_drawn"]) == (3, 0)  # not "3 drawn"
    assert data["stats"]["changed_instances"] == 0


# ------------------------------------------------------------------ --per-commit labels
def _commit_series(root: Path) -> None:
    """Four small commits on top of the base: grf_reduce twice (once a deletion only), grf_core
    once, and one commit that changes no RTL."""
    from tests.integration.test_diff_impact import git

    _edit(root, "grf_reduce.sv", "acc ^= din[i*W+:W];", "acc += din[i*W+:W];")
    git(root, "commit", "-q", "-am", "C1 reduce: add instead of xor")
    _edit(root, "grf_core.sv", "grf_stage #(.W(W), .K(3)) u_exec", "grf_stage #(.W(W), .K(5)) u_exec")
    git(root, "commit", "-q", "-am", "C2 core: exec stage K=5")
    _edit(root, "grf_reduce.sv", "    else dout <= acc;\n", "")
    git(root, "commit", "-q", "-am", "C3 reduce: delete the else line")
    (root / "notes.txt").write_text("not RTL\n")
    git(root, "add", "notes.txt")
    git(root, "commit", "-q", "-m", "C4 notes only")


@pytest.mark.usefixtures("isolated_git")
def test_GRF_I12_per_commit_labels_each_changed_instance_with_its_commits(tmp_path: Path) -> None:
    root = _git_grf(tmp_path)
    _commit_series(root)
    bid = _build(root)  # build of the head revision: the new side is exact
    code, env = run(root, "graph", "export", "--build", bid, "--diff-base", "HEAD~4",
                    "--diff-head", "HEAD", "--per-commit")
    assert code == 0, env.get("error")
    d = env["data"]["diff"]
    assert [c["subject"] for c in d["commits"]] == [
        "C1 reduce: add instead of xor", "C2 core: exec stage K=5",
        "C3 reduce: delete the else line", "C4 notes only"]
    assert [c["changes_drawn_definitions"] for c in d["commits"]] == [True, True, True, False]
    by = {x["qualified_name"]: x["commits"] for x in d["changed_definitions"]}
    assert by == {"grf_reduce": [0, 2], "grf_core": [1]}  # C3 is a pure deletion, still seen
    assert any("change no drawn module" in w for w in env["warnings"])
    data = _page_data(root / env["data"]["path"])
    for n in data["nodes"]:
        if n["diff"] == "changed":
            want = [0, 2] if n["definition"] == "grf_reduce" else [1]
            assert n["commits"] == want, n["path"]
        else:
            assert "commits" not in n
    assert sum(1 for n in data["nodes"] if n["diff"] == "changed") == 3 + 4  # 3 reducers, 4 cores


@pytest.mark.usefixtures("isolated_git")
def test_GRF_I12b_per_commit_is_not_guessed_without_a_head_build(tmp_path: Path) -> None:
    root = _git_grf(tmp_path)
    bid = _build(root)  # build of the BASE revision
    _commit_series(root)
    code, env = run(root, "graph", "export", "--build", bid, "--diff-base", "HEAD~4",
                    "--diff-head", "HEAD", "--per-commit")
    assert code == 0
    assert all(x["commits"] is None for x in env["data"]["diff"]["changed_definitions"])
    assert any("carry no commit labels" in w for w in env["warnings"])
    for bad in (["--diff-base", "HEAD", "--per-commit"], ["--per-commit"]):
        code, e = run(root, "graph", "export", "--build", bid, *bad)
        assert code == 2 and e["error"]["code"] == "INVALID_CONFIG", bad


@pytest.mark.usefixtures("isolated_git")
def test_GRF_I13_an_instance_between_two_changed_ones_is_inside_not_context(
        tmp_path: Path) -> None:
    """A changed module nested in another changed module (a baud generator inside a changed
    sub-system): the instances between them belong to the outer change, they are not context."""
    from tests.integration.test_diff_impact import git

    root = _git_grf(tmp_path)
    _edit(root, "grf_cluster.sv", "logic [CORES*W-1:0] core_bus;",
          "logic [CORES*W-1:0] core_bus;  // cluster change")
    _edit(root, "grf_stage.sv", "else q <= d + W'(K);", "else q <= d + W'(K);  // stage change")
    git(root, "commit", "-q", "-am", "cluster and stage")
    bid = _build(root)
    code, env = run(root, "graph", "export", "--build", bid, "--diff-base", "HEAD~1",
                    "--diff-head", "HEAD", "--per-commit", "--instance", "grf_soc.g_cl[0].u_cl")
    assert code == 0, env.get("error")
    marks = _marks(_page_data(root / env["data"]["path"]))
    cl = "grf_soc.g_cl[0].u_cl"
    assert marks[cl] == "changed"
    assert all(marks[f"{cl}.g_core[{i}].u_core"] == "inside" for i in (0, 1))  # was "context"
    assert all(m == "changed" for p, m in marks.items() if p.endswith(("u_fetch", "u_wb")))
    assert "context" not in marks.values()


def test_GRF_I14_the_default_page_is_the_complete_topology(tmp_path: Path) -> None:
    """No cap by default: every instance and every signal is drawn (a cluster clock used by 21
    instances was a hub, not drawn, under the former default --max-fanout 16). Caps are
    explicit reductions."""
    root, bid = _grf(tmp_path, clusters=2, cores=20)
    total = 1 + 2 * (2 + 5 * 20) + 1
    code, env = run(root, "graph", "export", "--build", bid)
    assert code == 0
    d = env["data"]
    assert d["truncated"] is False and d["stats"]["instances_shown"] == total == 206
    assert d["stats"]["max_instances"] is None and d["stats"]["max_fanout"] is None
    assert d["stats"]["hub_signals"] == 0 and env["pagination"] == {
        "limit": total, "next_cursor": None, "truncated": False}
    assert not any("hubs" in w for w in env["warnings"])
    page = _page_data(root / d["path"])
    assert page["hubs"] == []
    cl = "grf_soc.g_cl[0].u_cl"
    clk = {to for frm, to, sig, _ in _edges(page) if sig == f"{cl}.clk" and frm == cl}
    assert clk == {f"{cl}.g_core[{i}].u_core" for i in range(20)} | {f"{cl}.u_red"}
    code, env = run(root, "graph", "export", "--build", bid, "--max-fanout", "16", "--name", "r")
    assert code == 0 and f"{cl}.clk" in {h["signal"] for h in _page_data(
        root / env["data"]["path"])["hubs"]}  # the reduction still works when asked for


# ------------------------------------------------------------------ --split atlas
def test_GRF_I15_the_atlas_pages_together_are_exactly_the_complete_topology(
        tmp_path: Path) -> None:
    root, bid = _grf(tmp_path, clusters=4, cores=30)  # 1 + 4 * (2 + 150) + 1 = 610 instances
    code, env = run(root, "graph", "export", "--build", bid, "--name", "whole")
    whole = _page_data(root / env["data"]["path"])
    code, env = run(root, "graph", "export", "--build", bid, "--split", "--page-budget", "200",
                    "--name", "atlas")
    assert code == 0, env.get("error")
    a = env["data"]["atlas"]
    assert env["data"]["path"] == ".svweave/exports/atlas/index.html"
    # the index takes cluster 0 whole (1 + 4 portal-or-cluster nodes + reducer + 151 = 157);
    # clusters 1..3 (152 each) no longer fit and become pages of their own
    assert a["pages"] == 4 and a["budget"] == 200 and a["oversized_pages"] == 0
    assert a["largest_page"] <= 200
    d = root / env["data"]["directory"]
    pages = {f: _page_data(d / f) for f in a["files"]}
    index = pages["index.html"]
    portals = [n for n in index["nodes"] if "portal" in n]
    assert sorted(n["path"] for n in portals) == [f"grf_soc.g_cl[{c}].u_cl" for c in (1, 2, 3)]
    assert len(index["nodes"]) == 157
    for p in portals:  # every portal opens an existing page whose root it is
        sub = pages[p["portal"]["href"]]
        assert sub["meta"]["root"] == p["path"] and p["portal"]["instances"] == 152
        assert sub["meta"]["atlas"]["crumbs"][0] == ["index", "index.html"]
        assert [n["path"] for n in sub["nodes"] if n["parent"] is None] == [p["path"]]
    # nodes: every instance on exactly one page, a portal counted on its own page
    seen: dict[str, dict[str, Any]] = {}
    for pg in pages.values():
        for n in pg["nodes"]:
            if "portal" in n:
                continue
            assert n["path"] not in seen, n["path"]
            seen[n["path"]] = n
    wnodes = {n["path"]: n for n in whole["nodes"]}
    assert set(seen) == set(wnodes) and len(seen) == 610
    for path, n in seen.items():
        assert n["ports"] == wnodes[path]["ports"] and n["params"] == wnodes[path]["params"]
    # edges: the union over all pages is the complete page's edge set, nothing twice
    union: list[tuple[str, str, str, str]] = []
    for pg in pages.values():
        union += sorted(_edges(pg))
    assert len(union) == len(set(union)) and set(union) == _edges(whole)
    # the index page's search covers every instance and points at the page that shows it
    srch = index["atlas_search"]
    paths: list[str] = []
    for i in range(len(srch["p"])):
        rel = srch["s"][srch["r"][i]]
        paths.append(paths[srch["p"][i]] + "." + rel if srch["p"][i] >= 0 else rel)
    assert sorted(paths) == sorted(wnodes)
    for i, path in enumerate(paths):
        on = pages[srch["pages"][srch["pg"][i]]]
        assert path in {n["path"] for n in on["nodes"] if "portal" not in n}


def test_GRF_I16_atlas_refusals_and_a_subtree_atlas(tmp_path: Path) -> None:
    root, bid = _grf(tmp_path, clusters=4, cores=30)
    for bad in (["--split", "--diff-base", "HEAD"], ["--page-budget", "500"],
                ["--split", "--page-budget", "10"], ["--split", "--name", "../x"]):
        code, e = run(root, "graph", "export", "--build", bid, *bad)
        assert code == 2 and e["error"]["code"] == "INVALID_CONFIG", bad
    code, env = run(root, "graph", "export", "--build", bid, "--split", "--instance",
                    "grf_soc.g_cl[1].u_cl", "--page-budget", "100", "--name", "sub")
    assert code == 0, env.get("error")
    # 152 instances: the 30 cores (5 each) are below MIN_PORTAL, so one oversized page, said so
    assert env["data"]["atlas"]["pages"] == 1 and env["data"]["atlas"]["oversized_pages"] == 1
    assert any("never split off" in w for w in env["warnings"])
    index = _page_data(root / env["data"]["path"])
    assert [n["path"] for n in index["nodes"]][0] == "grf_soc.g_cl[1].u_cl"
    assert len(index["nodes"]) == 152


def _chunk_data(path: Path) -> dict[str, Any]:
    from svweave.application.graph_codec import decode

    text = path.read_text()
    assert text.startswith("svwChunk(") and text.endswith(");\n")
    data: dict[str, Any] = decode(json.loads(text[len("svwChunk("):-3]))
    return data


def test_GRF_I17_lazy_one_page_plus_parts_is_the_complete_topology(tmp_path: Path) -> None:
    root, bid = _grf(tmp_path, clusters=4, cores=30)
    code, env = run(root, "graph", "export", "--build", bid, "--name", "whole")
    whole = _page_data(root / env["data"]["path"])
    code, env = run(root, "graph", "export", "--build", bid, "--lazy", "--page-budget", "200",
                    "--name", "lazy")
    assert code == 0, env.get("error")
    lz = env["data"]["lazy"]
    assert lz["pages"] == 4 and lz["oversized_pages"] == 0
    d = root / env["data"]["directory"]
    assert sorted(lz["files"])[-1] == "index.html" and all(
        f == "index.html" or (f.startswith("c-") and f.endswith(".js")) for f in lz["files"])
    html = (d / "index.html").read_text()
    assert "script-src 'self' 'unsafe-inline'" in html  # only a --lazy page admits 'self'
    for other in (root / ".svweave/exports/whole.html",):
        assert "script-src 'unsafe-inline';" in other.read_text() and "'self'" not in \
            other.read_text().split("</head>")[0]
    index = _page_data(d / "index.html")
    assert index["meta"]["lazy"] == {"parts": 4, "budget": 200, "instances_total": 610}
    parts = {f: _chunk_data(d / f) for f in lz["files"] if f != "index.html"}
    portals = [n for n in index["nodes"] if "portal" in n]
    assert all(set(p["portal"]) == {"chunk", "instances"} for p in portals)
    for p in portals:
        part = parts[p["portal"]["chunk"]]
        assert part["meta"] == {"chunk": p["portal"]["chunk"], "root": p["path"]}
        assert part["nodes"][0]["path"] == p["path"]
    # same disjoint cut as --split: the union is the complete topology
    seen: set[str] = set()
    union: list[tuple[str, str, str, str]] = []
    for pg in [index, *parts.values()]:
        seen |= {n["path"] for n in pg["nodes"] if "portal" not in n}
        union += sorted(_edges(pg))
    assert seen == {n["path"] for n in whole["nodes"]}
    assert len(union) == len(set(union)) and set(union) == _edges(whole)
    assert index["atlas_search"]["parent"] == [-1, 0, 0, 0]
    code, e = run(root, "graph", "export", "--build", bid, "--lazy", "--split")
    assert code == 2 and e["error"]["code"] == "INVALID_CONFIG"
