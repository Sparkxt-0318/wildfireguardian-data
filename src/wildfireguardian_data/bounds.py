"""Bounding boxes, always paired with a CRS.

A bounds without a CRS is meaningless and this package will not accept one
(``docs/GLOSSARY.md``). ``(min_x, min_y, max_x, max_y)`` is in
(easting, northing) order -- see :mod:`wildfireguardian_data.crs` on why tuple
order is pinned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .crs import CRSLike, crs_equal, crs_to_string, parse_crs, require_same_crs
from .errors import ConfigError

__all__ = ["Bounds"]


@dataclass(frozen=True)
class Bounds:
    """An axis-aligned bounding box in a declared CRS."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float
    crs: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "min_x", float(self.min_x))
        object.__setattr__(self, "min_y", float(self.min_y))
        object.__setattr__(self, "max_x", float(self.max_x))
        object.__setattr__(self, "max_y", float(self.max_y))
        object.__setattr__(self, "crs", parse_crs(self.crs))
        if self.max_x < self.min_x or self.max_y < self.min_y:
            raise ConfigError(
                f"inverted bounds {self.as_tuple()!r}: expected min_x <= max_x "
                "and min_y <= max_y in (easting, northing) order. an inverted "
                "box usually means x and y were swapped, which is easy to do "
                "with the Korean KGD2002 CRSs (authority axis order is "
                "Northing, Easting)."
            )

    # -- degeneracy --------------------------------------------------------- #
    #: A zero-width or zero-height box is *allowed* here, because it is a real
    #: geometric fact: the bounds of a single point, or of a perfectly
    #: horizontal road segment, are degenerate. It is only an error where an
    #: extent is required -- a study area, a clip window -- so that check is
    #: opt-in via :meth:`require_nondegenerate` rather than applied to every
    #: box.
    @property
    def is_degenerate(self) -> bool:
        """Whether the box has zero width or zero height."""
        return self.width == 0.0 or self.height == 0.0

    def require_nondegenerate(self, context: str = "") -> "Bounds":
        """Return self, raising if the box has no extent.

        Called by study-area config parsing and by clipping, where a
        zero-extent box means the caller asked for nothing and would otherwise
        receive an empty layer that looks processed.
        """
        if self.is_degenerate:
            where = f" for {context}" if context else ""
            raise ConfigError(
                f"bounds{where} have zero extent: {self}. width={self.width:g}, "
                f"height={self.height:g}."
            )
        return self

    # -- geometry ----------------------------------------------------------- #
    def as_tuple(self) -> tuple[float, float, float, float]:
        """``(min_x, min_y, max_x, max_y)``."""
        return (self.min_x, self.min_y, self.max_x, self.max_y)

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def area(self) -> float:
        """Area in squared CRS units. Meaningless for a geographic CRS."""
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)

    def buffered(self, distance: float) -> "Bounds":
        """Expand (or, with a negative distance, shrink) by ``distance``.

        The distance is in CRS units -- metres for the projected Korean CRSs,
        **degrees** for EPSG:4326. Callers buffering in metres must be in a
        metre CRS; nothing here converts for them.
        """
        return Bounds(
            self.min_x - distance,
            self.min_y - distance,
            self.max_x + distance,
            self.max_y + distance,
            crs=self.crs,
        )

    def intersects(self, other: "Bounds") -> bool:
        """Whether two same-CRS boxes overlap. Raises on CRS mismatch."""
        require_same_crs([self.crs, other.crs], context="intersecting bounds")
        return not (
            self.max_x <= other.min_x
            or other.max_x <= self.min_x
            or self.max_y <= other.min_y
            or other.max_y <= self.min_y
        )

    def contains_point(self, x: float, y: float) -> bool:
        """Half-open containment: ``[min, max)`` on both axes.

        Half-open so that adjacent boxes tile a plane without double-counting a
        point on a shared edge.
        """
        return self.min_x <= x < self.max_x and self.min_y <= y < self.max_y

    def intersection(self, other: "Bounds") -> "Bounds":
        """Overlap of two same-CRS boxes. Raises if they do not overlap."""
        require_same_crs([self.crs, other.crs], context="intersecting bounds")
        if not self.intersects(other):
            raise ConfigError(
                f"bounds do not intersect: {self.as_tuple()} and {other.as_tuple()} "
                f"in {crs_to_string(self.crs)}. an empty intersection is "
                "returned as an error rather than as an empty box, because a "
                "silently empty study area produces empty layers that look "
                "processed."
            )
        return Bounds(
            max(self.min_x, other.min_x),
            max(self.min_y, other.min_y),
            min(self.max_x, other.max_x),
            min(self.max_y, other.max_y),
            crs=self.crs,
        )

    def same_crs_as(self, other: Any) -> bool:
        return crs_equal(self.crs, getattr(other, "crs", other))

    # -- serialisation ------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return {
            "min_x": self.min_x,
            "min_y": self.min_y,
            "max_x": self.max_x,
            "max_y": self.max_y,
            "crs": crs_to_string(self.crs),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Bounds":
        crs_text = payload.get("crs")
        return cls(
            payload["min_x"],
            payload["min_y"],
            payload["max_x"],
            payload["max_y"],
            crs=None if crs_text in (None, "UNKNOWN") else crs_text,
        )

    @classmethod
    def from_iterable(cls, values: Iterable[float], crs: CRSLike = None) -> "Bounds":
        vals = [float(v) for v in values]
        if len(vals) != 4:
            raise ConfigError(
                f"bounds needs exactly 4 values (min_x, min_y, max_x, max_y); got {vals!r}"
            )
        return cls(*vals, crs=crs)

    def __str__(self) -> str:
        return (
            f"Bounds({self.min_x:g}, {self.min_y:g}, {self.max_x:g}, "
            f"{self.max_y:g}) in {crs_to_string(self.crs)}"
        )
