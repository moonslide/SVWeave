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
    data: dict[str, Any] = json.loads(m.group(1))
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


# ------------------------------------------------------------------ subtree view (--root)
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
    code, env = run(root, "graph", "export", "--build", bid, "--root", cl)
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
    code, env = run(root, "graph", "export", "--build", bid, "--root", core0, "--name", "core")
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
            code, env = run(root, "graph", "export", "--build", bid, "--root", r["path"],
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
    code, env = run(root, "graph", "export", "--build", bid, "--root", cl, "--name", "by-path")
    iid = next(n["id"] for n in _page_data(root / env["data"]["path"])["nodes"] if n["path"] == cl)
    code, env = run(root, "graph", "export", "--build", bid, "--root", iid, "--name", "by-id")
    assert code == 0 and env["data"]["root"] == cl  # an instance id names the same subtree
    code, env = run(root, "graph", "export", "--build", bid, "--root", "grf_soc", "--name", "top")
    top = _page_data(root / env["data"]["path"])
    assert len(top["nodes"]) == 26 and top["boundary"] == []  # the top's ports are its own signals
    code, env = run(root, "graph", "export", "--build", bid, "--root", cl, "--max-instances", "3",
                    "--name", "cut")
    assert code == 0 and env["data"]["truncated"] is True
    cut = _page_data(root / env["data"]["path"])
    assert [n["path"] for n in cut["nodes"]][0] == cl and [n["depth"] for n in cut["nodes"]] == [0, 1, 1]
    code, e = run(root, "graph", "export", "--build", bid, "--root", "grf_soc.g_cl[9].u_cl")
    assert code == 3 and e["error"]["code"] == "MISSING_INPUT"
    for bad in ("", "  ", "grf_soc\nx"):
        code, e = run(root, "graph", "export", "--build", bid, "--root", bad)
        assert code == 2 and e["error"]["code"] == "INVALID_CONFIG", repr(bad)
    pages = list((root / ".svweave" / "exports").iterdir())
    assert pages and all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in pages)
