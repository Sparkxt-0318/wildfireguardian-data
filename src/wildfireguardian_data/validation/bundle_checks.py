"""Whole-bundle validation.

Two entry points:

* :func:`validate_bundle` -- validate an in-memory
  :class:`~wildfireguardian_data.study_area.bundle.StudyAreaBundle`, used by the
  build pipeline so a bundle is never written without a report;
* :func:`validate_bundle_directory` -- validate a bundle **on disk**, which
  additionally re-reads every file and re-checks its checksum. That is the
  stronger check: it catches a bundle whose data drifted from its provenance
  after it was written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..crs import crs_equal, crs_to_string
from ..errors import BundleError, PrivacyGuardError
from ..provenance.models import UNKNOWN, DataClass
from ..raster import RasterKind
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
from .report import Severity, ValidationReport

__all__ = ["validate_bundle", "validate_bundle_directory"]


def validate_bundle(bundle: Any, *, warn_missing_fraction: float = 0.10) -> ValidationReport:
    """Validate an in-memory bundle and return the report."""
    report = ValidationReport(
        target=f"bundle:{bundle.study_area_id}",
        context={
            "crs": crs_to_string(bundle.crs),
            "bounds": bundle.bounds.to_dict(),
            "layers": bundle.layer_names,
            "schema_version": bundle.schema_version,
        },
    )

    layers = bundle.all_layers()
    if not layers:
        report.add(
            "BND-001",
            Severity.ERROR,
            "bundle contains no layers",
        )
        return report

    check_layers_share_crs(layers, report)
    for layer in layers:
        check_crs_declared(layer, report)
        check_crs_projected_metre(layer, report)
        check_provenance_completeness(layer.provenance, report)
        check_provenance_consistency(layer.provenance, report)

    rasters = bundle.raster_layers()
    for layer in rasters:
        check_raster_nodata_declared(layer, report)
        check_raster_missing_data(layer, report, warn_fraction=warn_missing_fraction)
        check_raster_square_cells(layer, report)
    check_grid_alignment(rasters, report)

    for layer in bundle.vector_layers():
        check_vector_geometry(layer, report)

    # The study bounds must actually be covered by the terrain, or the bundle
    # claims an extent it does not have.
    if bundle.terrain is not None:
        dem_bounds = bundle.terrain.dem.bounds
        if crs_equal(dem_bounds.crs, bundle.bounds.crs):
            covers = (
                dem_bounds.min_x <= bundle.bounds.min_x
                and dem_bounds.min_y <= bundle.bounds.min_y
                and dem_bounds.max_x >= bundle.bounds.max_x
                and dem_bounds.max_y >= bundle.bounds.max_y
            )
            if not covers:
                report.add(
                    "BND-002",
                    Severity.WARNING,
                    f"terrain extent {dem_bounds} does not fully cover the declared "
                    f"study area {bundle.bounds}; the source did not reach the "
                    "requested boundary and nothing was invented to fill it",
                    layer=bundle.terrain.dem.name,
                )
        if bundle.terrain.slope is not None:
            slope = bundle.terrain.slope
            dem = bundle.terrain.dem
            expected_extra = slope.missing_count - dem.missing_count
            if expected_extra <= 0:
                report.add(
                    "BND-003",
                    Severity.ERROR,
                    "slope has no more missing cells than the DEM, which is "
                    "impossible for a 3x3 estimator that drops the array edge. "
                    "either the edge was extrapolated or the layers do not "
                    "correspond (docs/DECISIONS.md D-0005)",
                    layer=slope.name,
                    dem_missing=dem.missing_count,
                    slope_missing=slope.missing_count,
                )

    if bundle.population is not None:
        # The privacy guard normally raises at load time. A bundle validated
        # here may not have come through that path -- it may have been written
        # by an older version, by another tool, or edited by hand -- so the same
        # rule is re-applied as a finding rather than an exception, because a
        # validator that crashed would report nothing else about the bundle.
        from ..population.io import check_privacy

        try:
            check_privacy(
                bundle.population.layer.property_keys,
                context=f"population layer {bundle.population.layer.name!r}",
            )
        except PrivacyGuardError as exc:
            report.add(
                "POP-005",
                Severity.ERROR,
                f"population layer carries attribute names that look "
                f"person-level or medical: {exc}",
                layer=bundle.population.layer.name,
            )
        if bundle.population.villages:
            check_population_consistency(
                bundle.population.villages,
                report,
                layer_name=bundle.population.layer.name,
            )
    if bundle.facilities is not None and bundle.facilities.records:
        check_facility_caveats(
            bundle.facilities.records, report, layer_name=bundle.facilities.layer.name
        )
    if bundle.roads is not None and bundle.roads.qa is not None:
        check_road_qa(bundle.roads.qa, report, layer_name=bundle.roads.layer.name)

    if bundle.fuels is not None and bundle.fuels.layer is not None:
        fuel_layer = bundle.fuels.layer
        if fuel_layer.kind is not RasterKind.CATEGORICAL:
            report.add(
                "BND-004",
                Severity.ERROR,
                f"fuel layer is declared {fuel_layer.kind.value}; fuel classes are "
                "nominal categories (docs/ASSUMPTIONS.md A-FU-3)",
                layer=fuel_layer.name,
            )
        if bundle.fuels.scheme is None:
            report.add(
                "BND-005",
                Severity.ERROR,
                "fuel layer has no class scheme; without one its integers have no "
                "meaning",
                layer=fuel_layer.name,
            )
        else:
            # Re-check the codes against the scheme here, not only at ingest.
            # `fuels.fuel_layer_from_array` validates on the way in, but a
            # bundle can arrive from anywhere -- hand-edited, produced by an
            # older version, or written by another tool -- and a code the scheme
            # does not define is data whose meaning nobody knows.
            import numpy as np

            observed = np.unique(fuel_layer.data).tolist()
            undefined = bundle.fuels.scheme.unknown_codes(observed)
            if undefined:
                report.add(
                    "BND-008",
                    Severity.ERROR,
                    f"fuel layer contains class code(s) {list(undefined)} that "
                    f"scheme {bundle.fuels.scheme.name!r} does not define "
                    f"(known: {list(bundle.fuels.scheme.codes)}, nodata: "
                    f"{bundle.fuels.scheme.nodata_code}). an undefined code has "
                    "no meaning",
                    layer=fuel_layer.name,
                    undefined_codes=[int(code) for code in undefined],
                )
            if fuel_layer.nodata != bundle.fuels.scheme.nodata_code:
                report.add(
                    "BND-009",
                    Severity.ERROR,
                    f"fuel layer declares nodata={fuel_layer.nodata!r} but its "
                    f"scheme uses nodata_code={bundle.fuels.scheme.nodata_code}. "
                    "two missing-data conventions in one layer leave some "
                    "missing cells indistinguishable from a real class",
                    layer=fuel_layer.name,
                )

    synthetic = [
        layer.name
        for layer in layers
        if layer.provenance.data_class is DataClass.SYNTHETIC
    ]
    if synthetic:
        report.add(
            "BND-006",
            Severity.INFO,
            f"{len(synthetic)} layer(s) are SYNTHETIC: {synthetic}. this bundle "
            "describes no real place and must not be used for any statement "
            "about one",
        )
        if "synthetic" not in bundle.study_area_id.lower():
            report.add(
                "BND-007",
                Severity.WARNING,
                f"bundle id {bundle.study_area_id!r} contains synthetic layers but "
                "its name does not say 'synthetic'; a synthetic bundle named "
                "after a real place can be mistaken for real data (AGENTS.md §4)",
            )
    return report


def validate_bundle_directory(
    directory: str | Path, *, warn_missing_fraction: float = 0.10
) -> ValidationReport:
    """Validate a bundle on disk, including file checksums.

    Reads the bundle through
    :func:`~wildfireguardian_data.study_area.serialize.read_bundle`, which
    verifies every layer file's checksum against the manifest, then runs the
    in-memory checks and adds disk-specific ones.
    """
    from ..study_area.serialize import MANIFEST_NAME, read_bundle

    target = Path(directory)
    report = ValidationReport(target=f"directory:{target}")
    manifest_path = target / MANIFEST_NAME

    if not manifest_path.exists():
        report.add(
            "BND-010",
            Severity.ERROR,
            f"no {MANIFEST_NAME} found; {target} is not a study-area bundle",
        )
        return report

    try:
        bundle = read_bundle(target)
    except BundleError as exc:
        report.add(
            "BND-011",
            Severity.ERROR,
            f"bundle could not be read: {exc}",
        )
        return report

    inner = validate_bundle(bundle, warn_missing_fraction=warn_missing_fraction)
    report.context = {**inner.context, "directory": str(target)}
    report.extend(inner.findings)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Every layer in the manifest must have a provenance sidecar, and vice
    # versa: an orphan sidecar means a layer was removed without its provenance,
    # and a layer with no sidecar is unusable.
    manifest_layers = {entry["name"] for entry in manifest.get("layers", [])}
    sidecar_layers = set(bundle.provenance)
    for orphan in sorted(sidecar_layers - manifest_layers):
        report.add(
            "BND-012",
            Severity.WARNING,
            f"provenance sidecar for {orphan!r} has no corresponding layer in the "
            "manifest",
            layer=orphan,
        )
    for missing in sorted(manifest_layers - sidecar_layers):
        report.add(
            "BND-013",
            Severity.ERROR,
            f"manifest lists layer {missing!r} with no provenance sidecar",
            layer=missing,
        )

    for entry in manifest.get("layers", []):
        record = bundle.provenance.get(entry["name"])
        if record is None:
            continue
        if record.checksum == UNKNOWN:
            report.add(
                "BND-014",
                Severity.WARNING,
                "provenance records no checksum, so the artifact cannot be "
                "verified against its provenance",
                layer=entry["name"],
            )
        elif record.checksum != entry.get("checksum_sha256"):
            report.add(
                "BND-015",
                Severity.ERROR,
                f"provenance checksum {record.checksum} does not match the "
                f"manifest's {entry.get('checksum_sha256')} for this layer",
                layer=entry["name"],
            )

    extras = manifest.get("extras", {})
    for key, relative in extras.items():
        # The validation report is written *after* validation by definition, so
        # its absence during this run says nothing about the bundle.
        if key == "validation_report":
            continue
        if not (target / relative).exists():
            report.add(
                "BND-016",
                Severity.WARNING,
                f"manifest extra {key!r} points at missing file {relative}",
            )
    return report
