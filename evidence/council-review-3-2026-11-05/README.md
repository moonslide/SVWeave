# Third council review of SVWeave (2026-11-05)

The same six specialists (correctness, security, interface, architecture, test evidence,
policy/trust boundary; the `claude-code-review-council` agents) reviewed the whole codebase at
`41e99df` (after the follow-up of `../council-review-followup-2026-11-05/`, verified in its run5),
read-only, from a clean worktree of `origin/main` whose `AGENTS.md` was the trusted policy. The
reviewers could not run commands; the findings marked **reproduced** or **confirmed in code** were
checked by the author afterwards, the others are the reviewers' code reading.

**Status: reported, not fixed yet.** This round recorded the findings (the project owner asked
for test, review, README update and push); fixes follow separately.

## High

| # | Finding | Where | Check |
|---|---|---|---|
| H1 | The hardened git runner does not disable repository hooks: `svw_diff_impact` (read-only MCP tool) and `graph export --diff-base` run `.git/hooks/post-index-change` when a working-tree diff refreshes the index, for a repository delivered with its `.git/` (tarball, copied checkout; a `git clone` never carries hooks) | `src/svweave/security/git.py` (`HARDENED_CONFIG` lacks `core.hooksPath`) | **reproduced** with real git 2.43: marker written; adding `core.hooksPath=/dev/null` stops it |
| H2 | `svweave init` records `<--root>/.svweave` in the trusted dir of `--root`, while every other command verifies `<project_root>/.svweave` in the trusted dir of the manifest directory: with `project.root` other than `.` the two flip the single record and `init --adopt-state` adopts the wrong directory (upgrade and fresh projects can get stuck) | `application/init.py:45-64`, `config/context.py:140`, `config/trusted.py` | **confirmed in code** (correctness, interface and architecture reviewers independently) |
| H3 | When the workspace fails to load (including the new `state_dir_not_adopted` refusal) `doctor` builds its backend registry under `<root>/.svweave/backends`; with `--probe` it runs `rmtree`/`copytree` there, i.e. in the refused directory | `orchestration/doctor_checks.py:108-117`, `orchestration/registry.py:340-349` | **confirmed in code** |
| H4 | The ADR-014 record of the owner's approval of the `--lazy` CSP relaxation is written by the branch itself, with no verifiable by/date/ref record (the approval was given by the owner in the session of 2026-11-05; it needs a verifiable form, e.g. the PR approval) | `docs/adr/ADR-014-static-graph-export.md` | policy reviewer (trust boundary) |

## Medium

| # | Finding | Where |
|---|---|---|
| M1 | The state-dir record vouches for the directory (path, device, inode), not its contents: files a repository writes into a registered `.svweave/` (e.g. `git pull` overwriting ignored files that upstream force-added) are trusted. `edit recover --rollback` writes the journal's base bytes without the approval, editing-enabled, policy or journal-matches-transaction checks roll-forward has, and builds the path from an unvalidated `base_snapshot_id` (**confirmed in code**; the exploit chain is not reproduced) | `config/trusted.py`, `application/edit.py:1410-1419, 1518-1529` |
| M2 | The identity uses `st_dev`/`st_ino`: not stable on NFS mounts shared by login nodes, remounts or btrfs subvolumes (false refusals); inode reuse after `rm -rf && clone` can admit a shipped `.svweave/` | `config/trusted.py:86-88` |
| M3 | `qualify.py`'s dirty-tree gate passes when git fails (no checkout, dubious ownership): the uncommitted list is empty and nothing blocks (**confirmed in code**) | `scripts/qualify.py:769` |
| M4 | `svweave schema emit` forces exit 0 for every envelope without `data.out_of_sync`, so INTERNAL and POLICY_DENIED errors exit 0 (**confirmed in code**; older than the follow-up) | `src/svweave/cli.py:382` |
| M5 | On the MCP path (`serve`, `svweave-mcp`) the `state_dir_not_adopted` refusal prints only the message, not the `--adopt-state` hint; the message is untrue after an upgrade; doctor labels it `policy_tightening` | `cli.py`, `launcher.py`, `config/trusted.py`, `application/doctor.py` |
| M6 | The write-side no-follow for external roots is untested: SEC-EXT02 plants the link before the check, so reverting `write_target` alone still passes; M33 reverts three sites at once; the recovery writer is not exercised with an external root | `tests/unit/test_external_root_writes.py`, `scripts/mutation_suite.py` |
| M7 | `docs/architecture.md` says ARC-01 pins its dependency rules; ARC-01 checks nine module pairs and cannot see lazy imports, and `storage` ↔ `execution` and `execution` → `adapters/config/manifest/snapshot` already break the stated rules | `docs/architecture.md`, `tests/unit/test_architecture_leftovers.py` |
| M8 | The PARTIAL-build "absence is not proof" caveat exists only in the graph's `resolve_root`, not in the shared resolvers used by impact, hierarchy, connections, assertions and rename (rule 5) | `application/semantic_read.py`, `graph_model.py:95-111` |
| M9 | Trusted-directory files have four owners; the cursor key is minted with weaker hardening than the approval key | `application/keys.py`, `editing/approval.py`, `application/session.py`, `config/trusted.py` |
| M10 | `queries.py` is still a shared helper module; the 128 KiB cap is fitted by five routines; private cross-module access remains in `snapshot/incremental.py` (7 capture internals), `editing/plan.py` (`_governing_root`), `execution/` (`ExecutionLedger._open`) | listed files |
| M11 | The state directory's permissions are not checked or tightened on registration or adoption (0775/0777 project spaces) | `config/trusted.py`, `application/init.py` |

## Low and informational

* Correctness: safeio reports a missing parent as POLICY_DENIED (dead `FileNotFoundError` branches); the candidate snapshot is unprotected between derivation and its build; `svweave`/`svweave query` with no arguments print an error envelope whose message is the whole help text; the post-apply publish loop can leave an earlier profile published if computing old freshness raises; `report._git_commit` now ignores a global `safe.directory`; the default commit key built from `--patch-hash` reports a malformed hash as a bad `request_key`; per-pid temp name in `register_state_dir`; a regular file named `.svweave` raises NotADirectoryError.
* Security: the trusted directory's location/owner is not checked for the state record and cursor key; Click usage-error envelopes are not redacted; `.svweave/svweave.db` and `lock` are opened following links; some snapshot readers (`rename`, `diff_impact`, `graph_diff`, `queries` lsp positions) follow links and skip the hash check.
* Interface: docs and CHANGELOG miss several consumer-visible changes (upgrade step `init --adopt-state`, command tables, troubleshooting rows, usage-error envelopes, doctor exit selection, `schema emit` error, request-key validation, `--online`); `docs/mcp_contract.md` lacks the dependency_leak exit 3; `details.param` of usage errors holds Python names; some CLI-only messages still name Python parameters; read-only commands now create `.svweave/` and write the trust record.
* Architecture: two git runners with drifting overrides and three ref grammars; re-export shims are still the main import paths (and ARC-05 pins one); graph module seams (chunk framing and naming spelled in several places, the model depends on the codec for `owner_of`); dead code (`collapse_twins=False`, `build.cancel_job`, `default_state_dir`); `read_lock` returns the raw dict; the browser suite duplicates node detection and imports private test helpers; CLI edit commands bypass `services`.
* Test evidence: "every fix has a test that fails on the previous code" is overbroad (coverage tests GRF-I22..I27, SEC-DIF07 pass on the old code; several pins are source-text checks); weak assertions (IF-04 one order, IF-02 no reason, ARC-03 does not call `Session.close`, SEC-SNP06 does not cover the publish lock); ARC-01 runs `python -I` with `PYTHONPATH`, which `-I` ignores; run5's `commands.txt` omits the exported `SVWEAVE_REQUIRE_BACKENDS=1 SVWEAVE_REQUIRE_NODE=1`, no `git status` and no node/Playwright versions are recorded.
* Policy: the branch edits `AGENTS.md` (count, map, a browser command needing unpinned tools) and interprets rule 9 for maintainer tools in `docs/security.md` (human confirmation); `graph.py`'s docstring and ADR-009 still say "self-contained"; the run summaries say "0 failed" where JUnit records the 4 BLOCKED tests as 3 failures and 1 error (by design under `SVWEAVE_REQUIRE_BACKENDS=1`); graph-large figures have prose and a script but no raw output; the C906 example's license was not verified.

## Checked and found sound (reviewers' summary)

The semantic_read refactor keeps the old semantics; `editing/tree.py` and `editing/exact_range.py` are byte-identical moves; graph split, codec, search index and page reader are consistent; the viewer hash and CSP are correct; the request-key LIKE escaping, the per-profile publish, the no-follow read helpers, snapshot record validation, search's real-directory check, the gpg/fsmonitor/transport overrides, the sandbox bind rules, the doctor ranking, `--no-baseline`, the benchmark budget recheck and `gateway_lock_refresh.py --online` hold. run5's JUnit files contain every new test; M32–M40 are real kills with green baselines.
