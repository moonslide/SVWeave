"""Leftovers of the first council review that need a real build or the real edit loop (closed in
the follow-up of council review 2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from tests.integration.demo_support import demo_ws
from tests.integration.test_cli_semantic import run
from tests.integration.test_rename_preview import apply_through_edit_loop, preview

pytestmark = pytest.mark.integration


def _pointer(root: Path, profile: str) -> str | None:
    db = sqlite3.connect(root / ".svweave" / "svweave.db")
    row = db.execute("SELECT build_id FROM published_pointer WHERE profile=?", (profile,)).fetchone()
    return None if row is None else str(row[0])


def test_EDT_PUB01_post_apply_publishes_every_validate_profile_of_a_shared_build(
        tmp_path: Path) -> None:
    """Two validate profiles with identical inputs share one (content-addressed) build id. The
    post-apply publish moved only the pointer of the profile that first created the row, so the
    second profile reported STALE right after a VERIFIED commit."""
    w = demo_ws(tmp_path, editing=True)
    m = yaml.safe_load((w.root / "svweave.yaml").read_text())
    m["profiles"]["q"] = dict(m["profiles"]["p"])
    m["editing"]["validate_profiles"] = ["p", "q"]
    (w.root / "svweave.yaml").write_text(yaml.safe_dump(m, sort_keys=False))
    code, bp = run(w.root, "build", "--profile", "p")
    assert code == 0
    code, bq = run(w.root, "build", "--profile", "q")
    assert code == 0 and bq["build_id"] == bp["build_id"]  # one build, two profiles
    assert _pointer(w.root, "p") == _pointer(w.root, "q") == bp["build_id"]
    code, env = preview(w.root, str(bp["build_id"]), "--name", "demo_lane.data_o",
                        "--to", "data_out")
    assert code == 0 and env["data"]["ready_for_edit_request"] is True, env.get("error")
    out = apply_through_edit_loop(w.root, env["data"]["edit_request"])
    assert out["post_apply_status"] == "VERIFIED"
    new_p, new_q = out["post_apply"]["p"]["build_id"], out["post_apply"]["q"]["build_id"]
    assert new_p == new_q != bp["build_id"]
    assert _pointer(w.root, "p") == new_p
    assert _pointer(w.root, "q") == new_q, "the second profile's pointer was not moved"
