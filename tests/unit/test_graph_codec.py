"""GRF-C: the compact page format (svweave-graph/2) is lossless, and the viewer's JavaScript
decoder is the twin of the Python one."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from svweave.application.graph_codec import FORMAT, decode, encode
from svweave.application.graph_template import _PAGE

HOSTILE = '</script><script>alert(1)</script> &   "q"'


def _node(i: str, parent: str | None, path: str, **kw: Any) -> dict[str, Any]:
    n = {"id": i * 64, "parent": parent and parent * 64, "path": path, "name": path.rsplit(".", 1)[-1],
         "definition": kw.pop("definition", "m"), "depth": kw.pop("depth", 0), "top": False,
         "interface": False, "stub": False, "blackbox": False, "generate": [], "params": [],
         "file": "rtl/m.sv", "line": 3, "ports": []}
    n.update(kw)
    return n


def sample() -> dict[str, Any]:
    a = _node("a", None, "top", top=True, params=[
        {"name": "W", "value": "8", "overridden": True}, {"name": "N", "value": 2, "overridden": False},
        {"name": "Z", "value": None, "overridden": False}])
    b = _node("b", "a", "top.g[0].u", depth=1, generate=["g[0]"], definition=None, file=None,
              line=None, diff="changed", commits=[0, 2], stub=True,
              ports=[{"port": "d", "dir": "in", "actual": "x[3:0]", "kind": "expr",
                      "signals": ["top.x", "elsewhere.sig"], "supported": True},
                     {"port": HOSTILE, "dir": None, "actual": None, "kind": None, "signals": [],
                      "supported": False}])
    c = _node("c", "a", "\\esc.aped ", depth=1, name=HOSTILE, diff="context",
              children_total=7)  # a path that does not extend its parent's
    d = _node("d", None, "other_top", blackbox=True, interface=True,
              portal={"href": "p-0123456789.html", "instances": 42})
    return {
        "meta": {"build_id": "f" * 64, "root": HOSTILE},
        "nodes": [a, b, c, d],
        "edges": [
            {"from": "a" * 64, "to": "b" * 64, "signal": "top.x", "port": "d", "directed": True},
            {"from": "b" * 64, "to": "c" * 64, "signal": "elsewhere.sig", "port": "q -> d",
             "directed": True},
            {"from": "a" * 64, "to": "b" * 64, "signal": "top.y", "port": HOSTILE,
             "directed": False},
        ],
        "hubs": [{"signal": "top.clk", "instances": 99}], "boundary": [],
        "stats": {"instances_total": 4}, "truncated": False, "note": "n",
        "diff": {"commits": [{"index": 0, "sha": "abc", "subject": HOSTILE}]},
    }


def _edge_key(e: dict[str, Any]) -> tuple[Any, ...]:
    return (e["from"], e["to"], e["signal"], e["port"], e["directed"])


def test_GRF_C01_decode_of_encode_is_the_same_page() -> None:
    page = sample()
    enc = encode(page)
    assert enc["format"] == FORMAT and "nodes" not in enc and "edges" not in enc
    back = decode(json.loads(json.dumps(enc)))  # through JSON, like the page
    assert back["nodes"] == page["nodes"]
    assert sorted(map(_edge_key, back["edges"])) == sorted(map(_edge_key, page["edges"]))
    assert {k: v for k, v in back.items() if k not in ("nodes", "edges")} == \
        {k: v for k, v in page.items() if k not in ("nodes", "edges")}
    assert enc["n"]["full"] == [0, 2, 3]  # roots and the path that does not extend its parent


def test_GRF_C02_encode_refuses_what_it_cannot_represent() -> None:
    page = sample()
    page["nodes"][1]["unknown"] = 1
    with pytest.raises(ValueError, match="unknown keys"):
        encode(page)
    page = sample()
    page["nodes"] = [page["nodes"][1], page["nodes"][0]]  # child before its parent
    with pytest.raises(ValueError):
        encode(page)
    with pytest.raises(ValueError, match="not a svweave-graph/2"):
        decode({"nodes": []})


def test_GRF_C03_the_javascript_decoder_matches_the_python_decoder(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run the viewer's decoder")
    script = _PAGE.split("// codec-begin")[1].split("// codec-end")[0]
    enc = encode(sample())
    runner = tmp_path / "run.js"
    runner.write_text(
        "// codec-begin" + script + "\n"
        "var R = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));\n"
        "process.stdout.write(JSON.stringify(decodePage(R)));\n")
    (tmp_path / "page.json").write_text(json.dumps(enc))
    r = subprocess.run([node, str(runner), str(tmp_path / "page.json")], capture_output=True,
                       text=True, check=False)
    assert r.returncode == 0, r.stderr[-800:]
    js = json.loads(r.stdout)
    py = decode(enc)
    assert js["nodes"] == py["nodes"]  # ports included: the lazy getter is enumerable
    assert js["edges"] == py["edges"]
    assert {k: v for k, v in js.items() if k not in ("nodes", "edges")} == \
        {k: v for k, v in py.items() if k not in ("nodes", "edges")}


def test_GRF_C04_the_viewer_knows_every_optional_node_key_and_the_sample_uses_them_all() -> None:
    """Council review 2: a key added to graph_codec._OPTIONAL but not to the viewer would be
    dropped silently in the browser (GRF-C03 only covers keys its sample happens to use)."""
    import re

    from svweave.application.graph_codec import _OPTIONAL

    js = re.search(r"var OPTIONAL = \[(.*?)\];", _PAGE)
    assert js and [x.strip().strip('"') for x in js.group(1).split(",")] == list(_OPTIONAL)
    used = {k for n in sample()["nodes"] for k in n}
    assert set(_OPTIONAL) <= used


def test_GRF_C05_the_viewer_accepts_exactly_the_file_names_the_atlas_writes(tmp_path: Path) -> None:
    """The names graph_atlas writes and the viewer's link/script guards are one contract."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run the viewer's guards")
    from svweave.application.graph_atlas import chunk_file, page_file

    body = _PAGE.split("<script>\n", 1)[1]
    guards = "".join(
        body[body.index(f"function {name}("):].split("\n}\n", 1)[0] + "\n}\n"
        for name in ("safeHref", "safeChunk"))
    good = {"href": ["index.html", page_file("a.b"), page_file("x[3].y")],
            "chunk": [chunk_file("a.b"), chunk_file("x[3].y")]}
    bad = ["../p-0123456789.html", "p-0123456789.html?x", "p-012345678G.html",
           "javascript:alert(1)", "/etc/passwd", "p-0123456789.htm", "c-0123456789.jsx",
           "C-0123456789.js", "c-0123456789.js/", "", None, 17]
    runner = tmp_path / "g.js"
    runner.write_text(guards + "var t = JSON.parse(process.argv[2]);\n"
                      "process.stdout.write(JSON.stringify({"
                      "href: t.href.map(safeHref), chunk: t.chunk.map(safeChunk),"
                      "badHref: t.bad.map(safeHref), badChunk: t.bad.map(safeChunk)}));\n")
    r = subprocess.run([node, str(runner), json.dumps({**good, "bad": bad})], capture_output=True,
                       text=True, check=False)
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout)
    assert all(out["href"]) and all(out["chunk"])
    assert not any(out["badHref"]) and not any(out["badChunk"])
