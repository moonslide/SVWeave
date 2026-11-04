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

import bisect
import hashlib
import os
import re
from collections import defaultdict
from collections.abc import Callable, Iterator
from typing import Any

from svweave.application.diff_impact import (
    DIFF_NOTE,
    MAX_RANGE_COMMITS,
    check_ref,
    collect_changes,
    commits_in_range,
    commits_touching,
)
from svweave.application.graph_atlas import DEFAULT_PAGE_BUDGET, MIN_PORTAL
from svweave.application.graph_atlas import INDEX as ATLAS_INDEX
from svweave.application.graph_atlas import build_pages as build_atlas_pages
from svweave.application.graph_codec import encode as encode_page
from svweave.application.graph_template import render_html
from svweave.application.impact import _names
from svweave.application.options import WorkspaceOptions
from svweave.application.queries import _gap_limits, _resolve_instance, _session
from svweave.application.semantic_common import build_envelope
from svweave.domain.envelope import Envelope, Pagination
from svweave.domain.errors import ErrorCode, SvwError
from svweave.domain.states import EvidenceKind
from svweave.security.safeio import ensure_private_dir, write_under
from svweave.storage.semantic import loads

EXPORT_DIR = "exports"
#: Defaults draw the COMPLETE topology: every instance and every signal as edges. A cap is an
#: explicit reduction (``--max-instances``, ``--max-fanout``), like ``--instance``/``--diff-base``.
DEFAULT_MAX_INSTANCES: int | None = None
DEFAULT_MAX_FANOUT: int | None = None
#: above these the page still holds everything, but the envelope says how to make it smaller
LARGE_PAGE_INSTANCES = 20_000
LARGE_PAGE_BYTES = 64 * 1024 * 1024
#: smallest --page-budget; the envelope lists at most this many file names of an atlas
MIN_PAGE_BUDGET = 100
MAX_LISTED_FILES = 200
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
DIFF_VIEW_NOTE = (
    "diff view: instances of the definitions the git diff changes are drawn with their whole "
    "subtrees; their ancestors are context only and show only their drawn children and the "
    "connections among drawn instances"
)
ATLAS_NOTE = (
    "atlas: the topology is split into linked pages; a portal node is drawn on its parent page "
    "with its own port associations and opens the page of its subtree; together the pages are "
    "the complete topology, except connections through a signal no instance declares (a "
    "package or $unit variable) between instances on different pages"
)
LAZY_NOTE = (
    "lazy: one page that loads the parts of the topology from data files of its directory when "
    "a portal is opened (its CSP admits scripts from that directory); verified in Chromium only"
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


def _owned_in_build(s: Any, sig_paths: list[str]) -> set[str]:
    """Those of ``sig_paths`` that an instance of the build declares (some proper dotted prefix
    is an instance path); the others belong to no instance (package / $unit variables)."""
    prefixes: dict[str, list[str]] = defaultdict(list)
    for sp in sig_paths:
        end = len(sp)
        while (end := sp.rfind(".", 0, end)) > 0:
            prefixes[sp[:end]].append(sp)
    found = _rows_in(s, "SELECT path FROM instances WHERE build_id=? AND path IN ({})",
                     sorted(prefixes))
    return {sp for r in found for sp in prefixes[str(r["path"])]}


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
    return _load_rows(s, [root_row, *_descendants(s, [str(root_row["instance_id"])])])


def _descendants(s: Any, ids: list[str]) -> list[Any]:
    """Rows of every descendant of the instances ``ids`` (closure of the parent_id links)."""
    out: list[Any] = []
    level = ids
    while level:
        nxt = _rows_in(
            s, "SELECT * FROM instances WHERE build_id=? AND parent_id IN ({})", level
        )
        out.extend(nxt)
        level = [str(r["instance_id"]) for r in nxt]
    return out


def _load_rows(
    s: Any, rows: list[Any]
) -> tuple[list[Any], list[Any], Callable[[list[Any]], dict[str, str]]]:
    """Port bindings (by primary key) and, lazily, the kinds of the declarations they reference,
    for exactly the instance ``rows``."""
    rows = sorted({str(r["instance_id"]): r for r in rows}.values(), key=_ord)
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


def diff_selection(s: Any, definition_ids: set[str], root_row: Any = None) -> dict[str, Any]:
    """The instances a diff view draws: every instance of a changed definition (``changed``) with
    its whole subtree (``inside``), plus the ancestors that connect them (``context``) up to
    ``root_row`` or, without one, up to their lowest common ancestor. Instances of a changed
    definition outside ``root_row`` are counted, not drawn. Everything is read by index: the
    changed instances by definition, ancestors by id, descendants by parent_id."""
    changed = _rows_in(
        s,
        "SELECT * FROM instances WHERE build_id=? AND definition_declaration_id IN ({})",
        sorted(definition_ids),
    )
    known: dict[str, Any] = {str(r["instance_id"]): r for r in changed}
    todo = {str(r["parent_id"]) for r in changed if r["parent_id"] is not None} - set(known)
    while todo:  # ancestor rows, one level per round
        got = _rows_in(s, "SELECT * FROM instances WHERE build_id=? AND instance_id IN ({})",
                       sorted(todo))
        known.update({str(r["instance_id"]): r for r in got})
        todo = {str(r["parent_id"]) for r in got if r["parent_id"] is not None} - set(known)

    def chain(iid: str) -> list[str]:  # the instance, then its ancestors up to its top
        out = [iid]
        while (pid := known[out[-1]]["parent_id"]) is not None and str(pid) in known:
            out.append(str(pid))
        return out

    chains = {str(r["instance_id"]): chain(str(r["instance_id"])) for r in changed}
    if root_row is not None:
        rid = str(root_row["instance_id"])
        known[rid] = root_row
        kept = {c: ch[: ch.index(rid) + 1] for c, ch in chains.items() if rid in ch}
        stop: Any = root_row
    else:
        kept = dict(chains)
        common: list[str] = []
        downs = [ch[::-1] for ch in kept.values()]  # top first
        for level in zip(*downs, strict=False):
            if len(set(level)) != 1:
                break
            common.append(level[0])
        stop = known[common[-1]] if common else None
        if common:
            kept = {c: ch[: ch.index(common[-1]) + 1] for c, ch in kept.items()}
    marks: dict[str, str] = {c: "changed" for c in kept}
    for ch in kept.values():
        # walking up from a changed instance: everything below the next CHANGED ancestor is inside
        # that ancestor (e.g. the UART between a changed baud generator and its changed
        # sub-system), only what lies above every changed instance is context
        passed: list[str] = []
        for a in ch[1:]:
            if marks.get(a) == "changed":
                for p in passed:
                    marks[p] = "inside"
                break
            passed.append(a)
        else:
            for p in passed:
                marks.setdefault(p, "context")
    inside = _descendants(s, sorted(kept))
    context = sorted(i for i, m in marks.items() if m == "context")
    counts = {
        str(r["parent_id"]): int(r["n"])
        for r in _rows_in(
            s,
            "SELECT parent_id, COUNT(*) AS n FROM instances WHERE build_id=? AND parent_id IN ({}) "
            "GROUP BY parent_id",
            context,
        )
    }
    return {
        "rows": [known[i] for i in marks] + inside,
        "marks": marks,
        "children_total": counts,
        "root_row": stop,
        "changed_total": len(changed),
        "outside_root": len(chains) - len(kept),
    }


def build_model(
    s: Any,
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


MAX_TRACED_DEFINITIONS = 200


def _line_of(starts: list[int], offset: int) -> int:
    """1-based line of byte ``offset`` given the byte offsets where lines start."""
    return bisect.bisect_right(starts, offset)


def _per_commit(
    s: Any, ch: dict[str, Any], base: str, head: str, defs: set[str]
) -> tuple[list[dict[str, Any]], dict[str, list[int]], list[str]]:
    """Which commits of base..head changed each changed definition: git's line-history tracing
    (``log -L``) over the definition's own line span in the HEAD version, which is exact only
    when the build's snapshot of that file IS the head version (files[].side == "new"). Other
    definitions stay unlabelled, with a warning; nothing is guessed."""
    warns: list[str] = []
    commits, cut = commits_in_range(ch["top"], base, head)
    index = {c["sha"]: i for i, c in enumerate(commits)}
    if cut:
        warns.append(f"more than {MAX_RANGE_COMMITS} commits in {base}..{head}: only the oldest "
                     f"{MAX_RANGE_COMMITS} are listed and attributed")
    exact = {f["logical_path"]: f["path"] for f in ch["files"]
             if f.get("side") == "new" and f.get("aligned") is True}
    labels: dict[str, list[int]] = {}
    unlabelled: list[str] = []
    starts_of: dict[str, list[int]] = {}
    for n, d in enumerate(sorted(defs)):
        r = s.one("SELECT file, COALESCE(full_start, span_start) AS lo, "
                  "COALESCE(full_end, span_end) AS hi FROM declarations "
                  "WHERE build_id=? AND declaration_id=?", s.bid, d)
        if (n >= MAX_TRACED_DEFINITIONS or r is None or r["file"] not in exact
                or r["lo"] is None or r["hi"] is None):
            unlabelled.append(d)
            continue
        lf = str(r["file"])
        if lf not in starts_of:
            data = (ch["snap_dir"] / ch["by_logical"][lf].snapshot_path).read_bytes()
            starts_of[lf] = [0] + [i + 1 for i, b in enumerate(data) if b == 0x0A]
        st = starts_of[lf]
        lo, hi = int(r["lo"]), int(r["hi"])
        first, last = _line_of(st, lo), _line_of(st, max(hi - 1, lo))
        found = commits_touching(ch["top"], base, head, exact[lf], first, last)
        labels[d] = sorted({index[sha] for sha in found if sha in index})
    if unlabelled:
        warns.append(
            f"{len(unlabelled)} changed definition(s) carry no commit labels: per-commit "
            "attribution needs the build's snapshot of their file to be the --diff-head version "
            f"(build that revision), and at most {MAX_TRACED_DEFINITIONS} definitions are traced"
        )
    return commits, labels, warns


def _diff_view(
    s: Any, base: str, head: str | None, root_row: Any, *, per_commit: bool = False
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
    """git diff -> changed definitions -> the instances to draw (:func:`diff_selection`). The
    selection is None when the diff reaches no instance (nothing to draw is an answer, not an
    error: the build itself is not blocked)."""
    ch = collect_changes(s, base=base, head=head, repo=None, new_side=True)
    defs = set(ch["by_def"])
    files = [{"path": f["path"], "side": f.get("side"), "aligned": f.get("aligned")}
             for f in ch["files"]]
    summary = {
        "files_changed": len(ch["diffs"]),
        "files_in_build": len(ch["files"]),
        "files_not_in_build": len(ch["unmapped"]),
        "non_instance_declarations": len(ch["non_instance"]),
        "unattributed_ranges": len(ch["unattributed"]),
    }
    sel = diff_selection(s, defs, root_row) if defs else None
    if sel is not None and not sel["marks"]:
        sel_none_reason = (
            f"none of the {sel['changed_total']} instance(s) of the changed definition(s) is "
            "under the instance" if root_row is not None and sel["changed_total"]
            else "the changed definition(s) have no instance in this build")
        outside = sel["outside_root"]
        sel = None
    else:
        sel_none_reason = "no changed line falls inside a module/interface of this build"
        outside = sel["outside_root"] if sel is not None else 0
    names = _names(s, defs)
    totals = {
        str(r["definition_declaration_id"]): int(r["n"])
        for r in _rows_in(
            s,
            "SELECT definition_declaration_id, COUNT(*) AS n FROM instances WHERE build_id=? "
            "AND definition_declaration_id IN ({}) GROUP BY definition_declaration_id",
            sorted(defs),
        )
    }
    changed = sorted(
        ({"declaration_id": d,
          "qualified_name": names.get(d, {}).get("qualified_name"),
          "kind": names.get(d, {}).get("kind"),
          "changed_declarations": ch["by_def"][d]["declarations"],
          "instances": totals.get(d, 0), "instances_drawn": 0} for d in defs),
        key=lambda x: str(x["qualified_name"]),
    )
    commit_warns: list[str] = []
    commits: list[dict[str, Any]] | None = None
    if per_commit and head is not None:
        commits, labels, commit_warns = _per_commit(s, ch, base, head, defs)
        for x in changed:
            x["commits"] = labels.get(x["declaration_id"])  # None: not attributable
        used = {i for v in labels.values() for i in v}
        for i, c in enumerate(commits):
            c["index"] = i
            c["sha"] = c["sha"][:12]
            c["changes_drawn_definitions"] = i in used
    diff = {
        "base": base,
        "head": head or "working tree",
        "changed_definitions": changed,
        "files": files,
        "unmapped_files": [u["path"] for u in ch["unmapped"]][:200],
        "non_instance_declarations": [d["qualified_name"] for d in ch["non_instance"]][:200],
        "outside_root": outside,
        "summary": summary,
    }
    if commits is not None:
        diff["commits"] = commits
    if sel is None:
        diff["nothing_to_draw"] = sel_none_reason
    warns: list[str] = []
    approx = [f["path"] for f in files if f["aligned"] is not True]
    if approx:
        warns.append(
            f"{len(approx)} changed file(s) match neither side of the diff in the build's "
            "snapshot: their line mapping is approximate (rebuild, or diff against the revision "
            "the build was made from)"
        )
    if ch["unmapped"]:
        warns.append(f"{len(ch['unmapped'])} changed file(s) are not part of this build and were "
                     "not drawn")
    if ch["non_instance"]:
        warns.append(f"{len(ch['non_instance'])} changed declaration(s) belong to no "
                     "module/interface (package, class, free subroutine) and are listed, not drawn")
    if ch["unattributed"]:
        warns.append(f"{len(ch['unattributed'])} changed range(s) fall outside every declaration")
    if outside:
        warns.append(f"{outside} instance(s) of the changed definition(s) are outside "
                     "the instance and are not drawn")
    if commits is not None:
        idle = [c["sha"] for c in commits if not c["changes_drawn_definitions"]]
        if idle:
            warns.append(f"{len(idle)} commit(s) of the range change no drawn module/interface "
                         "(e.g. a change undone later, a package, a file outside the build)")
    warns += commit_warns
    return diff, sel, warns


def _page_meta(s: Any, limits: list[str]) -> dict[str, Any]:
    """What every page states about its evidence (rule 5): build, snapshot, completeness,
    freshness, provenance and the limitations of the envelope that wrote it."""
    return {
        "build_id": s.bid,
        "snapshot_id": str(s.build["snapshot_id"]),
        "profile": str(s.build["profile"]),
        "completeness": str(s.build["completeness"]),
        "freshness": s.fresh().value,
        "evidence_kind": EvidenceKind.HDL_STATIC_RESOLVED.value,
        "workspace_id": str(s.build["workspace_id"]),
        "limitations": list(limits),
    }


def _export_atlas(
    s: Any,
    request_id: str,
    *,
    root_row: Any,
    name: str | None,
    instance: str | None,
    budget: int,
    lazy: bool = False,
) -> Envelope:
    """--split: the complete topology (or one --instance subtree) as linked pages of at most
    ``budget`` instances in one directory; ``index.html`` is the top page (graph_atlas).
    --lazy: the same cut as one page (index.html) that loads the other parts (c-<hash>.js)."""
    stem = "graph-" + s.bid[:12] + ("-lazy" if lazy else "-atlas")
    if root_row is not None:
        stem += "-" + hashlib.sha256(str(root_row["path"]).encode()).hexdigest()[:10]
    dirname = name or stem
    if not _SAFE_NAME.fullmatch(dirname) or dirname.startswith("."):
        raise SvwError(ErrorCode.INVALID_CONFIG, "name must match [A-Za-z0-9._-]+ (no path)")
    start = [root_row] if root_row is not None else s.rows(
        "SELECT * FROM instances WHERE build_id=? AND parent_id IS NULL ORDER BY ord", s.bid)
    limits = _gap_limits(s) + [GRAPH_NOTE, LAZY_NOTE if lazy else ATLAS_NOTE]
    meta = _page_meta(s, limits)
    files, summary = build_atlas_pages(
        s, start, budget=budget, meta=meta,
        model_of=lambda region: build_model(
            s, max_instances=None, max_fanout=None, region_rows=region),
        lazy=lazy,
    )
    exports = ensure_private_dir(s.ctx.state_dir, EXPORT_DIR)
    out = ensure_private_dir(exports, dirname)
    for entry in os.scandir(out):  # pages of an earlier, different cut must not linger
        if entry.is_file(follow_symlinks=False) and entry.name not in files and (
                (entry.name.startswith("p-") and entry.name.endswith(".html"))
                or (entry.name.startswith("c-") and entry.name.endswith(".js"))):
            os.unlink(entry.path)
    for fname, data in files.items():
        write_under(out, fname, data)
    warns: list[str] = []
    if summary["oversized_pages"]:
        warns.append(f"{summary['oversized_pages']} page(s) hold more than {budget} instances: "
                     f"subtrees under {MIN_PORTAL} instances are never split off")
    return build_envelope(
        s.ctx, s.db, s.build, request_id,
        {
            "path": f".svweave/{EXPORT_DIR}/{dirname}/{ATLAS_INDEX}",
            "directory": f".svweave/{EXPORT_DIR}/{dirname}",
            "written": True,
            "served": False,
            "root": str(root_row["path"]) if root_row is not None else None,
            ("lazy" if lazy else "atlas"): {
                **summary, "files": sorted(files)[:MAX_LISTED_FILES],
                "files_total": len(files), "files_truncated": len(files) > MAX_LISTED_FILES},
            "note": GRAPH_NOTE,
        },
        warnings=warns,
        extra_limits=limits,
        freshness=s.fresh(),
    )


def export_graph(
    opts: WorkspaceOptions,
    build_id: str,
    request_id: str,
    *,
    name: str | None = None,
    max_instances: int | None = DEFAULT_MAX_INSTANCES,
    max_fanout: int | None = DEFAULT_MAX_FANOUT,
    instance: str | None = None,
    diff_base: str | None = None,
    diff_head: str | None = None,
    per_commit: bool = False,
    split: bool = False,
    page_budget: int | None = None,
    lazy: bool = False,
) -> Envelope:
    if split and lazy:
        raise SvwError(ErrorCode.INVALID_CONFIG, "give split (--split) OR lazy (--lazy): "
                       "linked pages or one page that loads its parts")
    if page_budget is not None and not (split or lazy):
        raise SvwError(ErrorCode.INVALID_CONFIG, "page_budget (--page-budget) needs split "
                       "(--split) or lazy (--lazy)")
    if (split or lazy) and (max_instances is not None or max_fanout is not None):
        # the pages together are the complete topology; a per-page cap would silently drop
        # instances and connections of every page and is not reported per page
        raise SvwError(ErrorCode.INVALID_CONFIG,
                       "max_instances/max_fanout (--max-instances/--max-fanout) cannot be "
                       "combined with split/lazy (--split/--lazy): those pages are the complete "
                       "topology; use page_budget (--page-budget) to make the pages smaller")
    if (split or lazy) and diff_base is not None:
        raise SvwError(ErrorCode.INVALID_CONFIG,
                       "split applies to the complete topology or an --instance subtree, not to "
                       "a diff view (a diff page is small already)")
    if page_budget is not None and (not isinstance(page_budget, int)
                                    or isinstance(page_budget, bool) or page_budget < MIN_PAGE_BUDGET):
        raise SvwError(ErrorCode.INVALID_CONFIG, f"page_budget (--page-budget) must be at least "
                       f"{MIN_PAGE_BUDGET} instances")
    if per_commit and (diff_base is None or diff_head is None):
        raise SvwError(
            ErrorCode.INVALID_CONFIG,
            "per_commit needs diff_base and diff_head (a commit range; build the head revision)",
        )
    if diff_base is not None:
        check_ref(diff_base, "diff_base")
    if diff_head is not None:
        if diff_base is None:
            raise SvwError(ErrorCode.INVALID_CONFIG, "diff_head needs diff_base")
        check_ref(diff_head, "diff_head")
    if instance is not None and (not isinstance(instance, str) or not instance.strip()):
        raise SvwError(ErrorCode.INVALID_CONFIG, "instance must be a non-empty instance path or id")
    if max_instances is not None and (
            not isinstance(max_instances, int) or isinstance(max_instances, bool)
            or max_instances < 1):
        raise SvwError(ErrorCode.INVALID_CONFIG, "max_instances must be at least 1 (omit: no cap)")
    if max_fanout is not None and (
            not isinstance(max_fanout, int) or isinstance(max_fanout, bool) or max_fanout < 2):
        raise SvwError(ErrorCode.INVALID_CONFIG, "max_fanout must be at least 2 (omit: no hubs)")
    with _session(opts, build_id) as s:
        root_row = resolve_root(s, instance) if instance is not None else None
        if split or lazy:
            return _export_atlas(
                s, request_id, root_row=root_row, name=name, instance=instance,
                budget=page_budget or DEFAULT_PAGE_BUDGET, lazy=lazy,
            )
        diff: dict[str, Any] | None = None
        warns: list[str] = []
        if diff_base is not None:
            diff, sel, dwarns = _diff_view(s, diff_base, diff_head, root_row,
                                           per_commit=per_commit)
            warns += dwarns
            if sel is None:  # nothing to draw: report it, write no page
                warns.insert(0, f"nothing to draw for {diff['base']} -> {diff['head']}: "
                                f"{diff['nothing_to_draw']}; no page was written")
                return build_envelope(
                    s.ctx, s.db, s.build, request_id,
                    {"path": None, "written": False, "served": False, "diff": diff,
                     "note": GRAPH_NOTE},
                    warnings=warns,
                    extra_limits=_gap_limits(s) + [DIFF_VIEW_NOTE, DIFF_NOTE],
                    freshness=s.fresh(),
                )
            root_row = sel["root_row"]
            model = build_model(
                s, max_instances=max_instances, max_fanout=max_fanout, selection=sel,
                root_row=root_row,
            )
            # count what the page really draws (after --max-instances), per definition
            def_of = {str(r["instance_id"]): str(r["definition_declaration_id"])
                      for r in sel["rows"]}
            drawn: dict[str, int] = defaultdict(int)
            for n in model["nodes"]:
                if n["diff"] == "changed":
                    drawn[def_of[n["id"]]] += 1
            for x in diff["changed_definitions"]:
                x["instances_drawn"] = drawn.get(x["declaration_id"], 0)
            if "commits" in diff:  # each changed instance carries its definition's commits
                by_def = {x["declaration_id"]: x.get("commits")
                          for x in diff["changed_definitions"]}
                for n in model["nodes"]:
                    if n["diff"] == "changed":
                        n["commits"] = by_def.get(def_of[n["id"]])
            diff["changed_definitions"].sort(
                key=lambda x: (-x["instances_drawn"], str(x["qualified_name"])))
            model["diff"] = diff
        else:
            model = build_model(
                s, max_instances=max_instances, max_fanout=max_fanout, root_row=root_row
            )
        stem = "graph-" + s.bid[:12]
        if diff is not None:
            key = f"{diff_base}\0{diff_head or ''}\0{instance or ''}\0{per_commit}"
            stem += "-diff-" + hashlib.sha256(key.encode()).hexdigest()[:10]
        elif root_row is not None:  # one default file per (build, root); a path is no file name
            stem += "-sub-" + hashlib.sha256(str(root_row["path"]).encode()).hexdigest()[:10]
        fname = f"{name or stem}.html"
        if not _SAFE_NAME.fullmatch(fname) or fname.startswith("."):
            raise SvwError(ErrorCode.INVALID_CONFIG, "name must match [A-Za-z0-9._-]+ (no path)")
        limits = _gap_limits(s) + [GRAPH_NOTE]
        if diff is not None:
            limits += [DIFF_VIEW_NOTE, DIFF_NOTE]
        elif root_row is not None:
            limits.append(SUBTREE_NOTE)
        meta = _page_meta(s, limits)
        if root_row is not None:
            meta["root"] = str(root_row["path"])
        if diff is not None:
            meta["diff"] = f"{diff['base']} -> {diff['head']}"
        page = render_html(encode_page({"meta": meta, **model}))
        data = page.encode("utf-8")
        exports = ensure_private_dir(s.ctx.state_dir, EXPORT_DIR)
        write_under(exports, fname, data)
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
        if len(model["nodes"]) > LARGE_PAGE_INSTANCES or len(data) > LARGE_PAGE_BYTES:
            warns.append(
                f"large page: {len(model['nodes'])} instances, {len(model['edges'])} edges, "
                f"{len(data) // (1024 * 1024)} MiB; a browser may be slow. It is the complete "
                "topology; to keep it complete in smaller pages use --split or --lazy, to "
                "reduce it use --instance, --diff-base, --max-instances or --max-fanout"
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
                "written": True,
                "root": model.get("root"),
                **({"diff": diff} if diff is not None else {}),
            },
            pagination=Pagination(
                limit=max_instances if max_instances is not None else len(model["nodes"]),
                truncated=bool(model["truncated"])),
            warnings=warns,
            extra_limits=limits,
            freshness=s.fresh(),
        )
