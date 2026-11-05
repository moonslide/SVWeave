"""Snapshot of the CANDIDATE (plan 12.4 validate): the base snapshot record with the edited files'
hashes replaced. Logical paths are identical to the base, so slang's results line up with the
baseline; the snapshot id is derived from the candidate content only.
"""

from __future__ import annotations

import os
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path

from svweave.config.context import WorkspaceContext, snapshot_rel
from svweave.domain.errors import ErrorCode, SvwError
from svweave.domain.ids import sha256_hex
from svweave.security.safeio import read_regular_under
from svweave.snapshot.capture import (
    _content_matches,
    _record_matches,
    load_record,
    remove_snapshot_dir,
    snapshot_id_for,
    snapshot_publish_lock,
    snapshots_dir,
)
from svweave.snapshot.models import SnapFile, SnapshotRecord
from svweave.storage.lock import workspace_lock


def _freeze(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for fn in filenames:
            os.chmod(os.path.join(dirpath, fn), stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        for dn in dirnames:
            os.chmod(os.path.join(dirpath, dn), 0o555)
    os.chmod(root, 0o555)


def derive_candidate_snapshot(
    ctx: WorkspaceContext, base: SnapshotRecord, changed: dict[str, bytes]
) -> SnapshotRecord | None:
    """Immutable snapshot of ``base`` with ``changed`` (logical path -> new bytes) applied.

    Returns ``None`` when no changed file belongs to this snapshot (the profile does not cover
    the edit, so it cannot validate it).
    """
    inv = {f.logical_path for f in base.files}
    touched = sorted(p for p in changed if p in inv)
    if not touched:
        return None
    files: list[SnapFile] = []
    for f in base.files:
        if f.logical_path in changed:
            data = changed[f.logical_path]
            files.append(f.model_copy(update={"sha256": sha256_hex(data), "size": len(data)}))
        else:
            files.append(f)
    # held like capture: a concurrent `svweave gc` (exclusive) must not delete the .tmp-* copy
    # or the candidate snapshot between creation and the build that reads it; the publish lock
    # serialises the check-then-replace of an existing directory with other publishers
    with workspace_lock(ctx.state_dir), snapshot_publish_lock(ctx):
        return _derive(ctx, base, files, changed)


def _derive(
    ctx: WorkspaceContext, base: SnapshotRecord, files: list[SnapFile], changed: dict[str, bytes]
) -> SnapshotRecord:
    sid = snapshot_id_for(base.spec, files, base.lookup)
    root = snapshots_dir(ctx)
    final = root / sid
    rec = SnapshotRecord(
        snapshot_id=sid,
        workspace_id=base.workspace_id,
        profile=base.profile,
        config_id=base.config_id,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        attempts=1,
        spec=base.spec,
        files=files,
        lookup=base.lookup,
    )
    if final.exists() or final.is_symlink():
        # an existing directory is reused only when it holds exactly these bytes and this record
        # (its id is computable from public facts; a planted one would make validate compile
        # other bytes than the candidate's); otherwise it is replaced by a verified copy
        if (not final.is_symlink() and _content_matches(final, files)
                and _record_matches(final, rec)):
            return load_record(final)
        remove_snapshot_dir(final)
    tmp = root / f".tmp-{uuid.uuid4().hex}"
    src_dir = root / base.snapshot_id
    try:
        (tmp / "files").mkdir(parents=True)
        for f in files:  # real byte copies, never links
            dest = tmp / "files" / snapshot_rel(f.logical_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if f.logical_path in changed:
                dest.write_bytes(changed[f.logical_path])
            else:  # the base's bytes, read through no link and checked against its record
                data = read_regular_under(src_dir, f.snapshot_path)
                if sha256_hex(data) != f.sha256:
                    raise SvwError(ErrorCode.MISSING_INPUT,
                                   f"base snapshot file {f.logical_path} does not match its record",
                                   {"reason": "snapshot_content_mismatch"})
                with open(dest, "xb") as fout:
                    fout.write(data)
        (tmp / "snapshot.json").write_text(rec.model_dump_json(indent=2), encoding="utf-8")
        _freeze(tmp)
        os.rename(tmp, final)
    except BaseException:
        remove_snapshot_dir(tmp)
        raise
    return rec
