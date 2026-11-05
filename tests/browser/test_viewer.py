"""GRF-B: the graph viewer as a user drives it, in Chromium (Playwright), on pages exported from
a real build (real pyslang worker): the banner, expand, search, atlas portals, links and crumbs,
the #f= focus, lazy loading, the commit filter, and the CSP that runs only the viewer's script.

Second council review (test evidence): the viewer's behaviour had no automated test, only hand
checks. Playwright and its Chromium are NOT in toolchain.lock.json, so this suite is outside the
required CI run (`tests/integration tests/fault_injection tests/acceptance`); it runs where they
are present (`pytest tests/browser`) and is recorded as such. `SVWEAVE_REQUIRE_BROWSER=1` turns a
missing browser into a failure."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_cli_semantic import run

pytestmark = pytest.mark.browser
HERE = Path(__file__).resolve().parent
DEEP = "grf_soc.g_cl[3].u_cl.g_core[7].u_core"
DEEP2 = "grf_soc.g_cl[2].u_cl.g_core[5].u_core.u_exec"


def _browser_or_skip() -> str:
    node = shutil.which("node")
    ok = node is not None and subprocess.run(
        [node, "-e", "require('playwright')"], capture_output=True, check=False).returncode == 0
    if not ok:
        msg = "node with the playwright module is required for the browser suite"
        if os.environ.get("SVWEAVE_REQUIRE_BROWSER") == "1":
            pytest.fail(msg, pytrace=False)
        pytest.skip(msg)
    assert node is not None
    return node


def _exports(tmp_path: Path) -> Path:
    """Build the grf fixture (610 instances) and write every page kind the viewer serves."""
    from tests.integration.test_graph_export import _commit_series, _git_grf

    root = _git_grf(tmp_path, cores=30, clusters=4)  # 610 instances: four atlas pages
    _commit_series(root)
    code, b = run(root, "build", "--profile", "p")
    assert code == 0, b.get("error")
    bid = str(b["build_id"])
    for args in (["--name", "whole"],
                 ["--split", "--page-budget", "200", "--name", "atlas"],
                 ["--lazy", "--page-budget", "200", "--name", "lazy"],
                 ["--diff-base", "HEAD~4", "--diff-head", "HEAD", "--per-commit", "--name", "diff"]):
        code, env = run(root, "graph", "export", "--build", bid, *args)
        assert code == 0, (args, env.get("error"))
    return root / ".svweave" / "exports"


@pytest.mark.usefixtures("isolated_git")
def test_GRF_B01_the_viewer_works_in_chromium(tmp_path: Path) -> None:
    node = _browser_or_skip()
    exports = _exports(tmp_path)
    r = subprocess.run([node, str(HERE / "viewer_check.js"), str(exports), DEEP, DEEP2],
                       capture_output=True, text=True, timeout=600, check=False)
    assert r.returncode == 0, r.stderr[-2000:]
    out: dict[str, Any] = json.loads(r.stdout)
    assert "exception" not in out, out
    w = out["whole"]
    assert w["errors"] == [] and "Complete topology" in w["banner"]
    assert w["expanded"] > w["before"] and w["hits"] >= 1
    a = out["atlas"]
    assert a["errors"] == [] and "Atlas page 1 of" in a["banner"]
    assert "NOT the complete topology" in a["banner"]
    assert a["portal"] and a["portal_link"] and a["sub_url"].startswith("p-")
    assert "Atlas page" in a["sub_banner"] and a["crumb_back"] == "index.html"
    assert a["back_url"] == "index.html"
    assert a["search_links"] >= 1 and a["found_url"].startswith("p-") and DEEP2 in a["found_side"]
    lz = out["lazy"]
    assert lz["errors"] == [] and "Complete topology in" in lz["banner"]
    assert lz["after"] != lz["before"]  # a part was loaded into the drawing
    assert lz["search_links"] >= 1 and DEEP2 in lz["found_side"] and lz["selected"] == 1
    d = out["diff"]
    assert d["errors"] == [] and "Diff view" in d["banner"] and d["rows"] == 4
    assert d["hit"] >= 1 and d["dim"] >= 1 and d["hit_after_clear"] == 0
    assert out["csp"] == {"injected_ran": False, "drawn": out["csp"]["drawn"]}
    assert out["csp"]["drawn"] > 0
