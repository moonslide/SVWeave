"""The shared semantic read core: the build-bound query session, the row converters, the
instance and declaration resolvers, the coverage-gap limitations and the definition-graph
helpers that queries, impact, rename, diff-impact and the graph export all build on.

Public on purpose (first council review, architecture finding 1): these were private names of
queries.py and impact.py imported across modules, so a change "inside queries" silently changed
what four other tools returned, and the session was typed ``Any`` where it was used."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from svweave.application.keys import cursor_key
from svweave.application.options import WorkspaceOptions
from svweave.application.semantic_common import freshness_of, require_build
from svweave.config.context import WorkspaceContext
from svweave.domain.errors import ErrorCode, SvwError
from svweave.domain.ids import is_sha256_hex
from svweave.domain.states import Freshness
from svweave.storage.db import DB_FILENAME, Database
from svweave.storage.semantic import loads


class QuerySession:
    def __init__(self, ctx: WorkspaceContext, db: Database, build: sqlite3.Row, key: bytes) -> None:
        self.ctx, self.db, self.build, self.key = ctx, db, build, key
        self._fresh: Freshness | None = None

    def fresh(self) -> Freshness:
        """Freshness is computed ONCE per query (it is the expensive part of an envelope)."""
        if self._fresh is None:
            self._fresh = freshness_of(self.ctx, self.db, self.build)
        return self._fresh

    @property
    def bid(self) -> str:
        return str(self.build["build_id"])

    def rows(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        return self.db.conn.execute(sql, args).fetchall()

    def one(self, sql: str, *args: Any) -> sqlite3.Row | None:
        r: sqlite3.Row | None = self.db.conn.execute(sql, args).fetchone()
        return r

    def count(self, sql: str, *args: Any) -> int:
        return int(self.db.conn.execute(sql, args).fetchone()[0])


def workspace_cursor_key(ctx: WorkspaceContext) -> bytes:
    """Workspace secret in the trusted dir (outside the repo), shared with svw_search."""
    return cursor_key(ctx)


@contextmanager
def session(opts: WorkspaceOptions, build_id: str) -> Iterator[QuerySession]:
    ctx = opts.load()
    with Database(ctx.state_dir / DB_FILENAME) as db:
        build = require_build(db, build_id)
        yield QuerySession(ctx, db, build, workspace_cursor_key(ctx))


def decl_dto(r: sqlite3.Row) -> dict[str, Any]:
    macro = loads(r["macro"])
    return {
        "symbol_id": r["symbol_id"],
        "declaration_id": r["declaration_id"],
        "kind": r["kind"],
        "name": r["name"],
        "qualified_name": r["qualified_name"],
        "scope": r["scope"],
        "type": r["type"],
        "compilation_unit": r["compilation_unit"],
        "library": r["library"],
        "location": loads(r["loc"]),
        "full_span": None
        if r["full_start"] is None
        else {"start": r["full_start"], "end": r["full_end"]},
        "macro_origin": macro,
        "attrs": loads(r["attrs"]) or {},
    }


def inst_dto(r: sqlite3.Row, depth: int | None = None) -> dict[str, Any]:
    d = {
        "instance_id": r["instance_id"],
        "path": r["path"],
        "hierarchical_path": r["hier_path"],
        "path_components": loads(r["components"]),
        "parent_id": r["parent_id"],
        "definition": r["definition"],
        "definition_declaration_id": r["definition_declaration_id"],
        "specialization": r["specialization"],
        "parameters": loads(r["parameters"]) or [],
        "generate_context": loads(r["generate_context"]) or [],
        "is_top": bool(r["is_top"]),
        "is_interface": bool(r["is_interface"]),
        "is_stub": bool(r["is_stub"]),
        "stub_name": r["stub_name"],
        "blackbox_candidate": bool(r["blackbox_candidate"]),
        "location": loads(r["loc"]),
        "bound_from": loads(r["bind"]),
    }
    if depth is not None:
        d["depth"] = depth
    return d


def qualified_names(s: QuerySession, ids: set[str]) -> dict[str, tuple[str, str]]:
    if not ids:
        return {}
    out: dict[str, tuple[str, str]] = {}
    ids_l = sorted(ids)
    for i in range(0, len(ids_l), 500):
        chunk = ids_l[i : i + 500]
        q = ",".join("?" for _ in chunk)
        for r in s.rows(
            f"SELECT declaration_id, qualified_name, kind FROM declarations "
            f"WHERE build_id=? AND declaration_id IN ({q})",
            s.bid,
            *chunk,
        ):
            out[r["declaration_id"]] = (r["qualified_name"], r["kind"])
    return out


def decl_rows_by_location(s: QuerySession, file: str, byte: int) -> list[sqlite3.Row]:
    ids: dict[str, sqlite3.Row] = {}
    for r in s.rows(
        "SELECT * FROM declarations WHERE build_id=? AND file=? AND span_start<=? AND ?<span_end",
        s.bid,
        file,
        byte,
        byte,
    ):
        ids[r["declaration_id"]] = r
    refs = s.rows(
        'SELECT target_id FROM "references" WHERE build_id=? AND file=? AND span_start<=? '
        "AND ?<span_end AND target_id IS NOT NULL",
        s.bid,
        file,
        byte,
        byte,
    )
    for ref in refs:
        if ref["target_id"] not in ids:
            d = s.one(
                "SELECT * FROM declarations WHERE build_id=? AND declaration_id=?",
                s.bid,
                ref["target_id"],
            )
            if d is not None:
                ids[d["declaration_id"]] = d
    return list(ids.values())


def gap_limits(s: QuerySession) -> list[str]:
    rows = s.rows(
        "SELECT kind, COUNT(*) n FROM coverage_gaps WHERE build_id=? GROUP BY kind", s.bid
    )
    msgs = {
        "generic_class_not_specialized": "{n} generic class(es) never specialized: references "
        "inside their bodies are not extracted",
        "definition_not_elaborated": "{n} module/interface definition(s) not elaborated under the "
        "tops: references inside them are not extracted (inactive_for_profile)",
        "library_member_not_loaded": "{n} library member(s) never loaded: not analysed",
        "stub_boundary": "{n} stub/blackbox boundary instance(s): internals unknown",
        "empty_module_body": "{n} instance(s) of modules with an empty body (possible blackbox)",
    }
    return [msgs[r["kind"]].format(n=r["n"]) for r in rows if r["kind"] in msgs]


def resolve_instance(s: QuerySession, ref: str) -> sqlite3.Row:
    if is_sha256_hex(ref):
        r = s.one("SELECT * FROM instances WHERE build_id=? AND instance_id=?", s.bid, ref)
        if r is not None:
            return r
    cleaned = ref.strip()
    rows = s.rows(
        "SELECT * FROM instances WHERE build_id=? AND (path=? OR hier_path=? OR TRIM(path)=? "
        "OR TRIM(hier_path)=?) ORDER BY ord",
        s.bid,
        ref,
        ref,
        cleaned,
        cleaned,
    )
    if not rows:
        raise SvwError(
            ErrorCode.MISSING_INPUT,
            f"unknown instance {ref!r}",
            {"reason": "instance_missing", "instance": ref},
        )
    if len(rows) > 1:
        raise SvwError(
            ErrorCode.AMBIGUOUS_SYMBOL,
            f"{len(rows)} instances match {ref!r}",
            {"candidates": [{"instance_id": r["instance_id"], "path": r["path"]} for r in rows]},
        )
    return rows[0]


FIT_LISTS = (
    "instances",
    "dependents",
    "dependencies",
    "connections",
    "signal_uses",
    "references",
    "ancestors",
    "descendants",
    "files",
)


def names_of(s: QuerySession, ids: set[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for did, (qn, kind) in qualified_names(s, ids).items():
        out[did] = {"declaration_id": did, "qualified_name": qn, "kind": kind}
    return out


def definition_graph(
    s: QuerySession,
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """(up, down): definition -> {parent definition: n}, definition -> {child definition: n}."""
    up: dict[str, dict[str, int]] = defaultdict(dict)
    down: dict[str, dict[str, int]] = defaultdict(dict)
    for r in s.rows(
        "SELECT i.definition_declaration_id AS d, p.definition_declaration_id AS pd, COUNT(*) AS n "
        "FROM instances i JOIN instances p ON p.build_id=i.build_id AND p.instance_id=i.parent_id "
        "WHERE i.build_id=? AND i.definition_declaration_id IS NOT NULL "
        "AND p.definition_declaration_id IS NOT NULL GROUP BY d, pd",
        s.bid,
    ):
        up[r["d"]][r["pd"]] = int(r["n"])
        down[r["pd"]][r["d"]] = int(r["n"])
    return up, down


def closure(
    graph: dict[str, dict[str, int]], start: str, depth: int
) -> list[tuple[int, str, int]]:
    """Breadth-first (level, definition, via_instances) from ``start``; each definition once, at
    its shortest level. ``start`` itself is never listed (recursive instantiation is not looped)."""
    seen = {start}
    frontier = [start]
    out: list[tuple[int, str, int]] = []
    for level in range(1, depth + 1):
        nxt: list[str] = []
        for d in frontier:
            for t, n in sorted(graph.get(d, {}).items()):
                if t in seen:
                    continue
                seen.add(t)
                nxt.append(t)
                out.append((level, t, n))
        frontier = nxt
        if not frontier:
            break
    return out


def closure_items(
    s: QuerySession, graph: dict[str, dict[str, int]], start: str, depth: int
) -> list[dict[str, Any]]:
    rows = closure(graph, start, depth)
    names = names_of(s, {d for _, d, _ in rows})
    return [
        {**names.get(d, {"declaration_id": d, "qualified_name": None, "kind": None}), "level": lv, "via_instances": n}
        for lv, d, n in rows
    ]


def instances_of(s: QuerySession, def_id: str, lim: int) -> tuple[int, list[dict[str, Any]]]:
    total = s.count(
        "SELECT COUNT(*) FROM instances WHERE build_id=? AND definition_declaration_id=?",
        s.bid,
        def_id,
    )
    paths = {
        r["instance_id"]: r["path"]
        for r in s.rows(
            "SELECT p.instance_id, p.path FROM instances p WHERE p.build_id=? AND p.instance_id IN "
            "(SELECT parent_id FROM instances WHERE build_id=? AND definition_declaration_id=? "
            "AND parent_id IS NOT NULL LIMIT ?)",
            s.bid,
            s.bid,
            def_id,
            lim,
        )
    }
    items = []
    for r in s.rows(
        "SELECT * FROM instances WHERE build_id=? AND definition_declaration_id=? ORDER BY ord LIMIT ?",
        s.bid,
        def_id,
        lim,
    ):
        d = inst_dto(r)
        items.append(
            {
                "instance_id": d["instance_id"],
                "path": d["path"],
                "parent_path": paths.get(d["parent_id"]) if d["parent_id"] else None,
                "parameters": d["parameters"],
                "location": d["location"],
            }
        )
    return total, items


def references_of(
    s: QuerySession, decl_ids: list[str], lim: int
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """(summary, sample references, per-file counts) of references to one entity (its declaration
    ids: a port and its implicit internal net are one entity)."""
    q = ",".join("?" for _ in decl_ids)
    total = s.count(
        f'SELECT COUNT(*) FROM "references" WHERE build_id=? AND target_id IN ({q})',
        s.bid,
        *decl_ids,
    )
    by_kind = {
        r["kind"]: int(r["n"])
        for r in s.rows(
            f'SELECT kind, COUNT(*) n FROM "references" WHERE build_id=? AND target_id IN ({q}) '
            "GROUP BY kind",
            s.bid,
            *decl_ids,
        )
    }
    files = [
        {"file": r["file"], "references": int(r["n"])}
        for r in s.rows(
            f'SELECT file, COUNT(*) n FROM "references" WHERE build_id=? AND target_id IN ({q}) '
            "GROUP BY file ORDER BY n DESC, file LIMIT ?",
            s.bid,
            *decl_ids,
            lim,
        )
    ]
    sample = []
    rows = s.rows(
        f'SELECT * FROM "references" WHERE build_id=? AND target_id IN ({q}) '
        "ORDER BY file, span_start LIMIT ?",
        s.bid,
        *decl_ids,
        lim,
    )
    cont = qualified_names(s, {r["container_id"] for r in rows if r["container_id"]})
    for r in rows:
        c = cont.get(r["container_id"]) if r["container_id"] else None
        sample.append(
            {
                "kind": r["kind"],
                "status": r["resolution_status"],
                "location": loads(r["loc"]),
                "container": c[0] if c else None,
            }
        )
    distinct_files = s.count(
        f'SELECT COUNT(DISTINCT file) FROM "references" WHERE build_id=? AND target_id IN ({q})',
        s.bid,
        *decl_ids,
    )
    return {"total": total, "by_kind": by_kind, "distinct_files": distinct_files}, sample, files


def scope_parents(scope: str) -> list[str]:
    """``scope`` and every enclosing scope, innermost first: ``top.g.f`` -> top.g.f, top.g, top;
    ``p::c::m`` -> p::c::m, p::c, p (generate blocks and subroutines nest inside a module)."""
    out = []
    cur = scope
    while cur:
        out.append(cur)
        cut = max(cur.rfind("."), cur.rfind("::"))
        if cut <= 0:
            break
        cur = cur[:cut]
    return out


def enclosing(s: QuerySession, scope: str | None, kinds: tuple[str, ...]) -> sqlite3.Row | None:
    """The innermost declaration of one of ``kinds`` that encloses ``scope`` (walking up through
    generate blocks, functions, tasks ...); None when there is none."""
    if not scope:
        return None
    q = ",".join("?" for _ in kinds)
    for cand in scope_parents(scope):
        r = s.one(
            f"SELECT * FROM declarations WHERE build_id=? AND qualified_name=? AND kind IN ({q}) "
            "ORDER BY span_start LIMIT 1",
            s.bid,
            cand,
            *kinds,
        )
        if r is not None:
            return r
    return None


def owner_definition(s: QuerySession, decl: sqlite3.Row) -> str | None:
    """Declaration id of the module/interface/program that owns a declaration: the innermost one
    enclosing its scope (a variable in a generate block or a function belongs to the module)."""
    r = enclosing(s, decl["scope"], ("module", "interface", "program"))
    return str(r["declaration_id"]) if r else None


def twins(rows: list[sqlite3.Row]) -> list[sqlite3.Row] | None:
    """slang declares an ANSI port as a port AND an implicit internal net at the same span. When
    every candidate has the same name and source span they are ONE entity: return them with the
    port first. Anything else stays ambiguous (None)."""
    if len(rows) < 2:
        return None
    keys = {(r["name"], r["file"], r["span_start"], r["span_end"], r["scope"]) for r in rows}
    if len(keys) != 1:
        return None
    return sorted(rows, key=lambda r: (r["kind"] != "port", r["declaration_id"]))


def with_twins(s: QuerySession, rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """A single row picked by symbol_id or location gains the other declarations at the SAME name
    span (slang's implicit net of an ANSI port), so every entry point sees one entity."""
    if len(rows) != 1:
        return rows
    r = rows[0]
    same = s.rows(
        "SELECT * FROM declarations WHERE build_id=? AND name=? AND file=? AND span_start=? "
        "AND span_end=? AND scope IS ?",
        s.bid,
        r["name"],
        r["file"],
        r["span_start"],
        r["span_end"],
        r["scope"],
    )
    return same if len(same) > 1 else rows


def fit_lists(data: dict[str, Any], budget: int, keys: tuple[str, ...] = FIT_LISTS) -> list[str]:
    """Shrink the list sections until the JSON fits ``budget``; returns the keys that were cut. A
    cut list keeps its ``*_total``/``summary`` figures, so the omission is visible."""
    cut: list[str] = []
    while len(json.dumps(data, default=str).encode("utf-8")) > budget:
        key = max(
            (k for k in keys if isinstance(data.get(k), list) and len(data[k]) > 1),
            key=lambda k: len(json.dumps(data[k], default=str)),
            default=None,
        )
        if key is None:
            break
        data[key] = data[key][: max(1, len(data[key]) // 2)]
        if key not in cut:
            cut.append(key)
    return cut


#: bound of one ``IN (...)`` list (SQLite's default host-parameter limit is far above this)
IN_CHUNK = 900


def rows_in(s: QuerySession, sql: str, ids: list[str], chunk: int = IN_CHUNK) -> list[sqlite3.Row]:
    """``sql`` has one ``{}`` for an ``IN (...)`` list and the build id as its first parameter;
    run it per chunk of ``ids`` (graph export and its atlas)."""
    out: list[sqlite3.Row] = []
    for i in range(0, len(ids), chunk):
        part = ids[i : i + chunk]
        out.extend(s.rows(sql.format(",".join("?" * len(part))), s.bid, *part))
    return out
