"""The one hardened way SVWeave runs git on a repository it does not trust (SEC-03; first council
review, architecture finding 3): diff-impact, the graph diff view and per-commit labels, and the
report's commit lookup. ``application/update_git.py`` keeps its own budgeted runner for its
private metadata copy but takes its environment from :func:`untrusted_repo_env`."""

from __future__ import annotations

import contextlib
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path

from svweave.domain.errors import ErrorCode, SvwError

# Repository metadata is untrusted (the same stance as update_git): git must not read the user's
# global/system config, must never reach a transport (a partial clone's lazy fetch would run
# `remote.<x>.uploadpack` / `core.sshCommand` from the repository config), must not descend into
# submodules (their own config and filters) and must not take output prefixes from config.
HARDENED_CONFIG = (
    "core.fsmonitor=false",
    "core.quotePath=false",
    "protocol.allow=never",
    "diff.noprefix=false",
    "diff.mnemonicPrefix=false",
    "diff.srcPrefix=a/",
    "diff.dstPrefix=b/",
    "diff.ignoreSubmodules=all",
    "submodule.recurse=false",
    # `log.showSignature` in the repository config makes `git log` verify every signed commit,
    # i.e. run `gpg.program` (or the ssh / x509 one): a program chosen by the repository
    "log.showSignature=false",
    "gpg.program=" + os.devnull,
    "gpg.ssh.program=" + os.devnull,
    "gpg.x509.program=" + os.devnull,
    # a repository delivered WITH its .git/ (tarball, copied checkout) can ship hooks: a working
    # tree diff refreshes and rewrites the index, which runs `post-index-change` (reproduced,
    # third council review H1). No hook ever runs, and a read never rewrites the index.
    "core.hooksPath=" + os.devnull,
    "diff.autoRefreshIndex=false",
)
#: every `git log` runs with these (signature checks, external diff and textconv off)
LOG_SAFE = ("--no-show-signature", "--no-ext-diff", "--no-textconv")


def untrusted_repo_env(path: str | None = None) -> dict[str, str]:
    """The whole environment of a git that reads an untrusted repository: no user/system config,
    no transport, no lazy fetch, no prompt (``path``: PATH, default the caller's)."""
    return {
        "PATH": os.environ.get("PATH", os.defpath) if path is None else path,
        "HOME": "/nonexistent",
        "XDG_CONFIG_HOME": "/nonexistent",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_ALLOW_PROTOCOL": "",
    }


def kill_group(proc: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


def run_git(
    repo: Path,
    args: list[str],
    ok: tuple[int, ...] = (0,),
    *,
    timeout_s: float,
    max_bytes: int,
) -> bytes:
    """``git -C repo <HARDENED_CONFIG> --no-pager args``: fixed argv, no shell, the untrusted-repo
    environment, a deadline that holds while git is silent, a stdout cap, and the whole process
    group killed on timeout or overflow."""
    argv = ["git", "-C", str(repo)]
    for kv in HARDENED_CONFIG:
        argv += ["-c", kv]
    argv += ["--no-pager", *args]
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell, refs validated by check_ref
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=untrusted_repo_env(),
            start_new_session=True,  # a timeout or overflow kills git AND its children
        )
    except FileNotFoundError as e:
        raise SvwError(
            ErrorCode.BACKEND_UNAVAILABLE, "git is not installed", {"reason": "git_missing"}
        ) from e
    # Both pipes are drained without blocking, so the deadline holds even while git writes
    # nothing; stdout is capped, so the diff (or a blob) is never buffered whole only to be
    # refused afterwards.
    chunks: list[bytes] = []
    err = bytearray()
    size = 0
    over = timed_out = False
    deadline = time.monotonic() + timeout_s
    assert proc.stdout is not None and proc.stderr is not None
    try:
        with selectors.DefaultSelector() as sel:
            for pipe in (proc.stdout, proc.stderr):
                os.set_blocking(pipe.fileno(), False)
                sel.register(pipe, selectors.EVENT_READ)
            while sel.get_map():
                left = deadline - time.monotonic()
                if left <= 0:
                    timed_out = True
                    break
                for key, _ in sel.select(timeout=left):
                    chunk = os.read(key.fd, 1 << 16)
                    if not chunk:
                        sel.unregister(key.fileobj)
                    elif key.fileobj is proc.stdout:
                        size += len(chunk)
                        if size > max_bytes:
                            over = True
                            break
                        chunks.append(chunk)
                    elif len(err) < 1 << 16:
                        err += chunk
                if over:
                    break
        if not (timed_out or over):
            try:
                rc = proc.wait(timeout=max(deadline - time.monotonic(), 0.05))
            except subprocess.TimeoutExpired:
                timed_out = True
    finally:
        kill_group(proc)  # also reaps children that outlived git and hold the pipes
        if proc.poll() is None:
            proc.wait()
        for f in (proc.stdout, proc.stderr):
            f.close()
    if timed_out:
        raise SvwError(
            ErrorCode.BUILD_TIMEOUT, f"git did not finish in {timeout_s}s", {"reason": "git_timeout"}
        )
    if over:
        raise SvwError(
            ErrorCode.OUTPUT_LIMIT, f"git output is larger than {max_bytes} bytes",
            {"reason": "diff_too_large"},
        )
    if rc not in ok:
        first = bytes(err).decode("utf-8", "replace").strip().splitlines()[:1]
        raise SvwError(
            ErrorCode.MISSING_INPUT,
            "git could not answer: " + (first[0][:200] if first else f"exit {rc}"),
            {"reason": "git_failed", "args": args[:3]},
        )
    return b"".join(chunks)
