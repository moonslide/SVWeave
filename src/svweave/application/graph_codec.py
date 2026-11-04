"""Compact, lossless encoding of a graph page (``svweave-graph/2``).

The graph model of ``graph.build_model`` repeats a lot: every edge carries two 64-hex instance ids
and a full hierarchical signal path, every node its full path, definitions, port names and
parameter values are written again and again. On the C906 SoC 56% of a page were edges, 44% of
those the ids. This codec writes the same information once:

* nodes are columns indexed 0..N-1 (pre-order: a parent always precedes its children); a path is
  stored relative to its parent's path when it extends it, else in full;
* every other scalar (definition, local name, port, direction, actual expression, parameter
  name/value, file, ...) is interned in one table ``s`` and referenced by index;
* a signal is ``[owner node, local name]`` when its owner instance is on the page, else its full
  path;
* edges are grouped per (from, to) pair.

``decode`` returns exactly the page dict ``encode`` was given (edges grouped per pair, i.e. in a
different order; their multiset is unchanged). The viewer decodes the same format in JavaScript
(``graph_template.py``); tests check both decoders against each other.
"""

from __future__ import annotations

import json
from typing import Any

FORMAT = "svweave-graph/2"
_FLAGS = (("top", 1), ("interface", 2), ("stub", 4), ("blackbox", 8))
_NODE_KEYS = ("id", "parent", "path", "name", "definition", "depth", "top", "interface",
              "stub", "blackbox", "generate", "params", "file", "line", "ports")
_OPTIONAL = ("diff", "commits", "children_total")


class _Table:
    def __init__(self) -> None:
        self.values: list[Any] = []
        self._index: dict[str, int] = {}

    def __call__(self, v: Any) -> int:
        key = json.dumps(v, sort_keys=True)  # 1 and "1" and True stay distinct
        i = self._index.get(key)
        if i is None:
            i = self._index[key] = len(self.values)
            self.values.append(v)
        return i


def _owner(path: str, index_of: dict[str, int]) -> tuple[int, str] | None:
    """The page node whose path is the longest proper prefix of ``path`` (a signal's scope)."""
    end = len(path)
    while True:
        end = path.rfind(".", 0, end)
        if end <= 0:
            return None
        i = index_of.get(path[:end])
        if i is not None:
            return i, path[end + 1 :]


def encode(page: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = page["nodes"]
    s = _Table()
    index_of = {n["id"]: i for i, n in enumerate(nodes)}
    path_index = {n["path"]: i for i, n in enumerate(nodes)}
    sig_table: list[list[Any]] = []
    sig_index: dict[str, int] = {}

    def sig(path: str) -> int:
        i = sig_index.get(path)
        if i is None:
            o = _owner(path, path_index)
            i = sig_index[path] = len(sig_table)
            sig_table.append([o[0], s(o[1])] if o else [-1, s(path)])
        return i

    cols: dict[str, Any] = {k: [] for k in ("id", "p", "r", "nm", "d", "dp", "f", "fl", "ln")}
    sparse: dict[str, dict[str, Any]] = {k: {} for k in ("g", "pa", "po", *_OPTIONAL)}
    full: list[int] = []
    for i, n in enumerate(nodes):
        extra = set(n) - set(_NODE_KEYS) - set(_OPTIONAL)
        if extra:
            raise ValueError(f"graph node has unknown keys {sorted(extra)}")
        p = index_of.get(n["parent"], -1) if n["parent"] is not None else -1
        if n["parent"] is not None and p < 0:
            raise ValueError("graph node parent is not on the page")
        if p >= i:
            raise ValueError("graph nodes are not in pre-order")
        rel = n["path"]
        if p >= 0 and rel.startswith(nodes[p]["path"] + "."):
            rel = rel[len(nodes[p]["path"]) + 1 :]
        else:
            full.append(i)
        cols["id"].append(n["id"])
        cols["p"].append(p)
        cols["r"].append(s(rel))
        cols["nm"].append(s(n["name"]))
        cols["d"].append(s(n["definition"]))
        cols["dp"].append(n["depth"])
        cols["f"].append(sum(bit for k, bit in _FLAGS if n[k]))
        cols["fl"].append(s(n["file"]))
        cols["ln"].append(n["line"])
        k = str(i)
        if n["generate"]:
            sparse["g"][k] = [s(x) for x in n["generate"]]
        if n["params"]:
            sparse["pa"][k] = [[s(x["name"]), s(x["value"]), 1 if x["overridden"] else 0]
                               for x in n["params"]]
        if n["ports"]:
            sparse["po"][k] = [[s(x["port"]), s(x["dir"]), s(x["actual"]), s(x["kind"]),
                                [sig(y) for y in x["signals"]], 1 if x["supported"] else 0]
                               for x in n["ports"]]
        for opt in _OPTIONAL:
            if opt in n:
                sparse[opt][k] = n[opt]
    groups: dict[tuple[int, int], list[list[Any]]] = {}
    for e in page["edges"]:
        if set(e) != {"from", "to", "signal", "port", "directed"}:
            raise ValueError(f"graph edge has keys {sorted(e)}")
        groups.setdefault((index_of[e["from"]], index_of[e["to"]]), []).append(
            [sig(e["signal"]), s(e["port"]), 1 if e["directed"] else 0])
    out = {k: v for k, v in page.items() if k not in ("nodes", "edges")}
    out.update(
        format=FORMAT,
        s=s.values,
        n={**cols, "full": full, **{k: v for k, v in sparse.items() if v}},
        sg=sig_table,
        e=[[a, b, items] for (a, b), items in groups.items()],
    )
    return out


def decode(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} graph page")
    s, n, sg = data["s"], data["n"], data["sg"]
    full = set(n["full"])
    paths: list[str] = []
    nodes: list[dict[str, Any]] = []
    for i, nid in enumerate(n["id"]):
        p = n["p"][i]
        rel = s[n["r"][i]]
        paths.append(rel if i in full else f"{paths[p]}.{rel}")
        k = str(i)
        flags = n["f"][i]
        node: dict[str, Any] = {
            "id": nid,
            "parent": n["id"][p] if p >= 0 else None,
            "path": paths[i],
            "name": s[n["nm"][i]],
            "definition": s[n["d"][i]],
            "depth": n["dp"][i],
            **{key: bool(flags & bit) for key, bit in _FLAGS},
            "generate": [s[x] for x in n.get("g", {}).get(k, [])],
            "params": [{"name": s[a], "value": s[b], "overridden": bool(c)}
                       for a, b, c in n.get("pa", {}).get(k, [])],
            "file": s[n["fl"][i]],
            "line": n["ln"][i],
        }
        nodes.append(node)

    def sig_path(i: int) -> str:
        owner, local = sg[i]
        return f"{paths[owner]}.{s[local]}" if owner >= 0 else str(s[local])

    for i, node in enumerate(nodes):
        k = str(i)
        node["ports"] = [{"port": s[a], "dir": s[b], "actual": s[c], "kind": s[d],
                          "signals": [sig_path(y) for y in ys], "supported": bool(sup)}
                         for a, b, c, d, ys, sup in n.get("po", {}).get(k, [])]
        for opt in _OPTIONAL:
            if k in n.get(opt, {}):
                node[opt] = n[opt][k]
    edges = [{"from": n["id"][a], "to": n["id"][b], "signal": sig_path(x), "port": s[y],
              "directed": bool(z)}
             for a, b, items in data["e"] for x, y, z in items]
    out = {k: v for k, v in data.items() if k not in ("format", "s", "n", "sg", "e")}
    out.update(nodes=nodes, edges=edges)
    return out
