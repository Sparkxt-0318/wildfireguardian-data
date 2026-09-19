"""Aggregate population models.

Aggregate only, at village / settlement level or coarser
(``docs/ASSUMPTIONS.md`` A-POP-1). No person-level records, and no medical,
disability-diagnosis, or care-status information about identifiable people
(A-POP-2, ``docs/DECISIONS.md`` D-0011).

The wider WildfireGuardian project studies mobility-limited residents, which
makes the pressure to carry a "who needs help" list into the data layer
structural rather than hypothetical. This module is where that pressure is
refused: an aggregate count of residents aged 65 and over in a village is
permitted; anything attached to an identifiable person or dwelling is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from ..errors import ConfigError
from ..provenance.models import UNKNOWN

__all__ = [
    "SettlementCentroidKind",
    "AgeStratum",
    "AgeStrataSet",
    "VillagePopulation",
]


class SettlementCentroidKind(str, Enum):
    """How a settlement's representative point was obtained.

    Recorded rather than assumed: a geometric centroid of a valley-shaped
    village polygon can land on a hillside with no houses on it, while a
    population-weighted centre lands among the dwellings. A downstream
    consumer measuring distance to that point needs to know which it got
    (A-POP-3).
    """

    GEOMETRIC_CENTROID = "geometric_centroid"
    REPRESENTATIVE_POINT = "representative_point"
    POPULATION_WEIGHTED = "population_weighted"
    SOURCE_PROVIDED = "source_provided"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AgeStratum:
    """A half-open age band ``[lower, upper)`` in whole years, with a count.

    Half-open and explicit because "65+" and "65-69" are routinely confused, and
    because inclusive upper bounds double-count boundary ages when strata are
    summed (A-POP-4). ``upper=None`` means open-ended (``65+``).

    ``count=None`` means *not supplied by the source*. It is never ``0``: a
    village with no reported figure and a village with zero residents in the
    band are different facts (A-POP-5).
    """

    lower: int
    upper: int | None
    count: int | None

    def __post_init__(self) -> None:
        if self.lower < 0:
            raise ConfigError(f"age stratum lower bound must be >= 0; got {self.lower}")
        if self.upper is not None:
            if self.upper <= self.lower:
                raise ConfigError(
                    f"age stratum [{self.lower}, {self.upper}) is empty or inverted; "
                    "bounds are half-open with upper > lower"
                )
        if self.count is not None and self.count < 0:
            raise ConfigError(
                f"age stratum [{self.lower}, {self.upper}) has negative count "
                f"{self.count}"
            )

    @property
    def label(self) -> str:
        return f"{self.lower}+" if self.upper is None else f"{self.lower}-{self.upper - 1}"

    def overlaps(self, other: AgeStratum) -> bool:
        """Whether two half-open bands share any age."""
        self_upper = float("inf") if self.upper is None else self.upper
        other_upper = float("inf") if other.upper is None else other.upper
        return self.lower < other_upper and other.lower < self_upper

    def to_dict(self) -> dict[str, Any]:
        return {
            "lower": self.lower,
            "upper": self.upper,
            "label": self.label,
            "count": self.count,
            "interval": "half_open_[lower,upper)_years",
        }


@dataclass(frozen=True)
class AgeStrataSet:
    """A set of non-overlapping age strata for one settlement."""

    strata: tuple[AgeStratum, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "strata", tuple(self.strata))
        for index, left in enumerate(self.strata):
            for right in self.strata[index + 1 :]:
                if left.overlaps(right):
                    raise ConfigError(
                        f"age strata {left.label} and {right.label} overlap; "
                        "overlapping strata double-count residents (A-POP-4)"
                    )

    @property
    def total_counted(self) -> int | None:
        """Sum of supplied counts, or ``None`` if none were supplied.

        Returns ``None`` rather than ``0`` when every count is missing, so an
        absent breakdown cannot be read as an empty village.
        """
        supplied = [s.count for s in self.strata if s.count is not None]
        return sum(supplied) if supplied else None

    @property
    def has_missing_counts(self) -> bool:
        return any(s.count is None for s in self.strata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strata": [s.to_dict() for s in self.strata],
            "total_counted": self.total_counted,
            "has_missing_counts": self.has_missing_counts,
        }


@dataclass(frozen=True)
class VillagePopulation:
    """Aggregate population for one settlement.

    Parameters
    ----------
    settlement_id:
        Stable identifier within the study area.
    name:
        Settlement name as the source gives it, or ``"UNKNOWN"``.
    geometry:
        Village polygon or boundary, if the source provides one.
    centroid:
        Representative point.
    centroid_kind:
        How ``centroid`` was obtained (A-POP-3).
    population_total:
        Total residents at the source's reference date, or ``None`` when not
        supplied -- never ``0`` as a stand-in (A-POP-5).
    age_strata:
        Optional age breakdown.
    count_basis:
        What the count counts: residential register, census, or ``"UNKNOWN"``.
        Not daytime or present population unless the source says so (A-POP-6).
    """

    settlement_id: str
    name: str = UNKNOWN
    geometry: BaseGeometry | None = None
    centroid: Point | None = None
    centroid_kind: SettlementCentroidKind = SettlementCentroidKind.UNKNOWN
    population_total: int | None = None
    age_strata: AgeStrataSet = field(default_factory=AgeStrataSet)
    count_basis: str = UNKNOWN
    reference_date: str = UNKNOWN
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.settlement_id).strip():
            raise ConfigError("VillagePopulation.settlement_id is required")
        object.__setattr__(
            self, "centroid_kind", SettlementCentroidKind(self.centroid_kind)
        )
        object.__setattr__(self, "attributes", dict(self.attributes))
        if self.population_total is not None and self.population_total < 0:
            raise ConfigError(
                f"settlement {self.settlement_id!r} has negative population_total "
                f"{self.population_total}"
            )
        if self.geometry is None and self.centroid is None:
            raise ConfigError(
                f"settlement {self.settlement_id!r} has neither geometry nor "
                "centroid; a population record with no location cannot be used "
                "spatially and is rejected rather than stored."
            )
        if self.centroid is None and self.geometry is not None:
            # Derived, and labelled as derived -- not silently presented as if
            # the source had supplied a point.
            object.__setattr__(self, "centroid", self.geometry.representative_point())
            if self.centroid_kind is SettlementCentroidKind.UNKNOWN:
                object.__setattr__(
                    self, "centroid_kind", SettlementCentroidKind.REPRESENTATIVE_POINT
                )

    @property
    def strata_total_matches_total(self) -> bool | None:
        """Whether the age strata sum to ``population_total``.

        ``None`` when either side is unknown. Discrepancies are **reported, not
        reconciled** (A-POP-5): a mismatch usually means the strata come from a
        different date or a different definition than the total, and
        rescaling one to fit the other would destroy that signal.
        """
        counted = self.age_strata.total_counted
        if counted is None or self.population_total is None:
            return None
        return counted == self.population_total

    def to_dict(self) -> dict[str, Any]:
        return {
            "settlement_id": self.settlement_id,
            "name": self.name,
            "population_total": self.population_total,
            "count_basis": self.count_basis,
            "reference_date": self.reference_date,
            "centroid": (
                [float(self.centroid.x), float(self.centroid.y)]
                if self.centroid is not None
                else None
            ),
            "centroid_kind": self.centroid_kind.value,
            "has_geometry": self.geometry is not None,
            "age_strata": self.age_strata.to_dict(),
            "strata_total_matches_total": self.strata_total_matches_total,
            "attributes": dict(self.attributes),
        }
