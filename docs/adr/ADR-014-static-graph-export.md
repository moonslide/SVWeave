# ADR-014: A static, self-contained HTML graph export; still no web server

- Status: Accepted
- Date: 2026-10-20
- Amends: ADR-009 (no public HTTP endpoint) and plan 4.3 (non-goal "no web UI")

## Context
`git_nexus_compare.md` lists a browsable graph UI as something GitNexus has and SVWeave lacks. The
plan (4.3) and ADR-009 forbid a web UI served over HTTP: a listening socket needs authentication,
Origin and DNS-rebinding protection and a new threat model, and the gateway is stdio only.

## Decision
`svweave graph export --build B` writes ONE self-contained HTML file to `.svweave/exports/`
(mode 0600, written with the no-follow safe writer). The page holds the data of that one build and
an inline viewer (SVG, vanilla JS, no library). It is opened from disk by the user's browser.

* No socket, no server, no new MCP tool. The gateway is unchanged and remains stdio only.
* The page makes no network access: CSP `default-src 'none'`, no external script, style or image,
  and no `fetch`, `XMLHttpRequest` or `WebSocket` in the script.
* Source-derived text is data (SEC-03): embedded as JSON with `<`, `>`, `&` and U+2028/2029
  escaped, and written into the DOM only through `textContent`.
* The export path is fixed under `.svweave/exports/`; `--name` is a bare file name
  (`[A-Za-z0-9._-]`, no leading dot). The user source tree is never written (AGENTS.md rule 8).
* Content: the elaborated instance tree (shallowest first, `--max-instances`) and connection
  edges derived from the build's direct port associations: two instances reference the same signal
  declaration, oriented by the DECLARED port direction. Signals used by more than `--max-fanout`
  instances (clock, reset) are listed as hubs instead of drawn. Parameters and genvars never form
  edges. The page repeats the build's completeness, freshness and limitations.

## Consequences
* A graph is as stale as the file: it states the build id, completeness and the freshness at
  export time. It does not update itself.
* It is not a netlist: no driver/load analysis, no bit level, no cross-hierarchy dataflow, no
  `tran`/`alias`. This is the same direct-association guarantee as `svweave query connections`.
* An interactive local server remains out of scope until a later ADR.

## Amendment (2026-11-03): subtree view, `--instance`
(Introduced as `--root`; renamed to `--instance` on 2026-11-04 because `--root` is the global
project-directory option and other commands call an instance `--instance`.)
`svweave graph export --build B --instance INSTANCE` writes the same kind of page for ONE instance and
its descendants (a path or an instance id; an unknown or ambiguous name is refused, never guessed).
It is a view of the same immutable build, not a new compile: nothing is re-elaborated and the
build id, completeness and freshness on the page are the build's.

* Only the subtree is read: the descendants are the closure of the stored `parent_id` links
  (indexed), their port bindings come by primary key and declaration kinds only for the
  declarations those bindings reference. A path-prefix match is never used to decide membership.
* Depth (collapse, `--max-instances` shallowest first) is relative to the root; the root has no
  drawn parent.
* A signal declared outside the subtree (the root's own port actuals, a hierarchical reference)
  has no drawn owner, so it is listed as a **boundary** signal (with the number of subtree
  instances that use it) instead of being drawn. The envelope warns and the page says so.
* Fan-out counts the uses inside the subtree. For a signal declared inside the subtree this equals
  the whole-build count unless an instance outside the subtree reaches it by a hierarchical
  reference in a port actual (not counted). Tests `GRF-I05` check, for every instance of the
  `grf_subtree` fixture as root, that nodes, ports, edges and hubs equal the full page restricted
  to that subtree.
* Measured on one host (`scripts/benchmark_graph_subtree.py`, see `docs/limitations.md` §8): the
  saving grows with the design; on a ~90-instance design the CLI time is Python start-up either way.

## Amendment (2026-11-04): diff view, `--diff-base` / `--diff-head`
`svweave graph export --build B --diff-base REF [--diff-head REF] [--instance I]` draws only what a
git diff changes, on the same immutable build (nothing is recompiled):

* The diff is read exactly like `svweave query diff-impact` (same fixed `git diff -U0` argv, same
  refusals, same working-tree filter policy) and its changed lines are attributed to the
  declarations of the build. Per file, the OLD side is mapped when the build's snapshot equals the
  base version (build first, then edit), else the NEW side when the snapshot equals the changed
  version (edit, then build); `files[].side` says which. A file that matches neither is mapped
  approximately and the envelope warns. `diff-impact` itself keeps the old-side mapping only.
* Every instance of a changed module/interface definition is drawn (`changed`) with its whole
  subtree (`inside`), plus the ancestors that connect them (`context`) up to `--instance` or, without
  it, up to their lowest common ancestor. Context ancestors show only their drawn children
  (`children_total` gives the real count) and only connections among drawn instances.
* Changes that map to no instance (package, class, a file not in the build, a range outside every
  declaration) are listed and warned about, never drawn. A diff that maps to no instance at all
  writes no page and answers OK with `data.written: false` and `data.diff.nothing_to_draw`
  (first shipped as a `MISSING_INPUT` refusal, which made a clean tree look like a BLOCKED build).
* The git it runs treats repository metadata as untrusted (no user/system config, no transport,
  submodules ignored, partial clones refused, bounded time and output): see diff_impact.py.
* Like `diff-impact`, STALE never changes the exit code in this mode: the workspace is expected to
  differ from the build.
* The saving depends on how local the change is: a change to a module instantiated everywhere
  draws most of the design. Measured with `scripts/benchmark_graph_subtree.py`.
* `--per-commit` (with `--diff-head`): the commits of `base..head` are listed, and each changed
  module/interface is labelled with the commits that touched its line span in the head version
  (`git log -s -L<first>,<last>:<file> base..head`, the same hardened git runner). Exact only when
  the build's snapshot of that file is the head version; otherwise the module is left unlabelled
  and the envelope warns. Labels are per definition, not per instance. An instance between two
  changed instances belongs to the outer change (`inside`), never `context`.
