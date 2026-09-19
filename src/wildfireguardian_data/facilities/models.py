"""Facility models: shelters, refuge candidates, responder bases, fire stations.

The governing rule (``docs/ASSUMPTIONS.md`` A-FAC-1): **presence in a dataset is
not fitness for purpose.** Every facility carries an ``operational_status`` that
defaults to ``UNKNOWN`` and a ``suitability_assessed`` flag that defaults to
``False``, and there is no field anywhere that says a facility is safe.

The roles below are **source-declared**, not verified capabilities (A-FAC-2). A
building tagged ``amenity=shelter`` in OpenStreetMap is as likely to be a
roadside bus shelter as a place anyone could shelter from a wildfire in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from ..errors import ConfigError
from ..provenance.models import UNKNOWN

__all__ = ["FacilityKind", "OperationalStatus", "Facility"]


class FacilityKind(str, Enum):
    """A facility's role **as declared by its source**.

    ``TEMPORARY_REFUGE_CANDIDATE`` is named "candidate" for the same reason
    ``single_egress_candidates`` is (``docs/DECISIONS.md`` D-0008): an open
    space that might serve as refuge is a hypothesis about a place, and the
    dataset cannot confirm it.
    """

    SHELTER = "shelter"
    TEMPORARY_REFUGE_CANDIDATE = "temporary_refuge_candidate"
    RESPONDER_BASE = "responder_base"
    FIRE_STATION = "fire_station"
    OTHER = "other"


class OperationalStatus(str, Enum):
    """Whether the facility is in service, as far as the source says.

    ``UNKNOWN`` is the default and the common case. ``ASSUMED_OPERATIONAL`` does
    not exist as a value on purpose: assuming is the failure this enum prevents.
    """

    OPERATIONAL = "operational"
    NOT_OPERATIONAL = "not_operational"
    SEASONAL = "seasonal"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Facility:
    """One facility record.

    Parameters
    ----------
    facility_id:
        Stable identifier within the study area.
    kind:
        :class:`FacilityKind`, as declared by the source.
    geometry:
        Point or polygon in the layer's CRS.
    capacity_persons:
        ``None`` unless the source supplies it. **Never** estimated from
        building footprint area (A-FAC-3): floor area per person is a building-
        code assumption, not a property of a polygon, and a fabricated capacity
        would propagate straight into a downstream sheltering analysis.
    suitability_assessed:
        ``False`` unless a documented assessment exists outside this
        repository. This repository performs no suitability assessment.
    """

    facility_id: str
    kind: FacilityKind
    geometry: BaseGeometry
    name: str = UNKNOWN
    operational_status: OperationalStatus = OperationalStatus.UNKNOWN
    capacity_persons: int | None = None
    suitability_assessed: bool = False
    suitability_note: str = UNKNOWN
    source_kind_tag: str = UNKNOWN
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.facility_id).strip():
            raise ConfigError("Facility.facility_id is required")
        object.__setattr__(self, "kind", FacilityKind(self.kind))
        object.__setattr__(
            self, "operational_status", OperationalStatus(self.operational_status)
        )
        object.__setattr__(self, "attributes", dict(self.attributes))
        if self.geometry is None or self.geometry.is_empty:
            raise ConfigError(
                f"facility {self.facility_id!r} has no geometry; a facility "
                "without a location cannot be used and is rejected rather than "
                "stored with a placeholder."
            )
        if self.capacity_persons is not None and self.capacity_persons < 0:
            raise ConfigError(
                f"facility {self.facility_id!r} has negative capacity "
                f"{self.capacity_persons}"
            )
        if self.suitability_assessed and self.suitability_note == UNKNOWN:
            raise ConfigError(
                f"facility {self.facility_id!r} claims suitability_assessed=True "
                "but gives no suitability_note. an assessment with no stated "
                "basis is not an assessment."
            )

    @property
    def representative_point(self) -> Point:
        """A point inside the facility's geometry."""
        if isinstance(self.geometry, Point):
            return self.geometry
        return self.geometry.representative_point()

    def to_dict(self) -> dict[str, Any]:
        point = self.representative_point
        return {
            "facility_id": self.facility_id,
            "kind": self.kind.value,
            "name": self.name,
            "operational_status": self.operational_status.value,
            "capacity_persons": self.capacity_persons,
            "suitability_assessed": self.suitability_assessed,
            "suitability_note": self.suitability_note,
            "source_kind_tag": self.source_kind_tag,
            "geometry_type": self.geometry.geom_type,
            "representative_point": [float(point.x), float(point.y)],
            "attributes": dict(self.attributes),
            "caveat": (
                "role is source-declared, not verified; this record makes no "
                "claim that the facility is usable or safe "
                "(docs/ASSUMPTIONS.md A-FAC-1/2)"
            ),
        }
