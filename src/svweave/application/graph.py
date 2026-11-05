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
import os
from collections import defaultdict
from typing import Any

from svweave.application.diff_impact import (
    DIFF_NOTE,
    check_ref,
)
from svweave.application.exports import (
    EXPORT_DIR,
    check_export_name,
    exports_dir,
    write_export,
)
from svweave.application.graph_atlas import DEFAULT_PAGE_BUDGET, MIN_PORTAL
from svweave.application.graph_atlas import INDEX as ATLAS_INDEX
from svweave.application.graph_atlas import build_pages as build_atlas_pages
from svweave.application.graph_codec import encode as encode_page

# the model and the diff view live in their own modules; re-exported for importers
from svweave.application.graph_diff import (  # noqa: F401
    diff_selection,
    diff_view,
)
from svweave.application.graph_model import (  # noqa: F401
    GRAPH_NOTE,
    build_model,
    resolve_root,
)
from svweave.application.graph_template import render_html
from svweave.application.options import WorkspaceOptions
from svweave.application.semantic_common import build_envelope
from svweave.application.semantic_read import (
    QuerySession,
)
from svweave.application.semantic_read import (
    gap_limits as _gap_limits,
)
from svweave.application.semantic_read import (
    session as _session,
)
from svweave.domain.envelope import Envelope, Pagination
from svweave.domain.errors import ErrorCode, SvwError
from svweave.domain.states import EvidenceKind
from svweave.security.safeio import ensure_private_dir, write_under

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


def _page_meta(s: QuerySession, limits: list[str]) -> dict[str, Any]:
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
    s: QuerySession,
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
    check_export_name(dirname, "--name")
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
    out = ensure_private_dir(exports_dir(s.ctx.state_dir), dirname)
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
            # one key for both directory outputs; `mode` says which (interface review 2)
            "mode": "lazy" if lazy else "split",
            "parts": {
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
        raise SvwError(ErrorCode.INVALID_CONFIG, "give --split OR --lazy: linked pages or one page "
                       "that loads its parts", {"field": "--lazy"})
    if page_budget is not None and not (split or lazy):
        raise SvwError(ErrorCode.INVALID_CONFIG, "--page-budget needs --split or --lazy",
                       {"field": "--page-budget"})
    if (split or lazy) and (max_instances is not None or max_fanout is not None):
        # the pages together are the complete topology; a per-page cap would silently drop
        # instances and connections of every page and is not reported per page
        raise SvwError(ErrorCode.INVALID_CONFIG,
                       "--max-instances/--max-fanout cannot be combined with --split/--lazy: "
                       "those pages are the complete topology; use --page-budget to make the "
                       "pages smaller", {"field": "--max-instances"})
    if (split or lazy) and diff_base is not None:
        raise SvwError(ErrorCode.INVALID_CONFIG,
                       "--split/--lazy apply to the complete topology or an --instance subtree, "
                       "not to a --diff-base view (a diff page is small already)",
                       {"field": "--diff-base"})
    if page_budget is not None and (not isinstance(page_budget, int)
                                    or isinstance(page_budget, bool) or page_budget < MIN_PAGE_BUDGET):
        raise SvwError(ErrorCode.INVALID_CONFIG, f"--page-budget must be at least "
                       f"{MIN_PAGE_BUDGET} instances", {"field": "--page-budget"})
    if per_commit and (diff_base is None or diff_head is None):
        raise SvwError(
            ErrorCode.INVALID_CONFIG,
            "--per-commit needs --diff-base and --diff-head (a commit range; build the "
            "--diff-head revision)", {"field": "--per-commit"},
        )
    if diff_base is not None:
        check_ref(diff_base, "--diff-base")
    if diff_head is not None:
        if diff_base is None:
            raise SvwError(ErrorCode.INVALID_CONFIG, "--diff-head needs --diff-base",
                           {"field": "--diff-head"})
        check_ref(diff_head, "--diff-head")
    if instance is not None and (not isinstance(instance, str) or not instance.strip()):
        raise SvwError(ErrorCode.INVALID_CONFIG, "--instance must be a non-empty instance path "
                       "or id", {"field": "--instance"})
    if max_instances is not None and (
            not isinstance(max_instances, int) or isinstance(max_instances, bool)
            or max_instances < 1):
        raise SvwError(ErrorCode.INVALID_CONFIG, "--max-instances must be at least 1 (omit it: "
                       "no cap)", {"field": "--max-instances"})
    if max_fanout is not None and (
            not isinstance(max_fanout, int) or isinstance(max_fanout, bool) or max_fanout < 2):
        raise SvwError(ErrorCode.INVALID_CONFIG, "--max-fanout must be at least 2 (omit it: no "
                       "hubs)", {"field": "--max-fanout"})
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
            diff, sel, dwarns = diff_view(s, diff_base, diff_head, root_row,
                                           per_commit=per_commit)
            warns += dwarns
            if sel is None:  # nothing to draw: report it, write no page
                warns.insert(0, f"nothing to draw for {diff['base']} -> {diff['head']}: "
                                f"{diff['nothing_to_draw']}; no page was written")
                return build_envelope(
                    s.ctx, s.db, s.build, request_id,
                    {"path": None, "mode": "page", "written": False, "served": False, "diff": diff,
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
        check_export_name(fname, "--name")
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
        write_export(s.ctx.state_dir, fname, data)
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
                "mode": "page",
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
