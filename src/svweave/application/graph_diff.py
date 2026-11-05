"""The graph diff view: which instances a git diff changes (:func:`diff_selection`), the
per-commit labels (``git log -L``) and the diff summary of a page. Split out of graph.py
(second council review, architecture finding 1)."""

from __future__ import annotations

import bisect
from typing import Any

from svweave.application.diff_impact import (
    MAX_RANGE_COMMITS,
    Changes,
    collect_changes,
    commits_in_range,
    commits_touching,
)
from svweave.application.graph_model import chunked_rows, descendants
from svweave.application.semantic_read import (
    QuerySession,
)
from svweave.application.semantic_read import (
    names_of as _names,
)
from svweave.snapshot.capture import read_snapshot_file


def diff_selection(s: QuerySession, definition_ids: set[str], root_row: Any = None) -> dict[str, Any]:
    """The instances a diff view draws: every instance of a changed definition (``changed``) with
    its whole subtree (``inside``), plus the ancestors that connect them (``context``) up to
    ``root_row`` or, without one, up to their lowest common ancestor. Instances of a changed
    definition outside ``root_row`` are counted, not drawn. Everything is read by index: the
    changed instances by definition, ancestors by id, descendants by parent_id."""
    changed = chunked_rows(
        s,
        "SELECT * FROM instances WHERE build_id=? AND definition_declaration_id IN ({})",
        sorted(definition_ids),
    )
    known: dict[str, Any] = {str(r["instance_id"]): r for r in changed}
    todo = {str(r["parent_id"]) for r in changed if r["parent_id"] is not None} - set(known)
    while todo:  # ancestor rows, one level per round
        got = chunked_rows(s, "SELECT * FROM instances WHERE build_id=? AND instance_id IN ({})",
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
    inside = descendants(s, sorted(kept))
    context = sorted(i for i, m in marks.items() if m == "context")
    counts = {
        str(r["parent_id"]): int(r["n"])
        for r in chunked_rows(
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


MAX_TRACED_DEFINITIONS = 200


def _line_of(starts: list[int], offset: int) -> int:
    """1-based line of byte ``offset`` given the byte offsets where lines start."""
    return bisect.bisect_right(starts, offset)


def _per_commit(
    s: QuerySession, ch: Changes, base: str, head: str, defs: set[str]
) -> tuple[list[dict[str, Any]], dict[str, list[int]], list[str]]:
    """Which commits of base..head changed each changed definition: git's line-history tracing
    (``log -L``) over the definition's own line span in the HEAD version, which is exact only
    when the build's snapshot of that file IS the head version (files[].side == "new"). Other
    definitions stay unlabelled, with a warning; nothing is guessed."""
    warns: list[str] = []
    commits, cut = commits_in_range(ch.top, base, head)
    index = {c["sha"]: i for i, c in enumerate(commits)}
    if cut:
        warns.append(f"more than {MAX_RANGE_COMMITS} commits in {base}..{head}: only the oldest "
                     f"{MAX_RANGE_COMMITS} are listed and attributed")
    exact = {f["logical_path"]: f["path"] for f in ch.files
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
            data = read_snapshot_file(ch.snap_dir, ch.by_logical[lf])
            starts_of[lf] = [0] + [i + 1 for i, b in enumerate(data) if b == 0x0A]
        st = starts_of[lf]
        lo, hi = int(r["lo"]), int(r["hi"])
        first, last = _line_of(st, lo), _line_of(st, max(hi - 1, lo))
        found = commits_touching(ch.top, base, head, exact[lf], first, last)
        labels[d] = sorted({index[sha] for sha in found if sha in index})
    if unlabelled:
        warns.append(
            f"{len(unlabelled)} changed definition(s) carry no commit labels: per-commit "
            "attribution needs the build's snapshot of their file to be the --diff-head version "
            f"(build that revision), and at most {MAX_TRACED_DEFINITIONS} definitions are traced"
        )
    return commits, labels, warns


def diff_view(
    s: QuerySession, base: str, head: str | None, root_row: Any, *, per_commit: bool = False
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
    """git diff -> changed definitions -> the instances to draw (:func:`diff_selection`). The
    selection is None when the diff reaches no instance (nothing to draw is an answer, not an
    error: the build itself is not blocked)."""
    ch = collect_changes(s, base=base, head=head, repo=None, new_side=True)
    defs = set(ch.by_def)
    files = [{"path": f["path"], "side": f.get("side"), "aligned": f.get("aligned")}
             for f in ch.files]
    summary = {
        "files_changed": len(ch.diffs),
        "files_in_build": len(ch.files),
        "files_not_in_build": len(ch.unmapped),
        "non_instance_declarations": len(ch.non_instance),
        "unattributed_ranges": len(ch.unattributed),
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
        for r in chunked_rows(
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
          "changed_declarations": ch.by_def[d]["declarations"],
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
        "unmapped_files": [u["path"] for u in ch.unmapped][:200],
        "non_instance_declarations": [d["qualified_name"] for d in ch.non_instance][:200],
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
    if ch.unmapped:
        warns.append(f"{len(ch.unmapped)} changed file(s) are not part of this build and were "
                     "not drawn")
    if ch.non_instance:
        warns.append(f"{len(ch.non_instance)} changed declaration(s) belong to no "
                     "module/interface (package, class, free subroutine) and are listed, not drawn")
    if ch.unattributed:
        warns.append(f"{len(ch.unattributed)} changed range(s) fall outside every declaration")
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
