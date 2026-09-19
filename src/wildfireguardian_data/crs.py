"""Coordinate-reference-system handling, built to fail loudly.

The governing rule (``docs/DECISIONS.md`` D-0002): **this package never
reprojects implicitly.** Every function here either answers a question about a
CRS or raises.

Coordinate tuple order
----------------------
This package always uses **(x, y) = (easting, northing)** tuple order in memory,
regardless of the authority's declared axis order. That matters in Korea: the
EPSG definitions of the KGD2002 belt systems (5179, 5185, 5186, 5187) declare
**(Northing, Easting)** axis order, so a naive read of an authority-ordered
coordinate pair swaps x and y. Reprojections in this package use
``pyproj.Transformer(..., always_xy=True)``, which is what Shapely, rasterio and
GeoJSON also assume. :func:`authority_axis_order_is_xy` reports the declared
order so the discrepancy can be recorded rather than discovered.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pyproj import CRS as PyprojCRS
from pyproj import Transformer

from .errors import (
    CRSMismatchError,
    GeographicCRSError,
    NonMetreCRSError,
    UnknownCRSError,
)
from .units import LengthUnit, parse_length_unit

__all__ = [
    "CRSLike",
    "parse_crs",
    "crs_to_string",
    "crs_equal",
    "require_crs",
    "require_same_crs",
    "require_projected_metre_crs",
    "is_geographic",
    "crs_axis_length_unit",
    "authority_axis_order_is_xy",
    "transformer_for",
    "KOREAN_CRS_NOTES",
    "KoreanCRSNote",
]

CRSLike = Any
"""Anything :func:`parse_crs` accepts: a ``pyproj.CRS``, an ``"EPSG:5179"``
string, an EPSG integer, a WKT/PROJ string, a CF-style dict, or ``None``."""


@dataclass(frozen=True)
class KoreanCRSNote:
    """A note about a CRS commonly encountered in Korean geospatial data.

    ``name`` is **not** hard-coded truth: :func:`check_korean_crs_notes` (and
    ``tests/test_crs_safeguards.py``) assert that the installed PROJ database
    agrees with it, so a PROJ upgrade that renames a CRS shows up as a test
    failure rather than as documentation drift.
    """

    code: str
    name: str
    kind: str  # "projected" | "geographic"
    axis_unit: str
    note: str


#: Reference notes only. Nothing in this package consults this table to *choose*
#: a CRS; a study area declares its own analysis CRS in its config (A-CRS-1).
KOREAN_CRS_NOTES: dict[str, KoreanCRSNote] = {
    "EPSG:5179": KoreanCRSNote(
        code="EPSG:5179",
        name="KGD2002 / Unified CS",
        kind="projected",
        axis_unit="metre",
        note=(
            "Single nationwide Transverse Mercator grid on the Korea 2000 / "
            "KGD2002 datum. Preferred analysis CRS for national-extent Korean "
            "work (A-CRS-2). Authority axis order is (Northing, Easting); this "
            "package uses (x=Easting, y=Northing) in memory. Also written "
            "'Korea 2000 / Unified CS' in older PROJ databases."
        ),
    ),
    "EPSG:5186": KoreanCRSNote(
        code="EPSG:5186",
        name="KGD2002 / Central Belt 2010",
        kind="projected",
        axis_unit="metre",
        note=(
            "Central belt of the KGD2002 belt system, the CRS most Korean "
            "cadastral and municipal layers arrive in. Accepted analysis CRS. "
            "Authority axis order is (Northing, Easting)."
        ),
    ),
    "EPSG:5187": KoreanCRSNote(
        code="EPSG:5187",
        name="KGD2002 / East Belt 2010",
        kind="projected",
        axis_unit="metre",
        note=(
            "East belt of the KGD2002 belt system; covers the eastern Korean "
            "coast including Uljin and Samcheok. Authority axis order is "
            "(Northing, Easting)."
        ),
    ),
    "EPSG:5174": KoreanCRSNote(
        code="EPSG:5174",
        name="Korean 1985 / Modified Central Belt",
        kind="projected",
        axis_unit="metre",
        note=(
            "Legacy Bessel-datum CRS. Older Korean data is often in this and is "
            "often *mislabelled* as EPSG:5186; the datum shift from Korean 1985 "
            "to KGD2002 is of order 100-200 m, which is enough to move a "
            "village to the wrong side of a ridge. Treated as a distinct CRS "
            "and never silently equated with 5186 "
            "(docs/FAILURE_MODES.md F-CRS-2)."
        ),
    ),
    "EPSG:32652": KoreanCRSNote(
        code="EPSG:32652",
        name="WGS 84 / UTM zone 52N",
        kind="projected",
        axis_unit="metre",
        note=(
            "UTM zone covering most of South Korea (126E-132E). Accepted "
            "analysis CRS; convenient when combining with global products. "
            "Authority axis order is (Easting, Northing)."
        ),
    ),
    "EPSG:4326": KoreanCRSNote(
        code="EPSG:4326",
        name="WGS 84",
        kind="geographic",
        axis_unit="degree",
        note=(
            "Geographic CRS in degrees. The CRS most global sources (Copernicus "
            "DEM, OSM) arrive in, and never a valid CRS for slope or length "
            "work here (D-0010). Authority axis order is (Latitude, Longitude)."
        ),
    ),
}


def parse_crs(value: CRSLike) -> PyprojCRS | None:
    """Parse anything CRS-shaped into a ``pyproj.CRS``, or ``None``.

    ``None`` in, ``None`` out: an unknown CRS is a legitimate *state* for a
    loaded layer (A-CRS-5), just not for an operation. Raises
    :class:`~wildfireguardian_data.errors.UnknownCRSError` when a non-``None``
    value cannot be parsed, rather than returning ``None`` -- an unparseable CRS
    and an absent CRS are different problems.
    """
    if value is None:
        return None
    if isinstance(value, PyprojCRS):
        return value
    try:
        return PyprojCRS.from_user_input(value)
    except Exception as exc:  # pyproj raises several types
        raise UnknownCRSError(
            f"could not parse CRS from {value!r}: {exc}. "
            "state the CRS explicitly, e.g. 'EPSG:5179'; do not leave it to be "
            "inferred from coordinate ranges."
        ) from exc


def crs_to_string(crs: CRSLike) -> str:
    """Render a CRS for provenance and error messages.

    Prefers the authority code (``"EPSG:5179"``) because that is what a human
    can check. Falls back to the CRS name, and finally to WKT, so the value is
    never empty and never silently loses identity. ``None`` renders as
    ``"UNKNOWN"`` -- the same literal used throughout provenance (D-0009).
    """
    parsed = parse_crs(crs)
    if parsed is None:
        return "UNKNOWN"
    auth = parsed.to_authority()
    if auth:
        return f"{auth[0]}:{auth[1]}"
    if parsed.name and parsed.name != "unknown":
        return f"name:{parsed.name}"
    return f"wkt:{parsed.to_wkt()}"


def crs_equal(left: CRSLike, right: CRSLike) -> bool:
    """Whether two CRSs are the same for this package's purposes.

    Two ``None``s are **not** equal: two layers of unknown CRS are not known to
    agree, and treating them as equal is exactly the silent error this package
    exists to prevent. Otherwise defers to ``pyproj.CRS.equals``, which compares
    datum and projection parameters rather than strings (so ``"EPSG:4326"`` and
    an equivalent WKT do compare equal), and which does **not** treat
    axis-order-only differences as sameness (A-CRS-3).
    """
    a = parse_crs(left)
    b = parse_crs(right)
    if a is None or b is None:
        return False
    return bool(a.equals(b))


def require_crs(crs: CRSLike, context: str = "") -> PyprojCRS:
    """Return the parsed CRS, raising if it is unknown."""
    parsed = parse_crs(crs)
    if parsed is None:
        where = f" while {context}" if context else ""
        raise UnknownCRSError(
            f"operation requires a declared CRS but the layer has none{where}. "
            "a layer with crs=None may be inspected but never combined "
            "(docs/ASSUMPTIONS.md A-CRS-5)."
        )
    return parsed


def require_same_crs(crss: Iterable[CRSLike], context: str = "") -> PyprojCRS:
    """Assert every CRS given is the same, and return it.

    The single choke point for cross-layer CRS safety. Raises
    :class:`UnknownCRSError` if any is ``None``, and :class:`CRSMismatchError`
    on the first disagreement -- never reprojects (D-0002).
    """
    parsed = [require_crs(c, context=context) for c in crss]
    if not parsed:
        raise UnknownCRSError("no CRSs given to require_same_crs")
    first = parsed[0]
    for other in parsed[1:]:
        if not first.equals(other):
            raise CRSMismatchError(
                crs_to_string(first), crs_to_string(other), context=context
            )
    return first


def is_geographic(crs: CRSLike) -> bool:
    """Whether the CRS is geographic (angular axes)."""
    return bool(require_crs(crs).is_geographic)


def crs_axis_length_unit(crs: CRSLike) -> LengthUnit:
    """The length unit of a projected CRS's axes.

    Raises :class:`GeographicCRSError` for a geographic CRS -- degrees are not a
    length -- and :class:`NonMetreCRSError` for a projected CRS whose axis unit
    this package does not accept for metre-based work.
    """
    parsed = require_crs(crs)
    if parsed.is_geographic:
        raise GeographicCRSError(
            f"{crs_to_string(parsed)} is geographic; its axes are in degrees, "
            "which is not a length unit (docs/DECISIONS.md D-0010)."
        )
    names = {axis.unit_name for axis in parsed.axis_info}
    if not names:
        raise NonMetreCRSError(
            f"{crs_to_string(parsed)} declares no axis units; refusing to assume metres."
        )
    if len(names) > 1:
        raise NonMetreCRSError(
            f"{crs_to_string(parsed)} mixes axis units {sorted(names)}; "
            "this package does not handle anisotropic axis units."
        )
    try:
        return parse_length_unit(next(iter(names)))
    except Exception as exc:
        raise NonMetreCRSError(
            f"{crs_to_string(parsed)} has axis unit {next(iter(names))!r}, which "
            "this package does not accept for length work: "
            f"{exc}"
        ) from exc


def require_projected_metre_crs(crs: CRSLike, context: str = "") -> PyprojCRS:
    """Assert the CRS is projected with metre axes, and return it.

    Gate for every length- and slope-dependent computation (D-0010): slope from
    a degree grid, and edge lengths from a degree graph, are both wrong in a way
    that looks plausible.
    """
    parsed = require_crs(crs, context=context)
    unit = crs_axis_length_unit(parsed)  # raises for geographic / odd units
    if unit is not LengthUnit.METRE:
        where = f" while {context}" if context else ""
        raise NonMetreCRSError(
            f"{crs_to_string(parsed)} has axis unit {unit.value}, not metre"
            f"{where}. reproject to a metre-based projected CRS "
            "(for Korea, see crs.KOREAN_CRS_NOTES)."
        )
    return parsed


def authority_axis_order_is_xy(crs: CRSLike) -> bool | None:
    """Whether the authority declares axis order as (x, y) = (easting, northing).

    Returns ``None`` when the order cannot be determined from the axis metadata.
    This package always works in (easting, northing) tuple order in memory; this
    function exists so that a layer arriving in authority order -- common for the
    Korean KGD2002 belt systems, which declare (Northing, Easting) -- can be
    *recorded* as such instead of being silently transposed.

    Decided from the axis **direction** (and then the axis name), never from the
    axis abbreviation. The abbreviation is a trap here: EPSG:5179 and EPSG:5186
    abbreviate their axes ``X`` and ``Y``, but follow the East-Asian survey
    convention in which ``X`` is the **northing**. Reading ``X`` as "the x
    coordinate" transposes every Korean coordinate pair while leaving plausible
    numbers behind (docs/FAILURE_MODES.md F-CRS-3).
    """
    parsed = require_crs(crs)
    axes = parsed.axis_info
    if len(axes) < 2:
        return None

    def axis_is_x(axis) -> bool | None:
        direction = (getattr(axis, "direction", "") or "").lower()
        if direction in {"east", "west"}:
            return True
        if direction in {"north", "south"}:
            return False
        name = (getattr(axis, "name", "") or "").lower()
        if "easting" in name or "longitude" in name:
            return True
        if "northing" in name or "latitude" in name:
            return False
        return None

    first, second = axis_is_x(axes[0]), axis_is_x(axes[1])
    if first is True and second is False:
        return True
    if first is False and second is True:
        return False
    return None


def transformer_for(src: CRSLike, dst: CRSLike) -> Transformer:
    """Build an ``always_xy=True`` transformer between two known CRSs.

    ``always_xy=True`` is not a stylistic choice: it pins tuple order to
    (easting, northing) / (longitude, latitude) so that the Korean belt systems'
    authority order cannot silently transpose coordinates. See the module
    docstring.
    """
    s = require_crs(src, context="building a coordinate transformer")
    d = require_crs(dst, context="building a coordinate transformer")
    return Transformer.from_crs(s, d, always_xy=True)


def check_korean_crs_notes() -> list[str]:
    """Verify :data:`KOREAN_CRS_NOTES` against the installed PROJ database.

    Returns a list of human-readable discrepancies (empty when consistent).
    Called by ``tests/test_crs_safeguards.py`` so that a documented CRS fact is
    checked against PROJ rather than trusted.
    """
    problems: list[str] = []
    for code, note in KOREAN_CRS_NOTES.items():
        parsed = parse_crs(code)
        assert parsed is not None
        if parsed.name != note.name:
            problems.append(
                f"{code}: PROJ name {parsed.name!r} != documented {note.name!r}"
            )
        actual_kind = "geographic" if parsed.is_geographic else "projected"
        if actual_kind != note.kind:
            problems.append(f"{code}: PROJ says {actual_kind}, documented {note.kind}")
        units = {axis.unit_name for axis in parsed.axis_info}
        if units != {note.axis_unit}:
            problems.append(
                f"{code}: PROJ axis units {sorted(units)} != documented "
                f"{note.axis_unit!r}"
            )
    return problems
