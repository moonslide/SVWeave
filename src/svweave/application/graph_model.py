"""The graph model of a build: the rows of a whole build, one subtree, a selection or one
atlas region, and :func:`build_model` (nodes, connection edges, hubs, boundary signals).
Split out of graph.py (second council review, architecture finding 1): pure functions of the
stored rows, testable without writing a page."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from svweave.application.graph_codec import owner_of
from svweave.application.semantic_read import (
    QuerySession,
    rows_in,
)
from svweave.application.semantic_read import (
    resolve_instance as _resolve_instance,
)
from svweave.storage.semantic import loads

MAX_PORTS_PER_INSTANCE = 200


# bound for one ``IN (...)`` list (SQLite's default host-parameter limit is far above this)
_SQL_CHUNK = 900


# declaration kinds that name a signal; parameters, genvars and the like are never connectivity
_SIGNAL_KINDS = frozenset({"net", "variable", "port"})


GRAPH_NOTE = (
    "connection edges are derived from direct port associations: two instances share a signal "
    "declaration; direction is the declared port direction, not a driver/load analysis; not a netlist"
)


def _local_name(components: list[Any] | None, path: str) -> str:
    if components:
        last = components[-1]
        if isinstance(last, str):
            return last
        if isinstance(last, dict):
            for k in ("name", "text", "label"):
                v = last.get(k)
                if isinstance(v, str):
                    return v
    return path.rsplit(".", 1)[-1]


def _owner_instance(sig_path: str, by_path: dict[str, str]) -> tuple[str, str] | None:
    """(path, id) of the instance whose scope declares the signal (graph_codec.owner_of)."""
    return owner_of(sig_path, by_path)


def _owned_in_build(s: QuerySession, sig_paths: list[str]) -> set[str]:
    """Those of ``sig_paths`` that an instance of the build declares (some proper dotted prefix
    is an instance path); the others belong to no instance (package / $unit variables)."""
    prefixes: dict[str, list[str]] = defaultdict(list)
    for sp in sig_paths:
        end = len(sp)
        while (end := sp.rfind(".", 0, end)) > 0:
            prefixes[sp[:end]].append(sp)
    found = chunked_rows(s, "SELECT path FROM instances WHERE build_id=? AND path IN ({})",
                     sorted(prefixes))
    return {sp for r in found for sp in prefixes[str(r["path"])]}


def _ord(r: Any) -> tuple[int, int]:
    """Sort key matching SQLite ``ORDER BY ord`` (NULL first) for rows sorted in Python."""
    o = r["ord"]
    return (0, 0) if o is None else (1, int(o))


def chunked_rows(s: QuerySession, sql: str, ids: list[str]) -> list[Any]:
    """:func:`semantic_read.rows_in` with this module's chunk (read at call time: tests lower it)."""
    return rows_in(s, sql, ids, _SQL_CHUNK)


def _load_full(s: QuerySession) -> tuple[list[Any], list[Any], Callable[[list[Any]], dict[str, str]]]:
    rows = s.rows("SELECT * FROM instances WHERE build_id=? ORDER BY ord", s.bid)
    bindings = s.rows("SELECT * FROM port_bindings WHERE build_id=? ORDER BY ord", s.bid)

    def kinds(_refs: list[Any]) -> dict[str, str]:
        decl_kind: dict[str, str] = {}
        for d in s.rows("SELECT declaration_id, kind FROM declarations WHERE build_id=?", s.bid):
            decl_kind[d["declaration_id"]] = d["kind"]
        return decl_kind

    return rows, bindings, kinds


def resolve_root(s: QuerySession, root: str) -> Any:
    """The instance row named by ``root`` (exact path first: indexed; else the shared resolver,
    which also accepts an instance id or a hierarchical path and refuses an ambiguous name)."""
    rows = s.rows("SELECT * FROM instances WHERE build_id=? AND path=?", s.bid, root)
    if len(rows) == 1:
        return rows[0]
    return _resolve_instance(s, root)  # its MISSING_INPUT says absence is no proof (PARTIAL)


def _load_subtree(
    s: QuerySession, root_row: Any
) -> tuple[list[Any], list[Any], Callable[[list[Any]], dict[str, str]]]:
    """Only the rows of ``root_row`` and its descendants. The subtree is the closure of the stored
    ``parent_id`` links (indexed), never a path-prefix guess; port bindings come by primary key and
    declaration kinds only for the declarations those bindings reference."""
    return _load_rows(s, [root_row, *descendants(s, [str(root_row["instance_id"])])])


def descendants(s: QuerySession, ids: list[str]) -> list[Any]:
    """Rows of every descendant of the instances ``ids`` (closure of the parent_id links)."""
    out: list[Any] = []
    level = ids
    while level:
        nxt = chunked_rows(
            s, "SELECT * FROM instances WHERE build_id=? AND parent_id IN ({})", level
        )
        out.extend(nxt)
        level = [str(r["instance_id"]) for r in nxt]
    return out


def _load_rows(
    s: QuerySession, rows: list[Any]
) -> tuple[list[Any], list[Any], Callable[[list[Any]], dict[str, str]]]:
    """Port bindings (by primary key) and, lazily, the kinds of the declarations they reference,
    for exactly the instance ``rows``."""
    rows = sorted({str(r["instance_id"]): r for r in rows}.values(), key=_ord)
    bindings = chunked_rows(
        s,
        "SELECT * FROM port_bindings WHERE build_id=? AND instance_id IN ({})",
        [str(r["instance_id"]) for r in rows],
    )
    bindings.sort(key=lambda b: (_ord(b), b["instance_id"], b["formal_port"]))

    def kinds(refs: list[Any]) -> dict[str, str]:
        ids = sorted(
            {str(x.get("declaration_id")) for x in refs if isinstance(x, dict)}
        )
        return {
            d["declaration_id"]: d["kind"]
            for d in chunked_rows(
                s,
                "SELECT declaration_id, kind FROM declarations WHERE build_id=? "
                "AND declaration_id IN ({})",
                ids,
            )
        }

    return rows, bindings, kinds


def build_model(
    s: QuerySession,
    *,
    max_instances: int | None,
    max_fanout: int | None,
    root_row: Any = None,
    selection: dict[str, Any] | None = None,
    region_rows: list[Any] | None = None,
) -> dict[str, Any]:
    """Graph model of the build behind session ``s``. Pure function of the stored rows.

    With ``root_row`` only that instance and its descendants are read and drawn (subtree view):
    depth is relative to the root, fan-out counts the uses inside the subtree, and a signal
    declared outside the subtree (the root's own port actuals, a hierarchical reference) is
    listed under ``boundary`` instead of being drawn: its owner is not on the page.

    With ``selection`` (from :func:`diff_selection`) exactly its rows are read and drawn, under
    the same rules, and every node carries its ``diff`` mark."""
    partial = root_row is not None or selection is not None or region_rows is not None
    if region_rows is not None:  # one page of an atlas (graph_atlas): exactly these instances
        rows, bindings, kinds = _load_rows(s, region_rows)
    elif selection is not None:
        rows, bindings, kinds = _load_rows(s, selection["rows"])
    elif root_row is not None:
        rows, bindings, kinds = _load_subtree(s, root_row)
    else:
        rows, bindings, kinds = _load_full(s)
    total = len(rows)
    depth: dict[str, int] = {}
    for r in rows:  # ord is pre-order, so a parent is always seen before its children
        pid = r["parent_id"]
        depth[r["instance_id"]] = 0 if pid is None or pid not in depth else depth[pid] + 1
    kept_rows = sorted(rows, key=lambda r: (depth[r["instance_id"]], r["ord"]))[:max_instances]
    # (a slice with None keeps every row: no cap means the complete tree)
    kept = {r["instance_id"] for r in kept_rows}
    kept_rows = sorted(kept_rows, key=lambda r: r["ord"])
    by_path = {r["path"]: r["instance_id"] for r in rows}
    path_by_id = {r["instance_id"]: r["path"] for r in rows}

    parsed = [(b, loads(b["referenced_symbols"]) or []) for b in bindings]
    decl_kind = kinds([x for _, refs in parsed for x in refs])

    ports: dict[str, list[dict[str, Any]]] = defaultdict(list)
    members: dict[str, dict[str, Any]] = {}
    for b, refs in parsed:
        iid = b["instance_id"]
        sigs = [
            x
            for x in refs
            if isinstance(x, dict) and decl_kind.get(str(x.get("declaration_id"))) in _SIGNAL_KINDS
        ]
        if iid in kept and len(ports[iid]) < MAX_PORTS_PER_INSTANCE:
            ports[iid].append(
                {
                    "port": b["formal_port"],
                    "dir": b["direction"],
                    "actual": b["actual_expression"],
                    "kind": b["actual_kind"],
                    "signals": [str(x.get("path")) for x in sigs],
                    "supported": bool(b["supported"]),
                }
            )
        for x in sigs:
            # key by the hierarchical PATH: u1.w and u2.w share one declaration id (same module
            # instantiated twice) but are different signals
            m = members.setdefault(str(x.get("path")), {"path": str(x.get("path")), "uses": []})
            m["uses"].append((iid, b["formal_port"], b["direction"]))

    edges: list[dict[str, Any]] = []
    hubs: list[dict[str, Any]] = []
    boundary: list[dict[str, Any]] = []
    # a signal no instance of the BUILD declares (a package or $unit variable) is drawn as on the
    # complete page (sibling out -> in edges); only one whose owner is off this page is boundary
    off_page = (_owned_in_build(s, [m["path"] for m in members.values()
                                    if _owner_instance(m["path"], by_path) is None])
                if partial else set())
    for m in members.values():
        users = {iid for iid, _, _ in m["uses"]}
        if m["path"] in off_page:
            boundary.append({"signal": m["path"], "instances": len(users)})
            continue
        if max_fanout is not None and len(users) > max_fanout:
            hubs.append({"signal": m["path"], "instances": len(users)})
            continue
        found = _owner_instance(m["path"], by_path)
        owner_path, owner = found if found is not None else ("", None)
        outs = [u for u in m["uses"] if u[2] == "out"]
        ins = [u for u in m["uses"] if u[2] == "in"]
        for iid, port, dirn in m["uses"]:  # signal declared in a parent scope -> parent edge
            if owner is None or owner == iid or iid not in kept or owner not in kept:
                continue
            if not path_by_id.get(iid, "").startswith(owner_path + "."):
                continue
            if dirn == "out":
                src, dst = iid, owner
            else:
                src, dst = owner, iid
            edges.append(
                {
                    "from": src,
                    "to": dst,
                    "signal": m["path"],
                    "port": port,
                    "directed": dirn in ("in", "out"),
                }
            )
        for o_iid, o_port, _ in outs:
            for i_iid, i_port, _ in ins:
                if o_iid == i_iid or o_iid not in kept or i_iid not in kept:
                    continue
                edges.append(
                    {
                        "from": o_iid,
                        "to": i_iid,
                        "signal": m["path"],
                        "port": f"{o_port} -> {i_port}",
                        "directed": True,
                    }
                )
    seen: set[tuple[Any, ...]] = set()
    uniq = []
    for e in edges:
        k = (e["from"], e["to"], e["signal"], e["port"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)

    nodes = []
    for r in kept_rows:
        iid = r["instance_id"]
        loc = loads(r["loc"]) or {}
        nodes.append(
            {
                "id": iid,
                "parent": r["parent_id"] if r["parent_id"] in kept else None,
                "path": r["path"],
                "name": _local_name(loads(r["components"]), r["path"]),
                "definition": r["definition"],
                "depth": depth[iid],
                "top": bool(r["is_top"]),
                "interface": bool(r["is_interface"]),
                "stub": bool(r["is_stub"]),
                "blackbox": bool(r["blackbox_candidate"]),
                "generate": loads(r["generate_context"]) or [],
                "params": [
                    {
                        "name": p.get("name"),
                        "value": p.get("value"),
                        "overridden": bool(p.get("overridden")),
                    }
                    for p in (loads(r["parameters"]) or [])
                    if isinstance(p, dict)
                ],
                "file": loc.get("file") if isinstance(loc, dict) else None,
                "line": loc.get("line") if isinstance(loc, dict) else None,
                "ports": ports.get(iid, []),
            }
        )
    model: dict[str, Any] = {
        "nodes": nodes,
        "edges": uniq,
        "hubs": sorted(hubs, key=lambda h: (-h["instances"], h["signal"])),
        "stats": {
            "instances_total": total,
            "instances_shown": len(nodes),
            "edges": len(uniq),
            "hub_signals": len(hubs),
            "max_instances": max_instances,
            "max_fanout": max_fanout,
        },
        "truncated": total > len(nodes),
        "note": GRAPH_NOTE,
    }
    if partial:
        model["boundary"] = sorted(boundary, key=lambda h: (-h["instances"], h["signal"]))
        model["stats"]["boundary_signals"] = len(boundary)
    if root_row is not None:
        model["root"] = str(root_row["path"])
        model["stats"]["root"] = str(root_row["path"])
    if selection is not None:
        marks, total_kids = selection["marks"], selection["children_total"]
        for n in nodes:
            n["diff"] = marks.get(n["id"], "inside")
            if n["id"] in total_kids:
                n["children_total"] = total_kids[n["id"]]
        model["stats"]["changed_instances"] = sum(1 for n in nodes if n["diff"] == "changed")
    return model
