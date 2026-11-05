"""One `svweave doctor` check result. In ``domain/`` so both the application layer (config and
policy checks) and ``orchestration`` (backend checks) build them without an upward import
(first council review, architecture finding 5)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from svweave.domain.errors import SvwError

Status = Literal["PASS", "FAIL", "WARN", "SKIP"]


class CheckResult(BaseModel):
    name: str
    status: Status
    code: str | None = None
    message: str = ""
    details: dict[str, object] = {}


def failed_check(name: str, err: SvwError) -> CheckResult:
    """A FAIL result carrying the error's code, message and details."""
    return CheckResult(name=name, status="FAIL", code=err.code.value, message=err.message,
                       details=err.details)
