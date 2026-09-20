"""Generic facility loaders.

One loader, parameterised by a mapping from the source's own role tags to
:class:`~wildfireguardian_data.facilities.models.FacilityKind`. That mapping is
always supplied by the caller: guessing that ``"대피소"`` or ``"shelter"`` means
a wildfire-capable shelter is precisely the inference A-FAC-2 forbids.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..crs import crs_to_string
from ..errors import IngestError
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
from ..vector import VectorLayer, read_geojson
from .models import Facility, FacilityKind, OperationalStatus

__all__ = ["load_facility_layer", "facilities_from_layer"]


def load_facility_layer(
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
    """Load a facility layer from GeoJSON with caller-supplied provenance."""
    source_path = Path(path)
    provenance = ProvenanceRecord(
        layer_name=name,
        data_class=DataClass(data_class),
        temporal_class=TemporalProvenance(temporal_class),
        temporal_reference=temporal_reference,
        sources=(source,),
        transformations=(
            Transformation(
                operation="load_facility_layer",
                parameters={"path": str(source_path), "declared_crs": crs_to_string(crs)},
                notes=(
                    "facility roles are source-declared; no suitability or "
                    "safety assessment is performed or implied "
                    "(docs/ASSUMPTIONS.md A-FAC-1/2)"
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
        source_path, name=name, provenance=provenance, crs=crs, feature_kind="facility"
    )
    return VectorLayer(
        name=layer.name,
        features=layer.features,
        crs=layer.crs,
        provenance=provenance.with_updates(
            original_crs=crs_to_string(layer.crs), output_crs=crs_to_string(layer.crs)
        ),
        feature_kind=layer.feature_kind,
    )


def facilities_from_layer(
    layer: VectorLayer,
    *,
    kind_property: str,
    kind_map: Mapping[str, FacilityKind],
    id_property: str = "facility_id",
    name_property: str = "name",
    capacity_property: str | None = "capacity_persons",
    status_property: str | None = "operational_status",
    unmapped: str = "error",
) -> tuple[Facility, ...]:
    """Build :class:`Facility` records from a vector layer.

    Parameters
    ----------
    kind_map:
        Source role tag -> :class:`FacilityKind`. Required; nothing is inferred
        from tag spelling.
    unmapped:
        What to do with a role tag absent from ``kind_map``: ``"error"``
        (default) raises, ``"other"`` maps it to :attr:`FacilityKind.OTHER`
        while keeping the original tag in ``source_kind_tag``. There is no
        option that guesses.
    capacity_property:
        Read only if present; a missing capacity stays ``None`` (A-FAC-3).
    """
    if unmapped not in {"error", "other"}:
        raise IngestError(f"unmapped must be 'error' or 'other'; got {unmapped!r}")

    facilities: list[Facility] = []
    for index, feature in enumerate(layer.features):
        properties = feature.properties
        raw_id = properties.get(id_property)
        if raw_id is None or not str(raw_id).strip():
            raise IngestError(
                f"feature {index} of layer {layer.name!r} has no {id_property!r}; "
                "facilities need stable identifiers."
            )
        tag = properties.get(kind_property)
        if tag is None:
            raise IngestError(
                f"facility {raw_id!r} has no {kind_property!r}; its role is "
                "unknown and is not defaulted to a kind."
            )
        key = str(tag)
        if key in kind_map:
            kind = FacilityKind(kind_map[key])
        elif unmapped == "other":
            kind = FacilityKind.OTHER
        else:
            raise IngestError(
                f"facility {raw_id!r} has {kind_property}={key!r}, which is not in "
                f"kind_map (known: {sorted(kind_map)}). add it explicitly or pass "
                "unmapped='other'; this package does not infer a facility's role "
                "from its tag's spelling (docs/ASSUMPTIONS.md A-FAC-2)."
            )

        capacity = (
            properties.get(capacity_property) if capacity_property is not None else None
        )
        status_value = (
            properties.get(status_property) if status_property is not None else None
        )
        try:
            status = (
                OperationalStatus(str(status_value))
                if status_value is not None
                else OperationalStatus.UNKNOWN
            )
        except ValueError:
            raise IngestError(
                f"facility {raw_id!r} has {status_property}={status_value!r}, which "
                f"is not a known OperationalStatus "
                f"({[s.value for s in OperationalStatus]}). an unrecognised "
                "status is not mapped to 'operational'."
            ) from None

        consumed = {id_property, name_property, kind_property}
        if capacity_property:
            consumed.add(capacity_property)
        if status_property:
            consumed.add(status_property)

        facilities.append(
            Facility(
                facility_id=str(raw_id),
                kind=kind,
                geometry=feature.geometry,
                name=str(properties.get(name_property, UNKNOWN)),
                operational_status=status,
                capacity_persons=None if capacity is None else int(capacity),
                source_kind_tag=key,
                attributes={k: v for k, v in properties.items() if k not in consumed},
            )
        )
    return tuple(facilities)
