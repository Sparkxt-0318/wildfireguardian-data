"""Writing and reading study-area bundles on disk.

Layout: ``docs/INTERFACES.md``. Internal and unstable (D-0001); ``manifest.json``
carries ``schema_version`` and a reader that does not recognise it must fail
rather than guess.

Every written artifact is checksummed, and the checksum goes into both the
manifest and the layer's provenance sidecar. That is what makes
"reproducible" checkable: rebuild, compare checksums.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..bounds import Bounds
from ..crs import crs_to_string, parse_crs
from ..errors import BundleError
from ..provenance.checksum import sha256_file, sha256_json
from ..provenance.models import utc_now_iso
from ..provenance.store import read_all_provenance, write_provenance
from ..raster import RasterLayer
from ..terrain.io import RASTER_SIDECAR_SUFFIX, read_raster, write_raster
from ..vector import VectorLayer, read_geojson, write_geojson
from .bundle import (
    BUNDLE_SCHEMA_VERSION,
    FacilitiesComponent,
    FuelsComponent,
    PopulationComponent,
    RoadsComponent,
    StudyAreaBundle,
    TerrainComponent,
)

__all__ = [
    "write_bundle",
    "read_bundle",
    "write_validation_report",
    "write_bundle_manifest",
    "MANIFEST_NAME",
]

MANIFEST_NAME = "manifest.json"

#: Sub-directory for each component's layers.
_COMPONENT_DIRS = {
    "terrain": "terrain",
    "roads": "roads",
    "fuels": "fuels",
    "population": "population",
    "facilities": "facilities",
}


def _layer_component(bundle: StudyAreaBundle, layer_name: str) -> str:
    for component_name, component in bundle.components().items():
        if component is None:
            continue
        if any(layer.name == layer_name for layer in component.layers()):
            return component_name
    return "other"


def write_bundle(
    bundle: StudyAreaBundle,
    directory: str | Path,
    *,
    prefer_geotiff: bool = True,
    validation_report: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> Path:
    """Write a bundle to ``directory`` and return the directory path.

    Refuses to write into a directory that already holds a manifest unless
    ``overwrite`` is set: silently replacing a bundle would destroy the
    provenance of whatever was there.
    """
    target = Path(directory)
    manifest_path = target / MANIFEST_NAME
    if manifest_path.exists() and not overwrite:
        raise BundleError(
            f"{manifest_path} already exists; pass overwrite=True (CLI: --force) "
            "to replace the bundle. refusing by default so an existing bundle's "
            "provenance is not destroyed silently."
        )
    target.mkdir(parents=True, exist_ok=True)

    layer_entries: list[dict[str, Any]] = []
    for layer in bundle.all_layers():
        component = _layer_component(bundle, layer.name)
        subdirectory = target / _COMPONENT_DIRS.get(component, "other")
        subdirectory.mkdir(parents=True, exist_ok=True)

        if isinstance(layer, RasterLayer):
            written = write_raster(
                layer, subdirectory / layer.name, prefer_geotiff=prefer_geotiff
            )
            files = [written]
            sidecar = written.with_name(written.stem + RASTER_SIDECAR_SUFFIX)
            if sidecar.exists():
                files.append(sidecar)
            entry: dict[str, Any] = {
                "name": layer.name,
                "component": component,
                "type": "raster",
                "format": "geotiff" if written.suffix in {".tif", ".tiff"} else "npz",
                "path": str(written.relative_to(target)),
                "extra_files": [str(f.relative_to(target)) for f in files[1:]],
                "kind": layer.kind.value,
                "value_unit": getattr(layer.value_unit, "value", str(layer.value_unit)),
                "shape": list(layer.shape),
                "resolution": list(layer.resolution),
                "dtype": str(layer.data.dtype),
                "valid_cells": layer.valid_count,
                "missing_cells": layer.missing_count,
            }
        else:
            written = write_geojson(layer, subdirectory / f"{layer.name}.geojson")
            entry = {
                "name": layer.name,
                "component": component,
                "type": "vector",
                "format": "geojson",
                "path": str(written.relative_to(target)),
                "extra_files": [],
                "feature_kind": layer.feature_kind,
                "features": len(layer),
                "geom_types": sorted(layer.geom_types),
            }
        entry["checksum_sha256"] = sha256_file(written)
        entry["checksum_algorithm"] = "sha256"
        layer_entries.append(entry)

        # The provenance sidecar records the checksum of the file as written, so
        # provenance and artifact can be checked against each other later.
        write_provenance(
            target, layer.provenance.with_updates(checksum=entry["checksum_sha256"])
        )

    extras: dict[str, str] = {}
    if bundle.roads is not None and bundle.roads.qa is not None:
        qa_path = target / "roads" / "roads_qa.json"
        qa_path.parent.mkdir(parents=True, exist_ok=True)
        qa_path.write_text(
            json.dumps(bundle.roads.qa.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        extras["roads_qa"] = str(qa_path.relative_to(target))
    if bundle.roads is not None and bundle.roads.graph is not None:
        graph_path = target / "roads" / "roads_graph_summary.json"
        graph_path.write_text(
            json.dumps(bundle.roads.graph.describe(), indent=2) + "\n", encoding="utf-8"
        )
        extras["roads_graph_summary"] = str(graph_path.relative_to(target))
    if bundle.terrain is not None and bundle.terrain.statistics:
        stats_path = target / "terrain" / "terrain_statistics.json"
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        stats_path.write_text(
            json.dumps(bundle.terrain.statistics, indent=2) + "\n", encoding="utf-8"
        )
        extras["terrain_statistics"] = str(stats_path.relative_to(target))
    if bundle.fuels is not None and bundle.fuels.scheme is not None:
        scheme_path = target / "fuels" / "fuel_scheme.json"
        scheme_path.parent.mkdir(parents=True, exist_ok=True)
        scheme_path.write_text(
            json.dumps(bundle.fuels.scheme.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        extras["fuel_scheme"] = str(scheme_path.relative_to(target))
    if bundle.population is not None and bundle.population.villages:
        villages_path = target / "population" / "villages_aggregate.json"
        villages_path.parent.mkdir(parents=True, exist_ok=True)
        villages_path.write_text(
            json.dumps(
                [village.to_dict() for village in bundle.population.villages],
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        extras["villages_aggregate"] = str(villages_path.relative_to(target))
    if bundle.facilities is not None and bundle.facilities.records:
        facilities_path = target / "facilities" / "facilities_records.json"
        facilities_path.parent.mkdir(parents=True, exist_ok=True)
        facilities_path.write_text(
            json.dumps(
                [record.to_dict() for record in bundle.facilities.records],
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        extras["facilities_records"] = str(facilities_path.relative_to(target))

    # The validation report's slot is always reserved in the manifest, even when
    # no report is supplied here. Validation is most useful *after* writing --
    # that is when checksums exist and can be compared -- so the CLI writes the
    # bundle, validates the directory, and then fills this path in with
    # :func:`write_validation_report`.
    (target / "validation").mkdir(parents=True, exist_ok=True)
    extras["validation_report"] = str(Path("validation") / "report.json")
    if validation_report is not None:
        write_validation_report(target, validation_report)

    # Checksum the extras and the provenance sidecars too, not only the layer
    # files. The road QA report is the artifact a downstream reader is most
    # likely to consume *without* re-deriving it, so an undetected edit there is
    # worse than an undetected edit to a raster. The validation report is
    # necessarily absent here -- it is written after validation, which runs
    # after this -- and is listed in `unchecksummed` so its absence is a stated
    # fact rather than an oversight.
    extras_checksums = {
        key: sha256_file(target / relative)
        for key, relative in sorted(extras.items())
        if key != "validation_report" and (target / relative).exists()
    }
    provenance_checksums = {
        path.name[: -len(".provenance.json")]: sha256_file(path)
        for path in sorted((target / "provenance").glob("*.provenance.json"))
    }

    # Aggregate the per-layer licence terms into the manifest. docs/INTERFACES.md
    # names manifest.json as a consumer's step 1, so an obligation that lives
    # only in a provenance sidecar can be missed by a redistributor who reads
    # the manifest and stops. The sidecars remain authoritative.
    licences: list[dict[str, Any]] = []
    for layer in bundle.all_layers():
        for source in layer.provenance.sources:
            if source.licence == "UNKNOWN":
                continue
            entry = {
                "layer": layer.name,
                "source": source.name,
                "licence": source.licence,
                "licence_url": source.licence_url,
                "publisher": source.publisher,
            }
            if entry not in licences:
                licences.append(entry)

    caveats = list(bundle.describe()["caveats"])
    obligations = sorted(
        {
            f"{entry['source']}: {entry['licence']}"
            for entry in licences
            if "ODbL" in entry["licence"] or "attribution" in entry["licence"].lower()
        }
    )
    if obligations:
        caveats.append(
            "this bundle carries data licence obligations that travel with it, "
            "including attribution and, for any ODbL source, share-alike on "
            "derived databases: " + " | ".join(obligations)
        )

    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "study_area_id": bundle.study_area_id,
        "crs": crs_to_string(bundle.crs),
        # WKT alongside the authority string, so a CRS with no authority code
        # survives the round trip -- the raster sidecar already does this and the
        # manifest did not.
        "crs_wkt": bundle.crs.to_wkt() if bundle.crs is not None else None,
        "bounds": bundle.bounds.to_dict(),
        "licences": licences,
        "layers": layer_entries,
        "extras": extras,
        "extras_checksums_sha256": extras_checksums,
        "provenance_checksums_sha256": provenance_checksums,
        "unchecksummed": {
            "validation_report": (
                "written after the manifest, because validation runs against the "
                "written bundle (docs/DECISIONS.md D-0014); it is therefore not "
                "covered by any checksum here"
            )
        },
        "metadata": bundle.metadata,
        "caveats": caveats,
    }
    manifest["manifest_checksum_sha256"] = sha256_json(manifest)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    # The canonical contract manifest: what a downstream repository codes
    # against, written alongside the internal manifest rather than instead of
    # it (docs/DECISIONS.md D-0027).
    #
    # Derived from the bundle **read back from disk**, not from the in-memory
    # one, for the same reason validation is (D-0014): checksums only exist
    # once the files do, so the in-memory bundle's provenance still says
    # UNKNOWN. Deriving from it would put an unchecksummed contract next to a
    # checksummed bundle -- which BND-023 catches, and which is how this was
    # found. The extra read also proves the write round-trips.
    write_bundle_manifest(target, read_bundle(target))
    return target


def write_bundle_manifest(
    directory: str | Path,
    bundle: StudyAreaBundle,
    *,
    created_at: str | None = None,
    validation: dict[str, Any] | None = None,
) -> Path:
    """Write ``bundle_manifest.json``, the canonical downstream contract.

    ``created_at`` defaults to now, which is correct for a *write*. Re-derivation
    for a drift comparison passes the value already on disk, so the comparison
    is not defeated by the clock.
    """
    from ..integration.manifest import BUNDLE_MANIFEST_NAME, build_bundle_manifest

    target = Path(directory)
    contract = build_bundle_manifest(
        bundle,
        created_at=created_at or utc_now_iso(),
        validation=validation,
        absent_reasons=bundle.metadata.get("absent_layer_reasons"),
    )
    path = target / BUNDLE_MANIFEST_NAME
    path.write_text(
        json.dumps(contract, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return path


def write_validation_report(
    directory: str | Path, report: dict[str, Any]
) -> Path:
    """Write ``validation/report.json`` into an existing bundle directory.

    Separate from :func:`write_bundle` so a bundle can be validated *after* it
    is written, when every artifact has a checksum to check against its
    provenance. Validating only the in-memory bundle would report every layer
    as unchecksummed and would never catch a write that went wrong.
    """
    target = Path(directory)
    report_path = target / "validation" / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report_path


def read_bundle(directory: str | Path) -> StudyAreaBundle:
    """Read a bundle written by :func:`write_bundle`.

    Verifies each layer file's checksum against the manifest and raises on any
    mismatch: a bundle whose data has drifted from its provenance is worse than
    no bundle at all.
    """
    target = Path(directory)
    manifest_path = target / MANIFEST_NAME
    if not manifest_path.exists():
        raise BundleError(f"no {MANIFEST_NAME} in {target}; not a study-area bundle")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = manifest.get("schema_version")
    if version != BUNDLE_SCHEMA_VERSION:
        raise BundleError(
            f"bundle schema_version {version!r} != supported "
            f"{BUNDLE_SCHEMA_VERSION!r}; refusing to guess the layout of an "
            "unrecognised version (docs/INTERFACES.md)."
        )

    provenance_records = read_all_provenance(target)
    crs = parse_crs(manifest.get("crs_wkt") or manifest["crs"])
    bounds = Bounds.from_dict(manifest["bounds"])

    rasters: dict[str, RasterLayer] = {}
    vectors: dict[str, VectorLayer] = {}
    for entry in manifest["layers"]:
        path = target / entry["path"]
        if not path.exists():
            raise BundleError(f"manifest lists {entry['path']} but the file is missing")
        actual = sha256_file(path)
        if actual != entry.get("checksum_sha256"):
            raise BundleError(
                f"checksum mismatch for {entry['path']}: manifest says "
                f"{entry.get('checksum_sha256')}, file is {actual}. the data has "
                "changed since the bundle was written."
            )
        record = provenance_records.get(entry["name"])
        if record is None:
            raise BundleError(
                f"layer {entry['name']!r} has no provenance sidecar; a layer "
                "without provenance is not usable (AGENTS.md §7)"
            )
        if entry["type"] == "raster":
            rasters[entry["name"]] = read_raster(
                path,
                provenance=record,
                name=entry["name"],
                kind=entry.get("kind", "continuous"),
                value_unit=entry.get("value_unit"),
            )
        else:
            vectors[entry["name"]] = read_geojson(
                path,
                name=entry["name"],
                provenance=record,
                feature_kind=entry.get("feature_kind", "UNKNOWN"),
            )

    terrain = None
    dem = rasters.get("dem")
    if dem is not None:
        slope = next(
            (layer for name, layer in rasters.items() if name.startswith("slope")), None
        )
        aspect = next(
            (layer for name, layer in rasters.items() if name.startswith("aspect")), None
        )
        statistics: dict[str, Any] = {}
        stats_rel = manifest.get("extras", {}).get("terrain_statistics")
        if stats_rel and (target / stats_rel).exists():
            statistics = json.loads((target / stats_rel).read_text(encoding="utf-8"))
        terrain = TerrainComponent(dem=dem, slope=slope, aspect=aspect, statistics=statistics)

    extras = manifest.get("extras", {})

    def _extra(key: str) -> Any:
        """Load one JSON extra written at build time, or ``None``.

        These are *stored* artifacts, so reading them back is faithful. What
        this function deliberately does not do is re-derive them: re-running the
        graph build or the population parse on read could produce different
        numbers from different parameters, and the bundle would then disagree
        with its own manifest.
        """
        relative = extras.get(key)
        if not relative:
            return None
        path = target / relative
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    roads = None
    if "roads" in vectors:
        # QA comes back as the plain dict it was written as. Validation and the
        # summary both accept that form; nothing re-computes it.
        roads = RoadsComponent(layer=vectors["roads"], graph=None, qa=_extra("roads_qa"))

    fuels = None
    if "fuels" in rasters:
        scheme_payload = _extra("fuel_scheme")
        scheme = None
        if scheme_payload is not None:
            from ..fuels.classes import FuelClassScheme

            scheme = FuelClassScheme.from_dict(scheme_payload)
        fuels = FuelsComponent(layer=rasters["fuels"], scheme=scheme)

    population = None
    if "villages" in vectors:
        population = PopulationComponent(
            layer=vectors["villages"], villages=tuple(_extra("villages_aggregate") or ())
        )

    facilities = None
    if "facilities" in vectors:
        facilities = FacilitiesComponent(
            layer=vectors["facilities"],
            records=tuple(_extra("facilities_records") or ()),
        )

    return StudyAreaBundle(
        study_area_id=manifest["study_area_id"],
        crs=crs,
        bounds=bounds,
        terrain=terrain,
        roads=roads,
        fuels=fuels,
        population=population,
        facilities=facilities,
        provenance=provenance_records,
        metadata={
            **manifest.get("metadata", {}),
            "read_from": str(target),
            "read_note": (
                "read back from disk. the road QA report, fuel scheme, village "
                "records and facility records are loaded from the JSON written "
                "at build time and appear as plain dicts (except the fuel "
                "scheme). the networkx road graph is NOT rebuilt: re-deriving it "
                "on read could use different parameters and then disagree with "
                "the manifest"
            ),
        },
    )
