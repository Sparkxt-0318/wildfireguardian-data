"""The individual validation checks.

Each check takes data and a :class:`~.report.ValidationReport`, appends
findings, and returns nothing. Checks never raise on the condition they are
checking for -- that is the point of a report -- but they do raise on being
handed something structurally unusable.

Finding codes are grouped by area: ``CRS-*``, ``RAS-*``, ``VEC-*``, ``PRV-*``,
``POP-*``, ``FAC-*``, ``RD-*``, ``BND-*``. Codes are stable; see
``docs/VALIDATION.md`` for the catalogue.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from ..crs import (
    authority_axis_order_is_xy,
    crs_axis_length_unit,
    crs_equal,
    crs_to_string,
)
from ..errors import CRSError
from ..population.io import DEFAULT_MIN_AGGREGATE_COUNT
from ..provenance.models import UNKNOWN, DataClass, ProvenanceRecord, TemporalProvenance
from ..raster import RasterLayer
from ..units import LengthUnit
from ..vector import VectorLayer
from .report import Severity, ValidationReport

__all__ = [
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


# --------------------------------------------------------------------------- #
# CRS
# --------------------------------------------------------------------------- #
def check_crs_declared(layer: Any, report: ValidationReport) -> None:
    """CRS-001: a layer must declare a CRS to be usable in combination."""
    if getattr(layer, "crs", None) is None:
        report.add(
            "CRS-001",
            Severity.ERROR,
            "layer declares no CRS; it cannot be combined with any other layer "
            "(docs/ASSUMPTIONS.md A-CRS-5)",
            layer=layer.name,
        )


def check_crs_projected_metre(layer: Any, report: ValidationReport) -> None:
    """CRS-002/003: analysis layers must be projected in metres.

    CRS-004 records the authority axis order as INFO. That is not a defect: it
    is the fact that, for the Korean KGD2002 systems, the authority declares
    (Northing, Easting) while this package works in (Easting, Northing). A
    reader who does not know that will transpose coordinates.
    """
    crs = getattr(layer, "crs", None)
    if crs is None:
        return
    try:
        unit = crs_axis_length_unit(crs)
    except CRSError as exc:
        report.add(
            "CRS-002",
            Severity.ERROR,
            f"CRS {crs_to_string(crs)} is not usable for length or slope work: {exc}",
            layer=layer.name,
        )
        return
    if unit is not LengthUnit.METRE:
        report.add(
            "CRS-003",
            Severity.ERROR,
            f"CRS {crs_to_string(crs)} has axis unit {unit.value}, not metre",
            layer=layer.name,
            axis_unit=unit.value,
        )
    order = authority_axis_order_is_xy(crs)
    if order is False:
        report.add(
            "CRS-004",
            Severity.INFO,
            f"CRS {crs_to_string(crs)} declares authority axis order "
            "(Northing, Easting); this package stores coordinates as "
            "(Easting, Northing). a consumer reading authority order will "
            "transpose x and y",
            layer=layer.name,
        )


def check_layers_share_crs(layers: Sequence[Any], report: ValidationReport) -> None:
    """CRS-005: every layer in a bundle must share one analysis CRS."""
    declared = [(layer.name, getattr(layer, "crs", None)) for layer in layers]
    known = [(name, crs) for name, crs in declared if crs is not None]
    if len(known) < 2:
        return
    reference_name, reference = known[0]
    for name, crs in known[1:]:
        if not crs_equal(reference, crs):
            report.add(
                "CRS-005",
                Severity.ERROR,
                f"layer CRS {crs_to_string(crs)} differs from {reference_name}'s "
                f"{crs_to_string(reference)}; a bundle has one analysis CRS "
                "(docs/DECISIONS.md D-0002)",
                layer=name,
                expected=crs_to_string(reference),
                found=crs_to_string(crs),
            )


# --------------------------------------------------------------------------- #
# Rasters
# --------------------------------------------------------------------------- #
def check_raster_nodata_declared(layer: RasterLayer, report: ValidationReport) -> None:
    """RAS-001: an undeclared nodata value is not the same as no missing cells."""
    if layer.nodata is None:
        severity = (
            Severity.WARNING
            if np.issubdtype(layer.data.dtype, np.floating)
            else Severity.ERROR
        )
        report.add(
            "RAS-001",
            severity,
            "raster declares no nodata value. that states nothing about whether "
            "cells are missing (docs/ASSUMPTIONS.md A-RAS-3)"
            + (
                ""
                if severity is Severity.WARNING
                else "; for an integer raster there is no NaN fallback, so missing "
                "cells would be indistinguishable from real values"
            ),
            layer=layer.name,
            dtype=str(layer.data.dtype),
        )


def check_raster_missing_data(
    layer: RasterLayer,
    report: ValidationReport,
    *,
    warn_fraction: float = 0.10,
) -> None:
    """RAS-002/003: report fully missing rasters and high missing fractions."""
    total = int(layer.data.size)
    missing = layer.missing_count
    if missing == total:
        report.add(
            "RAS-002",
            Severity.ERROR,
            "every cell is missing; any statistic over this layer is undefined, "
            "not zero",
            layer=layer.name,
            cells=total,
        )
        return
    fraction = missing / total if total else 0.0
    if fraction > warn_fraction:
        report.add(
            "RAS-003",
            Severity.WARNING,
            f"{fraction:.1%} of cells are missing (threshold {warn_fraction:.0%}); "
            "derived layers will have at least this much missing, plus one cell "
            "of edge loss per derivative pass",
            layer=layer.name,
            missing_cells=missing,
            total_cells=total,
            missing_fraction=fraction,
        )
    # A raster that declares nodata but contains none is worth an INFO: it is
    # often a sign that a sentinel was converted to a real value upstream.
    if layer.nodata is not None and missing == 0:
        report.add(
            "RAS-004",
            Severity.INFO,
            "raster declares a nodata value but contains no missing cells",
            layer=layer.name,
            nodata=repr(layer.nodata),
        )


def check_raster_square_cells(layer: RasterLayer, report: ValidationReport) -> None:
    """RAS-005: non-square cells usually mean an unspecified reprojection."""
    if not layer.transform.is_square:
        report.add(
            "RAS-005",
            Severity.WARNING,
            f"cells are not square: {layer.resolution[0]:g} x "
            f"{layer.resolution[1]:g}. this is usually the result of a "
            "reprojection with no explicit target resolution, and it makes "
            "slope anisotropic with respect to cell count",
            layer=layer.name,
            resolution=list(layer.resolution),
        )


def check_grid_alignment(
    layers: Sequence[RasterLayer], report: ValidationReport, *, tolerance: float = 1e-6
) -> None:
    """RAS-006/007: rasters in one bundle should share a grid.

    "Aligned" means same CRS, same cell size, and same origin **modulo cell
    size** (A-RAS-5). Near-alignment is reported with the offset rather than
    tolerated: a half-cell offset between a DEM and a fuel raster silently
    shifts every fuel value by 15 m on a 30 m grid.
    """
    if len(layers) < 2:
        return
    reference = layers[0]
    for layer in layers[1:]:
        if not crs_equal(reference.crs, layer.crs):
            continue  # already reported by CRS-005
        if not (
            np.isclose(reference.transform.x_size, layer.transform.x_size, rtol=0, atol=tolerance)
            and np.isclose(
                reference.transform.y_size, layer.transform.y_size, rtol=0, atol=tolerance
            )
        ):
            report.add(
                "RAS-006",
                Severity.WARNING,
                f"cell size {layer.resolution} differs from {reference.name}'s "
                f"{reference.resolution}; the layers are on different grids",
                layer=layer.name,
            )
            continue
        dx = (layer.transform.x_origin - reference.transform.x_origin) % layer.transform.x_size
        dy = (layer.transform.y_origin - reference.transform.y_origin) % layer.transform.y_size
        dx = min(dx, layer.transform.x_size - dx)
        dy = min(dy, layer.transform.y_size - dy)
        if dx > tolerance or dy > tolerance:
            report.add(
                "RAS-007",
                Severity.WARNING,
                f"grid origin is offset from {reference.name}'s by "
                f"({dx:.6g}, {dy:.6g}) m within a cell; values do not correspond "
                "cell-for-cell even though cell sizes match",
                layer=layer.name,
                offset_x=float(dx),
                offset_y=float(dy),
            )


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def check_provenance_completeness(
    record: ProvenanceRecord, report: ValidationReport
) -> None:
    """PRV-001/002: count UNKNOWNs, and flag ones that block interpretation.

    Most UNKNOWNs are WARNINGs (D-0009). A few are ERRORs, because without them
    the values cannot be interpreted at all: the output CRS, the value unit, and
    the missing-data representation.
    """
    unknowns = record.unknown_fields()
    blocking = {"output_crs", "value_unit", "nodata_representation"}
    blocking_found = sorted(set(unknowns) & blocking)
    if blocking_found:
        report.add(
            "PRV-001",
            Severity.ERROR,
            f"provenance fields {blocking_found} are UNKNOWN; without them the "
            "layer's values cannot be interpreted",
            layer=record.layer_name,
            fields=blocking_found,
        )
    remaining = sorted(set(unknowns) - blocking)
    if remaining:
        report.add(
            "PRV-002",
            Severity.WARNING,
            f"{len(remaining)} provenance field(s) are UNKNOWN: {remaining}. an "
            "honest gap, reported so incompleteness is visible rather than silent "
            "(docs/DECISIONS.md D-0009)",
            layer=record.layer_name,
            fields=remaining,
        )


def check_provenance_consistency(
    record: ProvenanceRecord, report: ValidationReport
) -> None:
    """PRV-003..007: internal consistency of a provenance record."""
    if not record.sources:
        report.add(
            "PRV-003",
            Severity.ERROR,
            "provenance names no source at all",
            layer=record.layer_name,
        )
    if record.data_class is DataClass.DERIVED and not record.parents:
        report.add(
            "PRV-004",
            Severity.ERROR,
            "layer is DERIVED but names no parent layer",
            layer=record.layer_name,
        )
    if record.data_class is DataClass.SYNTHETIC:
        report.add(
            "PRV-005",
            Severity.INFO,
            "layer is SYNTHETIC: constructed for testing, and makes no claim "
            "about any real place (docs/GLOSSARY.md)",
            layer=record.layer_name,
        )
    if record.temporal_class is TemporalProvenance.RETROSPECTIVE:
        report.add(
            "PRV-006",
            Severity.INFO,
            "layer is RETROSPECTIVE: compiled after the fact, so it was not "
            "available to any real-time decision. using it as if it were "
            "observation-time data is the temporal analogue of data leakage",
            layer=record.layer_name,
        )
    if (
        record.temporal_class is not TemporalProvenance.STATIC
        and record.temporal_reference in (UNKNOWN, "not_applicable")
    ):
        report.add(
            "PRV-007",
            Severity.WARNING,
            f"temporal_class is {record.temporal_class.value} but "
            f"temporal_reference is {record.temporal_reference!r}; the period the "
            "values describe is unstated",
            layer=record.layer_name,
        )
    if record.checksum == UNKNOWN:
        report.add(
            "PRV-008",
            Severity.WARNING,
            "no checksum recorded; the artifact cannot be verified against its "
            "provenance",
            layer=record.layer_name,
        )
    if record.surface_model == "dsm":
        report.add(
            "PRV-009",
            Severity.INFO,
            "elevation source is a DIGITAL SURFACE MODEL: over forest, slope "
            "and aspect describe the canopy top, not the ground. a 20 m canopy "
            "step across one 30 m cell yields a ~34 degree slope that no "
            "terrain has (docs/FAILURE_MODES.md F-TER-3)",
            layer=record.layer_name,
        )
    if record.valid_from == UNKNOWN or record.valid_to == UNKNOWN:
        # WARNING, not ERROR. An unestablished validity interval is an honest
        # gap -- and for a DSM it is a genuinely open question, not an oversight.
        # It is called out separately from the PRV-002 count because this is the
        # field a consumer needs in order to avoid reading post-event data as
        # pre-event truth, and a line item in a list of nine is easy to miss.
        report.add(
            "PRV-010",
            Severity.WARNING,
            f"temporal validity is not established (valid_from="
            f"{record.valid_from!r}, valid_to={record.valid_to!r}). a consumer "
            "cannot tell from this layer alone whether it describes the world "
            "before or after a given event, so it must not be treated as "
            "pre-event truth without establishing that separately",
            layer=record.layer_name,
        )


# --------------------------------------------------------------------------- #
# Vectors
# --------------------------------------------------------------------------- #
def check_vector_geometry(layer: VectorLayer, report: ValidationReport) -> None:
    """VEC-001..003: emptiness, mixed geometry types, invalid geometry."""
    if layer.is_empty:
        report.add(
            "VEC-001",
            Severity.ERROR,
            "vector layer has no features; every metric over it would be a "
            "vacuous zero",
            layer=layer.name,
        )
        return
    types = layer.geom_types
    if len(types) > 1:
        report.add(
            "VEC-002",
            Severity.WARNING,
            f"layer mixes geometry types {sorted(types)}; downstream code that "
            "assumes one type will silently skip the others",
            layer=layer.name,
        )
    invalid = [
        index for index, feature in enumerate(layer.features) if not feature.geometry.is_valid
    ]
    if invalid:
        report.add(
            "VEC-003",
            Severity.ERROR,
            f"{len(invalid)} feature(s) have invalid geometry (self-intersection "
            "or similar); area and intersection results on them are undefined",
            layer=layer.name,
            feature_indices=invalid[:20],
        )


def _field(record: Any, name: str, default: Any = None) -> Any:
    """Read a field from either a dataclass record or its serialised dict.

    Bundles read back from disk carry village and facility records as the JSON
    dicts they were written as (see
    :func:`~wildfireguardian_data.study_area.serialize.read_bundle`). The checks
    must give the same findings either way, so that validating a bundle in
    memory and validating it on disk cannot disagree.
    """
    if isinstance(record, dict):
        return record.get(name, default)
    value = getattr(record, name, default)
    return getattr(value, "value", value)


def check_population_consistency(
    villages: Sequence[Any],
    report: ValidationReport,
    *,
    layer_name: str = "population",
    min_aggregate_count: int = DEFAULT_MIN_AGGREGATE_COUNT,
) -> None:
    """POP-001..004: totals vs strata, missing totals, small counts, privacy."""
    for village in villages:
        settlement_id = _field(village, "settlement_id")
        total = _field(village, "population_total")
        strata = _field(village, "age_strata")
        counted = (
            strata.get("total_counted")
            if isinstance(strata, dict)
            else getattr(strata, "total_counted", None)
        )
        matches = _field(village, "strata_total_matches_total")
        if matches is None and counted is not None and total is not None:
            matches = counted == total
        if matches is False:
            report.add(
                "POP-001",
                Severity.WARNING,
                f"settlement {settlement_id!r}: age strata sum to {counted} but "
                f"population_total is {total}. reported, not reconciled: a "
                "mismatch usually means the two come from different dates or "
                "definitions (docs/ASSUMPTIONS.md A-POP-5)",
                layer=layer_name,
                settlement_id=settlement_id,
            )
        if total is None:
            report.add(
                "POP-002",
                Severity.WARNING,
                f"settlement {settlement_id!r} has no population_total "
                "(None, not 0)",
                layer=layer_name,
                settlement_id=settlement_id,
            )
        elif 0 < total < min_aggregate_count:
            report.add(
                "POP-003",
                Severity.WARNING,
                f"settlement {settlement_id!r} has an aggregate count of {total}, "
                f"below the k-anonymity floor of {min_aggregate_count}. not "
                "suppressed -- a genuinely tiny Korean hamlet is a real study "
                "object -- but disclosive if published "
                "(docs/DECISIONS.md D-0011)",
                layer=layer_name,
                settlement_id=settlement_id,
            )
        if _field(village, "count_basis") == UNKNOWN:
            report.add(
                "POP-004",
                Severity.WARNING,
                f"settlement {settlement_id!r} does not state what its count "
                "counts (residential register, census, present population); see "
                "docs/ASSUMPTIONS.md A-POP-6",
                layer=layer_name,
                settlement_id=settlement_id,
            )


def check_facility_caveats(
    facilities: Sequence[Any], report: ValidationReport, *, layer_name: str = "facilities"
) -> None:
    """FAC-001..003: unassessed suitability, unknown status, absent capacity."""
    if not facilities:
        return
    unassessed = [
        _field(f, "facility_id")
        for f in facilities
        if not _field(f, "suitability_assessed", False)
    ]
    if unassessed:
        report.add(
            "FAC-001",
            Severity.INFO,
            f"{len(unassessed)} facility/facilities have no suitability "
            "assessment. this repository performs none; presence in a dataset is "
            "not fitness for purpose (docs/ASSUMPTIONS.md A-FAC-1)",
            layer=layer_name,
            facility_ids=unassessed[:20],
        )
    unknown_status = [
        _field(f, "facility_id")
        for f in facilities
        if _field(f, "operational_status") == UNKNOWN
    ]
    if unknown_status:
        report.add(
            "FAC-002",
            Severity.WARNING,
            f"{len(unknown_status)} facility/facilities have UNKNOWN operational "
            "status; do not assume operational",
            layer=layer_name,
            facility_ids=unknown_status[:20],
        )
    no_capacity = [
        _field(f, "facility_id")
        for f in facilities
        if _field(f, "capacity_persons") is None
    ]
    if no_capacity:
        report.add(
            "FAC-003",
            Severity.INFO,
            f"{len(no_capacity)} facility/facilities have no sourced capacity. it "
            "is not estimated from footprint area (A-FAC-3)",
            layer=layer_name,
            facility_ids=no_capacity[:20],
        )


def check_road_qa(
    qa: Any, report: ValidationReport, *, layer_name: str = "roads"
) -> None:
    """RD-001..006: turn road-QA diagnostics into findings.

    None of these are safety judgements. ``RD-003`` in particular says that a
    component has one exit **in this graph** -- which may be an artifact of the
    clip (A-RD-4).
    """
    payload = qa.to_dict() if hasattr(qa, "to_dict") else qa
    connectivity = payload["connectivity"]
    egress = payload["egress"]

    if connectivity["component_count"] > 1:
        report.add(
            "RD-001",
            Severity.WARNING,
            f"road network has {connectivity['component_count']} connected "
            "components; as digitised, parts of it do not connect to each other. "
            "this is often a missing junction node rather than a real "
            "disconnection (docs/DECISIONS.md D-0007)",
            layer=layer_name,
            component_count=connectivity["component_count"],
        )
    if connectivity["isolated_segments"]:
        report.add(
            "RD-002",
            Severity.WARNING,
            f"{len(connectivity['isolated_segments'])} isolated segment(s): "
            "single edges alone in their component with no exit",
            layer=layer_name,
            count=len(connectivity["isolated_segments"]),
        )
    if egress["single_egress_candidates"]:
        report.add(
            "RD-003",
            Severity.INFO,
            f"{len(egress['single_egress_candidates'])} component(s) contain a "
            "settlement and exactly one exit node in this graph. a topological "
            "observation, not a claim that anyone is trapped "
            "(docs/DECISIONS.md D-0008)",
            layer=layer_name,
            candidates=egress["single_egress_candidates"],
        )
    if egress["no_egress_components"]:
        report.add(
            "RD-004",
            Severity.WARNING,
            f"{len(egress['no_egress_components'])} component(s) contain a "
            "settlement and no exit node. most often the extent was clipped too "
            "tightly or the network is incomplete",
            layer=layer_name,
            components=egress["no_egress_components"],
        )
    if egress["exits"]["clip_boundary_warning"]:
        report.add(
            "RD-005",
            Severity.INFO,
            "some exit nodes were inferred from the study-area boundary rather "
            "than from source tags; egress counts therefore depend on where the "
            "study area was cut (docs/ASSUMPTIONS.md A-RD-4)",
            layer=layer_name,
            boundary_exits=egress["exits"]["from_boundary"],
        )
    if payload["crossings_without_node"]["count"]:
        report.add(
            "RD-006",
            Severity.WARNING,
            f"{payload['crossings_without_node']['count']} line pair(s) cross "
            "without a shared node. either genuine grade separation, or missing "
            "junction nodes that under-connect the network "
            "(docs/DECISIONS.md D-0007)",
            layer=layer_name,
            count=payload["crossings_without_node"]["count"],
        )
    if egress["settlements"]["unmatched_settlements"]:
        report.add(
            "RD-007",
            Severity.WARNING,
            f"{len(egress['settlements']['unmatched_settlements'])} settlement(s) "
            f"matched no road node within "
            f"{egress['settlements']['snap_distance_m']} m",
            layer=layer_name,
            unmatched=egress["settlements"]["unmatched_settlements"],
        )
