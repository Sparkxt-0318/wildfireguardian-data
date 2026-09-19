"""Raster I/O: GeoTIFF via rasterio, and a GDAL-free ``.npz`` fallback.

Ingestion always requires the caller to supply a
:class:`~wildfireguardian_data.provenance.models.SourceRecord` and a
:class:`~wildfireguardian_data.provenance.models.DataClass`. Nothing here
invents provenance from a filename: a file called ``dem_2023_srtm.tif`` is not
evidence of anything (AGENTS.md §3).

Two on-disk formats, both recorded in the bundle manifest:

* **GeoTIFF** -- the interoperable default, needs the ``[geo]`` extra;
* **NPZ** (``.npz`` array plus a ``.sidecar.json``) -- exists so a bundle can be
  written and read with no GDAL at all (``docs/DECISIONS.md`` D-0003), and so
  the test suite exercises the round trip in any environment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from ..crs import crs_to_string, parse_crs
from ..errors import IngestError, OptionalDependencyError, RasterGeometryError
from ..provenance.checksum import sha256_array, sha256_file
from ..provenance.models import (
    UNKNOWN,
    DataClass,
    ProvenanceRecord,
    SourceRecord,
    TemporalProvenance,
    Transformation,
    utc_now_iso,
)
from ..raster import GridTransform, RasterKind, RasterLayer
from ..units import LengthUnit

__all__ = [
    "read_geotiff",
    "write_geotiff",
    "read_npz_raster",
    "write_npz_raster",
    "read_raster",
    "write_raster",
    "RASTER_SIDECAR_SUFFIX",
]

RASTER_SIDECAR_SUFFIX = ".sidecar.json"


def _rasterio():
    try:
        import rasterio

        return rasterio
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise OptionalDependencyError("rasterio", purpose="GeoTIFF I/O") from exc


def read_geotiff(
    path: str | Path,
    *,
    name: str,
    source: SourceRecord,
    data_class: DataClass,
    temporal_class: TemporalProvenance,
    band: int = 1,
    kind: RasterKind = RasterKind.CONTINUOUS,
    value_unit: Any = LengthUnit.METRE,
    vertical_datum: str = UNKNOWN,
    temporal_reference: str = UNKNOWN,
    notes: str = "",
) -> RasterLayer:
    """Read one band of a GeoTIFF into a :class:`RasterLayer`.

    ``data_class`` and ``temporal_class`` are required: a DEM tile on disk does
    not say whether it is an observation or a model output, nor what period it
    describes, and guessing either would put a fabricated fact into provenance.

    ``value_unit`` defaults to metres because that is what a GeoTIFF DEM almost
    always holds -- but the default is recorded in provenance as an *assumption*
    note when the file itself carries no unit metadata, so a reader can see that
    it was assumed rather than read.
    """
    rasterio = _rasterio()
    source_path = Path(path)
    with rasterio.open(source_path) as dataset:
        if band < 1 or band > dataset.count:
            raise IngestError(
                f"{source_path} has {dataset.count} band(s); band={band} requested"
            )
        array = dataset.read(band)
        try:
            transform = GridTransform.from_affine(dataset.transform)
        except RasterGeometryError as exc:
            raise IngestError(
                f"{source_path} is not a north-up axis-aligned raster: {exc}"
            ) from exc
        crs = parse_crs(dataset.crs.to_wkt()) if dataset.crs else None
        nodata = dataset.nodatavals[band - 1]
        unit_tag = None
        try:
            unit_tag = dataset.units[band - 1]
        except Exception:
            unit_tag = None

    is_local = source_path.exists()
    checksum = sha256_file(source_path) if is_local else UNKNOWN
    unit_note = (
        f"band unit tag from file: {unit_tag!r}"
        if unit_tag
        else (
            f"file declares no band unit; value_unit={getattr(value_unit, 'value', value_unit)!r} "
            "was supplied by the caller, not read from the file"
        )
    )
    if crs is None:
        unit_note += (
            " | file declares no CRS: the layer is loadable but cannot be "
            "combined with anything (docs/ASSUMPTIONS.md A-CRS-5)"
        )

    provenance = ProvenanceRecord(
        layer_name=name,
        data_class=DataClass(data_class),
        temporal_class=TemporalProvenance(temporal_class),
        temporal_reference=temporal_reference,
        sources=(source,),
        transformations=(
            Transformation(
                operation="read_geotiff",
                parameters={
                    "path": str(source_path),
                    "band": band,
                    "driver": "GTiff",
                    "dtype": str(array.dtype),
                    "shape": list(array.shape),
                    "nodata_in_file": repr(nodata),
                    "file_crs": crs_to_string(crs),
                    "cell_size": [transform.x_size, transform.y_size],
                },
                notes=unit_note,
            ),
        ),
        original_crs=crs_to_string(crs),
        output_crs=crs_to_string(crs),
        spatial_resolution=(transform.x_size, transform.y_size),
        resolution_unit=_resolution_unit_text(crs),
        value_unit=getattr(value_unit, "value", str(value_unit)),
        vertical_datum=vertical_datum,
        nodata_representation="none_declared" if nodata is None else repr(nodata),
        checksum=checksum,
        notes=notes,
    )
    return RasterLayer(
        name=name,
        data=array,
        transform=transform,
        crs=crs,
        nodata=nodata,
        kind=RasterKind(kind),
        value_unit=value_unit,
        provenance=provenance,
    )


def write_geotiff(layer: RasterLayer, path: str | Path, *, compress: str = "deflate") -> Path:
    """Write a layer as a single-band GeoTIFF.

    Writes the layer's ``nodata`` into the file so the missing-data semantics
    survive the round trip. A layer with ``nodata=None`` is written without a
    nodata tag rather than with ``0``, preserving the distinction between "no
    missing-data value declared" and "zero means missing" (A-RAS-3).
    """
    rasterio = _rasterio()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    profile: dict[str, Any] = {
        "driver": "GTiff",
        "height": layer.height,
        "width": layer.width,
        "count": 1,
        "dtype": layer.data.dtype,
        "transform": layer.transform.to_affine(),
        "compress": compress,
        "tiled": False,
    }
    if layer.crs is not None:
        profile["crs"] = rasterio.crs.CRS.from_wkt(layer.crs.to_wkt())
    if layer.nodata is not None:
        profile["nodata"] = layer.nodata
    with rasterio.open(target, "w", **profile) as dataset:
        dataset.write(layer.data, 1)
        dataset.update_tags(
            wg_layer_name=layer.name,
            wg_value_unit=getattr(layer.value_unit, "value", str(layer.value_unit)),
            wg_kind=layer.kind.value,
            wg_data_class=layer.provenance.data_class.value,
            wg_temporal_class=layer.provenance.temporal_class.value,
            wg_provenance_note=(
                "full provenance is in the bundle's provenance/ sidecar; these "
                "tags are a convenience copy and are not authoritative"
            ),
        )
    return target


def write_npz_raster(layer: RasterLayer, path: str | Path) -> Path:
    """Write a layer as ``.npz`` plus a JSON sidecar (no GDAL needed).

    The sidecar holds grid, CRS (as WKT **and** as an authority string), nodata,
    units, kind, and the array checksum. WKT as well as the code because an
    authority string alone cannot round-trip a CRS that has no authority code.
    """
    target = Path(path)
    if target.suffix != ".npz":
        target = target.with_suffix(".npz")
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(target, data=layer.data)
    sidecar = target.with_name(target.stem + RASTER_SIDECAR_SUFFIX)
    payload = {
        "format": "wg_npz_raster_v1",
        "layer_name": layer.name,
        "dtype": str(layer.data.dtype),
        "shape": list(layer.shape),
        "transform": layer.transform.to_dict(),
        "crs_authority": crs_to_string(layer.crs),
        "crs_wkt": layer.crs.to_wkt() if layer.crs is not None else None,
        "nodata": _json_nodata(layer.nodata),
        "kind": layer.kind.value,
        "value_unit": getattr(layer.value_unit, "value", str(layer.value_unit)),
        "array_checksum_sha256": sha256_array(layer.data),
        "written_at": utc_now_iso(),
    }
    sidecar.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def read_npz_raster(path: str | Path, *, provenance: ProvenanceRecord) -> RasterLayer:
    """Read a layer written by :func:`write_npz_raster`.

    Verifies the array checksum from the sidecar and raises on mismatch: a
    bundle whose data changed under its provenance is worse than no bundle.
    """
    target = Path(path)
    sidecar_path = target.with_name(target.stem + RASTER_SIDECAR_SUFFIX)
    if not sidecar_path.exists():
        raise IngestError(
            f"no sidecar {sidecar_path.name} beside {target.name}; the array "
            "alone does not carry grid, CRS, units, or nodata, and this package "
            "will not guess them."
        )
    meta = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if meta.get("format") != "wg_npz_raster_v1":
        raise IngestError(
            f"{sidecar_path} declares format {meta.get('format')!r}, expected "
            "'wg_npz_raster_v1'"
        )
    with np.load(target) as handle:
        array = handle["data"]
    expected = meta.get("array_checksum_sha256")
    actual = sha256_array(array)
    if expected and expected != actual:
        raise IngestError(
            f"{target} array checksum {actual} does not match the sidecar's "
            f"{expected}: the data has changed since it was written."
        )
    crs = meta.get("crs_wkt") or (
        None if meta.get("crs_authority") in (None, "UNKNOWN") else meta["crs_authority"]
    )
    return RasterLayer(
        name=meta["layer_name"],
        data=array,
        transform=GridTransform.from_dict(meta["transform"]),
        crs=crs,
        nodata=_nodata_from_json(meta.get("nodata")),
        kind=RasterKind(meta["kind"]),
        value_unit=meta.get("value_unit", UNKNOWN),
        provenance=provenance,
    )


def write_raster(layer: RasterLayer, path: str | Path, *, prefer_geotiff: bool = True) -> Path:
    """Write a raster as GeoTIFF when possible, else as ``.npz``.

    Returns the path actually written, which the caller records in the manifest
    -- the format is never assumed from the requested name.
    """
    target = Path(path)
    if prefer_geotiff:
        try:
            return write_geotiff(layer, target.with_suffix(".tif"))
        except OptionalDependencyError:
            pass
    return write_npz_raster(layer, target.with_suffix(".npz"))


def read_raster(path: str | Path, *, provenance: ProvenanceRecord, **kwargs: Any) -> RasterLayer:
    """Read a raster written by :func:`write_raster`, dispatching on suffix."""
    target = Path(path)
    if target.suffix == ".npz":
        return read_npz_raster(target, provenance=provenance)
    if target.suffix in {".tif", ".tiff"}:
        rasterio = _rasterio()
        with rasterio.open(target) as dataset:
            array = dataset.read(1)
            transform = GridTransform.from_affine(dataset.transform)
            crs = parse_crs(dataset.crs.to_wkt()) if dataset.crs else None
            nodata = dataset.nodatavals[0]
            tags = dataset.tags()
        return RasterLayer(
            name=kwargs.get("name", tags.get("wg_layer_name", target.stem)),
            data=array,
            transform=transform,
            crs=crs,
            nodata=nodata,
            kind=RasterKind(kwargs.get("kind", tags.get("wg_kind", "continuous"))),
            value_unit=kwargs.get("value_unit", tags.get("wg_value_unit", UNKNOWN)),
            provenance=provenance,
        )
    raise IngestError(
        f"unsupported raster suffix {target.suffix!r} for {target}; expected "
        "'.tif', '.tiff' or '.npz'"
    )


def _resolution_unit_text(crs: Any) -> str:
    """Axis unit of a CRS, as provenance text, without guessing."""
    if crs is None:
        return UNKNOWN
    try:
        units = {axis.unit_name for axis in crs.axis_info}
    except Exception:
        return UNKNOWN
    if len(units) == 1:
        return next(iter(units))
    return UNKNOWN if not units else "/".join(sorted(units))


def _json_nodata(nodata: Any) -> Any:
    """Serialise a nodata value, preserving ``NaN`` as a tagged string."""
    if nodata is None:
        return None
    try:
        if np.isnan(nodata):
            return "nan"
    except TypeError:
        pass
    if hasattr(nodata, "item"):
        return nodata.item()
    return nodata


def _nodata_from_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        if value == "nan":
            return float("nan")
        raise IngestError(
            f"unrecognised nodata encoding {value!r} in raster sidecar; expected "
            "null, a number, or 'nan'"
        )
    return value
