# Council review of SVWeave and its fixes (2026-11-04)

A six-specialist review (correctness, security, policy/trust boundary, test evidence,
architecture, interface; the `claude-code-review-council` agents) of the whole codebase at
`2acb116`, read-only. Findings were checked against the code; the ones fixed below were each
reproduced first (a PoC or a failing test) and each fix has a test that fails on the previous code.

## Fixed

| Finding | Severity | Fix commit message | Test(s) that fail without it |
|---|---|---|---|
| Repository git config could run a command on the host (partial-clone lazy fetch via `remote.<x>.uploadpack`) from diff-impact / graph `--diff-base` | High | Treat repository metadata as untrusted in diff-impact's git runner | SEC-DIF01 |
| git timeout not enforced while git is silent (2 s took 30 s); children not killed | Medium | same | SEC-DIF03 |
| `diff.mnemonicPrefix`/`srcPrefix`/`noprefix` made every changed file "not in build" | Medium | same | SEC-DIF02 |
| New binary file reported as `/dev/null` | Low | same | SEC-DIF04 |
| Repository `svweave.yaml` could read any environment variable and echo it to the MCP client | Medium | Bound filelist environment expansion by the trusted policy (**breaking**) | SEC-ENV01/02 |
| Roll-forward judged only the first 20 changes | Medium | Fix roll-forward gate truncation ...; Keep _stale as the single freshness seam ... | `test_a3_a_foreign_change_behind_twenty_own_changes_is_still_refused`, mutation M30 |
| A build without bwrap isolation left no record | Medium | Fix roll-forward gate truncation, record degraded builds ... | SEC-ISO01/02 |
| Rename preview halved changed lines (ready, then refused by prepare) | Medium | same | RNM-I12 |
| `pagination.truncated` false on cut diff-impact / graph lists | Medium | Fix graph export and diff-impact interface findings | DIF-I07, GRF-I11 |
| Graph export: clean tree reported BLOCKED; `data.diff.files` missing; `instances_drawn` before truncation; `--root` name clash; doc/help/count errors; evidence CPU | Medium/Low | same | GRF-I07/I10/I11 |

Not fixed here (reported, not verified line by line, left for follow-up): the architecture
findings (private cross-module helpers, three target resolvers, three git runners, write-safety
code under the Serena adapter, layer cycles), the remaining interface items (Click usage errors
without an envelope, `schema emit --check` without `error`, `doctor` exit code by check order,
`PATH_NOT_ALLOWED` 3 vs 6), and policy items (qualify on a dirty tree, G5 trusting row labels,
`--no-baseline`, report listing files that may not exist, `schema emit` writing via
`write_text`, two networked maintainer scripts). Lower-severity correctness items: publish under
the wrong profile for shared build ids, candidate snapshot without the workspace lock, CLI
request key not validated.

## Verification

Both runs: one 4-CPU cloud session (not CI), `SVWEAVE_REQUIRE_BACKENDS=1` for every pytest step,
real pyslang 12 under mandatory bwrap, real codebase-memory-mcp, Serena and UVM (installed and
verified from the lock; UVM rebuilt from its git tag with the locked sha256, docs/install.md).

* [`run1-regression/`](run1-regression/README.md) at `d431b42`: **FAILED**. It found a
  regression in this work (two defence-in-depth CAS tests; M05/M06/M08 red baseline), fixed in
  `a3777ff`.
* [`run2/`](run2/) at `a3777ff` (`git_head.txt`; the later commits change only evidence and
  docs): `steps.txt` has every command's exit code and wall time, `junit-*.xml` every test.

| Step | Run 2 |
|---|---|
| ruff, mypy (strict), `svweave schema emit --check`, `bootstrap_locked.py --verify-only` | rc 0 |
| unit + contract | 1741 tests: **1737 passed**, 0 failed; 4 **BLOCKED** (not executable here, see below) |
| fault injection | 20 passed |
| integration (real backends) | 219 passed, 0 skipped |
| acceptance | 12 passed |
| mutation suite | **30/30 KILLED**, 0 survived, 0 errors (`run2/mutation-2026-11-04.json`) |
| backend processes left behind | none (`leftover_processes.txt` empty) |

BLOCKED, never PASS: `test_f10_python_bind_is_narrowed_to_exe_stdlib_and_libpython` (the
interpreter is under /usr: nothing to narrow), `test_DEP_01_actual_wheel_contains_byte_identical_runtime_assets`
and `test_DEP_01_actual_sdist_retains_wheel_asset_sources` (need a wheel/sdist build this session
did not make), `test_N6_unreadable_lookup_dir_raises_instead_of_dropping_subtree` (as root a
directory cannot be made unreadable). In a normal run they SKIP; `SVWEAVE_REQUIRE_BACKENDS=1`
reports a skipped required test as a failure, by design, so `unit_contract rc=1` in `steps.txt`.
This is not a qualification run (`scripts/qualify.py` was not run).
