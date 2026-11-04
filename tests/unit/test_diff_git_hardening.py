"""SEC-DIF: the git that diff-impact / the graph diff view runs treats repository metadata as
untrusted. Real git, local repositories only (no network): each test fails on the runner before
the council-review fixes (repo-config command execution, unenforced timeout, config-chosen diff
prefixes, binary /dev/null path)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import svweave.application.diff_impact as dimp
from svweave.domain.errors import ErrorCode, SvwError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="real git required")

_ENV = {"PATH": os.environ.get("PATH", os.defpath), "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], env=_ENV, check=True, capture_output=True,
                          text=True).stdout


def _partial_clone(tmp: Path) -> tuple[Path, Path]:
    """A blob-less clone whose repository config names a marker-writing upload-pack: any lazy
    fetch would run it. Returns (clone, marker)."""
    origin, clone, marker = tmp / "origin", tmp / "clone", tmp / "PWNED"
    git("init", "-q", "-b", "main", str(origin))
    (origin / "a.sv").write_text("module a; endmodule\n")
    git("-C", str(origin), "add", "a.sv")
    git("-C", str(origin), "commit", "-qm", "base")
    git("-C", str(origin), "config", "uploadpack.allowFilter", "true")
    git("clone", "-q", "--filter=blob:none", "--no-checkout", f"file://{origin}", str(clone))
    git("-C", str(clone), "config", "remote.origin.uploadpack",
        f"sh -c 'touch {marker}; exec git-upload-pack \"$@\"' --")
    missing = git("-C", str(clone), "rev-list", "--objects", "--missing=print", "HEAD")
    assert any(ln.startswith("?") for ln in missing.splitlines()), "setup: a blob must be missing"
    assert not marker.exists()
    return clone, marker


def test_SEC_DIF01_partial_clone_is_refused_and_no_transport_command_runs(tmp_path: Path) -> None:
    clone, marker = _partial_clone(tmp_path)
    with pytest.raises(SvwError) as e:
        dimp._refuse_lazy_objects(clone)
    assert e.value.code == ErrorCode.POLICY_DENIED
    assert e.value.details["reason"] == "partial_clone_unsupported"
    # even a read that bypasses the layout check never fetches: transports are disabled
    with pytest.raises(SvwError):
        dimp._git(clone, ["cat-file", "blob", "HEAD:a.sv"])
    assert not marker.exists(), "repository config made SVWeave's git run a command"


def test_SEC_DIF02_user_and_repository_config_cannot_change_the_diff_prefixes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "r"
    git("init", "-q", "-b", "main", str(repo))
    (repo / "x.sv").write_text("a\n")
    git("-C", str(repo), "add", "x.sv")
    git("-C", str(repo), "commit", "-qm", "b")
    (repo / "x.sv").write_text("b\n")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[diff]\n\tmnemonicPrefix = true\n")
    monkeypatch.setenv("HOME", str(home))  # the user's own config is ignored, not inherited
    git("-C", str(repo), "config", "diff.srcPrefix", "zz/")  # and the repository's is overridden
    git("-C", str(repo), "config", "diff.noprefix", "true")
    out = dimp._git(repo, ["diff", "-U0", "--no-color", "HEAD", "--"]).decode()
    assert "--- a/x.sv" in out and "+++ b/x.sv" in out
    assert [f.path for f in dimp.parse_diff(out)] == ["x.sv"]


def test_SEC_DIF03_timeout_holds_while_git_is_silent_and_kills_its_children(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "bin"
    fake.mkdir()
    pidfile = tmp_path / "child.pid"
    (fake / "git").write_text(f"#!/bin/sh\necho partial\nsleep 60 &\necho $! > {pidfile}\nwait\n")
    (fake / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ.get('PATH', '')}")
    monkeypatch.setattr(dimp, "GIT_TIMEOUT_S", 1.5)
    t0 = time.monotonic()
    with pytest.raises(SvwError) as e:
        dimp._git(tmp_path, ["diff"])
    assert e.value.code == ErrorCode.BUILD_TIMEOUT
    assert time.monotonic() - t0 < 6, "the deadline was not enforced while git wrote nothing"
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(pid, 9)
        pytest.fail("a child of git outlived the timeout")


def test_SEC_DIF04_a_new_binary_file_is_named_by_its_path_not_dev_null() -> None:
    files = dimp.parse_diff("diff --git a/x.bin b/x.bin\nnew file mode 100644\n"
                            "Binary files /dev/null and b/x.bin differ\n")
    assert [(f.old, f.new, f.path, f.binary) for f in files] == [(None, "x.bin", "x.bin", True)]
    gone = dimp.parse_diff("Binary files a/y.bin and /dev/null differ\n")
    assert [(f.old, f.new) for f in gone] == [("y.bin", None)]
