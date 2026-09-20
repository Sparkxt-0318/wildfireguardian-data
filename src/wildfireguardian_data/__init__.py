"""wildfireguardian-data: the geospatial data foundation for WildfireGuardian.

This package turns raw Korean (and synthetic) geospatial data into
reproducible, provenance-preserving, quality-controlled study-area packages.

**It does one job.** It does not predict wildfire behaviour, compare forecasts,
route evacuations or rescues, or assert that any road, shelter or area is safe.
See ``docs/SCOPE.md``.

Read before using:

* ``docs/PROJECT_CONTEXT.md`` -- what this repository is for
* ``docs/ASSUMPTIONS.md`` -- every scientific assumption and where it is enforced
* ``docs/FAILURE_MODES.md`` -- what can still go wrong, and what is not caught
* ``AGENTS.md`` -- the contract every contributor and agent works under

The three rules that shape the whole API:

1. **CRS mismatch raises.** Nothing reprojects implicitly (D-0002).
2. **Missing data stays missing.** Never silently zero (D-0005, F-MD-1).
3. **UNKNOWN is a value.** An unknown fact is recorded as ``"UNKNOWN"``, never
   guessed (D-0009).
"""

from __future__ import annotations

__version__ = "0.2.0"

from .bounds import Bounds
from .crs import (
    KOREAN_CRS_NOTES,
    crs_equal,
    crs_to_string,
    parse_crs,
    require_projected_metre_crs,
    require_same_crs,
)
from .errors import (
    BundleError,
    ConfigError,
    CRSError,
    CRSMismatchError,
    GeographicCRSError,
    GraphError,
    IngestError,
    MissingDataError,
    NetworkAccessError,
    OptionalDependencyError,
    PrivacyGuardError,
    ProvenanceError,
    RasterError,
    UnitError,
    UnitMismatchError,
    UnknownCRSError,
    ValidationFailedError,
    WGDataError,
)
from .provenance import (
    NOT_APPLICABLE,
    UNKNOWN,
    DataClass,
    ProvenanceRecord,
    SourceRecord,
    TemporalProvenance,
    Transformation,
)
from .raster import GridTransform, RasterKind, RasterLayer
from .units import (
    AreaUnit,
    DistanceUnit,
    ElevationUnit,
    LengthUnit,
    SlopeUnit,
    TimeUnit,
    convert_length,
    convert_slope,
)
from .vector import Feature, VectorLayer

__all__ = [
    "__version__",
    # core models
    "RasterLayer",
    "GridTransform",
    "RasterKind",
    "VectorLayer",
    "Feature",
    "Bounds",
    # provenance
    "ProvenanceRecord",
    "SourceRecord",
    "Transformation",
    "DataClass",
    "TemporalProvenance",
    "UNKNOWN",
    "NOT_APPLICABLE",
    # units
    "LengthUnit",
    "DistanceUnit",
    "ElevationUnit",
    "SlopeUnit",
    "TimeUnit",
    "AreaUnit",
    "convert_length",
    "convert_slope",
    # crs
    "parse_crs",
    "crs_to_string",
    "crs_equal",
    "require_same_crs",
    "require_projected_metre_crs",
    "KOREAN_CRS_NOTES",
    # errors
    "WGDataError",
    "ConfigError",
    "IngestError",
    "CRSError",
    "CRSMismatchError",
    "UnknownCRSError",
    "GeographicCRSError",
    "UnitError",
    "UnitMismatchError",
    "RasterError",
    "MissingDataError",
    "GraphError",
    "ProvenanceError",
    "PrivacyGuardError",
    "ValidationFailedError",
    "BundleError",
    "NetworkAccessError",
    "OptionalDependencyError",
]


def __getattr__(name: str):
    """Lazily expose the sub-packages as attributes.

    Keeps ``import wildfireguardian_data`` cheap and free of any GDAL import,
    while ``wildfireguardian_data.terrain`` still works without an explicit
    submodule import (D-0003).
    """
    if name in {
        "terrain",
        "roads",
        "fuels",
        "population",
        "facilities",
        "study_area",
        "validation",
        "provenance",
        "fixtures",
        "sources",
        "cli",
    }:
        import importlib

        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
