"""``svweave graph export --split``: the complete topology as an atlas of linked pages.

A page that holds a whole large SoC is too big for a browser (about 7.6 KB per instance before
compaction, under 1 KB after; a 115k-instance design is still a 40.9 MB page that opens in about
10 s, evidence/bench/graph-large/). The atlas cuts the
instance tree into pages of at most ``budget`` instances. A subtree that does not fit becomes a
PORTAL: it stays on its parent page as one node, linked to its own page, where it is the root.

Why the pages together are exactly the complete topology: an edge joins an instance and a signal
declared in the scope that instantiates it (its parent) or a sibling sharing that signal. A
portal's own port associations reference its parent's scope, so they are drawn on the parent
page; everything below the portal references the portal's scope or deeper, so it is drawn on the
portal's page. No edge is drawn twice and none is lost (tests compare the union with the single
complete page). A hierarchical reference across pages is listed as a boundary signal. The one
exception, stated on every page (graph.ATLAS_NOTE): a signal no instance declares (a package
variable) connecting instances on different pages; each page draws its own pairs only.

Everything is one build: nothing is recompiled. ``--split`` links are plain relative links between
files of one directory: no server, no script loading, the same strict CSP as a single page.
``--lazy`` loads ``c-<hash>.js`` data files of the same directory (its CSP adds 'self'; ADR-014).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from svweave.application.graph_codec import encode as encode_page
from svweave.application.graph_template import dumps_safe, render_html
from svweave.application.semantic_read import QuerySession, rows_in

INDEX = "index.html"
DEFAULT_PAGE_BUDGET = 5000
#: a subtree smaller than this is never its own page (no page of three instances): its parent
#: page may then exceed the budget, which the envelope reports
MIN_PORTAL = 50


@dataclass
class Page:
    file: str
    roots: list[str]
    parent: int | None
    members: list[str] = field(default_factory=list)
    portals: list[str] = field(default_factory=list)


def page_file(root_path: str) -> str:
    return "p-" + hashlib.sha256(root_path.encode()).hexdigest()[:10] + ".html"


def chunk_file(root_path: str) -> str:
    return "c-" + hashlib.sha256(root_path.encode()).hexdigest()[:10] + ".js"


def plan(rows: list[Any], start: list[str], budget: int) -> tuple[list[Page], dict[str, int]]:
    """Cut the tree under ``start`` into pages. ``rows``: (instance_id, parent_id, path, ord) of
    at least every instance under ``start``, in pre-order. Returns the pages (index first) and
    the subtree size of every instance."""
    kids: dict[str, list[str]] = defaultdict(list)
    path: dict[str, str] = {}
    order: dict[str, int] = {}
    for r in rows:
        iid = str(r["instance_id"])
        path[iid] = str(r["path"])
        order[iid] = int(r["ord"]) if r["ord"] is not None else 0
        if r["parent_id"] is not None:
            kids[str(r["parent_id"])].append(iid)
    size: dict[str, int] = {}
    for r in reversed(rows):  # pre-order reversed: children before parents
        iid = str(r["instance_id"])
        size[iid] = 1 + sum(size[c] for c in kids.get(iid, ()))

    pages: list[Page] = []
    todo: list[tuple[list[str], int | None]] = [(start, None)]
    while todo:
        roots, parent = todo.pop(0)
        page = Page(INDEX if parent is None else page_file(path[roots[0]]), roots, parent)
        members = list(roots)

        def cost(c: str) -> int:
            """What ``c`` costs this page at least: a subtree too small to be a page is always
            on it whole; a larger one at least as one (portal) node."""
            return size[c] if size[c] < MIN_PORTAL else 1

        frontier = [c for r in roots for c in kids.get(r, ())]
        used = len(members) + sum(cost(c) for c in frontier)
        portals: list[str] = []
        queue = sorted(frontier, key=lambda c: order[c])  # shallowest, then source order first
        i = 0
        while i < len(queue):
            c = queue[i]
            i += 1
            if size[c] < MIN_PORTAL:
                _take(c, kids, members)  # already counted whole
            elif used - 1 + size[c] <= budget:
                used += size[c] - 1
                _take(c, kids, members)  # the whole subtree fits
            elif used + sum(cost(k) for k in kids.get(c, ())) <= budget:
                used += sum(cost(k) for k in kids.get(c, ()))
                members.append(c)  # open it: its children join the frontier
                queue.extend(sorted(kids.get(c, ()), key=lambda x: order[x]))
            else:
                members.append(c)
                portals.append(c)  # no room: its own page, a portal here
        page.members = sorted(members, key=lambda x: order[x])
        page.portals = portals
        pages.append(page)
        index = len(pages) - 1
        todo.extend(([p], index) for p in portals)
    return pages, size


def _take(c: str, kids: dict[str, list[str]], out: list[str]) -> None:
    """Append ``c`` and its whole subtree to ``out``."""
    stack = [c]
    while stack:
        x = stack.pop()
        out.append(x)
        stack.extend(kids.get(x, ()))


def search_index(rows: list[Any], pages: list[Page]) -> dict[str, Any]:
    """Every instance of the atlas -> the page that shows it (a portal is found on its own page,
    where it is the root). Columns over ``rows`` order: parent position (-1: path stored in
    full), path relative to the parent's path (interned in ``s``), page index into ``pages``."""
    home: dict[str, int] = {}
    for i, pg in enumerate(pages):
        for m in pg.members:
            home.setdefault(m, i)
        for r in pg.roots:
            home[r] = i
    pos: dict[str, int] = {}
    paths: dict[str, str] = {}
    strings: list[str] = []
    sidx: dict[str, int] = {}
    cols: dict[str, list[int]] = {"p": [], "r": [], "pg": []}
    for r in rows:
        iid = str(r["instance_id"])
        if iid not in home:
            continue
        own = str(r["path"])
        parent = str(r["parent_id"]) if r["parent_id"] is not None else None
        if parent in pos and own.startswith(paths[parent] + "."):
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
        cols["pg"].append(home[iid])
    return {"pages": [pg.file for pg in pages], "parent": [-1 if pg.parent is None else pg.parent
                                                          for pg in pages], "s": strings, **cols}


def build_pages(
    s: QuerySession,
    start_rows: list[Any],
    *,
    budget: int,
    meta: dict[str, Any],
    model_of: Any,
    lazy: bool = False,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    """All pages of the atlas under ``start_rows`` as {file name: bytes}, plus a summary.
    ``model_of(region_rows)`` builds one page model (graph.build_model with region_rows).

    ``lazy``: ONE page (index.html, the top part) plus one data file per other part
    (``c-<hash>.js``, ``svwChunk(<page data>);``) that the page loads into the same drawing when
    a portal is opened. Same cut, same disjoint parts; only the index page's CSP also admits
    scripts from its own directory ('self'), which the loading needs on file://."""

    light = s.rows("SELECT instance_id, parent_id, path, ord FROM instances WHERE build_id=? "
                   "ORDER BY ord", s.bid)
    start = [str(r["instance_id"]) for r in start_rows]
    if len(start) == 1 and start_rows[0]["parent_id"] is not None:  # an --instance atlas
        keep = _subtree(light, start[0])
        light = [r for r in light if str(r["instance_id"]) in keep]
    pages, size = plan(light, start, budget)
    files: dict[str, bytes] = {}
    crumbs: list[list[list[str]]] = []
    summary: dict[str, Any] = {"pages": len(pages), "budget": budget, "largest_page": 0,
                               "oversized_pages": 0}
    path_of = {str(r["instance_id"]): str(r["path"]) for r in light}
    if lazy:
        for pg in pages[1:]:
            pg.file = chunk_file(path_of[pg.roots[0]])
    file_of_root = {pg.roots[0]: pg.file for pg in pages[1:]}
    total = sum(size[x] for x in start)
    for i, pg in enumerate(pages):
        if lazy:
            region = rows_in(s, "SELECT * FROM instances WHERE build_id=? AND instance_id IN ({})",
                             pg.members)
            model = model_of(region)
            portal_ids = set(pg.portals)
            for n in model["nodes"]:
                if n["id"] in portal_ids:
                    n["portal"] = {"chunk": file_of_root[n["id"]], "instances": size[n["id"]]}
            if i == 0:
                data = {"meta": {**meta, "lazy": {"parts": len(pages), "budget": budget,
                                                  "instances_total": total}},
                        **model, "atlas_search": search_index(light, pages)}
                files[pg.file] = render_html(encode_page(data), lazy=True).encode("utf-8")
            else:
                data = {"meta": {"chunk": pg.file, "root": path_of[pg.roots[0]]}, **model}
                files[pg.file] = ("svwChunk(" + dumps_safe(encode_page(data)) + ");\n").encode()
            summary["largest_page"] = max(summary["largest_page"], len(pg.members))
            summary["oversized_pages"] += len(pg.members) > budget
            continue
        trail = (crumbs[pg.parent] if pg.parent is not None else []) + [
            [path_of[pg.roots[0]] if pg.parent is not None else "index", pg.file]]
        crumbs.append(trail)
        region = rows_in(s, "SELECT * FROM instances WHERE build_id=? AND instance_id IN ({})",
                         pg.members)
        model = model_of(region)
        portal_set = set(pg.portals)
        for n in model["nodes"]:
            if n["id"] in portal_set:
                n["portal"] = {"href": page_file(n["path"]), "instances": size[n["id"]]}
        page_meta = {**meta, "atlas": {"crumbs": trail, "page": i + 1, "pages": len(pages),
                                       "budget": budget}}
        if pg.parent is not None:
            page_meta["root"] = path_of[pg.roots[0]]
        data = {"meta": page_meta, **model}
        if i == 0:
            data["atlas_search"] = search_index(light, pages)
        files[pg.file] = render_html(encode_page(data)).encode("utf-8")
        summary["largest_page"] = max(summary["largest_page"], len(pg.members))
        if len(pg.members) > budget:
            summary["oversized_pages"] += 1
    summary["bytes_total"] = sum(len(b) for b in files.values())
    summary["largest_file_bytes"] = max(len(b) for b in files.values())
    return files, summary


def _subtree(rows: list[Any], root: str) -> set[str]:
    keep = {root}
    for r in rows:  # pre-order: a parent is seen before its children
        if r["parent_id"] is not None and str(r["parent_id"]) in keep:
            keep.add(str(r["instance_id"]))
    return keep
