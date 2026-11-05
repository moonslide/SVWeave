# Run 1 (FAILED): full verification after the council-review fixes

Source: commit `d431b42` (`git_head.txt`), lock sha256 in `lock_sha256.txt`, one 4-CPU cloud
session (not CI), `SVWEAVE_REQUIRE_BACKENDS=1` for every pytest step. Commands and exit codes are
in `steps.txt`; each step's output is in its `.log`, pytest results in `junit-*.xml`.

| Step | Result |
|---|---|
| ruff, mypy, `schema emit --check`, `bootstrap_locked.py --verify-only` | rc 0 |
| unit + contract | **FAIL**: 1741 tests, 5 failures, 1 error |
| fault injection | 20 passed |
| integration (real pyslang, codebase-memory-mcp, Serena, UVM) | 219 passed, 0 skipped |
| acceptance | 12 passed |
| mutation suite | **27/30 KILLED, 3 ERROR** (M05, M06, M08: red baseline), 0 survived |

The failures:

* **Regression from this work (fixed in the next commit):**
  `test_edt02_stale_expected_hash_check_protects_the_target_even_if_snapshot_check_passes` and
  `test_m7b_the_pre_journal_cas_refuses_before_any_journal_exists`. The roll-forward fix had
  bypassed `_stale`, the seam these defence-in-depth tests switch off, so the freshness gate
  refused before the compare-and-set they test could. The same red baseline made M05/M06/M08
  ERROR.
* **Not executable on this host (BLOCKED, never PASS):** `test_f10_python_bind_is_narrowed_to_exe_stdlib_and_libpython`
  (the interpreter is under /usr, nothing to narrow), the two `test_DEP_01_actual_wheel/sdist_*`
  tests and `test_N6_unreadable_lookup_dir_raises_instead_of_dropping_subtree` (running as root,
  a directory cannot be made unreadable). In a normal run they SKIP; `SVWEAVE_REQUIRE_BACKENDS=1`
  turns a skip of a required test into a failure, by design.

`mutation-partial-rerun-M05-M06-M08-M30.json` is the rerun of the four affected mutations after
the fix (4/4 KILLED). The full run-1 mutation JSON was overwritten by that rerun; its text result
is `mutation.log`.
