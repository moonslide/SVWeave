"""Moved to :mod:`svweave.adapters.capability` (both adapters and orchestration use it; the
dependency runs orchestration -> adapters only: first council review, architecture finding 5).
Re-exported here for existing importers."""

from svweave.adapters.capability import (
    CBM_ALLOWLIST,
    CBM_CORE_FEATURES,
    CBM_FEATURES,
    SERENA_ALLOWLIST,
    SERENA_CORE_FEATURES,
    SERENA_FEATURES,
    SERENA_FORBIDDEN,
    CapabilityComparison,
    SerenaEditSubset,
    ToolStatus,
    compare_capabilities,
    derive_serena_edit_subset,
)

__all__ = [
    "CBM_ALLOWLIST",
    "CBM_CORE_FEATURES",
    "CBM_FEATURES",
    "CapabilityComparison",
    "SERENA_ALLOWLIST",
    "SERENA_CORE_FEATURES",
    "SERENA_FEATURES",
    "SERENA_FORBIDDEN",
    "SerenaEditSubset",
    "ToolStatus",
    "compare_capabilities",
    "derive_serena_edit_subset",
]
