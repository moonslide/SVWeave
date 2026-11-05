"""The viewer's JavaScript is checked with the host's `node` (not in toolchain.lock.json: a
test-only tool, like a browser). A missing node skips locally but FAILS when
`SVWEAVE_REQUIRE_NODE=1` (set in CI's fast job) or `SVWEAVE_REQUIRE_BACKENDS=1`: a skip is never
a green check of the viewer (second council review, test evidence)."""

from __future__ import annotations

import os
import shutil

import pytest


def node_or_skip(what: str) -> str:
    node = shutil.which("node")
    if node is None:
        msg = f"node is required to {what}"
        if "1" in (os.environ.get("SVWEAVE_REQUIRE_NODE"), os.environ.get("SVWEAVE_REQUIRE_BACKENDS")):
            pytest.fail(msg, pytrace=False)
        pytest.skip(msg)
    return node
