"""Fuel / vegetation layer ingestion -- a generic architecture, not a dataset.

The design goal is that plugging in a real Korean vegetation product later
requires supplying a :class:`~wildfireguardian_data.fuels.classes.FuelClassScheme`
and a source record, and nothing else. What it must never allow is a fuel layer
arriving without a declared class scheme, data class, or nodata code
(``docs/ASSUMPTIONS.md`` A-FU-1/2/3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ..crs import crs_to_string, parse_crs, require_same_crs
from ..errors import (
    ConfigError,
    IngestError,
    MissingDataError,
    OptionalDependencyError,
)
from ..provenance.checksum import sha256_file
from ..provenance.models import (
    NOT_APPLICABLE,
    UNKNOWN,
    DataClass,
    ProvenanceRecord,
    SourceRecord,
    TemporalProvenance,
    Transformation,
)
from ..raster import GridTransform, RasterKind, RasterLayer
from ..terrain.io import resolution_unit_text
from ..vector import VectorLayer
from .classes import FuelClassScheme

__all__ = ["read_fuel_geotiff", "fuel_layer_from_array", "rasterize_fuel_vector"]


def _fuel_provenance(
    *,
    name: str,
    scheme: FuelClassScheme,
    source: SourceRecord,
    data_class: DataClass,
    temporal_class: TemporalProvenance,
    temporal_reference: str,
    crs: Any,
    transform: GridTransform,
    transformation: Transformation,
    checksum: str,
    notes: str,
    valid_from: str = UNKNOWN,
    valid_to: str = UNKNOWN,
) -> ProvenanceRecord:
    return ProvenanceRecord(
        layer_name=name,
        data_class=DataClass(data_class),
        temporal_class=TemporalProvenance(temporal_class),
        temporal_reference=temporal_reference,
        sources=(source,),
        transformations=(transformation,),
        original_crs=crs_to_string(crs),
        output_crs=crs_to_string(crs),
        spatial_resolution=(transform.x_size, transform.y_size),
        # Read from the CRS, never assumed: "m" is wrong for a projected CRS
        # whose axis unit is a foot, and a false unit is worse than UNKNOWN.
        resolution_unit=resolution_unit_text(crs),
        value_unit="class",
        nodata_representation=repr(scheme.nodata_code),
        # A land-cover or fuel raster is not an elevation model, so there is no
        # surface-versus-terrain question to be unknown about.
        surface_model=NOT_APPLICABLE,
        # The scheme knows its own vintage; the layer does not know how long
        # that vintage stays valid, so a caller that knows must pass it. See
        # D-0025 on why this is not defaulted from the vintage.
        valid_from=valid_from,
        valid_to=valid_to,
        checksum=checksum,
        notes=(
            # The SYNTHETIC marker is prepended here rather than left to each
            # caller, so a synthetic fuel layer is greppable as such from its
            # notes alone and not only from its data_class field.
            ("SYNTHETIC. " if DataClass(data_class) is DataClass.SYNTHETIC else "")
            + f"fuel class scheme: {scheme.name} ({scheme.data_class.value}); "
            + f"source: {scheme.source}. nominal categories only"
            + (f" | {notes}" if notes else "")
        ),
    )


def fuel_layer_from_array(
    array: np.ndarray,
    *,
    name: str,
    transform: GridTransform,
    crs: Any,
    scheme: FuelClassScheme,
    source: SourceRecord,
    data_class: DataClass,
    temporal_class: TemporalProvenance,
    temporal_reference: str = UNKNOWN,
    valid_from: str = UNKNOWN,
    valid_to: str = UNKNOWN,
    notes: str = "",
) -> RasterLayer:
    """Wrap an integer class array as a categorical fuel layer.

    Rejects values the scheme does not define. An undefined class code is data
    whose meaning is unknown, and passing it through would let it reach a
    downstream model as if it meant something.
    """
    data = np.asarray(array)
    if not np.issubdtype(data.dtype, np.integer):
        raise ConfigError(
            f"fuel layer {name!r} has dtype {data.dtype}; fuel classes are "
            "nominal integer codes. a float class raster usually means it was "
            "resampled by averaging, which invents classes "
            "(docs/ASSUMPTIONS.md A-FU-3)."
        )
    unknown = scheme.unknown_codes(np.unique(data).tolist())
    if unknown:
        raise IngestError(
            f"fuel layer {name!r} contains class codes {list(unknown)} that scheme "
            f"{scheme.name!r} does not define (known: {list(scheme.codes)}, "
            f"nodata: {scheme.nodata_code}). an undefined code has no meaning and "
            "is not passed through."
        )
    transformation = Transformation(
        operation="fuel_layer_from_array",
        parameters={
            "scheme": scheme.name,
            "scheme_data_class": scheme.data_class.value,
            "nodata_code": scheme.nodata_code,
            "classes_present": sorted(int(c) for c in np.unique(data)),
            "dtype": str(data.dtype),
            "shape": list(data.shape),
            "crs": crs_to_string(crs),
        },
        notes="categorical layer; only nearest-neighbour resampling is valid",
    )
    provenance = _fuel_provenance(
        name=name,
        scheme=scheme,
        source=source,
        data_class=data_class,
        temporal_class=temporal_class,
        temporal_reference=temporal_reference,
        crs=parse_crs(crs),
        transform=transform,
        transformation=transformation,
        checksum=UNKNOWN,
        notes=notes,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    return RasterLayer(
        name=name,
        data=data,
        transform=transform,
        crs=crs,
        nodata=scheme.nodata_code,
        kind=RasterKind.CATEGORICAL,
        value_unit="class",
        provenance=provenance,
    )


def read_fuel_geotiff(
    path: str | Path,
    *,
    name: str,
    scheme: FuelClassScheme,
    source: SourceRecord,
    data_class: DataClass,
    temporal_class: TemporalProvenance,
    band: int = 1,
    temporal_reference: str = UNKNOWN,
    valid_from: str = UNKNOWN,
    valid_to: str = UNKNOWN,
    notes: str = "",
) -> RasterLayer:
    """Read a categorical fuel raster from a GeoTIFF.

    The file's nodata value must either be absent or equal the scheme's
    ``nodata_code``. A disagreement is an error: two different "missing"
    conventions in one layer means some missing cells will be read as a real
    class.
    """
    try:
        import rasterio
    except ImportError as exc:  # pragma: no cover
        raise OptionalDependencyError("rasterio", purpose="fuel GeoTIFF I/O") from exc

    source_path = Path(path)
    with rasterio.open(source_path) as dataset:
        array = dataset.read(band)
        transform = GridTransform.from_affine(dataset.transform)
        crs = parse_crs(dataset.crs.to_wkt()) if dataset.crs else None
        file_nodata = dataset.nodatavals[band - 1]

    if file_nodata is not None and int(file_nodata) != int(scheme.nodata_code):
        raise IngestError(
            f"{source_path} declares nodata={file_nodata} but scheme "
            f"{scheme.name!r} uses nodata_code={scheme.nodata_code}. two "
            "missing-data conventions in one layer would leave some missing "
            "cells indistinguishable from a real class."
        )

    unknown = scheme.unknown_codes(np.unique(array).tolist())
    if unknown:
        raise IngestError(
            f"{source_path} contains class codes {list(unknown)} not defined by "
            f"scheme {scheme.name!r}"
        )

    transformation = Transformation(
        operation="read_fuel_geotiff",
        parameters={
            "path": str(source_path),
            "band": band,
            "scheme": scheme.name,
            "file_nodata": repr(file_nodata),
            "scheme_nodata_code": scheme.nodata_code,
            "classes_present": sorted(int(c) for c in np.unique(array)),
            "file_crs": crs_to_string(crs),
        },
        notes="categorical; nearest-neighbour resampling only",
    )
    provenance = _fuel_provenance(
        name=name,
        scheme=scheme,
        source=source,
        data_class=data_class,
        temporal_class=temporal_class,
        temporal_reference=temporal_reference,
        crs=crs,
        transform=transform,
        transformation=transformation,
        checksum=sha256_file(source_path) if source_path.exists() else UNKNOWN,
        notes=notes,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    return RasterLayer(
        name=name,
        data=array,
        transform=transform,
        crs=crs,
        nodata=scheme.nodata_code,
        kind=RasterKind.CATEGORICAL,
        value_unit="class",
        provenance=provenance,
    )


def rasterize_fuel_vector(
    layer: VectorLayer,
    *,
    name: str,
    class_property: str,
    scheme: FuelClassScheme,
    template: RasterLayer,
    source: SourceRecord | None = None,
    data_class: DataClass | None = None,
    temporal_class: TemporalProvenance | None = None,
    all_touched: bool = False,
) -> RasterLayer:
    """Burn a polygon fuel layer onto ``template``'s grid.

    Cells no polygon covers get ``scheme.nodata_code``, never ``0``: an
    unmapped cell is unmapped, not "non-vegetated"
    (``docs/FAILURE_MODES.md`` F-MD-1).

    ``all_touched=False`` (the default) burns a cell only when its **centre**
    falls inside a polygon. Note that this is a *different* rule from A-RAS-2's
    whole-cell semantics, not the same one: A-RAS-2 says a cell's value applies
    to its whole area, and :meth:`GridTransform.rowcol` implements a whole-cell
    containment test, whereas rasterisation here tests the centre only. The two
    disagree for any polygon that covers part of a cell without covering its
    centre. The centre rule is chosen because the alternative
    (``all_touched=True``) inflates every polygon by up to one cell in every
    direction, which is the larger distortion -- but it is a choice, not an
    equivalence.

    A consequence, reported rather than hidden: a polygon smaller than a cell,
    or one straddling a cell boundary without containing a centre, burns
    **nothing**. Korean cadastral and forest-type data contains many sub-30 m
    parcels. Any submitted class code that ends up absent from the output is
    recorded in provenance as ``codes_that_burned_no_cells``, and
    ``cells_burned`` counts cells rather than submitted shapes.
    """
    try:
        from rasterio.features import rasterize
    except ImportError as exc:  # pragma: no cover
        raise OptionalDependencyError("rasterio", purpose="rasterising fuel polygons") from exc

    require_same_crs(
        [layer.crs, template.crs], context=f"rasterising fuel layer {layer.name!r}"
    )
    shapes: list[tuple[Any, int]] = []
    missing_property = 0
    for feature in layer.features:
        value = feature.properties.get(class_property)
        if value is None:
            missing_property += 1
            continue
        code = int(value)
        if code in scheme.unknown_codes([code]):
            raise IngestError(
                f"feature carries {class_property}={code}, which scheme "
                f"{scheme.name!r} does not define"
            )
        shapes.append((feature.geometry, code))

    if missing_property:
        raise MissingDataError(
            f"{missing_property} feature(s) in layer {layer.name!r} have no "
            f"{class_property!r} value. they are not burned as nodata silently: "
            "either the attribute is missing upstream, or the property name is "
            "wrong. fix the input or filter them out explicitly."
        )
    if not shapes:
        raise MissingDataError(f"no features to rasterise in layer {layer.name!r}")

    array = rasterize(
        shapes,
        out_shape=template.shape,
        transform=template.transform.to_affine(),
        fill=scheme.nodata_code,
        all_touched=all_touched,
        dtype="int32",
    )

    # Count what actually landed on the grid, not what was submitted. A code
    # present in the input but absent from the output means every polygon
    # carrying it fell between cell centres, and reporting the submitted count
    # as "burned" would overstate the coverage of a layer that partly vanished.
    submitted_codes = {int(code) for _, code in shapes}
    present_codes = {int(code) for code in np.unique(array)} - {int(scheme.nodata_code)}
    lost_codes = sorted(submitted_codes - present_codes)
    cells_burned = int((array != scheme.nodata_code).sum())

    transformation = Transformation(
        operation="rasterize_fuel_vector",
        parameters={
            "source_layer": layer.name,
            "class_property": class_property,
            "scheme": scheme.name,
            "template_layer": template.name,
            "grid_shape": list(template.shape),
            "cell_size": list(template.resolution),
            "all_touched": all_touched,
            "fill": scheme.nodata_code,
            "features_submitted": len(shapes),
            "cells_burned": cells_burned,
            "codes_submitted": sorted(submitted_codes),
            "codes_that_burned_no_cells": lost_codes,
        },
        notes=(
            "unburned cells are the scheme's nodata code, not class 0 "
            "(docs/FAILURE_MODES.md F-MD-1)"
            + (
                " | all_touched=True inflates polygons by up to one cell"
                if all_touched
                else " | cell-CENTRE rule, which is not the same as A-RAS-2's "
                "whole-cell rule: a polygon covering part of a cell without "
                "covering its centre burns nothing"
            )
            + (
                f" | WARNING: class code(s) {lost_codes} burned no cells at all; "
                "every polygon carrying them falls between cell centres"
                if lost_codes
                else ""
            )
        ),
    )
    provenance = layer.provenance.derive(
        name,
        transformation,
        spatial_resolution=(template.transform.x_size, template.transform.y_size),
        resolution_unit=resolution_unit_text(template.crs),
        value_unit="class",
        nodata_representation=repr(scheme.nodata_code),
    )
    if data_class is not None:
        provenance = provenance.with_updates(data_class=DataClass(data_class))
    if temporal_class is not None:
        provenance = provenance.with_updates(
            temporal_class=TemporalProvenance(temporal_class)
        )
    if source is not None:
        provenance = provenance.with_updates(sources=provenance.sources + (source,))

    return RasterLayer(
        name=name,
        data=array,
        transform=template.transform,
        crs=template.crs,
        nodata=scheme.nodata_code,
        kind=RasterKind.CATEGORICAL,
        value_unit="class",
        provenance=provenance,
    )
