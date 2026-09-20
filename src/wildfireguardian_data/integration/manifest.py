"""The canonical, versioned bundle manifest — this repository's only contract.

Two manifests exist, on purpose.

``manifest.json`` (``study_area/serialize.py``) is the **writer's record**: file
paths, dtypes, cell counts, per-file checksums. A reader needs it to load the
bundle back, and it changes whenever storage details change.

``bundle_manifest.json``, built here, is the **contract**. It is what another
WildfireGuardian repository is allowed to code against
(``docs/INTERFACES.md``). It is deliberately a *different shape* from the
writer's record, for two reasons:

1. **Absence must be representable.** The contract has a fixed five-slot
   ``layers`` object -- ``terrain``, ``roads``, ``fuels``, ``population``,
   ``facilities`` -- and every slot is always present, carrying a ``status``.
   The writer's record lists only the layers that exist, so "no population
   layer" is expressed by a *missing key*, which a consumer reads as easily as
   an oversight as a finding. Here it is ``status: "ABSENT"`` with the reason.
2. **It must be stable when storage is not.** Item 34 freezes this schema.
   Deriving it, rather than making the writer's record do both jobs, means a
   change to how rasters are stored does not break a downstream contract.

Because it is derived, it cannot drift: it is regenerated from the bundle, and
``wg-data validate-study-area`` re-derives it and reports a mismatch.

**What this manifest never says.** It reports what a layer *is* and where it
came from. It does not say a bundle is scientifically valid, fit for a purpose,
or that anything in it is safe (``AGENTS.md`` §5). ``compatibility.py`` reports
*readiness*, which is a statement about completeness, not about correctness.
"""

from __future__ import annotations

from typing import Any

from ..crs import crs_to_string
from ..provenance.models import (
    NOT_APPLICABLE,
    UNKNOWN,
    ProvenanceRecord,
    governance_class_name,
)
from ..raster import RasterLayer
from ..study_area.bundle import StudyAreaBundle

__all__ = [
    "BUNDLE_CONTRACT_SCHEMA_VERSION",
    "CONTRACT_LAYER_SLOTS",
    "LayerStatus",
    "build_bundle_manifest",
    "BUNDLE_MANIFEST_NAME",
]

#: The contract's own version, independent of ``BUNDLE_SCHEMA_VERSION`` (the
#: on-disk layout) and of ``PROVENANCE_SCHEMA_VERSION`` (one layer's record).
#: Three separate versions because they change for different reasons and a
#: consumer cares about exactly one of them (Phase 2 item 26).
BUNDLE_CONTRACT_SCHEMA_VERSION = "1.0.0"

#: Written next to ``manifest.json``.
BUNDLE_MANIFEST_NAME = "bundle_manifest.json"

#: The five slots, always all present. Fixed so that a consumer can iterate the
#: contract without discovering a sixth key it has no branch for.
CONTRACT_LAYER_SLOTS = ("terrain", "roads", "fuels", "population", "facilities")


class LayerStatus:
    """Why a slot is or is not usable. Uppercase, as governance requires."""

    #: Data is present.
    PRESENT = "PRESENT"
    #: No data, and this is a documented gap rather than an oversight. The
    #: reason is carried alongside; see ``reports/SOURCE_ACCESS_STATUS.md``.
    ABSENT = "ABSENT"


def _source_field(record: ProvenanceRecord, field: str) -> Any:
    """First source's value for ``field``, or ``UNKNOWN`` if there is no source.

    First rather than merged: a layer with several sources has genuinely several
    licences and dates, and picking one silently would be a claim. The full list
    is carried in ``sources`` so a consumer never has to rely on this.
    """
    if not record.sources:
        return UNKNOWN
    return getattr(record.sources[0], field, UNKNOWN)


def _layer_entry(layer: Any, record: ProvenanceRecord) -> dict[str, Any]:
    """The per-layer contract fields required by Phase 2 item 25."""
    is_raster = isinstance(layer, RasterLayer)
    return {
        "name": layer.name,
        "geometry": "raster" if is_raster else "vector",
        # Uppercase at the export boundary, as research governance requires
        # (their OC-029). Lowercase internally; see D-0023.
        "data_class": governance_class_name(record.data_class),
        "planner_legal": record.data_class.planner_legal,
        "temporal_class": record.temporal_class.value,
        "temporal_reference": record.temporal_reference,
        "valid_from": record.valid_from,
        "valid_to": record.valid_to,
        "surface_model": record.surface_model,
        "units": {
            "values": record.value_unit,
            "resolution": record.resolution_unit,
            "vertical_datum": record.vertical_datum,
        },
        "spatial_resolution": (
            list(record.spatial_resolution)
            if record.spatial_resolution is not None
            else None
        ),
        "missing_data_representation": record.nodata_representation,
        "checksum": record.checksum,
        "checksum_algorithm": record.checksum_algorithm,
        "sources": [
            {
                "name": source.name,
                "identifier": source.url_or_identifier,
                "publisher": source.publisher,
                "source_date": source.source_date,
                "acquisition_date": source.acquisition_date,
                "licence": source.licence,
                "licence_url": source.licence_url,
            }
            for source in record.sources
        ],
        # The single most useful field for a consumer deciding whether it may
        # use this layer, and the one a summary is most tempted to drop.
        "licence": _source_field(record, "licence"),
        "unknown_fields": record.unknown_fields(),
        "derived_from": list(record.parents),
        "random_seed": record.random_seed,
        "limitations": _limitations(layer, record),
    }


def _limitations(layer: Any, record: ProvenanceRecord) -> list[str]:
    """Machine-readable limitation tags, not prose.

    Prose limitations live in the provenance ``notes`` and in ``docs/``. A
    consumer cannot branch on prose, so the ones with consequences become tags.
    Derived from recorded facts only -- nothing here is asserted by hand.
    """
    tags: list[str] = []
    if record.surface_model == "dsm":
        tags.append("SURFACE_MODEL_NOT_TERRAIN")
    if record.valid_from == UNKNOWN or record.valid_to == UNKNOWN:
        tags.append("TEMPORAL_VALIDITY_NOT_ESTABLISHED")
    if record.checksum == UNKNOWN:
        tags.append("NOT_CHECKSUMMED")
    if not record.sources:
        tags.append("NO_SOURCE_RECORDED")
    if _source_field(record, "licence") in (UNKNOWN, None, ""):
        tags.append("LICENCE_UNKNOWN")
    if isinstance(layer, RasterLayer):
        if layer.nodata is None and record.nodata_representation != NOT_APPLICABLE:
            tags.append("NO_NODATA_DECLARED")
        for transformation in record.transformations:
            grid = transformation.parameters.get("target_grid_supplied")
            if transformation.operation == "reproject_raster" and grid is False:
                tags.append("REPROJECTED_ONTO_ITS_OWN_GRID")
                break
    if record.unknown_fields():
        tags.append("INCOMPLETE_PROVENANCE")
    return sorted(set(tags))


def _canonical_grid(bundle: StudyAreaBundle) -> dict[str, Any]:
    """The one grid every raster in the bundle shares, or an explicit mismatch.

    Named ``canonical_grid`` because a consumer indexing two rasters by the same
    (row, col) needs to know there *is* one. If the rasters disagree, that is
    reported rather than papered over by picking the first.
    """
    rasters = bundle.raster_layers()
    if not rasters:
        return {"status": "NO_RASTERS", "grid": None, "shape": None}
    grids = {
        (
            layer.transform.x_origin,
            layer.transform.y_origin,
            layer.transform.x_size,
            layer.transform.y_size,
            layer.shape,
        )
        for layer in rasters
    }
    reference = rasters[0]
    entry = {
        "grid": reference.transform.to_dict(),
        "shape": list(reference.shape),
        "crs": crs_to_string(reference.crs),
    }
    if len(grids) == 1:
        entry["status"] = "SHARED"
        return entry
    # Not an error here -- validation reports it (RAS-006/007). But a consumer
    # must not read the reference grid as if every raster were on it.
    entry["status"] = "MISMATCHED"
    entry["mismatch_detail"] = {
        layer.name: {
            "grid": layer.transform.to_dict(),
            "shape": list(layer.shape),
        }
        for layer in rasters
    }
    return entry


def build_bundle_manifest(
    bundle: StudyAreaBundle,
    *,
    created_at: str,
    validation: dict[str, Any] | None = None,
    absent_reasons: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Derive the canonical contract manifest from ``bundle``.

    Parameters
    ----------
    created_at:
        Required, and **not** defaulted to "now". A manifest regenerated for
        comparison must reproduce the original byte-for-byte, which a wall-clock
        default makes impossible -- and a "created_at" that silently means
        "when this was last re-derived" is worse than none.
    validation:
        The bundle's validation summary, if one has been run. ``None`` becomes
        ``status: "NOT_RUN"``, which is distinct from a clean run.
    absent_reasons:
        Per-slot reason a layer is missing. An absent layer with no reason is
        reported as ``UNKNOWN`` reason, which is itself a finding: a gap nobody
        documented.
    """
    reasons = dict(absent_reasons or {})
    layers: dict[str, Any] = {}
    for slot in CONTRACT_LAYER_SLOTS:
        component = bundle.components()[slot]
        if component is None:
            layers[slot] = {
                "status": LayerStatus.ABSENT,
                "reason": reasons.get(slot, UNKNOWN),
                "layers": [],
            }
            continue
        entries = []
        for layer in component.layers():
            record = bundle.provenance.get(layer.name, layer.provenance)
            entries.append(_layer_entry(layer, record))
        slot_entry: dict[str, Any] = {
            "status": LayerStatus.PRESENT,
            "reason": NOT_APPLICABLE,
            "layers": entries,
        }
        # Slot-specific context that is meaningless as a per-layer field.
        if slot == "fuels":
            scheme = getattr(component, "scheme", None)
            slot_entry["class_scheme"] = _scheme_entry(scheme)
        if slot == "roads":
            slot_entry["graph"] = _graph_entry(component)
        layers[slot] = slot_entry

    return {
        "bundle_schema_version": BUNDLE_CONTRACT_SCHEMA_VERSION,
        "bundle_id": bundle.study_area_id,
        "created_at": created_at,
        "study_area": {
            "study_area_id": bundle.study_area_id,
            "bounds": bundle.bounds.to_dict(),
            "extent_m": [bundle.bounds.width, bundle.bounds.height],
        },
        "crs": crs_to_string(bundle.crs),
        "canonical_grid": _canonical_grid(bundle),
        "layers": layers,
        "provenance": {
            "provenance_schema_version": _provenance_schema_versions(bundle),
            "internal_bundle_schema_version": bundle.schema_version,
            "layer_count": len(bundle.all_layers()),
            "unknown_field_count": sum(
                len(record.unknown_fields()) for record in bundle.provenance.values()
            ),
            "data_classes_present": sorted(
                {
                    governance_class_name(record.data_class)
                    for record in bundle.provenance.values()
                }
            ),
        },
        "validation": validation
        if validation is not None
        else {"status": "NOT_RUN", "counts": None},
        "checksums": {
            layer.name: bundle.provenance.get(
                layer.name, layer.provenance
            ).checksum
            for layer in bundle.all_layers()
        },
        "caveats": [
            "this manifest reports what each layer is and where it came from. "
            "it does NOT assert that the bundle is scientifically valid, fit "
            "for any particular analysis, or that anything in it is safe, "
            "passable, or usable (AGENTS.md sections 5 and 8)",
            "an ABSENT layer is absent. nothing was estimated, interpolated, "
            "or defaulted to fill it (AGENTS.md section 3)",
            "data_class is uppercase here and lowercase in this repository's "
            "own artifacts; see docs/DECISIONS.md D-0023 for the RETROSPECTIVE "
            "axis collapse this boundary performs",
        ],
    }


def _provenance_schema_versions(bundle: StudyAreaBundle) -> list[str]:
    """Every provenance schema version in the bundle.

    A list, not one value: a bundle can legitimately mix versions after a
    partial rewrite, and collapsing that to the first would hide it.
    """
    return sorted({record.schema_version for record in bundle.provenance.values()})


def _scheme_entry(scheme: Any) -> dict[str, Any]:
    """The fuel/vegetation scheme, or an explicit statement that there is none."""
    if scheme is None:
        return {"status": "ABSENT", "name": UNKNOWN}
    kind = getattr(scheme, "scheme_kind", None)
    return {
        "status": "PRESENT",
        "name": getattr(scheme, "name", UNKNOWN),
        # The distinction Phase 2 item 6 turns on: whether these classes are
        # the publisher's own legend or somebody's model of fuel behaviour.
        "scheme_kind": getattr(kind, "value", str(kind) if kind else UNKNOWN),
        "represents": getattr(scheme, "represents", UNKNOWN),
        "class_count": len(getattr(scheme, "codes", ()) or ()),
        "nodata_code": getattr(scheme, "nodata_code", None),
        "spatial_resolution_m": getattr(scheme, "spatial_resolution_m", None),
        "vintage": getattr(scheme, "vintage", UNKNOWN),
        "licence": getattr(scheme, "licence", UNKNOWN),
        "known_limitations": list(getattr(scheme, "known_limitations", ()) or ()),
        "is_fire_behaviour_fuel_model": False,
    }


def _graph_entry(component: Any) -> dict[str, Any]:
    """Road topology counts, phrased as topology and never as access.

    Read from the **QA report**, not from the live ``RoadGraph``. The graph is
    an in-memory object that ``read_bundle`` does not reconstruct, so sourcing
    the contract from it would make this manifest depend on whether the bundle
    happened to be freshly built or read from disk -- and a contract that says
    different things about the same bundle is not a contract. The QA report is
    persisted, so these numbers round-trip.
    """
    qa = getattr(component, "qa", None)
    if qa is None:
        return {"status": "ABSENT", "reason": "no road QA report in this bundle"}
    graph = _as_mapping(qa, "graph")
    connectivity = _as_mapping(qa, "connectivity")
    return {
        "status": "PRESENT",
        "source": "road QA report",
        "node_count": graph.get("nodes"),
        "edge_count": graph.get("edges"),
        "component_count": connectivity.get("component_count"),
        "total_length_m": graph.get("total_length_m"),
        "length_unit": graph.get("length_unit", UNKNOWN),
        "directed": graph.get("directed"),
        "noting": (
            "counts describe graph topology as digitised. they do not state "
            "that any route is passable, any area reachable, or anyone "
            "trapped (AGENTS.md section 5). lengths are planar geometry in "
            "the analysis CRS, not travel distances (A-RD-5)"
        ),
    }


def _as_mapping(container: Any, key: str) -> dict[str, Any]:
    """``container[key]`` or ``container.key``, whichever the object supports.

    The QA report is a dataclass when freshly built and a plain dict when read
    back, and both must produce the same manifest.
    """
    if isinstance(container, dict):
        value = container.get(key)
    else:
        value = getattr(container, key, None)
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        return result if isinstance(result, dict) else {}
    return {
        name: getattr(value, name)
        for name in getattr(value, "__dataclass_fields__", ())
    }
