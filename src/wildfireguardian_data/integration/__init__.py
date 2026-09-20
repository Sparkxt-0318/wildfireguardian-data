"""The versioned boundary between this repository and its consumers.

Everything a downstream WildfireGuardian repository is permitted to depend on
lives here. ``StudyAreaBundle`` itself does not (``docs/DECISIONS.md`` D-0001).

This package **specifies** adapter contracts and reports readiness. It contains
no mission logic, no routing, no dispatch timing, and no dynamic state: those
belong to the consumer (``docs/SCOPE.md``, Phase 2 items 23 and 24).
"""

from __future__ import annotations

from .compatibility import (
    CONSUMER_PROFILES,
    ReadinessLevel,
    build_compatibility_report,
)
from .manifest import (
    BUNDLE_CONTRACT_SCHEMA_VERSION,
    BUNDLE_MANIFEST_NAME,
    CONTRACT_LAYER_SLOTS,
    LayerStatus,
    build_bundle_manifest,
)

__all__ = [
    "BUNDLE_CONTRACT_SCHEMA_VERSION",
    "BUNDLE_MANIFEST_NAME",
    "CONTRACT_LAYER_SLOTS",
    "LayerStatus",
    "build_bundle_manifest",
    "CONSUMER_PROFILES",
    "ReadinessLevel",
    "build_compatibility_report",
]
