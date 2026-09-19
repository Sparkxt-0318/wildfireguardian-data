"""Explicit units.

Every physical quantity in this package carries its unit as data, not as a
convention in a docstring. ``docs/ASSUMPTIONS.md`` A-TER-4/8 and A-RD-5 depend
on this module; changing a factor or a default here is a scientific change and
requires a ``docs/DECISIONS.md`` entry.

Canonical units: length **metre**, slope **degree**, time **second**, area
**square metre**.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from enum import Enum

from .errors import UnitMismatchError, UnknownUnitError

__all__ = [
    "LengthUnit",
    "DistanceUnit",
    "ElevationUnit",
    "SlopeUnit",
    "TimeUnit",
    "AreaUnit",
    "convert_length",
    "convert_slope",
    "convert_time",
    "convert_area",
    "parse_length_unit",
    "parse_slope_unit",
    "require_same_length_unit",
    "KOREA_TIMEZONE",
]

#: Korean local time. UTC+09:00, and Korea has observed no DST since 1988.
#: Used for interpreting source dates that are stated in local time.
KOREA_TIMEZONE = "Asia/Seoul"


class LengthUnit(str, Enum):
    """A unit of length, used for horizontal distance, elevation and resolution.

    Horizontal distance, elevation and cell size all share this enum on purpose:
    slope is only meaningful when the vertical and horizontal units are the same
    (A-TER-8), and a shared type makes that check possible.
    """

    METRE = "m"
    KILOMETRE = "km"
    FOOT = "ft"

    @property
    def metres(self) -> float:
        """Number of metres in one of this unit (exact for all three)."""
        return _LENGTH_TO_METRE[self]


#: The international foot, exactly 0.3048 m. The US survey foot
#: (1200/3937 m) is deliberately **not** offered: a source that uses it must say
#: so, and mixing the two silently is a classic sub-metre error.
_LENGTH_TO_METRE: dict[LengthUnit, float] = {
    LengthUnit.METRE: 1.0,
    LengthUnit.KILOMETRE: 1000.0,
    LengthUnit.FOOT: 0.3048,
}

#: Aliases that make call sites read correctly. They are the *same* enum; see
#: the class docstring for why that is intentional.
DistanceUnit = LengthUnit
ElevationUnit = LengthUnit


class SlopeUnit(str, Enum):
    """A unit of slope (steepness).

    ``PERCENT`` is percent *rise* (``100 * tan(theta)``), not percent of 90
    degrees. Conversion between ``PERCENT`` and the angular units is therefore
    **non-linear**, which is why slope conversion never multiplies by a factor.
    """

    DEGREE = "deg"
    RADIAN = "rad"
    PERCENT = "percent"


class TimeUnit(str, Enum):
    """A unit of duration. Canonical unit is the second."""

    SECOND = "s"
    MINUTE = "min"
    HOUR = "h"

    @property
    def seconds(self) -> float:
        return _TIME_TO_SECOND[self]


_TIME_TO_SECOND: dict[TimeUnit, float] = {
    TimeUnit.SECOND: 1.0,
    TimeUnit.MINUTE: 60.0,
    TimeUnit.HOUR: 3600.0,
}


class AreaUnit(str, Enum):
    """A unit of area. Canonical unit is the square metre."""

    SQUARE_METRE = "m2"
    HECTARE = "ha"
    SQUARE_KILOMETRE = "km2"

    @property
    def square_metres(self) -> float:
        return _AREA_TO_SQUARE_METRE[self]


_AREA_TO_SQUARE_METRE: dict[AreaUnit, float] = {
    AreaUnit.SQUARE_METRE: 1.0,
    AreaUnit.HECTARE: 10_000.0,
    AreaUnit.SQUARE_KILOMETRE: 1_000_000.0,
}


def parse_length_unit(value: str | LengthUnit) -> LengthUnit:
    """Resolve a length-unit string, accepting the spellings sources use.

    Raises :class:`~wildfireguardian_data.errors.UnknownUnitError` rather than
    guessing. In particular ``"US survey foot"`` is rejected, not mapped to
    ``FOOT``.
    """
    if isinstance(value, LengthUnit):
        return value
    key = str(value).strip().lower()
    known = {
        "m": LengthUnit.METRE,
        "metre": LengthUnit.METRE,
        "meter": LengthUnit.METRE,
        "metres": LengthUnit.METRE,
        "meters": LengthUnit.METRE,
        "km": LengthUnit.KILOMETRE,
        "kilometre": LengthUnit.KILOMETRE,
        "kilometer": LengthUnit.KILOMETRE,
        "ft": LengthUnit.FOOT,
        "foot": LengthUnit.FOOT,
        "feet": LengthUnit.FOOT,
        "international foot": LengthUnit.FOOT,
    }
    if key in known:
        return known[key]
    raise UnknownUnitError(
        f"unrecognised length unit {value!r}. known: {sorted(known)}. "
        "if the source uses the US survey foot, that is a different unit and "
        "must be converted explicitly by the caller."
    )


def parse_slope_unit(value: str | SlopeUnit) -> SlopeUnit:
    """Resolve a slope-unit string, raising on anything ambiguous."""
    if isinstance(value, SlopeUnit):
        return value
    key = str(value).strip().lower()
    known = {
        "deg": SlopeUnit.DEGREE,
        "degree": SlopeUnit.DEGREE,
        "degrees": SlopeUnit.DEGREE,
        "rad": SlopeUnit.RADIAN,
        "radian": SlopeUnit.RADIAN,
        "radians": SlopeUnit.RADIAN,
        "percent": SlopeUnit.PERCENT,
        "%": SlopeUnit.PERCENT,
        "percent_rise": SlopeUnit.PERCENT,
    }
    if key in known:
        return known[key]
    raise UnknownUnitError(f"unrecognised slope unit {value!r}. known: {sorted(known)}")


def convert_length(value, src: LengthUnit, dst: LengthUnit):
    """Convert a length (scalar or array) between length units.

    Works on NumPy arrays as well as scalars because the operation is a single
    multiplication.
    """
    src = parse_length_unit(src)
    dst = parse_length_unit(dst)
    if src is dst:
        return value
    return value * (src.metres / dst.metres)


def convert_slope(value, src: SlopeUnit, dst: SlopeUnit):
    """Convert a slope between degree, radian, and percent rise.

    Non-linear through ``PERCENT``: ``percent = 100 * tan(radians)``. A slope of
    100 percent is 45 degrees, not 90. Vertical (90 degrees) has no finite
    percent representation and converts to ``inf``.
    """
    src = parse_slope_unit(src)
    dst = parse_slope_unit(dst)
    if src is dst:
        return value

    # to radians
    if src is SlopeUnit.DEGREE:
        rad = _deg2rad(value)
    elif src is SlopeUnit.RADIAN:
        rad = value
    else:  # PERCENT
        rad = _arctan(value / 100.0)

    if dst is SlopeUnit.RADIAN:
        return rad
    if dst is SlopeUnit.DEGREE:
        return _rad2deg(rad)
    return _tan(rad) * 100.0


def convert_time(value, src: TimeUnit, dst: TimeUnit):
    """Convert a duration between time units."""
    if src is dst:
        return value
    return value * (TimeUnit(src).seconds / TimeUnit(dst).seconds)


def convert_area(value, src: AreaUnit, dst: AreaUnit):
    """Convert an area between area units."""
    if src is dst:
        return value
    return value * (AreaUnit(src).square_metres / AreaUnit(dst).square_metres)


def require_same_length_unit(
    units: Iterable[LengthUnit], context: str = ""
) -> LengthUnit:
    """Assert that every length unit given is the same one, and return it.

    Used where mixing units would be a scaling opportunity rather than an error
    -- for example vertical vs horizontal units in a slope computation
    (A-TER-8). Raises :class:`UnitMismatchError`.
    """
    seen = [parse_length_unit(u) for u in units]
    if not seen:
        raise UnitMismatchError("no units given to require_same_length_unit")
    first = seen[0]
    for other in seen[1:]:
        if other is not first:
            where = f" while {context}" if context else ""
            raise UnitMismatchError(
                f"length units differ{where}: {first.value} vs {other.value}. "
                "this package does not rescale one to the other implicitly; "
                "convert explicitly with units.convert_length."
            )
    return first


# Small shims so this module works on scalars and NumPy arrays alike without
# importing NumPy at module import time.
def _deg2rad(v):
    try:
        return math.radians(v)
    except TypeError:
        import numpy as np

        return np.radians(v)


def _rad2deg(v):
    try:
        return math.degrees(v)
    except TypeError:
        import numpy as np

        return np.degrees(v)


def _tan(v):
    try:
        return math.tan(v)
    except TypeError:
        import numpy as np

        return np.tan(v)


def _arctan(v):
    try:
        return math.atan(v)
    except TypeError:
        import numpy as np

        return np.arctan(v)
