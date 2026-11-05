"""SEM-ABS (rule 5): a target that a build which is not COMPLETE does not hold is reported as
``MISSING_INPUT`` with the caveat that its absence is no proof, by every shared resolver: impact,
rename preview, hierarchy, connections, assertions, symbol and references (third council review
M8: only the graph export's root resolver said so). A COMPLETE build states no caveat."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from svweave.application import impact, queries, rename
from svweave.application.options import WorkspaceOptions
from svweave.domain.errors import ErrorCode, SvwError
from svweave.domain.states import Freshness
from svweave.storage.db import Database
from tests.conftest import Ws
from tests.unit.test_reference_coverage import _stage

UNKNOWN = "f" * 64

CALLS: dict[str, Callable[[WorkspaceOptions, str], Any]] = {
    "impact symbol": lambda o, b: impact.impact(o, b, "r", symbol_id=UNKNOWN),
    "impact instance": lambda o, b: impact.impact(o, b, "r", instance="top.nope"),
    "rename preview": lambda o, b: rename.rename_preview(o, b, "r", symbol_id=UNKNOWN,
                                                         new_name="renamed"),
    "hierarchy": lambda o, b: queries.hierarchy(o, b, "r", instance="top.nope"),
    "connections": lambda o, b: queries.connections(o, b, "top.nope", "r"),
    "assertions": lambda o, b: queries.assertions(o, b, "r", scope="top.nope"),
    "symbol": lambda o, b: queries.symbol(o, b, "r", symbol_id=UNKNOWN),
    "references": lambda o, b: queries.references(o, b, UNKNOWN, "r"),
}


@pytest.mark.parametrize("call", sorted(CALLS))
@pytest.mark.parametrize("completeness", ["PARTIAL", "COMPLETE"])
def test_SEM_ABS_01_missing_target_says_absence_is_no_proof_unless_complete(
        ws: Ws, monkeypatch: pytest.MonkeyPatch, call: str, completeness: str) -> None:
    bid, _ = _stage(ws)
    with Database(ws.root / ".svweave" / "svweave.db") as db:
        db.conn.execute("UPDATE builds SET completeness=? WHERE build_id=?", (completeness, bid))
        db.conn.commit()
    monkeypatch.setattr(queries._S, "fresh", lambda self: Freshness.CURRENT)
    with pytest.raises(SvwError) as ei:
        CALLS[call](WorkspaceOptions(root=ws.root), bid)
    e = ei.value
    assert e.code is ErrorCode.MISSING_INPUT, (call, e)
    assert e.details["reason"] in ("symbol_missing", "instance_missing")
    if completeness == "PARTIAL":
        assert "not proof" in e.message and "PARTIAL" in e.message, (call, e.message)
        assert e.details["absence_is_proof"] is False and e.details["completeness"] == "PARTIAL"
    else:
        assert "not proof" not in e.message and "absence_is_proof" not in e.details
