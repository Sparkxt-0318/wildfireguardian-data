"""Exception hierarchy for :mod:`wildfireguardian_data`.

Every failure mode this repository cares about has a *named* exception. That is
deliberate: the repository's purpose is to make data problems loud, and a named
exception is the loudest thing a library can do. See ``docs/DECISIONS.md``
D-0002 and ``AGENTS.md`` §7.

Never replace a raise in this package with a warning or a default value without
a ``docs/DECISIONS.md`` entry.
"""

from __future__ import annotations

__all__ = [
    "WGDataError",
    "ConfigError",
    "OptionalDependencyError",
    "NetworkAccessError",
    "IngestError",
    "CRSError",
    "CRSMismatchError",
    "UnknownCRSError",
    "GeographicCRSError",
    "NonMetreCRSError",
    "UnitError",
    "UnitMismatchError",
    "UnknownUnitError",
    "RasterError",
    "RasterGeometryError",
    "RasterAlignmentError",
    "MissingDataError",
    "GraphError",
    "ProvenanceError",
    "PrivacyGuardError",
    "ValidationFailedError",
    "BundleError",
]


class WGDataError(Exception):
    """Base class for every error raised deliberately by this package."""


class ConfigError(WGDataError):
    """A study-area config is missing a required key or has an invalid value."""


class OptionalDependencyError(WGDataError):
    """An operation needs an optional dependency that is not installed.

    Raised instead of a bare :class:`ImportError` so the message can name the
    extra to install. See ``docs/DECISIONS.md`` D-0003.
    """

    def __init__(self, package: str, extra: str = "geo", purpose: str = "") -> None:
        detail = f" (needed for {purpose})" if purpose else ""
        super().__init__(
            f"optional dependency {package!r} is not installed{detail}. "
            f"install it with: pip install 'wildfireguardian-data[{extra}]'"
        )
        self.package = package
        self.extra = extra


class NetworkAccessError(WGDataError):
    """Network access is required but was not permitted or not available.

    Fetching real source data is always opt-in (``--allow-network``); a pipeline
    never reaches the network implicitly.
    """


class IngestError(WGDataError):
    """A source file could not be read, or is not what it claims to be."""


# --------------------------------------------------------------------------- #
# CRS
# --------------------------------------------------------------------------- #
class CRSError(WGDataError):
    """Base class for coordinate-reference-system problems."""


class CRSMismatchError(CRSError):
    """Two layers in one operation have different CRSs.

    This is never resolved by implicit reprojection. See ``docs/DECISIONS.md``
    D-0002.
    """

    def __init__(self, left: str, right: str, context: str = "") -> None:
        where = f" while {context}" if context else ""
        super().__init__(
            f"CRS mismatch{where}: {left} vs {right}. "
            "this package never reprojects implicitly - reproject explicitly "
            "(terrain.reproject_raster / vector.reproject_vector_layer), which "
            "records the operation in provenance."
        )
        self.left = left
        self.right = right
        self.context = context


class UnknownCRSError(CRSError):
    """A layer has no declared CRS and cannot participate in the operation.

    A layer with ``crs=None`` may be loaded and inspected, never combined.
    See ``docs/ASSUMPTIONS.md`` A-CRS-5.
    """


class GeographicCRSError(CRSError):
    """A length- or slope-dependent operation was given a geographic CRS.

    Degrees are not a length unit, and the degree-to-metre scale factor differs
    between the x and y directions by ``1/cos(latitude)``. See
    ``docs/DECISIONS.md`` D-0010.
    """


class NonMetreCRSError(CRSError):
    """A projected CRS whose axis unit is not the metre was given to metre code."""


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #
class UnitError(WGDataError):
    """Base class for unit problems."""


class UnitMismatchError(UnitError):
    """Two quantities in one operation carry incompatible units."""


class UnknownUnitError(UnitError):
    """A unit string could not be resolved to a known unit."""


# --------------------------------------------------------------------------- #
# Rasters
# --------------------------------------------------------------------------- #
class RasterError(WGDataError):
    """Base class for raster problems."""


class RasterGeometryError(RasterError):
    """A raster's geometry violates an invariant (rotation, orientation, shape)."""


class RasterAlignmentError(RasterError):
    """Two rasters are not on the same grid and the operation requires it."""


class MissingDataError(WGDataError):
    """A missing-data situation that must not be papered over.

    Raised when an operation would have to invent values to proceed, or when a
    caller asks for a statistic over a fully masked array.
    """


class GraphError(WGDataError):
    """A road graph could not be built, or is not valid for the requested QA."""


class ProvenanceError(WGDataError):
    """A provenance record is incomplete, inconsistent, or missing."""


class PrivacyGuardError(WGDataError):
    """Input data appears to contain person-level or medical information.

    See ``docs/DECISIONS.md`` D-0011 and ``docs/ASSUMPTIONS.md`` A-POP-1/2.
    This guard is a tripwire, not a security boundary
    (``docs/FAILURE_MODES.md`` F-POP-1).
    """


class ValidationFailedError(WGDataError):
    """Validation produced findings at a severity the caller treats as fatal."""


class BundleError(WGDataError):
    """A study-area bundle is malformed, incomplete, or of an unknown version."""
