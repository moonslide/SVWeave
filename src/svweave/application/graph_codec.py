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
import re
from collections.abc import Mapping
from typing import Any

FORMAT = "svweave-graph/2"
_FLAGS = (("top", 1), ("interface", 2), ("stub", 4), ("blackbox", 8))
_NODE_KEYS = ("id", "parent", "path", "name", "definition", "depth", "top", "interface",
              "stub", "blackbox", "generate", "params", "file", "line", "ports")
OPTIONAL_KEYS = ("diff", "commits", "children_total", "portal")


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


def owner_of[T](path: str, table: Mapping[str, T]) -> tuple[str, T] | None:
    """(prefix, value) of the longest proper dotted prefix of ``path`` that ``table`` holds: the
    instance whose scope declares a signal. Walks the path from the right, so the cost is its
    depth, not the table size. The one copy of this rule (second council review, architecture
    finding 11: the graph model and the codec each had their own)."""
    end = len(path)
    while True:
        end = path.rfind(".", 0, end)
        if end <= 0:
            return None
        v = table.get(path[:end])
        if v is not None:
            return path[:end], v


def _owner(path: str, index_of: dict[str, int]) -> tuple[int, str] | None:
    """The page node whose path is the longest proper prefix of ``path`` (a signal's scope)."""
    o = owner_of(path, index_of)
    return None if o is None else (o[1], path[len(o[0]) + 1 :])


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
    sparse: dict[str, dict[str, Any]] = {k: {} for k in ("g", "pa", "po", *OPTIONAL_KEYS)}
    full: list[int] = []
    for i, n in enumerate(nodes):
        extra = set(n) - set(_NODE_KEYS) - set(OPTIONAL_KEYS)
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
        for opt in OPTIONAL_KEYS:
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
        for opt in OPTIONAL_KEYS:
            if k in n.get(opt, {}):
                node[opt] = n[opt][k]
    edges = [{"from": n["id"][a], "to": n["id"][b], "signal": sig_path(x), "port": s[y],
              "directed": bool(z)}
             for a, b, items in data["e"] for x, y, z in items]
    out = {k: v for k, v in data.items() if k not in ("format", "s", "n", "sg", "e")}
    out.update(nodes=nodes, edges=edges)
    return out


_DATA_BLOCK = re.compile(r'<script id="data" type="application/json">(.*?)</script>', re.S)
_CHUNK_HEAD, _CHUNK_TAIL = "svwChunk(", ");\n"


def read_page(html: str) -> dict[str, Any]:
    """The decoded page dict of a written graph page (its one JSON data block). The one Python
    reader of the HTML layout (second council review, architecture finding 12: tests and the
    benchmark each scraped it)."""
    m = _DATA_BLOCK.search(html)
    if m is None:
        raise ValueError("not an SVWeave graph page: no data block")
    return decode(json.loads(m.group(1)))


def read_chunk(js: str) -> dict[str, Any]:
    """The decoded part of a ``--lazy`` data file (``svwChunk(<json>);``)."""
    if not (js.startswith(_CHUNK_HEAD) and js.endswith(_CHUNK_TAIL)):
        raise ValueError("not an SVWeave lazy part")
    return decode(json.loads(js[len(_CHUNK_HEAD) : -len(_CHUNK_TAIL)]))


def encode_search(
    items: list[tuple[str, str | None, str, int]], pages: list[str], parents: list[int]
) -> dict[str, Any]:
    """The atlas search index: ``items`` (instance id, parent id, path, page index) in pre-order
    become columns like the page nodes: parent position (-1: path stored in full), path relative
    to the parent's path (interned in ``s``), page index. ``pages`` are the file names,
    ``parents`` each page's parent page (-1 for the index). Lives here, beside the node-path
    encoding it mirrors (second council review, architecture finding 5); the viewer's
    atlasSearch reads it, :func:`decode_search` is its Python twin."""
    pos: dict[str, int] = {}
    paths: dict[str, str] = {}
    strings: list[str] = []
    sidx: dict[str, int] = {}
    cols: dict[str, list[int]] = {"p": [], "r": [], "pg": []}
    for iid, parent, own, page in items:
        if parent is not None and parent in pos and own.startswith(paths[parent] + "."):
            pi, rel = pos[parent], own[len(paths[parent]) + 1 :]
        else:
            pi, rel = -1, own
        pos[iid] = len(cols["pg"])
        paths[iid] = own
        if rel not in sidx:
            sidx[rel] = len(strings)
            strings.append(rel)
        cols["p"].append(pi)
        cols["r"].append(sidx[rel])
        cols["pg"].append(page)
    return {"pages": list(pages), "parent": list(parents), "s": strings, **cols}


def decode_search(idx: dict[str, Any]) -> list[tuple[str, str]]:
    """(instance path, file of the page that shows it) for every entry of a search index."""
    out: list[tuple[str, str]] = []
    paths: list[str] = []
    for i in range(len(idx["p"])):
        rel = idx["s"][idx["r"][i]]
        p = idx["p"][i]
        paths.append(f"{paths[p]}.{rel}" if p >= 0 else rel)
        out.append((paths[i], idx["pages"][idx["pg"][i]]))
    return out
