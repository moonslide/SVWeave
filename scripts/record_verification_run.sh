#!/bin/bash
# One recorded verification run (used for run4..run7 under evidence/council-review-*):
#   scripts/record_verification_run.sh evidence/<dir>/runN
# Runs ruff, mypy, the schema check, the lock check, the unit+contract, fault-injection,
# integration, acceptance and browser suites and the mutation suite with the real backends, and
# writes the commands, exit codes and wall times, JUnit files, logs, the commit, `git status`
# before and after, the lock hash, tool versions and leftover backend processes into the
# directory. About 21 minutes on a 4-CPU host. It is not scripts/qualify.py: its output is
# evidence for the README, not a release decision (AGENTS.md rule 15).
# Commit the directory only after the script has printed DONE in steps.txt; a failed run is kept
# (write a RESULT.md saying what failed) and the next run gets the next number.
cd "$(dirname "$0")/.." || exit 1
E="${1:?usage: record_verification_run.sh evidence/<dir>/runN}"
mkdir -p "$E"
export SVWEAVE_REQUIRE_BACKENDS=1 SVWEAVE_REQUIRE_NODE=1
step() { name=$1; shift; echo "$name: $*" >> "$E/commands.txt"; t0=$(date +%s); "$@" > "$E/$name.log" 2>&1; rc=$?; echo "$name rc=$rc wall_s=$(( $(date +%s)-t0 ))" >> "$E/steps.txt"; }
: > "$E/steps.txt"
echo "# exported for every step below: SVWEAVE_REQUIRE_BACKENDS=1 SVWEAVE_REQUIRE_NODE=1 (browser step also SVWEAVE_REQUIRE_BROWSER=1)" > "$E/commands.txt"
git rev-parse HEAD > "$E/git_head.txt"
git status --porcelain > "$E/git_status_before.txt"
sha256sum toolchain.lock.json > "$E/lock_sha256.txt"
{ echo "python: $(.venv/bin/python --version 2>&1)"; echo "node: $(node --version 2>&1)"; echo "playwright (node, preinstalled): $(node -e 'console.log(require("playwright/package.json").version)' 2>&1)"; echo "nproc: $(nproc)"; uname -srm; } > "$E/host_versions.txt"
step ruff .venv/bin/ruff check .
step mypy .venv/bin/mypy
step schema_check .venv/bin/svweave schema emit --check
step lock_verify .venv/bin/python scripts/bootstrap_locked.py --verify-only
step unit_contract .venv/bin/python -m pytest -p no:cacheprovider --no-cov -q -rfEs tests/unit tests/contract --junitxml="$E/junit-unit-contract.xml"
step fault_injection .venv/bin/python -m pytest -p no:cacheprovider --no-cov -q -rfEs tests/fault_injection --junitxml="$E/junit-fault-injection.xml"
step integration .venv/bin/python -m pytest -p no:cacheprovider --no-cov -q -rfEs -m integration tests/integration --junitxml="$E/junit-integration.xml"
step acceptance .venv/bin/python -m pytest -p no:cacheprovider --no-cov -q -rfEs -m acceptance --junitxml="$E/junit-acceptance.xml"
step browser env SVWEAVE_REQUIRE_BROWSER=1 .venv/bin/python -m pytest -p no:cacheprovider --no-cov -q -rfEs tests/browser --junitxml="$E/junit-browser.xml"
step mutation .venv/bin/python scripts/mutation_suite.py --out "$E/mutation.json"
pgrep -fa 'codebase-memory|verible|serena' | grep -v pgrep > "$E/leftover_processes.txt"
git status --porcelain > "$E/git_status_after.txt"
echo DONE >> "$E/steps.txt"
