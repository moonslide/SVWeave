"""Trusted approval store (plan 12.4, 23.2; tests EDT-04, SEC-06).

An approval is a JSON record in ``<trusted_dir>/approvals/<txn>.json``, HMAC-SHA256 signed with a
key in ``<trusted_dir>/approval.key`` (0600). It binds: transaction id, patch hash, base snapshot
id, the validate-profile set, the trusted policy hash and an expiry. ANY change of those makes it
invalid. The record is created only by the human CLI (``svweave edit approve``); no MCP request
field can create or influence it.

HONEST LIMIT (plan 23.2): the key and the store live in the same trust domain as the user. An
agent with the SAME UID and an unrestricted shell can read the key and forge a record. The HMAC
defeats a fabricated or edited file written by a process that cannot read the key, and the
absence of any approval input in the MCP surface defeats ``approved=true``; it is NOT a defence
against a same-UID unrestricted agent (``doctor`` reports the client boundary as not qualified).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from svweave.config.trusted import create_secret, ensure_trusted_dir, read_secret
from svweave.domain.errors import ErrorCode, SvwError
from svweave.domain.ids import canonical_json
from svweave.security import safeio

KEY_FILENAME = "approval.key"
APPROVALS_DIR = "approvals"
DEFAULT_TTL_S = 30 * 60
SCHEMA = "svweave-approval-v1"


@dataclass(frozen=True)
class Binding:
    """What an approval is bound to; recomputed from live state at commit time."""

    transaction_id: str
    patch_hash: str
    base_snapshot_id: str
    profiles: tuple[str, ...]
    policy_hash: str
    config_ids: tuple[tuple[str, str], ...] = ()  # (profile, config_id), sorted
    #: hash of the editing-policy inputs at approval time (validate floor, allowed_paths, source
    #: root writability of the edited files); a change invalidates the approval (A4)
    edit_policy_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "patch_hash": self.patch_hash,
            "base_snapshot_id": self.base_snapshot_id,
            "profiles": sorted(self.profiles),
            "policy_hash": self.policy_hash,
            "config_ids": {p: c for p, c in sorted(self.config_ids)},
            "edit_policy_hash": self.edit_policy_hash,
        }


def _utcnow() -> datetime:
    return datetime.now(UTC)


def approval_key(trusted: Path, *, create: bool) -> bytes | None:
    """The HMAC key of the approval store (``config/trusted.py`` helpers: private, owned, read
    without following a link); created on the first approval when ``create``."""
    key = read_secret(trusted, KEY_FILENAME, min_bytes=32, reason="approval_key_unsafe")
    if key is not None or not create:
        return key
    return create_secret(trusted, KEY_FILENAME, 32, reason="approval_key_unsafe")


def _sig(key: bytes, body: dict[str, Any]) -> str:
    return hmac.new(key, canonical_json(body).encode(), hashlib.sha256).hexdigest()


def approval_path(trusted: Path, txn_id: str) -> Path:
    if safeio.safe_rel_path(txn_id) is None or "/" in txn_id:
        raise SvwError(ErrorCode.INVALID_CONFIG, "bad transaction id", {"field": "transaction_id"})
    return trusted / APPROVALS_DIR / f"{txn_id}.json"


def write_approval(
    trusted: Path,
    binding: Binding,
    *,
    method: str,
    ttl_s: int = DEFAULT_TTL_S,
    now: datetime | None = None,
) -> dict[str, Any]:
    key = approval_key(trusted, create=True)
    assert key is not None
    t = now or _utcnow()
    body = {
        "schema": SCHEMA,
        **binding.as_dict(),
        "method": method,
        "approved_at": t.isoformat(timespec="seconds"),
        "expires_at": (t + timedelta(seconds=ttl_s)).isoformat(timespec="seconds"),
    }
    rec = {**body, "hmac": _sig(key, body)}
    ensure_trusted_dir(trusted)
    d = trusted / APPROVALS_DIR
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    safeio.write_under(
        trusted,
        f"{APPROVALS_DIR}/{binding.transaction_id}.json",
        (json.dumps(rec, indent=2, sort_keys=True) + "\n").encode(),
        0o600,
        durable=True,
    )
    return rec


def verify_approval(
    trusted: Path,
    binding: Binding,
    *,
    allow_test_approvals: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the valid record or raise APPROVAL_REQUIRED / APPROVAL_EXPIRED. Never repairs."""

    def need(reason: str, **extra: Any) -> SvwError:
        return SvwError(
            ErrorCode.APPROVAL_REQUIRED,
            f"no valid human approval for this transaction ({reason}); run "
            "`svweave edit approve` in a trusted terminal",
            {"reason": reason, "transaction_id": binding.transaction_id, **extra},
        )

    p = approval_path(trusted, binding.transaction_id)
    key = approval_key(trusted, create=False)
    if key is None or not p.exists():
        raise need("approval_missing")
    try:
        rec = json.loads(safeio.read_regular(p, 1 << 16).decode("utf-8"))
    except (OSError, ValueError, SvwError) as e:
        raise need("approval_unreadable") from e
    if not isinstance(rec, dict) or not isinstance(rec.get("hmac"), str):
        raise need("approval_malformed")
    body = {k: v for k, v in rec.items() if k != "hmac"}
    if not hmac.compare_digest(rec["hmac"], _sig(key, body)):
        raise need("approval_signature_invalid")
    if body.get("schema") != SCHEMA:
        raise need("approval_schema")
    for k, v in binding.as_dict().items():
        if body.get(k) != v:
            raise need("approval_binding_mismatch", field=k)
    if body.get("method") != "tty" and not allow_test_approvals:
        raise need("test_approvals_not_allowed", method=str(body.get("method")))
    try:
        exp = datetime.fromisoformat(str(body["expires_at"]))
    except (KeyError, ValueError) as e:
        raise need("approval_malformed") from e
    if (now or _utcnow()) >= exp:
        raise SvwError(
            ErrorCode.APPROVAL_EXPIRED,
            "the human approval has expired; approve again",
            {"transaction_id": binding.transaction_id, "expires_at": body["expires_at"]},
        )
    return rec
