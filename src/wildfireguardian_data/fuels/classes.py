"""Fuel / vegetation class schemes.

This package **does not invent Korean fuel datasets or crosswalks**
(``docs/ASSUMPTIONS.md`` A-FU-1). A scheme must either cite a real source or
declare itself :attr:`DataClass.SYNTHETIC`, and the constructor enforces that:
an unsourced, unlabelled class table would be indistinguishable from a real one
once it reached a downstream fire-behaviour model.

Fuel class codes are **nominal** categories (A-FU-3). Nothing here defines an
ordering, a flammability ranking, or a spread parameter -- those are
fire-behaviour modelling, which is out of scope (``docs/SCOPE.md``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..errors import ConfigError
from ..provenance.models import UNKNOWN, DataClass

__all__ = ["FuelClass", "FuelClassScheme", "SYNTHETIC_DEMO_SCHEME"]


@dataclass(frozen=True)
class FuelClass:
    """One nominal fuel/vegetation class.

    ``description`` is free text from the source. There is deliberately no
    ``flammability``, ``rate_of_spread``, or ``load`` field: assigning those is
    fire-behaviour modelling and does not belong in this repository.
    """

    code: int
    label: str
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.code, int) or isinstance(self.code, bool):
            raise ConfigError(
                f"fuel class code must be an int; got {self.code!r} "
                f"({type(self.code).__name__})"
            )
        if not self.label.strip():
            raise ConfigError(f"fuel class {self.code} has an empty label")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "label": self.label, "description": self.description}


@dataclass(frozen=True)
class FuelClassScheme:
    """A named set of fuel classes, with its provenance obligations enforced.

    Parameters
    ----------
    name:
        Scheme identifier, e.g. ``"synthetic_demo_v1"``.
    classes:
        The classes. Codes must be unique.
    data_class:
        :class:`DataClass`. ``SYNTHETIC`` is the only value permitted without a
        real ``source``.
    source:
        The dataset or publication defining the scheme. Required unless
        ``data_class`` is ``SYNTHETIC``.
    nodata_code:
        The code meaning "no class here". Required, and must not collide with a
        real class: without it, a fuel raster's gaps would have to be written as
        some real class, most likely ``0``, silently turning missing vegetation
        data into a vegetation type (``docs/FAILURE_MODES.md`` F-MD-1).
    """

    name: str
    classes: tuple[FuelClass, ...]
    data_class: DataClass
    nodata_code: int
    source: str = UNKNOWN
    source_url: str = UNKNOWN
    notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "classes", tuple(self.classes))
        object.__setattr__(self, "data_class", DataClass(self.data_class))
        if not self.name.strip():
            raise ConfigError("FuelClassScheme.name is required")
        if not self.classes:
            raise ConfigError(f"fuel scheme {self.name!r} defines no classes")
        codes = [c.code for c in self.classes]
        if len(set(codes)) != len(codes):
            duplicates = sorted({c for c in codes if codes.count(c) > 1})
            raise ConfigError(
                f"fuel scheme {self.name!r} has duplicate class codes {duplicates}"
            )
        if self.nodata_code in codes:
            raise ConfigError(
                f"fuel scheme {self.name!r} uses nodata_code={self.nodata_code}, "
                f"which is also a real class code. missing data and a real fuel "
                "class must be distinguishable (docs/ASSUMPTIONS.md A-FU-2)."
            )
        if self.data_class is not DataClass.SYNTHETIC and self.source == UNKNOWN:
            raise ConfigError(
                f"fuel scheme {self.name!r} is declared {self.data_class.value} but "
                "cites no source. a non-synthetic fuel scheme must name where it "
                "comes from; this package does not invent Korean fuel crosswalks "
                "(docs/ASSUMPTIONS.md A-FU-1). if it is a test construct, declare "
                "data_class=DataClass.SYNTHETIC."
            )

    @property
    def codes(self) -> tuple[int, ...]:
        return tuple(c.code for c in self.classes)

    def label_for(self, code: int) -> str:
        """Label for a code; ``"UNKNOWN"`` for an unrecognised one, never a guess."""
        if code == self.nodata_code:
            return "nodata"
        for entry in self.classes:
            if entry.code == code:
                return entry.label
        return UNKNOWN

    def unknown_codes(self, observed: Iterable[int]) -> tuple[int, ...]:
        """Observed codes that the scheme does not define.

        A non-empty result means the raster and the scheme disagree, which is
        reported as a validation ERROR rather than passed through: an
        undefined code is data whose meaning nobody knows.
        """
        known = set(self.codes) | {self.nodata_code}
        return tuple(sorted({int(c) for c in observed} - known))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_class": self.data_class.value,
            "source": self.source,
            "source_url": self.source_url,
            "nodata_code": self.nodata_code,
            "classes": [c.to_dict() for c in self.classes],
            "notes": self.notes,
            "semantics": (
                "nominal categories; no ordering, no flammability ranking, no "
                "spread parameters (docs/ASSUMPTIONS.md A-FU-3)"
            ),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FuelClassScheme":
        return cls(
            name=payload["name"],
            classes=tuple(FuelClass(**c) for c in payload["classes"]),
            data_class=DataClass(payload["data_class"]),
            nodata_code=int(payload["nodata_code"]),
            source=payload.get("source", UNKNOWN),
            source_url=payload.get("source_url", UNKNOWN),
            notes=payload.get("notes", ""),
        )


#: A deliberately generic, explicitly synthetic scheme used by the fixtures and
#: the example bundle. The class names describe broad land-cover ideas that are
#: not specific to any Korean dataset, precisely so that nobody mistakes this
#: for a real Korean forest-type classification. Real Korean vegetation data was
#: not obtained -- see ``docs/DATA_PROVENANCE.md`` §Access attempts.
SYNTHETIC_DEMO_SCHEME = FuelClassScheme(
    name="synthetic_demo_v1",
    data_class=DataClass.SYNTHETIC,
    nodata_code=255,
    classes=(
        FuelClass(1, "non_vegetated", "bare ground, rock, built-up, water"),
        FuelClass(2, "grass_or_crop", "herbaceous cover including paddy and dry field"),
        FuelClass(3, "shrub", "shrubland and regenerating cover"),
        FuelClass(4, "broadleaf_forest", "broadleaf-dominated forest canopy"),
        FuelClass(5, "conifer_forest", "conifer-dominated forest canopy"),
        FuelClass(6, "mixed_forest", "mixed broadleaf and conifer canopy"),
    ),
    notes=(
        "SYNTHETIC. invented for testing and for the example bundle; it is not a "
        "crosswalk to any Korean forest-type classification and must not be "
        "treated as one."
    ),
)
