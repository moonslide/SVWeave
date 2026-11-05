# Follow-up of both council reviews (2026-11-05)

After council review 2 (`../council-review-2-2026-11-04/`) the project owner asked for every item
still open in both reviews to be handled, and decided the three questions only the owner could
decide (2026-11-05):

* the `--lazy` CSP relaxation (`script-src 'self'` on its index page) is **approved** (ADR-014);
* a `.svweave/` SVWeave did not create is **not trusted**: its identity is recorded in the trusted
  directory and anything else needs `svweave init --adopt-state` (**breaking**);
* the first review's leftovers are handled too, write-safety moves as behaviour-preserving
  refactors checked by the full suite and the mutation suite.

Each listed test guards the change: the safety rules have negative tests that fail on the
previous code and, where they protect writes, mutation entries (M32–M40, killed in run5);
GRF-I22..I27 and SEC-DIF07 add coverage for behaviour that already existed (they pass on the
previous code too); some pins (QUAL-02/05, SEC-DIF08, ARC-05) check source text, not behaviour;
behaviour-preserving moves are checked by the unchanged suites. The third council review
(`../council-review-3-2026-11-05/`) found new issues, some in this follow-up's own changes.

## Owner decisions

| Item | Change | Tests |
|---|---|---|
| `--lazy` CSP | ADR-014 records the owner's approval | GRF-I17, GRF-B01 |
| Repository-shipped `.svweave/` | state dir identity in `state_dir.json` (trusted dir); non-empty unrecorded `.svweave/` refused (`state_dir_not_adopted`); `svweave init --adopt-state` | SEC-STA01..05, mutation M32 |

## First review leftovers

| Finding | Change | Tests |
|---|---|---|
| Correctness 4: post-apply published a shared build under one profile only | `publish_build(bid, profile)` | EDT-PUB01 |
| Correctness 5: candidate snapshot without the workspace lock | shared workspace + publish lock | SEC-SNP06 |
| Correctness 6: CLI commit request key not validated | key pattern check; LIKE-escaped lookup | f7 key test |
| Security 4: external-root writes followed linked parents | no-follow read/stat/write below the root | SEC-EXT01/02, M33 |
| Interface 4: Click usage errors without envelope | `INVALID_CONFIG` envelope, reason `usage_error` | IF-01 |
| Interface 5: `PATH_NOT_ALLOWED` 3 vs 6 | `reason: dependency_leak`, catalog documents exit 3 | blocked-build CLI test |
| Interface 6: `schema emit --check` ERROR without error | `INVALID_CONFIG`, `schemas_out_of_sync` | IF-02 |
| Interface 7: doctor exit code by check order | most fundamental failure decides; all failing checks listed | IF-04 |
| Policy 2: RTL coverage slot showed pytest coverage | slot reports RTL coverage evidence only | QUAL-02 |
| Policy 3: release decision from a dirty tree | uncommitted changes block | QUAL-03 |
| Policy 4: networked maintainer scripts | `gateway_lock_refresh.py --online`, real digest check; scope documented | QUAL-05 |
| Policy 5: README results without evidence | marked unrecorded | (docs) |
| Policy 7: G5 trusted PASS labels | measured vs budget recomputed | bench test |
| Policy 8: `--no-baseline` counted KILLED | `UNVERIFIED`, never killed | QUAL-04 |
| Policy 9: report listed missing files | only existing files | QUAL-03 |
| Policy 11: `schema emit` followed links | atomic no-follow write | IF-03 |
| Test evidence 2/3/8/9/12/13 | graph diff approximate mapping, head-side new side, PARTIAL `--instance`, exact cut, chunked SQL, two tops; working-tree new-side guards | GRF-I22..I26, GRF-I03, SEC-DIF07 |
| Test evidence 4/6: guards without mutation entries | M34 freeze check, M35 binding, M36 test token, M37 expiry, M38 key mode, M39 journal/patch (new negative test) | mutation suite |
| Test evidence 14: G0 could skip UVM | pinned | QUAL-01 |
| Test evidence 15: install.md repro claim | reproduced and recorded | `evidence/uvm-archive-repro/` |
| Architecture 1: private query core | `application/semantic_read.py`, typed `QuerySession` | unchanged suites |
| Architecture 2: three target resolvers | one resolver; svw_symbol's no-twin-collapse explicit | unchanged suites |
| Architecture 3: three git runners | `security/git.py`; report through it; update_git shares env | SEC-DIF08 |
| Architecture 4: write safety in the Serena adapter | `editing/tree.py`, `editing/exact_range.py`; M40 | ARC-05, M40 |
| Architecture 5: layer cycles | Confirmation, schemas, CheckResult, toolchain/capability moved | ARC-01 |
| Architecture 6/7: services logic, DB name | `exports.py`, `snapshot_gc_state`, `storage/paths.py` | unchanged suites |
| Architecture 8: lock parsed three ways | `read_lock` validates | ARC-02 |
| Architecture 9: unbounded caches | LRU 512, `reset_caches`, Session.close | ARC-03 |
| Architecture 10: CLI built outside services | `build_sync`/`update_sync`, `post_build` | ARC-04 |
| Architecture 11: redaction in the gateway | `security/redaction.py` | ARC-01 |
| Architecture 12: stale docs | `docs/architecture.md`, AGENTS map | DOC tests |

## Second review leftovers

| Finding | Change | Tests |
|---|---|---|
| Interface: CLI-only errors named Python parameters | CLI flags, `details.field` | GRF-I16 |
| Interface: docstring / CLI row said one `.html` | directory output described | (docs) |
| Interface: doctor did not check the env allowlist | WARN per profile variable the policy lacks | IF-05 |
| Interface: per-mode envelope keys | `data.mode` + `data.parts` | GRF-I15..I17 |
| Architecture 1: graph.py four concerns | `graph_model.py`, `graph_diff.py`, `graph.py` | unchanged suites |
| Architecture 2: graph ↔ atlas cycle | atlas no longer imports graph | none (structure only; not pinned by a test) |
| Architecture 3/4: viewer in a Python string; schema copies | `graph_viewer.js`, optional keys from the codec | GRF-U06, GRF-C04 |
| Architecture 5: search index outside the codec | `encode_search` / `decode_search` | GRF-I15 |
| Architecture 7: untyped `collect_changes` | `Changes` dataclass | mypy strict |
| Architecture 9/10: export naming, `s: Any` | `exports.py`, typed sessions | mypy strict |
| Architecture 11/12: owner rule twice, no page reader | `owner_of`, `read_page`/`read_chunk` | unchanged suites |
| Test evidence: viewer behaviour untested | `tests/browser` (Chromium) | GRF-B01 |
| Test evidence: node skips counted green | `SVWEAVE_REQUIRE_NODE=1` in CI | checked by hand (fails without node when required, skips otherwise); CI config |
| Test evidence 11: merge commits in range | attributed to the branch commit | GRF-I27 |

## Still open

* A file renamed inside a `--per-commit` range is not tested (`git log -L` does not follow
  renames; such commits stay unlabelled with the existing warning).
* Playwright and its Chromium are not in `toolchain.lock.json`: the browser suite is an opt-in,
  recorded suite outside the required CI run.

## Verification

One 4-CPU cloud session (not CI), `SVWEAVE_REQUIRE_BACKENDS=1 SVWEAVE_REQUIRE_NODE=1` exported by
the run script for every pytest step (`commands.txt` lists each command without that exported
environment; no `git status`, node or Playwright version was recorded), real pyslang 12 under mandatory bwrap, real codebase-memory-mcp, Serena and UVM
verified from the lock (sha256 `ea20baf9…`). Each run directory has `commands.txt` (every
command), `steps.txt` (exit code and wall time), `junit-*.xml` (every test), `mutation.json`.

* [`run4/`](run4/README.md) at `72c35ee`: **FAILED**, one integration test, a regression of this
  follow-up in a test helper's contract (kept as recorded; fixed in `de0e6fe`).
* [`run5/`](run5/) at `de0e6fe`:

| Step | run5 |
|---|---|
| ruff, mypy (strict), `svweave schema emit --check`, `bootstrap_locked.py --verify-only` | rc 0 |
| unit + contract | 1797 tests: **1793 passed**; 4 **BLOCKED** (the same four as runs 2 and 3; JUnit records them as 3 failures and 1 error because `SVWEAVE_REQUIRE_BACKENDS=1` turns a required skip into a failure) |
| fault injection | 21 passed |
| integration (real backends) | **240 passed**, 0 skipped |
| acceptance | 12 passed |
| browser (opt-in suite, Chromium of Playwright 1.63.0) | 1 passed |
| mutation suite | **40/40 KILLED**, baseline checked, 0 survived, 0 errors |
| backend processes left behind | none |

BLOCKED, never PASS: `test_f10_python_bind_is_narrowed_to_exe_stdlib_and_libpython` (the
interpreter is under /usr), the two `test_DEP_01_actual_*` wheel/sdist tests (need hatchling for an
offline build) and `test_N6_unreadable_lookup_dir_raises_instead_of_dropping_subtree` (as root a
directory cannot be made unreadable); `SVWEAVE_REQUIRE_BACKENDS=1` reports a skipped required
test as a failure by design, hence `unit_contract rc=1`. Not a `scripts/qualify.py` qualification
run.
