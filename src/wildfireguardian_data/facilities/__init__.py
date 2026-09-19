"""Facilities: shelters, refuge candidates, responder bases, fire stations.

Presence in a dataset is not fitness for purpose
(``docs/ASSUMPTIONS.md`` A-FAC-1).
"""

from __future__ import annotations

from .io import facilities_from_layer, load_facility_layer
from .models import Facility, FacilityKind, OperationalStatus

__all__ = [
    "Facility",
    "FacilityKind",
    "OperationalStatus",
    "load_facility_layer",
    "facilities_from_layer",
]
