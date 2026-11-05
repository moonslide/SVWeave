"""Workspace secret used to sign pagination cursors (plan 14.2).

Stored in the trusted per-workspace directory (``config/trusted.py``), never inside the repository,
so repository content cannot read or forge cursors. The key is >= 32 random bytes, mode 0600, in a
0700 directory, created and read by the same hardened helpers as the approval key: a weak, linked,
foreign or group/other-readable file is refused (delete it to generate a new one; outstanding
cursors then stop working), never used or silently tightened.
"""

from __future__ import annotations

from svweave.config.context import WorkspaceContext
from svweave.config.trusted import create_secret, read_secret

KEY_FILENAME = "cursor.key"
KEY_BYTES = 32
_CHECK = {"reason": "cursor_key_untrusted", "weak_reason": "cursor_key_weak"}


def cursor_key(ctx: WorkspaceContext) -> bytes:
    key = read_secret(ctx.trusted_dir, KEY_FILENAME, min_bytes=KEY_BYTES, **_CHECK)
    if key is not None:
        return key
    return create_secret(ctx.trusted_dir, KEY_FILENAME, KEY_BYTES, **_CHECK)
