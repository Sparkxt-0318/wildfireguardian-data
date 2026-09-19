"""Population: aggregate settlement-level data only.

No person-level or medical information, enforced at load time
(``docs/DECISIONS.md`` D-0011).
"""

from __future__ import annotations

from .io import (
    DEFAULT_MIN_AGGREGATE_COUNT,
    PRIVACY_FORBIDDEN_FIELD_PATTERNS,
    check_privacy,
    load_population_layer,
    villages_from_layer,
)
from .models import AgeStrataSet, AgeStratum, SettlementCentroidKind, VillagePopulation

__all__ = [
    "AgeStratum",
    "AgeStrataSet",
    "VillagePopulation",
    "SettlementCentroidKind",
    "check_privacy",
    "load_population_layer",
    "villages_from_layer",
    "PRIVACY_FORBIDDEN_FIELD_PATTERNS",
    "DEFAULT_MIN_AGGREGATE_COUNT",
]
