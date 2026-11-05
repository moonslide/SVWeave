"""IF: interface leftovers of both council reviews: Click usage errors as envelopes, `schema emit
--check` naming its error, `doctor`'s exit code independent of check order, the environment
allowlist check, schema files written without following links."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from svweave.application import doctor
from svweave.application.doctor import CheckResult
from svweave.cli import cli
from svweave.domain.envelope import Envelope
from tests.conftest import Ws, profile


def _run(root: Path, *args: str) -> tuple[int, dict[str, Any] | None, str]:
    out = CliRunner().invoke(cli, ["--root", str(root), "--compact", *args])
    try:
        body = json.loads(out.stdout) if out.stdout.strip() else None
    except ValueError:
        body = None  # not an envelope (help text)
    return out.exit_code, body, out.output


@pytest.mark.parametrize("args", [
    ["query", "hierarchy"],  # missing required --build
    ["query", "assertions", "--build", "a" * 64, "--kind", "bogus"],  # bad choice
    ["supplement", "show", "--supplement", "x", "--limit", "101"],  # out of range
    ["query", "hierarchy", "--build", "a" * 64, "--bogus"],  # unknown option
    ["no-such-command"],
])
def test_IF_01_click_usage_errors_print_an_invalid_config_envelope(
        tmp_path: Path, args: list[str]) -> None:
    code, env, output = _run(tmp_path, *args)
    assert code == 2 and env is not None, output
    Envelope.model_validate(env)
    assert env["outcome"] == "ERROR" and env["error"]["code"] == "INVALID_CONFIG"
    assert env["error"]["details"]["reason"] == "usage_error"


def test_IF_01b_help_stays_text(tmp_path: Path) -> None:
    code, env, output = _run(tmp_path, "query", "--help")
    assert code == 0 and env is None and "Usage:" in output


def test_IF_02_schema_check_out_of_sync_names_its_error(tmp_path: Path) -> None:
    out = tmp_path / "schemas"
    code, env, _ = _run(tmp_path, "schema", "emit", "--out", str(out))
    assert code == 0
    victim = next(out.glob("*.schema.json"))
    victim.write_text("{}\n")
    code, env, _ = _run(tmp_path, "schema", "emit", "--out", str(out), "--check")
    assert code == 2 and env is not None
    assert env["outcome"] == "ERROR" and env["error"]["code"] == "INVALID_CONFIG"
    assert victim.name in env["error"]["details"]["out_of_sync"]


def test_IF_03_schema_emit_replaces_a_planted_link_instead_of_writing_through_it(
        tmp_path: Path) -> None:
    out = tmp_path / "schemas"
    assert _run(tmp_path, "schema", "emit", "--out", str(out))[0] == 0
    target = tmp_path / "outside.txt"
    target.write_text("keep")
    victim = next(out.glob("*.schema.json"))
    victim.unlink()
    victim.symlink_to(target)
    assert _run(tmp_path, "schema", "emit", "--out", str(out))[0] == 0
    assert target.read_text() == "keep" and not victim.is_symlink()


def test_IF_04_doctor_error_is_the_most_fundamental_failure_not_the_first(
        ws: Ws, monkeypatch: pytest.MonkeyPatch) -> None:
    def backend(dc: Any) -> list[CheckResult]:
        return [CheckResult(name="backend:x", status="FAIL", code="BACKEND_UNAVAILABLE",
                            message="missing")]

    def config(dc: Any) -> list[CheckResult]:
        return [CheckResult(name="config:y", status="FAIL", code="INVALID_CONFIG",
                            message="bad")]

    monkeypatch.setattr(doctor, "CHECKS", [backend, config])  # backend fails FIRST
    code, env, _ = _run(ws.root, "doctor")
    assert env is not None and env["error"]["code"] == "INVALID_CONFIG" and code == 2
    assert env["error"]["details"]["failed_checks"] == ["backend:x", "config:y"]


def test_IF_05_doctor_warns_about_a_profile_variable_the_policy_does_not_allow(ws: Ws) -> None:
    ws.config({"p": profile(environment_allowlist=["PROJ_HOME", "OTHER_VAR"])})
    ws.policy(environment_allowlist=["PROJ_HOME"])
    code, env, _ = _run(ws.root, "doctor")
    assert env is not None
    msgs = [w for w in env["warnings"] if w.startswith("environment_allowlist:p")]
    assert msgs and "OTHER_VAR" in msgs[0] and "PROJ_HOME" not in msgs[0]
