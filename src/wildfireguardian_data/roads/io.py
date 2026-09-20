"""Road-layer ingestion.

GeoJSON is read through :func:`wildfireguardian_data.vector.read_geojson`, which
refuses to default an undeclared CRS. Other formats (GeoPackage, shapefile) go
through geopandas/pyogrio when the ``[geo]`` extra is installed.

No attribute read here is interpreted. A ``surface`` or ``width`` column is
carried as an opaque property, never as evidence that a road can be used
(``docs/ASSUMPTIONS.md`` A-RD-6).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..crs import crs_to_string, parse_crs
from ..errors import IngestError, OptionalDependencyError
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
from ..vector import Feature, VectorLayer, read_geojson

__all__ = ["read_road_geojson", "read_road_vector"]


def read_road_geojson(
    path: str | Path,
    *,
    name: str,
    source: SourceRecord,
    data_class: DataClass,
    temporal_class: TemporalProvenance,
    crs: Any = None,
    temporal_reference: str = UNKNOWN,
    notes: str = "",
) -> VectorLayer:
    """Read a road network from GeoJSON, with caller-supplied provenance."""
    source_path = Path(path)
    provenance = ProvenanceRecord(
        layer_name=name,
        data_class=DataClass(data_class),
        temporal_class=TemporalProvenance(temporal_class),
        temporal_reference=temporal_reference,
        sources=(source,),
        transformations=(
            Transformation(
                operation="read_road_geojson",
                parameters={"path": str(source_path), "declared_crs": crs_to_string(crs)},
                notes=(
                    "source attributes are carried opaquely; none is read as a "
                    "statement about usability (docs/ASSUMPTIONS.md A-RD-6)"
                ),
            ),
        ),
        original_crs=crs_to_string(crs) if crs is not None else UNKNOWN,
        output_crs=crs_to_string(crs) if crs is not None else UNKNOWN,
        value_unit="not_applicable",
        # A vector layer has no cell size, no vertical datum and no nodata
        # convention: those facts do not exist rather than being unknown, and
        # recording UNKNOWN for them would dilute the incompleteness metric
        # D-0009 exists to keep meaningful. The OSM fetcher already did this;
        # the local-file loaders did not.
        resolution_unit=NOT_APPLICABLE,
        vertical_datum=NOT_APPLICABLE,
        nodata_representation=NOT_APPLICABLE,
        surface_model=NOT_APPLICABLE,
        checksum=sha256_file(source_path) if source_path.exists() else UNKNOWN,
        notes=notes,
    )
    layer = read_geojson(
        source_path,
        name=name,
        provenance=provenance,
        crs=crs,
        feature_kind="road segment",
    )
    if layer.is_empty:
        raise IngestError(
            f"{source_path} contains no features; an empty road layer is "
            "reported rather than returned."
        )
    return layer.__class__(
        name=layer.name,
        features=layer.features,
        crs=layer.crs,
        provenance=provenance.with_updates(
            original_crs=crs_to_string(layer.crs), output_crs=crs_to_string(layer.crs)
        ),
        feature_kind=layer.feature_kind,
    )


def read_road_vector(
    path: str | Path,
    *,
    name: str,
    source: SourceRecord,
    data_class: DataClass,
    temporal_class: TemporalProvenance,
    layer_name: str | None = None,
    temporal_reference: str = UNKNOWN,
    notes: str = "",
) -> VectorLayer:
    """Read a road network from any OGR-readable vector format.

    Requires the ``[geo]`` extra. The file's own CRS is used and recorded; a
    file with no CRS raises rather than being assumed into one.
    """
    try:
        import geopandas as gpd
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise OptionalDependencyError(
            "geopandas", purpose="reading GeoPackage/shapefile road layers"
        ) from exc

    source_path = Path(path)
    frame = gpd.read_file(source_path, layer=layer_name) if layer_name else gpd.read_file(source_path)
    if frame.crs is None:
        raise IngestError(
            f"{source_path} declares no CRS. this package does not infer a CRS "
            "from coordinate ranges (docs/ASSUMPTIONS.md A-CRS-5); set it in the "
            "source file or convert it explicitly first."
        )
    crs = parse_crs(frame.crs.to_wkt())

    features: list[Feature] = []
    for row in frame.itertuples(index=False):
        mapping = row._asdict() if hasattr(row, "_asdict") else dict(row)
        geometry = mapping.pop("geometry", None)
        if geometry is None or geometry.is_empty:
            raise IngestError(
                f"{source_path} contains a feature with no geometry; rejected "
                "rather than skipped so the count read matches the count in the file."
            )
        features.append(Feature(geometry, {k: _plain(v) for k, v in mapping.items()}))

    provenance = ProvenanceRecord(
        layer_name=name,
        data_class=DataClass(data_class),
        temporal_class=TemporalProvenance(temporal_class),
        temporal_reference=temporal_reference,
        sources=(source,),
        transformations=(
            Transformation(
                operation="read_road_vector",
                parameters={
                    "path": str(source_path),
                    "ogr_layer": layer_name or UNKNOWN,
                    "features": len(features),
                    "file_crs": crs_to_string(crs),
                },
                notes="read via geopandas/pyogrio; attributes carried opaquely",
            ),
        ),
        original_crs=crs_to_string(crs),
        output_crs=crs_to_string(crs),
        value_unit="not_applicable",
        # A vector layer has no cell size, no vertical datum and no nodata
        # convention: those facts do not exist rather than being unknown, and
        # recording UNKNOWN for them would dilute the incompleteness metric
        # D-0009 exists to keep meaningful. The OSM fetcher already did this;
        # the local-file loaders did not.
        resolution_unit=NOT_APPLICABLE,
        vertical_datum=NOT_APPLICABLE,
        nodata_representation=NOT_APPLICABLE,
        surface_model=NOT_APPLICABLE,
        checksum=sha256_file(source_path) if source_path.exists() else UNKNOWN,
        notes=notes,
    )
    return VectorLayer(
        name=name,
        features=tuple(features),
        crs=crs,
        provenance=provenance,
        feature_kind="road segment",
    )


def _plain(value: Any) -> Any:
    """Coerce a pandas value to a plain Python value, keeping missing as None."""
    if value is None:
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    if isinstance(value, float) and math.isnan(value):
        # NaN from a vector attribute table means "attribute absent", and None
        # says that; leaving NaN would make it a number.
        return None
    return value
