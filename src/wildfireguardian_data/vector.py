"""The internal vector model: features + CRS + provenance.

Shapely geometries in (easting, northing) order, a declared CRS, opaque source
properties, and provenance. Reprojection is always explicit and always recorded
(``docs/DECISIONS.md`` D-0002, D-0009).

GeoJSON handling note
---------------------
RFC 7946 mandates WGS 84 longitude/latitude for GeoJSON. This package writes
GeoJSON in the **study area's own projected CRS** and records that CRS both in
the provenance sidecar and in a non-standard ``"crs"`` member of the written
file. That is a deliberate, documented departure: silently reprojecting every
vector layer to EPSG:4326 on write, and back on read, would introduce two
resampling-free but datum-dependent coordinate conversions per round trip, and a
reader that ignored the CRS member would place Korean metre coordinates on a
degree globe. Refusing to write projected GeoJSON would instead force every
bundle through a lossy conversion. The chosen trade is to stay in the analysis
CRS and to make any reader that ignores the declaration fail loudly, which
:func:`read_geojson` does. See ``docs/FAILURE_MODES.md`` F-VEC-1.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from .bounds import Bounds
from .crs import (
    CRSLike,
    crs_equal,
    crs_to_string,
    parse_crs,
    require_crs,
    transformer_for,
)
from .errors import IngestError, ProvenanceError
from .provenance.models import ProvenanceRecord, Transformation, require_provenance

__all__ = ["Feature", "VectorLayer", "read_geojson", "write_geojson"]

#: Written into GeoJSON files so a reader can tell which CRS the coordinates are
#: in. Not part of RFC 7946; see the module docstring.
GEOJSON_CRS_MEMBER = "crs"


@dataclass(frozen=True)
class Feature:
    """One geometry plus its source properties.

    Properties are carried **opaquely**: this package does not interpret a
    ``surface`` or ``width`` attribute, and specifically never reads one as a
    statement about whether a road is usable (``docs/SCOPE.md``).
    """

    geometry: BaseGeometry
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.geometry, BaseGeometry):
            raise IngestError(
                f"Feature.geometry must be a shapely geometry; got "
                f"{type(self.geometry).__name__}"
            )
        if self.geometry.is_empty:
            raise IngestError(
                "Feature.geometry is empty. an empty geometry is dropped by most "
                "GIS tools without comment; here it is rejected so the upstream "
                "cause is visible."
            )
        object.__setattr__(self, "properties", dict(self.properties))

    @property
    def geom_type(self) -> str:
        return self.geometry.geom_type

    def get(self, key: str, default: Any = None) -> Any:
        return self.properties.get(key, default)

    def to_geojson(self) -> dict[str, Any]:
        return {
            "type": "Feature",
            "geometry": mapping(self.geometry),
            "properties": dict(self.properties),
        }


@dataclass(frozen=True)
class VectorLayer:
    """A set of features in one declared CRS, with provenance."""

    name: str
    features: tuple[Feature, ...]
    crs: Any
    provenance: ProvenanceRecord
    #: Free-text note on what one feature represents (``"road segment"``,
    #: ``"village polygon"``). Descriptive only; nothing branches on it.
    feature_kind: str = "UNKNOWN"

    def __post_init__(self) -> None:
        object.__setattr__(self, "features", tuple(self.features))
        for index, feature in enumerate(self.features):
            if not isinstance(feature, Feature):
                raise IngestError(
                    f"features[{index}] is {type(feature).__name__}, not a Feature"
                )
        object.__setattr__(self, "crs", parse_crs(self.crs))
        require_provenance(self.provenance, context=f"constructing layer {self.name!r}")
        if self.provenance.layer_name != self.name:
            raise ProvenanceError(
                f"layer name {self.name!r} does not match provenance layer_name "
                f"{self.provenance.layer_name!r}"
            )

    # -- container behaviour ------------------------------------------------ #
    def __len__(self) -> int:
        return len(self.features)

    def __iter__(self) -> Iterator[Feature]:
        return iter(self.features)

    def __getitem__(self, index: int) -> Feature:
        return self.features[index]

    @property
    def is_empty(self) -> bool:
        return len(self.features) == 0

    @property
    def geom_types(self) -> set[str]:
        return {f.geom_type for f in self.features}

    @property
    def property_keys(self) -> set[str]:
        keys: set[str] = set()
        for feature in self.features:
            keys |= set(feature.properties)
        return keys

    @property
    def bounds(self) -> Bounds | None:
        """Bounds of all features, or ``None`` for an empty layer.

        ``None`` rather than a zero box: an empty layer has no extent, and a
        zero box at the origin would silently place it off the coast of Ghana.
        """
        if not self.features:
            return None
        xs_min, ys_min, xs_max, ys_max = zip(
            *(f.geometry.bounds for f in self.features), strict=True
        )
        return Bounds(min(xs_min), min(ys_min), max(xs_max), max(ys_max), crs=self.crs)

    def filter(
        self,
        predicate: Callable[[Feature], bool],
        *,
        name: str | None = None,
        transformation: Transformation | None = None,
    ) -> VectorLayer:
        """A new layer containing features satisfying ``predicate``.

        When ``transformation`` is given the result is a provenance-derived
        layer; otherwise it is the same layer with fewer features, which is only
        appropriate for intermediate use inside one function.
        """
        kept = tuple(f for f in self.features if predicate(f))
        new_name = name or self.name
        if transformation is None:
            return replace(self, features=kept, name=new_name)
        record = self.provenance.derive(
            new_name,
            transformation.__class__(
                operation=transformation.operation,
                parameters={
                    **transformation.parameters,
                    "features_in": len(self.features),
                    "features_out": len(kept),
                },
                notes=transformation.notes,
            ),
        )
        return VectorLayer(
            name=new_name,
            features=kept,
            crs=self.crs,
            provenance=record,
            feature_kind=self.feature_kind,
        )

    def derived(
        self,
        features: Iterable[Feature],
        *,
        name: str,
        transformation: Transformation,
        crs: CRSLike = ...,
        feature_kind: str | None = None,
        provenance_changes: dict[str, Any] | None = None,
    ) -> VectorLayer:
        """A new layer derived from this one, carrying provenance forward."""
        new_crs = self.crs if crs is ... else parse_crs(crs)
        changes = dict(provenance_changes or {})
        changes.setdefault("output_crs", crs_to_string(new_crs))
        record = self.provenance.derive(name, transformation, **changes)
        return VectorLayer(
            name=name,
            features=tuple(features),
            crs=new_crs,
            provenance=record,
            feature_kind=feature_kind if feature_kind is not None else self.feature_kind,
        )

    def reproject(self, dst_crs: CRSLike, *, name: str | None = None) -> VectorLayer:
        """Reproject explicitly, recording the operation in provenance.

        Never called implicitly by anything in this package (D-0002).
        Geometries are transformed with an ``always_xy=True`` transformer, so
        tuple order is (easting, northing) on both sides regardless of what the
        authorities declare (see :mod:`wildfireguardian_data.crs`).
        """
        src = require_crs(self.crs, context=f"reprojecting layer {self.name!r}")
        dst = require_crs(dst_crs, context=f"reprojecting layer {self.name!r}")
        if src.equals(dst):
            return self
        transformer = transformer_for(src, dst)

        def _tx(x, y, z=None):  # shapely passes coordinate sequences
            return transformer.transform(x, y)

        new_features = tuple(
            Feature(shapely_transform(_tx, f.geometry), dict(f.properties))
            for f in self.features
        )
        transformation = Transformation(
            operation="reproject_vector",
            parameters={
                "src_crs": crs_to_string(src),
                "dst_crs": crs_to_string(dst),
                "always_xy": True,
                "features": len(new_features),
            },
            notes=(
                "vector reprojection is exact per-vertex; segment interiors are "
                "not densified, so long segments keep their straight-line "
                "geometry in the target CRS"
            ),
        )
        return self.derived(
            new_features,
            name=name or self.name,
            transformation=transformation,
            crs=dst,
        )

    def same_crs_as(self, other: Any) -> bool:
        return crs_equal(self.crs, getattr(other, "crs", other))

    def describe(self) -> dict[str, Any]:
        bounds = self.bounds
        return {
            "name": self.name,
            "features": len(self.features),
            "geom_types": sorted(self.geom_types),
            "feature_kind": self.feature_kind,
            "crs": crs_to_string(self.crs),
            "bounds": bounds.to_dict() if bounds is not None else None,
            "property_keys": sorted(self.property_keys),
            "data_class": self.provenance.data_class.value,
            "temporal_class": self.provenance.temporal_class.value,
        }

    def to_geojson(self) -> dict[str, Any]:
        """A GeoJSON ``FeatureCollection`` with a non-standard CRS member."""
        return {
            "type": "FeatureCollection",
            GEOJSON_CRS_MEMBER: {
                "type": "name",
                "properties": {"name": crs_to_string(self.crs)},
            },
            # WKT as well as the authority string: a custom projection has no
            # authority code, and an authority-string-only declaration cannot
            # round-trip it (the raster sidecar has always done this).
            "wg_crs_wkt": self.crs.to_wkt() if self.crs is not None else None,
            "wg_layer_name": self.name,
            "wg_feature_kind": self.feature_kind,
            "features": [f.to_geojson() for f in self.features],
        }

    def __str__(self) -> str:
        return (
            f"VectorLayer({self.name!r}, {len(self.features)} features, "
            f"{sorted(self.geom_types)}, crs={crs_to_string(self.crs)})"
        )


def write_geojson(layer: VectorLayer, path: str | Path) -> Path:
    """Write a vector layer as GeoJSON in its own CRS.

    Requires a declared CRS: writing coordinates whose CRS is unknown produces a
    file that cannot be used correctly by anyone.
    """
    require_crs(layer.crs, context=f"writing layer {layer.name!r} to GeoJSON")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(layer.to_geojson(), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def read_geojson(
    path: str | Path,
    *,
    name: str,
    provenance: ProvenanceRecord,
    crs: CRSLike = None,
    feature_kind: str = "UNKNOWN",
    require_declared_crs: bool = True,
) -> VectorLayer:
    """Read a GeoJSON file into a :class:`VectorLayer`.

    The CRS is taken from the ``crs`` argument when given, else from the file's
    ``crs`` member. When neither is present and ``require_declared_crs`` is true
    (the default) this raises rather than assuming EPSG:4326 -- the assumption
    RFC 7946 would license and that would place Korean metre coordinates near
    the Gulf of Guinea (``docs/FAILURE_MODES.md`` F-VEC-1).

    When both are present and they disagree, this raises: the caller's belief
    and the file's declaration differing is a real conflict, not something to
    resolve by precedence.
    """
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IngestError(f"could not read GeoJSON {source}: {exc}") from exc

    if payload.get("type") != "FeatureCollection":
        raise IngestError(
            f"{source} is a GeoJSON {payload.get('type')!r}, not a FeatureCollection"
        )

    declared = None
    crs_member = payload.get(GEOJSON_CRS_MEMBER)
    if isinstance(crs_member, dict):
        declared = (crs_member.get("properties") or {}).get("name")
    # Prefer the WKT when present: it round-trips a CRS with no authority code,
    # which the authority string cannot.
    declared = payload.get("wg_crs_wkt") or declared

    if crs is not None and declared not in (None, "UNKNOWN"):
        if not crs_equal(crs, declared):
            raise IngestError(
                f"{source} declares CRS {declared!r} but was read with "
                f"crs={crs_to_string(crs)}. these disagree; resolve the conflict "
                "explicitly rather than trusting one of them."
            )
    effective = crs if crs is not None else declared
    if effective in (None, "UNKNOWN") and require_declared_crs:
        raise IngestError(
            f"{source} has no CRS declaration and none was supplied. this package "
            "does not default GeoJSON to EPSG:4326 (docs/FAILURE_MODES.md "
            "F-VEC-1); pass crs=... explicitly."
        )

    features: list[Feature] = []
    for index, raw in enumerate(payload.get("features", [])):
        geometry = raw.get("geometry")
        if geometry is None:
            raise IngestError(
                f"{source} feature {index} has a null geometry. null-geometry "
                "features are rejected rather than skipped, so the count of "
                "features read always equals the count in the file."
            )
        try:
            geom = shape(geometry)
        except Exception as exc:
            raise IngestError(
                f"{source} feature {index} has an unreadable geometry: {exc}"
            ) from exc
        features.append(Feature(geom, raw.get("properties") or {}))

    return VectorLayer(
        name=name,
        features=tuple(features),
        crs=None if effective in (None, "UNKNOWN") else effective,
        provenance=provenance,
        feature_kind=feature_kind,
    )
