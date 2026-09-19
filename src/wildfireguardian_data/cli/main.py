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
    from ..study_area.serialize import write_bundle, write_validation_report
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


_COMMANDS = {
    "build-study-area": _cmd_build,
    "validate-study-area": _cmd_validate,
    "summarize-study-area": _cmd_summarize,
    "make-fixtures": _cmd_make_fixtures,
    "list-fixtures": _cmd_list_fixtures,
    "version": _cmd_version,
}


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
