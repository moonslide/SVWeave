"""Static graph export of one build (ADR-014).

``svweave graph export --build B`` writes ONE self-contained HTML file under
``.svweave/exports/``: the elaborated instance hierarchy plus the connections derived from the
build's direct port associations, drawn by an inline viewer. Nothing is served: no socket, no
network, no external script. The page is data derived from one immutable build; it is not a netlist.

Connection edges mean "these two instances reference the same signal declaration in a direct port
association", oriented by the DECLARED port direction. There is no driver/load analysis, no bit
level and no cross-hierarchy dataflow (docs/limitations.md). Text coming from the sources is data:
it is embedded as JSON with ``<``, ``>``, ``&`` and the line separators escaped, and the viewer only
ever assigns it through ``textContent``.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Callable, Iterator
from typing import Any

from svweave.application.graph_template import render_html
from svweave.application.options import WorkspaceOptions
from svweave.application.queries import _gap_limits, _resolve_instance, _session
from svweave.application.semantic_common import build_envelope
from svweave.domain.envelope import Envelope
from svweave.domain.errors import ErrorCode, SvwError
from svweave.security.safeio import ensure_private_dir, write_under
from svweave.storage.semantic import loads

EXPORT_DIR = "exports"
DEFAULT_MAX_INSTANCES = 5000
DEFAULT_MAX_FANOUT = 16
MAX_PORTS_PER_INSTANCE = 200
# bound for one ``IN (...)`` list (SQLite's default host-parameter limit is far above this)
_SQL_CHUNK = 900
# declaration kinds that name a signal; parameters, genvars and the like are never connectivity
_SIGNAL_KINDS = frozenset({"net", "variable", "port"})

_SAFE_NAME = re.compile(r"[A-Za-z0-9._-]{1,120}")

GRAPH_NOTE = (
    "connection edges are derived from direct port associations: two instances share a signal "
    "declaration; direction is the declared port direction, not a driver/load analysis; not a netlist"
)
SUBTREE_NOTE = (
    "subtree view: only the root instance and its descendants were read; fan-out counts uses "
    "inside the subtree, and signals declared outside it are listed as boundary signals, not drawn"
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
    """(path, id) of the instance whose scope declares the signal: the longest instance path that
    prefixes the signal path. Walks the dotted path from the right, so cost is the path depth, not
    the number of instances (115k on a real SoC)."""
    end = len(sig_path)
    while True:
        end = sig_path.rfind(".", 0, end)
        if end <= 0:
            return None
        iid = by_path.get(sig_path[:end])
        if iid is not None:
            return sig_path[:end], iid


def _ord(r: Any) -> tuple[int, int]:
    """Sort key matching SQLite ``ORDER BY ord`` (NULL first) for rows sorted in Python."""
    o = r["ord"]
    return (0, 0) if o is None else (1, int(o))


def _chunks(items: list[str]) -> Iterator[list[str]]:
    for i in range(0, len(items), _SQL_CHUNK):
        yield items[i : i + _SQL_CHUNK]


def _rows_in(s: Any, sql: str, ids: list[str]) -> list[Any]:
    """``sql`` has one ``{}`` for an ``IN (...)`` list; run it per chunk of ``ids``."""
    out: list[Any] = []
    for part in _chunks(ids):
        out.extend(s.rows(sql.format(",".join("?" * len(part))), s.bid, *part))
    return out


def _load_full(s: Any) -> tuple[list[Any], list[Any], Callable[[list[Any]], dict[str, str]]]:
    rows = s.rows("SELECT * FROM instances WHERE build_id=? ORDER BY ord", s.bid)
    bindings = s.rows("SELECT * FROM port_bindings WHERE build_id=? ORDER BY ord", s.bid)

    def kinds(_refs: list[Any]) -> dict[str, str]:
        decl_kind: dict[str, str] = {}
        for d in s.rows("SELECT declaration_id, kind FROM declarations WHERE build_id=?", s.bid):
            decl_kind[d["declaration_id"]] = d["kind"]
        return decl_kind

    return rows, bindings, kinds


def resolve_root(s: Any, root: str) -> Any:
    """The instance row named by ``root`` (exact path first: indexed; else the shared resolver,
    which also accepts an instance id or a hierarchical path and refuses an ambiguous name)."""
    rows = s.rows("SELECT * FROM instances WHERE build_id=? AND path=?", s.bid, root)
    if len(rows) == 1:
        return rows[0]
    try:
        return _resolve_instance(s, root)
    except SvwError as e:
        if e.code == ErrorCode.MISSING_INPUT and str(s.build["completeness"]) != "COMPLETE":
            raise SvwError(
                ErrorCode.MISSING_INPUT,
                f"unknown instance {root!r} in a {s.build['completeness']} build: absence from a "
                "build that is not COMPLETE is not proof that the instance does not exist",
                {"reason": "instance_missing", "instance": root},
            ) from e
        raise


def _load_subtree(
    s: Any, root_row: Any
) -> tuple[list[Any], list[Any], Callable[[list[Any]], dict[str, str]]]:
    """Only the rows of ``root_row`` and its descendants. The subtree is the closure of the stored
    ``parent_id`` links (indexed), never a path-prefix guess; port bindings come by primary key and
    declaration kinds only for the declarations those bindings reference."""
    rows = [root_row]
    level = [str(root_row["instance_id"])]
    while level:
        nxt = _rows_in(
            s, "SELECT * FROM instances WHERE build_id=? AND parent_id IN ({})", level
        )
        rows.extend(nxt)
        level = [str(r["instance_id"]) for r in nxt]
    rows.sort(key=_ord)
    bindings = _rows_in(
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
            for d in _rows_in(
                s,
                "SELECT declaration_id, kind FROM declarations WHERE build_id=? "
                "AND declaration_id IN ({})",
                ids,
            )
        }

    return rows, bindings, kinds


def build_model(
    s: Any, *, max_instances: int, max_fanout: int, root_row: Any = None
) -> dict[str, Any]:
    """Graph model of the build behind session ``s``. Pure function of the stored rows.

    With ``root_row`` only that instance and its descendants are read and drawn (subtree view):
    depth is relative to the root, fan-out counts the uses inside the subtree, and a signal
    declared outside the subtree (the root's own port actuals, a hierarchical reference) is
    listed under ``boundary`` instead of being drawn: its owner is not on the page."""
    if root_row is None:
        rows, bindings, kinds = _load_full(s)
    else:
        rows, bindings, kinds = _load_subtree(s, root_row)
    total = len(rows)
    depth: dict[str, int] = {}
    for r in rows:  # ord is pre-order, so a parent is always seen before its children
        pid = r["parent_id"]
        depth[r["instance_id"]] = 0 if pid is None or pid not in depth else depth[pid] + 1
    kept_rows = sorted(rows, key=lambda r: (depth[r["instance_id"]], r["ord"]))[:max_instances]
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
    for m in members.values():
        users = {iid for iid, _, _ in m["uses"]}
        if root_row is not None and _owner_instance(m["path"], by_path) is None:
            boundary.append({"signal": m["path"], "instances": len(users)})
            continue
        if len(users) > max_fanout:
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
    if root_row is not None:
        model["root"] = str(root_row["path"])
        model["boundary"] = sorted(boundary, key=lambda h: (-h["instances"], h["signal"]))
        model["stats"]["root"] = str(root_row["path"])
        model["stats"]["boundary_signals"] = len(boundary)
    return model


def export_graph(
    opts: WorkspaceOptions,
    build_id: str,
    request_id: str,
    *,
    name: str | None = None,
    max_instances: int = DEFAULT_MAX_INSTANCES,
    max_fanout: int = DEFAULT_MAX_FANOUT,
    root: str | None = None,
) -> Envelope:
    if root is not None and (not isinstance(root, str) or not root.strip()):
        raise SvwError(ErrorCode.INVALID_CONFIG, "root must be a non-empty instance path or id")
    if not isinstance(max_instances, int) or not 1 <= max_instances <= 100_000:
        raise SvwError(ErrorCode.INVALID_CONFIG, "max_instances must be in 1..100000")
    if not isinstance(max_fanout, int) or not 2 <= max_fanout <= 1000:
        raise SvwError(ErrorCode.INVALID_CONFIG, "max_fanout must be in 2..1000")
    with _session(opts, build_id) as s:
        root_row = resolve_root(s, root) if root is not None else None
        model = build_model(
            s, max_instances=max_instances, max_fanout=max_fanout, root_row=root_row
        )
        stem = "graph-" + s.bid[:12]
        if root_row is not None:  # one default file per (build, root); the path is not a file name
            stem += "-sub-" + hashlib.sha256(str(root_row["path"]).encode()).hexdigest()[:10]
        fname = f"{name or stem}.html"
        if not _SAFE_NAME.fullmatch(fname) or fname.startswith("."):
            raise SvwError(ErrorCode.INVALID_CONFIG, "name must match [A-Za-z0-9._-]+ (no path)")
        meta = {
            "build_id": s.bid,
            "snapshot_id": str(s.build["snapshot_id"]),
            "profile": str(s.build["profile"]),
            "completeness": str(s.build["completeness"]),
            "freshness": s.fresh().value,
            "evidence_kind": "HDL_STATIC_RESOLVED",
            "workspace_id": str(s.build["workspace_id"]),
        }
        if root_row is not None:
            meta["root"] = str(root_row["path"])
        page = render_html({"meta": meta, **model})
        data = page.encode("utf-8")
        exports = ensure_private_dir(s.ctx.state_dir, EXPORT_DIR)
        write_under(exports, fname, data)
        warns = []
        limits = _gap_limits(s) + [GRAPH_NOTE]
        if root_row is not None:
            limits.append(SUBTREE_NOTE)
        if model["truncated"]:
            warns.append(
                f"truncated: {model['stats']['instances_shown']} of "
                f"{model['stats']['instances_total']} instances shown (shallowest first); "
                "an instance not drawn must not be assumed absent"
            )
        if model["hubs"]:
            warns.append(
                f"{len(model['hubs'])} signal(s) referenced by more than {max_fanout} instances "
                "are listed as hubs, not drawn as edges"
            )
        if model.get("boundary"):
            warns.append(
                f"{len(model['boundary'])} signal(s) declared outside the subtree root are listed "
                "as boundary signals, not drawn as edges"
            )
        return build_envelope(
            s.ctx,
            s.db,
            s.build,
            request_id,
            {
                "path": f".svweave/{EXPORT_DIR}/{fname}",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "stats": model["stats"],
                "truncated": model["truncated"],
                "note": GRAPH_NOTE,
                "served": False,
                "root": model.get("root"),
            },
            warnings=warns,
            extra_limits=limits,
            freshness=s.fresh(),
        )
