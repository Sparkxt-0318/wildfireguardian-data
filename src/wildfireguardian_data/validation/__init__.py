"""Validation: CRS, units, missing data, alignment, provenance, bundle integrity.

Severity is a scientific judgement (see :mod:`.report`); the check catalogue is
in ``docs/VALIDATION.md``.
"""

from __future__ import annotations

from .bundle_checks import validate_bundle, validate_bundle_directory
from .checks import (
    check_crs_declared,
    check_crs_projected_metre,
    check_facility_caveats,
    check_grid_alignment,
    check_layers_share_crs,
    check_population_consistency,
    check_provenance_completeness,
    check_provenance_consistency,
    check_raster_missing_data,
    check_raster_nodata_declared,
    check_raster_square_cells,
    check_road_qa,
    check_vector_geometry,
)
from .report import REPORT_SCHEMA_VERSION, Finding, Severity, ValidationReport

__all__ = [
    "Severity",
    "Finding",
    "ValidationReport",
    "REPORT_SCHEMA_VERSION",
    "validate_bundle",
    "validate_bundle_directory",
    "check_crs_declared",
    "check_crs_projected_metre",
    "check_layers_share_crs",
    "check_raster_nodata_declared",
    "check_raster_missing_data",
    "check_raster_square_cells",
    "check_grid_alignment",
    "check_provenance_completeness",
    "check_provenance_consistency",
    "check_vector_geometry",
    "check_population_consistency",
    "check_facility_caveats",
    "check_road_qa",
]
