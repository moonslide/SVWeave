# Second council review of SVWeave and its fixes (2026-11-04)

The same six specialists (correctness, security, policy/trust boundary, test evidence,
architecture, interface; the `claude-code-review-council` agents) reviewed the whole codebase at
`dc345e4` / `ed1d0de` (the large-SoC pages, `--per-commit` and the complete-topology default had
been added since the first review), read-only, from a clean worktree of `origin/main` reading the
branch. Every finding below was checked against the code; the fixed ones were reproduced first
(a PoC with real git, Chromium or a failing test), and each fix has a test that fails on the
previous code (shown by reverting the fix or injecting the defect).

## Fixed

| Finding | Severity | Test(s) that fail without the fix |
|---|---|---|
| `--per-commit`'s `git log` ran the repository's `gpg.program` for a signed commit when the repository config set `log.showSignature` (reproduced: marker file written) | High | SEC-DIF05 |
| A `.svweave/snapshots/<id>/files` symlink (a repository can ship `.svweave/`) made the text-search fallback walk any directory, e.g. `$HOME` | High | SEC-SNP01 |
| `snapshot.json` trusted as written (freshness, byte offsets); a reused snapshot checked only its final path component and kept the old record | Medium | SEC-SNP02/03/04 |
| An existing candidate snapshot directory was reused unverified by `edit validate` | Medium | SEC-SNP05, mutation M31 |
| Sandbox: a read-only bind of a parent of `$HOME` (or of `~/.ssh`) was accepted | Low | SEC-SBX01 |
| Graph pages admitted any inline script (`'unsafe-inline'`); now only the viewer's sha256, plus `base-uri`/`form-action 'none'` (Chromium: injected inline script blocked, viewer and `--lazy` loading work) | Low (hardening) | GRF-I17 |
| `--split`/`--lazy` with `--max-instances`/`--max-fanout` dropped content of every page unreported | Medium | GRF-I16 |
| The atlas index page said "Complete topology"; pages carried no limitations; file list cut at 200 unmarked | Medium | GRF-I15 (meta, `files_total`), Chromium check |
| A signal no instance declares (package variable) was boundary on atlas pages but drawn on the complete page | Low | GRF-I18 |
| A hunk past the end of an unaligned file covered the whole file | Low | DIF-U05 |
| "Nothing to draw" named an `--instance` that was not given | Low | none (wording only; no dedicated test) |
| Rename preview fused a last line without newline with the next (count below the edit gate) | Low | RNM-I13 |
| A BLOCKED build of an unisolated worker stored no isolation record | Low | SEC-ISO03 |
| `env_not_allowed_by_policy` gave no hint; `svweave init` policy lacked `environment_allowlist`; docs inconsistent | Medium | SEC-ENV01 (hint), SEC-ENV03 |
| Viewer optional keys and file-name guards had no contract test; `safeHref`/`safeChunk` no negative test; script/href sinks unchecked | Medium | GRF-C04, GRF-C05, GRF-U02 |
| Planner "open" branch and nested portals, stale-page removal and file modes, partial-clone refusal at its call site, per-commit limits: untested | Medium | GRF-I20, GRF-I19, SEC-DIF06, GRF-I21 |
| Docs: CHANGELOG `--root`, stale sizes (15.8 MB, old caps in zh READMEs), lazy CSP wording, `graph_atlas` docstring, per-mode envelope undocumented, `--page-budget` minimum, `--name` as directory | Low | DOC tests |
| Evidence without provenance (graph-large, C906 example); new features in no recorded run | Medium | this run; provenance sections added |

## Open

* **Decision for the project owner (policy finding 1):** the ADR-014 amendments were written on
  this branch. The owner chose the complete-topology default and the three large-SoC steps in the
  session; the `--lazy` CSP relaxation (`script-src 'self'` on its index page) is recorded as the
  branch's proposal and needs the owner's explicit sign-off before merge.
* **Decision for the project owner (security finding 2, rest):** a `.svweave/svweave.db` shipped
  in a repository is still opened as the workspace state, so its build rows would answer queries.
  The exploitable path (walking a planted symlink) is closed and records are checked against
  their identity, but trusting the database needs a design change (e.g. a marker signed with the
  trusted-directory key, with an adoption step for existing state directories).
* Architecture findings (not changed here): `graph.py` holds four concerns; `graph`/`graph_atlas`
  import each other; the viewer is a non-raw Python string; node schema defined in four places;
  the atlas search index is a third path format; `collect_changes` returns an untyped dict;
  export naming duplicated with rename; `s: Any` in graph code; path-string signal ownership;
  no Python reader for written pages.
* Interface, remaining: `INVALID_CONFIG` messages for `per_commit`, `diff_base`/`diff_head`,
  `max_instances`/`max_fanout` and `instance` still use Python parameter names; the
  `graph export` command docstring and README CLI-table row still describe one `.html` file;
  `svweave doctor` has no check comparing profile and policy `environment_allowlist`;
  `data.atlas` vs `data.lazy` keys kept (documented) rather than unified.
* Test evidence, remaining: the viewer's interactive behaviour (chunk merge, search, `#f=`
  focus, commit filter) has no automated browser test, only the recorded Chromium checks;
  `node` (GRF-C03/C04/C05, GRF-U05) is not pinned in the lock.
* Items left open by the first review are unchanged (see `../council-review-2026-11-04/`).

## Verification: [`run3/`](run3/) at `4491a54`

One 4-CPU cloud session (not CI), `SVWEAVE_REQUIRE_BACKENDS=1` for every pytest step, real
pyslang 12 under mandatory bwrap, real codebase-memory-mcp, Serena and UVM verified from the lock
(`run3/lock_sha256.txt`). `run3/commands.txt` has every command, `run3/steps.txt` its exit code
and wall time, `run3/junit-*.xml` every test.

| Step | Result |
|---|---|
| ruff, mypy (strict), `svweave schema emit --check`, `bootstrap_locked.py --verify-only` | rc 0 |
| unit + contract | 1756 tests: **1752 passed**, 0 failed; 4 **BLOCKED** (below) |
| fault injection | 20 passed |
| integration (real backends) | 233 passed, 0 skipped |
| acceptance | 12 passed |
| mutation suite | **31/31 KILLED**, 0 survived, 0 errors (`run3/mutation-2026-11-04.json`) |
| backend processes left behind | none (`leftover_processes.txt` empty) |

BLOCKED, never PASS (the same four as run 2): `test_f10_python_bind_is_narrowed_to_exe_stdlib_and_libpython`
(the interpreter is under /usr), `test_DEP_01_actual_wheel_contains_byte_identical_runtime_assets`
and `test_DEP_01_actual_sdist_retains_wheel_asset_sources` (need hatchling for an offline
wheel/sdist build), `test_N6_unreadable_lookup_dir_raises_instead_of_dropping_subtree` (as root a
directory cannot be made unreadable). `SVWEAVE_REQUIRE_BACKENDS=1` reports a skipped required
test as a failure by design, hence `unit_contract rc=1`.

A first start of this run at `fce444e` was stopped by hand after its first steps, to add the
missing tests above; its partial output was overwritten, nothing of it is reported.

After run 3, only documentation changed (README env-allowlist wording, the per-mode envelope
paragraph, Status sections, this file); the DOC tests (`tests/unit/test_gateway_cli.py -k DOC`)
pass on it. Not a `scripts/qualify.py` qualification run.
