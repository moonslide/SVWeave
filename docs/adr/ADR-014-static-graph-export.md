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

## Amendment (2026-11-03): subtree view, `--root`
`svweave graph export --build B --root INSTANCE` writes the same kind of page for ONE instance and
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
