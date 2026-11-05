# run6 (`b916eda`): FAILED, kept as a record

One integration test failed: `tests/integration/test_incremental_update.py::
test_SNP_01_02_03_update_matches_full_capture_and_real_semantics` asserted that the state directory
`Ws.policy()` records is empty; since the token record (`673fdb4`) it holds `.svweave-id`. A test
assumption, not a product defect; fixed in the commit that adds this file, verified in run7.

Everything else as expected: unit + contract 1804 passed and the same 4 BLOCKED as run5 (JUnit: 3
failures, 1 error; required tests that cannot execute on this host under
`SVWEAVE_REQUIRE_BACKENDS=1`); fault injection 26, integration 239 of 240, acceptance 12, browser 1
passed; mutations 45/45 KILLED.
