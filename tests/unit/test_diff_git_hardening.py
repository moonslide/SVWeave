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


def test_SEC_DIF05_per_commit_git_log_never_runs_the_repository_gpg_program(tmp_path: Path) -> None:
    """``log.showSignature`` in the repository config makes ``git log`` verify a signed commit
    with ``gpg.program``: a program the repository names. Both log calls of --per-commit."""
    repo, marker = tmp_path / "r", tmp_path / "PWNED"
    git("init", "-q", "-b", "main", str(repo))
    (repo / "a.sv").write_text("module a;\nendmodule\n")
    git("-C", str(repo), "add", "a.sv")
    git("-C", str(repo), "commit", "-qm", "base")
    (repo / "a.sv").write_text("module a;\nwire w;\nendmodule\n")
    git("-C", str(repo), "add", "a.sv")
    tree = git("-C", str(repo), "write-tree").strip()
    parent = git("-C", str(repo), "rev-parse", "HEAD").strip()
    body = (f"tree {tree}\nparent {parent}\nauthor t <t@t> 1 +0000\ncommitter t <t@t> 1 +0000\n"
            "gpgsig -----BEGIN PGP SIGNATURE-----\n garbage\n -----END PGP SIGNATURE-----\n\n"
            "signed\n")
    obj = tmp_path / "commit.txt"
    obj.write_text(body)
    sha = git("-C", str(repo), "hash-object", "-t", "commit", "-w", str(obj)).strip()
    git("-C", str(repo), "update-ref", "refs/heads/main", sha)
    prog = tmp_path / "gpg.sh"
    prog.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    prog.chmod(0o755)
    git("-C", str(repo), "config", "log.showSignature", "true")
    git("-C", str(repo), "config", "gpg.program", str(prog))
    # setup check: plain git with this config does run the program
    git("-C", str(repo), "log", "-1", "--format=%H")
    assert marker.exists(), "setup: git log must run gpg.program for a signed commit"
    marker.unlink()
    assert [c["sha"] for c in dimp.commits_in_range(repo, "HEAD~1", "HEAD")[0]] == [sha]
    assert dimp.commits_touching(repo, "HEAD~1", "HEAD", "a.sv", 1, 3) == [sha]
    assert not marker.exists(), "repository config made SVWeave's git log run gpg.program"


def test_SEC_DIF07_the_working_tree_new_side_never_leaves_the_repository(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """First council review (test-evidence finding 3): the guards of the working-tree new side (a
    link leaving the repository, a non-regular file, the size cap) were untested."""
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "in.sv").write_bytes(b"module a; endmodule\n")
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"outside the repository\n")
    (repo / "out.sv").symlink_to(secret)
    (repo / "dir.sv").mkdir()
    assert dimp._new_version(repo, "in.sv", None) == b"module a; endmodule\n"
    assert dimp._new_version(repo, "out.sv", None) is None
    assert dimp._new_version(repo, "../secret.txt", None) is None
    assert dimp._new_version(repo, "dir.sv", None) is None
    monkeypatch.setattr(dimp, "MAX_DIFF_BYTES", 4)
    assert dimp._new_version(repo, "in.sv", None) is None


def test_SEC_DIF08_the_report_commit_lookup_uses_the_hardened_runner(tmp_path: Path) -> None:
    """First council review (architecture finding 3): report._git_commit passed the whole
    environment to git; it now runs through security/git.py like diff-impact."""
    import inspect

    from svweave.application import report

    repo = tmp_path / "r"
    git("init", "-q", "-b", "main", str(repo))
    (repo / "a.sv").write_text("module a; endmodule\n")
    git("-C", str(repo), "add", "a.sv")
    git("-C", str(repo), "commit", "-qm", "c")
    head = git("-C", str(repo), "rev-parse", "HEAD").strip()
    assert report._git_commit(repo) == head
    assert report._git_commit(tmp_path / "not-a-repo") is None
    assert "run_git(" in inspect.getsource(report._git_commit)


def test_SEC_DIF09_a_working_tree_diff_runs_no_repository_hook_and_leaves_the_index(
        tmp_path: Path) -> None:
    """Third council review H1 (reproduced): a repository delivered WITH its .git/ can ship
    hooks; `git diff` against the working tree refreshes the index when timestamps changed but
    content did not, and writing the index runs `post-index-change`."""
    repo, marker = tmp_path / "r", tmp_path / "PWNED"
    git("init", "-q", "-b", "main", str(repo))
    (repo / "a.sv").write_text("module a; endmodule\n")
    (repo / "b.sv").write_text("module b; endmodule\n")
    git("-C", str(repo), "add", "-A")
    git("-C", str(repo), "commit", "-qm", "base")
    hook = repo / ".git" / "hooks" / "post-index-change"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    index = repo / ".git" / "index"
    time.sleep(1.1)
    os.utime(repo / "a.sv")  # same bytes, new timestamps: an index refresh would follow
    (repo / "b.sv").write_text("module b; wire w; endmodule\n")
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    out = dimp._git(repo, ["diff", "-U0", "--no-color", "--no-ext-diff", "--no-textconv",
                           "--no-renames", "HEAD", "--"]).decode()
    assert "b.sv" in out and "a.sv" not in out
    assert not marker.exists(), "SVWeave's git ran a hook the repository ships"
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before  # a read writes nothing
    # setup check: plain git with the same repository does run the hook
    git("-C", str(repo), "diff", "HEAD", "--")
    assert marker.exists(), "setup: plain git diff must run post-index-change here"


def test_SEC_DIF10_the_users_own_safe_directory_still_applies_a_repositorys_never(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Third council review: with the user's config hidden, a repository another user owns (a
    shared project space the person marked `safe.directory`) lost its commit in the report.
    Only the system/global entries count: one in the repository's own config is ignored."""

    import svweave.security.git as G
    from svweave.application.report import _git_commit

    repo = tmp_path / "shared"
    repo.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    for a in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "c"]):
        subprocess.run(["git", "-C", str(repo), *a], env=env, check=True, capture_output=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], env=env, check=True,
                          capture_output=True, text=True).stdout.strip()
    real_env = G.untrusted_repo_env
    # git's own test knob: treat the repository as owned by someone else (no chown needed)
    monkeypatch.setattr(G, "untrusted_repo_env",
                        lambda path=None: {**real_env(path), "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"})
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    G.user_safe_directories.cache_clear()
    try:
        # the repository marks itself safe: ignored (only protected config counts)
        subprocess.run(["git", "-C", str(repo), "config", "safe.directory", str(repo)], env=env,
                       check=True)
        assert _git_commit(repo) is None
        G.user_safe_directories.cache_clear()
        (home / ".gitconfig").write_text(f"[safe]\n\tdirectory = {repo}\n")
        assert G.user_safe_directories() == (str(repo),)
        assert _git_commit(repo) == head
    finally:
        G.user_safe_directories.cache_clear()
