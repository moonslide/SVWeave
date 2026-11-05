# Architecture as implemented

Layering: thin surfaces (`cli.py`, `server.py`, `launcher.py`) -> `gateway/` (MCP contract) ->
`application/` (use cases, one function per externally visible operation) -> `editing/` (the
controlled write path), `adapters/` and `orchestration/` (backends), `execution/` (local and Slurm
process control), `snapshot/`, `manifest/`, `config/`, `storage/` (SQLite), `security/` and
`domain/`. The CLI and the MCP tools call the same functions in `application/services.py`
(builds and updates included: `services.build_sync` / `update_sync` and the MCP job runner share
`evidence.post_build`).

Allowed dependency directions (pinned by `tests/unit/test_architecture_leftovers.py`, ARC-01):

* `domain/` imports nothing of SVWeave above it; `config/`, `snapshot/`, `storage/` and
  `security/` import only `domain/` and each other (`storage/paths.py` is the leaf that names the
  database).
* `editing/` (write safety: candidate tree hashing `tree.py`, the exact-range patch
  `exact_range.py`, approval, journal, CAS) never imports `application/`, `gateway/` or a backend
  adapter. Backend adapters import `editing/` (the Serena guard re-exports its helpers), never
  the reverse.
* `adapters/` never imports `orchestration/`; `orchestration/` composes the adapters (the
  toolchain and capability models live in `adapters/toolchain.py` and `adapters/capability.py`,
  re-exported by `orchestration/`).
* `execution/` imports `domain/`, `storage/` and `security/` only; `application/` drives it.
* Envelope redaction is `security/redaction.py` (shared by CLI and MCP, no gateway import);
  the one hardened git runner for untrusted repositories is `security/git.py`.

```
src/svweave/
  cli.py                  click CLI: parses argv, calls services, prints ONE envelope (usage errors
                          included), exit codes 15.4
  server.py, launcher.py  MCP stdio server; the global `svweave-mcp` launcher (workspace binding)
  gateway/                MCP schemas of the 14 tools, ToolSpec registry, validation, dispatch
  application/
    services.py           one function per use case used by CLI + MCP
    semantic_read.py      the shared query core: QuerySession, row converters, instance and
                          declaration-target resolvers, gap limitations, definition-graph helpers
    queries.py, impact.py, diff_impact.py, rename.py   semantic tools built on semantic_read
    graph.py              graph export orchestration (pages, atlas, lazy parts; ADR-014)
    graph_model.py        rows and build_model (nodes, edges, hubs, boundary signals)
    graph_diff.py         diff selection, per-commit labels, diff summary
    graph_atlas.py        the atlas cut and page files; graph_codec.py the svweave-graph/2 format,
                          the search index and read_page/read_chunk; graph_template.py the page
                          and its CSP; graph_viewer.js the viewer itself
    exports.py            .svweave/exports/: directory, name rule, private writes
    build.py, update.py, update_git.py, jobs.py, use_cases.py, edit.py, evidence.py, report.py,
    status.py, doctor.py, schemas.py (schema emission), init.py, session.py, ...
  editing/                candidate, plan, tree hashing, exact-range patch, approval, journal,
                          confirmation, diffing, state machine (rule 8)
  execution/              local and Slurm execution: ledger, recovery, supervisor, transport
  domain/                 states, error catalog + exit codes, ids, envelope, cursor, locations,
                          doctor check results
  config/                 manifest/policy/local models, loader, trusted dir (policy, cursor key,
                          the recorded state directory), toolchain lock model and reader
  manifest/, snapshot/    profile resolution (ledger) and immutable snapshots
  storage/                SQLite (db.py, migrations, semantic.py), locks, GC, paths.py
  adapters/slang/         pyslang worker (separate process), runner (process-tree cancel), ingest
  adapters/codebase_memory, adapters/serena, adapters/verilator, adapters/mcp_client.py
                          upstream backends; adapters/toolchain.py, adapters/capability.py
  orchestration/          backend registry, health, doctor backend checks
  security/               bwrap sandbox, redaction (incl. envelopes), safe IO, git runner
```

Data flow of `svw_build`: `Gateway.call` validates the arguments -> `services.build` ->
`JobManager.submit_build` (jobs row `QUEUED`, thread) -> `run_build` (manifest -> snapshot ->
worker -> staged publish; row `RUNNING` -> terminal) -> result stored in `jobs.result`, static
evidence recorded. `svw_job` reads the table; `cancel` calls `WorkerHandle.cancel()` which kills the
worker process group.

Storage added in M3 (migration v3): `jobs.{kind, profile, request_id, owner_pid, created_at,
result, cancel_requested}` and `evidence.{evidence_type, profile, test_name, seed, run_id,
tool_version, link_state, link_reason, claimed_snapshot_id, claimed_build_id, record,
imported_at}`.

Trust boundaries: the trusted dir (outside the repo) holds `policy.json` and `cursor.key`; the
repo-local `.svweave/` holds runtime state only (snapshots, DB, runs, bundles, backend state).
Backends are started only through the sandbox runner and only on first real use.
