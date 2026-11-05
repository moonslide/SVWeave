# run4 at `72c35ee`: FAILED (one integration test), kept as recorded

`SVWEAVE_REQUIRE_BACKENDS=1 SVWEAVE_REQUIRE_NODE=1`, one 4-CPU cloud session (not CI). Commands
in `commands.txt`, exit codes and wall times in `steps.txt`, every test in `junit-*.xml`.

| Step | Result |
|---|---|
| ruff, mypy (strict), schema check, lock verify | rc 0 |
| unit + contract | 1797 tests: 1793 passed, 0 failed, 4 BLOCKED (the same four as runs 2 and 3) |
| fault injection | 21 passed |
| integration | 240 tests: 239 passed, **1 FAILED** |
| acceptance | 12 passed |
| browser (opt-in, Chromium) | 1 passed |
| mutation suite | 40/40 KILLED, baseline checked (`mutation.json`) |

The failure, `test_incremental_update.py::test_SNP_01_02_03_update_matches_full_capture_and_real_semantics`,
is a regression of this follow-up in a test helper's contract, not in the product: the test helper
`Ws.policy()` now creates and records an empty `.svweave/` like `svweave init` (needed by the new
state-directory trust rule), and that test asserted a fresh workspace has no `.svweave/` at all.
Its assertion now checks that the state directory holds nothing (the intent: no runtime state).
The fix is in the commit after `72c35ee`; run5 is the recorded run of the fixed tree.
