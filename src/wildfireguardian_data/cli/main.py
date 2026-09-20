"""``wg-data`` command-line interface.

The CLI is this repository's **one stable interface** (``docs/INTERFACES.md``).
Exit codes are part of that contract:

====  =========================================================
code  meaning
====  =========================================================
0     success; for ``validate``, no ERROR findings
1     validation found ERRORs, or ``--strict`` and WARNINGs exist
2     usage error
3     input data error (CRS mismatch, unit mismatch, unreadable source)
4     optional dependency missing for the requested operation
5     network required but not permitted or not available
====  =========================================================

A non-zero exit is always accompanied by a message on stderr naming the
specific problem. Nothing here catches an exception and continues with a
default.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..errors import (
    ConfigError,
    CRSError,
    IngestError,
    MissingDataError,
    NetworkAccessError,
    OptionalDependencyError,
    PrivacyGuardError,
    ProvenanceError,
    RasterError,
    UnitError,
    WGDataError,
)
from .imports import IMPORT_COMMANDS

__all__ = ["main", "build_parser"]

EXIT_OK = 0
EXIT_VALIDATION_FAILED = 1
EXIT_USAGE = 2
EXIT_DATA_ERROR = 3
EXIT_MISSING_DEPENDENCY = 4
EXIT_NETWORK = 5


def _version() -> str:
    from .. import __version__

    return __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wg-data",
        description=(
            "Reproducible, provenance-preserving geospatial study-area packages "
            "for the WildfireGuardian project. This tool prepares landscape and "
            "infrastructure inputs only: it does not predict fire, route "
            "evacuations, or assert that anything is safe."
        ),
        epilog="see docs/INTERFACES.md for exit codes and the --json contract",
    )
    parser.add_argument("--version", action="version", version=f"wg-data {_version()}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser(
        "build-study-area",
        help="build a study-area bundle from a YAML config",
        description=(
            "Build a study-area bundle. Reprojection, clipping and derivation "
            "are all recorded in each layer's provenance."
        ),
    )
    build.add_argument("config", help="path to the study-area YAML config")
    build.add_argument(
        "--out",
        default=None,
        help="output bundle directory (default: data/study_areas/<study_area_id>)",
    )
    build.add_argument(
        "--allow-network",
        action="store_true",
        help="permit network sources; without it a config needing the network fails",
    )
    build.add_argument(
        "--cache-dir",
        default="data/raw",
        help="where fetched raw source data is cached (default: data/raw, git-ignored)",
    )
    build.add_argument(
        "--force", action="store_true", help="overwrite an existing bundle directory"
    )
    build.add_argument(
        "--npz",
        action="store_true",
        help="write rasters as .npz + sidecar instead of GeoTIFF (no GDAL needed)",
    )
    build.add_argument(
        "--strict",
        action="store_true",
        help="treat validation WARNINGs as failure (bundle is still written)",
    )
    build.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    validate = subparsers.add_parser(
        "validate-study-area",
        help="validate a bundle on disk, including checksums",
    )
    validate.add_argument("bundle", help="path to a bundle directory")
    validate.add_argument(
        "--strict", action="store_true", help="treat WARNINGs as failure"
    )
    validate.add_argument("--json", action="store_true", help="emit JSON")

    summarize = subparsers.add_parser(
        "summarize-study-area", help="summarise a bundle on disk"
    )
    summarize.add_argument("bundle", help="path to a bundle directory")
    summarize.add_argument("--json", action="store_true", help="emit JSON")

    provenance_parser = subparsers.add_parser(
        "provenance",
        help="print every layer's provenance, or one layer's in full",
        description=(
            "Report where each layer came from, what its values mean, and what "
            "is not known about it. UNKNOWN is a reported finding, not a "
            "formatting artifact (docs/DECISIONS.md D-0009)."
        ),
    )
    provenance_parser.add_argument("bundle", help="path to a bundle directory")
    provenance_parser.add_argument(
        "--layer", default=None, help="report only this layer, in full"
    )
    provenance_parser.add_argument(
        "--unknown-only",
        action="store_true",
        help="report only the fields that are UNKNOWN",
    )
    provenance_parser.add_argument("--json", action="store_true", help="emit JSON")

    compat = subparsers.add_parser(
        "compatibility",
        help="report which consumers' declared inputs this bundle satisfies",
        description=(
            "Report readiness for FORECAST_VALUE, OSSE and ASSISTED_DISPATCH. "
            "READY means every input that consumer named is present with "
            "interpretable provenance. It does NOT mean the data is accurate, "
            "the sources authoritative, or any result computed from it correct: "
            "this repository makes no claim of scientific validity for any "
            "bundle."
        ),
    )
    compat.add_argument("bundle", help="path to a bundle directory")
    compat.add_argument(
        "--profile",
        default=None,
        help="report only this consumer profile",
    )
    compat.add_argument("--json", action="store_true", help="emit JSON")

    from .imports import add_import_parsers

    add_import_parsers(subparsers)

    fixtures_parser = subparsers.add_parser(
        "make-fixtures",
        help="write the synthetic fixtures to a directory",
        description=(
            "Write the synthetic fixtures as GeoJSON/GeoTIFF for inspection. "
            "Every output is labelled SYNTHETIC in its provenance."
        ),
    )
    fixtures_parser.add_argument("out_dir", help="output directory")
    fixtures_parser.add_argument(
        "--only", default=None, help="write only this fixture (see 'list-fixtures')"
    )
    fixtures_parser.add_argument("--json", action="store_true", help="emit JSON")

    subparsers.add_parser("list-fixtures", help="list available synthetic fixtures")
    subparsers.add_parser("version", help="print the package version")
    return parser


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def _cmd_build(args: argparse.Namespace) -> int:
    from ..study_area.build import build_study_area
    from ..study_area.config import StudyAreaConfig
    from ..study_area.serialize import (
        read_bundle,
        write_bundle,
        write_bundle_manifest,
        write_validation_report,
    )
    from ..study_area.summary import summarize_bundle
    from ..validation.bundle_checks import validate_bundle_directory

    config = StudyAreaConfig.from_yaml(args.config)
    bundle = build_study_area(
        config, allow_network=args.allow_network, cache_dir=args.cache_dir
    )
    out_dir = Path(args.out) if args.out else Path("data/study_areas") / config.study_area_id
    written = write_bundle(
        bundle, out_dir, prefer_geotiff=not args.npz, overwrite=args.force
    )
    # Validate what is on disk, not what is in memory: only the written bundle
    # has checksums, and only reading it back proves the write round-trips.
    report = validate_bundle_directory(written)
    write_validation_report(written, report.to_dict(strict=args.strict))

    status = report.status(strict=args.strict)
    # Re-write the contract manifest now that validation has actually run, so
    # its ``validation`` block carries the result rather than "NOT_RUN". The
    # ``created_at`` written moments ago is preserved, because it records when
    # the bundle was built and not when this line executed.
    write_bundle_manifest(
        written,
        # The bundle as written, not as built: checksums exist only once the
        # files do, so the in-memory bundle's provenance still says UNKNOWN.
        # BND-023 catches the difference (D-0014, D-0027).
        read_bundle(written),
        created_at=_existing_created_at(written),
        validation={
            "status": status,
            "strict": bool(args.strict),
            "counts": report.counts,
            "report_path": "validation/report.json",
        },
    )
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "status": status,
                    "study_area_id": config.study_area_id,
                    "bundle_dir": str(written),
                    "findings": [f.to_dict() for f in report.findings],
                    "summary": summarize_bundle(bundle),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(f"built {config.study_area_id} -> {written}")
        print(f"layers: {', '.join(bundle.layer_names)}")
        print(report.to_text())
        print(f"status: {status}")
    return EXIT_VALIDATION_FAILED if status == "fail" else EXIT_OK


def _existing_created_at(directory: Path) -> str | None:
    """The ``created_at`` already on disk, or ``None`` if there is none.

    Preserved across a re-write so the field keeps meaning "when this bundle was
    built" rather than "when it was last touched".
    """
    from ..integration.manifest import BUNDLE_MANIFEST_NAME

    path = Path(directory) / BUNDLE_MANIFEST_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("created_at")
    except (OSError, json.JSONDecodeError, AttributeError):
        # A corrupt contract manifest is not a reason to fail a build that
        # otherwise succeeded; it is about to be overwritten anyway.
        return None


def _cmd_validate(args: argparse.Namespace) -> int:
    from ..validation.bundle_checks import validate_bundle_directory

    report = validate_bundle_directory(args.bundle)
    status = report.status(strict=args.strict)
    if args.json:
        print(json.dumps(report.to_dict(strict=args.strict), indent=2, ensure_ascii=False))
    else:
        print(report.to_text())
        print(f"status: {status}")
    return EXIT_VALIDATION_FAILED if status == "fail" else EXIT_OK


def _cmd_summarize(args: argparse.Namespace) -> int:
    from ..study_area.serialize import read_bundle
    from ..study_area.summary import format_summary_text, summarize_bundle

    bundle = read_bundle(args.bundle)
    summary = summarize_bundle(bundle)
    # The road QA and village/facility records are written as JSON at build
    # time and are not re-derived on read, so surface them from disk rather than
    # recomputing them with possibly different parameters.
    for key, relative in (
        ("roads_qa", "roads/roads_qa.json"),
        ("terrain_statistics", "terrain/terrain_statistics.json"),
    ):
        path = Path(args.bundle) / relative
        if key not in summary and path.exists():
            summary[key] = json.loads(path.read_text(encoding="utf-8"))
    if "roads_qa" in summary and "roads_qa_headline" not in summary:
        qa = summary["roads_qa"]
        summary["roads_qa_headline"] = {
            "nodes": qa["graph"]["nodes"],
            "edges": qa["graph"]["edges"],
            "total_length_m": qa["graph"]["total_length_m"],
            "components": qa["connectivity"]["component_count"],
            "dead_ends": qa["connectivity"]["dead_end_count"],
            "isolated_segments": len(qa["connectivity"]["isolated_segments"]),
            "exit_nodes": qa["egress"]["exits"]["count"],
            "single_egress_candidates": len(qa["egress"]["single_egress_candidates"]),
            "no_egress_components": len(qa["egress"]["no_egress_components"]),
            "articulation_points": len(qa["fragility"]["articulation_points"]),
            "bridge_edges": len(qa["fragility"]["bridge_edges"]),
            "critical_links": len(qa["fragility"]["critical_links"]),
            "crossings_without_node": qa["crossings_without_node"]["count"],
        }

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(format_summary_text(summary))
    return EXIT_OK


def _cmd_make_fixtures(args: argparse.Namespace) -> int:
    from ..fixtures.synthetic import FIXTURES, fixture_names, make_fixture
    from ..raster import RasterLayer
    from ..terrain.io import write_raster
    from ..vector import VectorLayer, write_geojson

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = [args.only] if args.only else fixture_names()
    unknown = [name for name in names if name not in FIXTURES]
    if unknown:
        print(
            f"unknown fixture(s) {unknown}; known: {fixture_names()}", file=sys.stderr
        )
        return EXIT_USAGE

    written: list[dict[str, Any]] = []

    def _write(layer: Any, label: str) -> None:
        if isinstance(layer, RasterLayer):
            path = write_raster(layer, out_dir / layer.name)
        elif isinstance(layer, VectorLayer):
            path = write_geojson(layer, out_dir / f"{layer.name}.geojson")
        else:
            return
        written.append(
            {
                "fixture": label,
                "layer": layer.name,
                "path": str(path),
                "data_class": layer.provenance.data_class.value,
            }
        )

    for name in names:
        produced = make_fixture(name)
        if isinstance(produced, (list, tuple)):
            for item in produced:
                _write(item, name)
        elif hasattr(produced, "__dataclass_fields__") and not isinstance(
            produced, (RasterLayer, VectorLayer)
        ):
            for field_name in produced.__dataclass_fields__:
                _write(getattr(produced, field_name), name)
        else:
            _write(produced, name)

    if args.json:
        print(json.dumps({"out_dir": str(out_dir), "written": written}, indent=2))
    else:
        print(f"wrote {len(written)} layer(s) to {out_dir}")
        for entry in written:
            print(f"  {entry['fixture']:<24} {entry['path']}")
    return EXIT_OK


def _cmd_list_fixtures(_: argparse.Namespace) -> int:
    from ..fixtures.synthetic import fixture_names

    for name in fixture_names():
        print(name)
    return EXIT_OK


def _cmd_version(_: argparse.Namespace) -> int:
    print(_version())
    return EXIT_OK


def _cmd_provenance(args: argparse.Namespace) -> int:
    """Report provenance. Exits non-zero only on a *blocking* gap.

    An UNKNOWN field is a finding, not a failure -- an honest gap is the correct
    output when nobody established the fact (D-0009). The exception is the three
    fields without which a layer's values cannot be interpreted at all, which
    ``validate-study-area`` reports as PRV-001; this command agrees with it
    rather than inventing a second opinion.
    """
    from ..study_area.serialize import read_bundle

    bundle = read_bundle(args.bundle)
    blocking_fields = {"output_crs", "value_unit", "nodata_representation"}

    records = dict(sorted(bundle.provenance.items()))
    if args.layer is not None:
        if args.layer not in records:
            print(
                f"bundle has no layer {args.layer!r}; it has "
                f"{sorted(records)}",
                file=sys.stderr,
            )
            return EXIT_USAGE
        records = {args.layer: records[args.layer]}

    blocking_found: dict[str, list[str]] = {}
    for name, record in records.items():
        found = sorted(set(record.unknown_fields()) & blocking_fields)
        if found:
            blocking_found[name] = found

    if args.json:
        payload = {
            "schema_version": "1.0.0",
            "bundle_id": bundle.study_area_id,
            "layers": {
                name: (
                    {"unknown_fields": record.unknown_fields()}
                    if args.unknown_only
                    else record.to_dict()
                )
                for name, record in records.items()
            },
            "blocking_unknown_fields": blocking_found,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for name, record in records.items():
            print(f"== {name}")
            if args.unknown_only:
                unknown = record.unknown_fields()
                if unknown:
                    print(f"  UNKNOWN ({len(unknown)}): {', '.join(unknown)}")
                else:
                    print("  no UNKNOWN fields")
                print()
                continue
            print(f"  data_class:        {record.data_class.value}")
            print(f"  temporal_class:    {record.temporal_class.value}")
            print(f"  temporal_ref:      {record.temporal_reference}")
            print(f"  valid:             {record.valid_from} .. {record.valid_to}")
            print(f"  surface_model:     {record.surface_model}")
            print(f"  value_unit:        {record.value_unit}")
            print(f"  missing data as:   {record.nodata_representation}")
            print(f"  output_crs:        {record.output_crs}")
            for source in record.sources:
                print(f"  source:            {source.name}")
                print(f"    identifier:      {source.url_or_identifier}")
                print(f"    source_date:     {source.source_date}")
                print(f"    licence:         {source.licence}")
            if record.parents:
                print(f"  derived from:      {', '.join(record.parents)}")
            unknown = record.unknown_fields()
            if unknown:
                print(f"  UNKNOWN ({len(unknown)}):     {', '.join(unknown)}")
            if args.layer is not None and not args.unknown_only:
                for transformation in record.transformations:
                    print(f"  transformation:    {transformation.operation}")
                    for key in sorted(transformation.parameters):
                        print(f"    {key}: {transformation.parameters[key]}")
                if record.notes:
                    print(f"  notes:             {record.notes}")
            print()
        if blocking_found:
            print(
                "BLOCKING: these layers have UNKNOWN in a field without which "
                "their values cannot be interpreted (validation PRV-001): "
                + "; ".join(
                    f"{name}: {', '.join(fields)}"
                    for name, fields in blocking_found.items()
                )
            )
    return EXIT_VALIDATION_FAILED if blocking_found else EXIT_OK


def _cmd_compatibility(args: argparse.Namespace) -> int:
    """Report readiness per consumer profile. Never asserts validity.

    Exit code is 0 whatever the readiness: INCOMPLETE is a true and useful
    answer about a bundle, not an error in producing it. A CI job that wants to
    gate on readiness reads the JSON.
    """
    from ..integration import build_compatibility_report
    from ..integration.manifest import build_bundle_manifest
    from ..study_area.serialize import read_bundle

    bundle = read_bundle(args.bundle)
    manifest = build_bundle_manifest(
        bundle, created_at=_existing_created_at(Path(args.bundle)) or "UNKNOWN"
    )
    report = build_compatibility_report(bundle, manifest)

    if args.profile is not None:
        if args.profile not in report["profiles"]:
            print(
                f"unknown profile {args.profile!r}; known: "
                f"{sorted(report['profiles'])}",
                file=sys.stderr,
            )
            return EXIT_USAGE
        report["profiles"] = {args.profile: report["profiles"][args.profile]}

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return EXIT_OK

    print(f"bundle: {report['bundle_id']}")
    governance = report["governance"]
    print(f"data classes: {', '.join(governance['data_classes_present'])}")
    if not governance["all_layers_planner_legal"]:
        print(
            "  NOTE: not every layer is planner-legal under research "
            f"governance: {', '.join(governance['not_planner_legal'])}"
        )
    print()
    for name, profile in report["profiles"].items():
        print(f"== {name}: {profile['readiness']}")
        for slot in profile["inputs"]:
            mark = "+" if slot["status"] == "PRESENT" else "-"
            detail = ""
            if slot["status"] != "PRESENT":
                detail = f"  ({slot['reason']})"
            elif slot.get("material_limitations"):
                detail = f"  [{', '.join(slot['material_limitations'])}]"
            print(f"  {mark} {slot['slot']}{detail}")
        print("  not supplied by this repository:")
        for item in profile["not_supplied_here"]:
            print(f"    - {item}")
        print()
    print(
        "READY means the named inputs are present with interpretable "
        "provenance. it is NOT a claim that the data is accurate or that any "
        "result computed from it would be correct."
    )
    return EXIT_OK


def _cmd_import(args: argparse.Namespace) -> int:
    """Run one ``import-*`` command. Its own module; see ``cli/imports.py``."""
    from .imports import run_import

    summary = run_import(args.command, args)
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"imported {summary['layer']} -> {summary['written']}")
        print(f"provenance: {summary['provenance']}")
        checksum = summary["checksum"]
        print(f"sha256: {checksum['sha256']}")
        print(f"  {checksum['note']}")
        unknown = summary["unknown_provenance_fields"]
        if unknown:
            print(f"UNKNOWN ({len(unknown)}): {', '.join(unknown)}")
            print(
                "  these are honest gaps, not failures. supply them if you know "
                "them; do not guess them (AGENTS.md section 3)"
            )
    return EXIT_OK


_COMMANDS = {
    "build-study-area": _cmd_build,
    "provenance": _cmd_provenance,
    "compatibility": _cmd_compatibility,
    "validate-study-area": _cmd_validate,
    "summarize-study-area": _cmd_summarize,
    "make-fixtures": _cmd_make_fixtures,
    "list-fixtures": _cmd_list_fixtures,
    "version": _cmd_version,
}
_COMMANDS.update({command: _cmd_import for command in IMPORT_COMMANDS})


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns an exit code; never raises for an expected failure."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _COMMANDS[args.command]
    try:
        return handler(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except NetworkAccessError as exc:
        print(f"network required: {exc}", file=sys.stderr)
        return EXIT_NETWORK
    except OptionalDependencyError as exc:
        print(f"missing dependency: {exc}", file=sys.stderr)
        return EXIT_MISSING_DEPENDENCY
    except (
        CRSError,
        UnitError,
        RasterError,
        MissingDataError,
        IngestError,
        ProvenanceError,
        PrivacyGuardError,
    ) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    except WGDataError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    except FileNotFoundError as exc:
        print(f"file not found: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
