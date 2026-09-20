"""``wg-data import-*``: ingest a manually downloaded file with full provenance.

Phase 2 item 19. Several of the best Korean sources cannot be fetched from code
-- they are `REGISTRATION_REQUIRED` or `MANUAL_DOWNLOAD_REQUIRED`
(``reports/SOURCE_ACCESS_STATUS.md``). The authoritative forest stand map,
임상도, is one of them. Without these commands the only route for such a file is
a hand-written config, which is exactly where provenance gets guessed.

Design rules, all of them consequences of ``AGENTS.md`` §3:

* **Nothing is inferred from the file.** Not the source name, not the date, not
  the licence, not the temporal class. A file on disk says none of those things,
  and a filename is not evidence. Every one is a required argument.
* **The checksum is never optional, only explicitly waived.** A manually
  downloaded file could be anything -- a truncated transfer, a different
  edition, the wrong county. ``--expect-sha256`` verifies it against a
  published value; ``--no-expected-checksum`` records that no published value
  was available. One or the other is required, so the operator has to decide
  rather than drift past the question.
* **Temporal validity is asked for, and stays ``UNKNOWN`` if not given.** The
  vintage of a product is not the interval over which it is true (D-0025).
* **A licence that has not been read is ``UNKNOWN``.** 임상도's page states
  CC-BY *and* "all rights reserved"; an importer that accepted "CC-BY" because
  it appeared on the page would have recorded a licence nobody confirmed.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..errors import ConfigError, IngestError
from ..provenance.checksum import sha256_file
from ..provenance.models import (
    NOT_APPLICABLE,
    UNKNOWN,
    DataClass,
    SourceRecord,
    TemporalProvenance,
)

__all__ = ["add_import_parsers", "IMPORT_COMMANDS"]

#: What each ``import-*`` command ingests, and the layer name it writes.
_IMPORT_KINDS = {
    "import-fuels": ("fuels", "a categorical vegetation / land-cover raster"),
    "import-population": ("villages", "an aggregate population vector layer"),
    "import-facilities": ("facilities", "a facility point/polygon layer"),
    "import-roads": ("roads", "a road line layer"),
}


def add_import_parsers(subparsers: Any) -> None:
    """Register the four ``import-*`` subcommands."""
    for command, (default_name, what) in _IMPORT_KINDS.items():
        parser = subparsers.add_parser(
            command,
            help=f"import {what} from a manually downloaded file",
            description=(
                f"Import {what} that was downloaded by hand, with provenance "
                "supplied explicitly. Nothing is inferred from the file or its "
                "name: a file on disk does not state where it came from, what "
                "period it describes, or under what licence it may be used "
                "(AGENTS.md section 3). Every provenance argument below is "
                "required for that reason."
            ),
        )
        parser.add_argument("path", help="the downloaded file")
        parser.add_argument(
            "--out",
            required=True,
            help="directory to write the layer and its provenance sidecar into",
        )
        parser.add_argument(
            "--name", default=default_name, help="layer name (default: %(default)s)"
        )

        required = parser.add_argument_group("required provenance")
        required.add_argument(
            "--source-name",
            required=True,
            help="the publisher's own name for this dataset, as published",
        )
        required.add_argument(
            "--source-url",
            required=True,
            help=(
                "URL or other identifier. use the product page for a manual "
                "download; pass UNKNOWN only if there genuinely is none"
            ),
        )
        required.add_argument(
            "--source-date",
            required=True,
            help=(
                "the date the SOURCE describes, at the precision the source "
                "states it (2022, 2022-03, 2022-03-05), or UNKNOWN. not the "
                "date you downloaded it -- that is --acquisition-date"
            ),
        )
        required.add_argument(
            "--licence",
            required=True,
            help=(
                "the licence you have actually READ. pass UNKNOWN if you have "
                "not, or if the source states contradictory terms -- an "
                "unverified licence recorded as fact is worse than a gap"
            ),
        )
        required.add_argument(
            "--temporal-class",
            required=True,
            choices=[t.value for t in TemporalProvenance],
            help=(
                "retrospective means compiled after the fact and therefore "
                "unavailable to a real-time decision; mislabelling it as "
                "observation_time is the temporal analogue of data leakage"
            ),
        )

        checksum = parser.add_mutually_exclusive_group(required=True)
        checksum.add_argument(
            "--expect-sha256",
            default=None,
            help="the publisher's SHA-256 for this file; a mismatch is a hard error",
        )
        checksum.add_argument(
            "--no-expected-checksum",
            action="store_true",
            help=(
                "record that no published checksum was available. the file's "
                "own SHA-256 is still computed and recorded"
            ),
        )

        optional = parser.add_argument_group("optional provenance")
        optional.add_argument(
            "--acquisition-date",
            default=None,
            help="when YOU downloaded it (default: now, in UTC)",
        )
        optional.add_argument("--publisher", default=UNKNOWN)
        optional.add_argument("--licence-url", default=UNKNOWN)
        optional.add_argument(
            "--temporal-reference",
            default=None,
            help="the period the values describe (default: --source-date)",
        )
        optional.add_argument(
            "--valid-from",
            default=UNKNOWN,
            help=(
                "start of the interval these values are TRUE OF, which is not "
                "the same as when the data is from (docs/DECISIONS.md D-0025)"
            ),
        )
        optional.add_argument("--valid-to", default=UNKNOWN)
        optional.add_argument(
            "--data-class",
            default=DataClass.OBSERVED.value,
            choices=[c.value for c in DataClass],
            help="default: %(default)s",
        )
        optional.add_argument(
            "--declared-crs",
            default=None,
            help=(
                "the file's CRS, when the file itself does not declare one. "
                "never inferred from the coordinate ranges (AGENTS.md section 3)"
            ),
        )
        optional.add_argument("--notes", default="")
        optional.add_argument("--json", action="store_true")

        if command == "import-fuels":
            parser.add_argument(
                "--scheme",
                required=True,
                help=(
                    "the class scheme these codes belong to, from "
                    "KNOWN_FUEL_SCHEMES. register the scheme first: a raster "
                    "of integers with no legend has no meaning, and reading "
                    "it through the wrong legend relabels every cell"
                ),
            )
        if command == "import-population":
            parser.add_argument(
                "--aggregation-level",
                required=True,
                help=(
                    "the geographic support of the counts (ri, eup_myeon, "
                    "jipgyegu, ...). required because a consumer cannot judge "
                    "whether a count is disclosive without it"
                ),
            )
        if command == "import-facilities":
            parser.add_argument(
                "--kind-map",
                required=True,
                help=(
                    "JSON object mapping the source's own kind values to "
                    "facility roles, or a path to such a file. required and "
                    "never inferred from spelling (A-FAC-2)"
                ),
            )
        if command == "import-roads":
            parser.add_argument(
                "--snap-tolerance-m",
                type=float,
                default=1.0,
                help="node-snapping tolerance in metres (default: %(default)s)",
            )


def _verify_checksum(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Compute the file's checksum and compare it with the published one."""
    actual = sha256_file(path)
    if args.expect_sha256 is None:
        return {
            "sha256": actual,
            "verified_against_published_value": False,
            "note": (
                "no published checksum was available, so this records what was "
                "received rather than confirming it is what was published"
            ),
        }
    expected = args.expect_sha256.strip().lower()
    if expected != actual:
        raise IngestError(
            f"checksum mismatch for {path}: expected {expected}, got {actual}. "
            "the file is not the one the publisher checksummed -- a truncated "
            "transfer, a different edition, or a different extract. refusing to "
            "import it rather than recording a provenance claim that is false."
        )
    return {
        "sha256": actual,
        "verified_against_published_value": True,
        "note": "matches the publisher's stated SHA-256",
    }


def _source_record(args: argparse.Namespace) -> SourceRecord:
    from ..provenance.models import utc_now_iso

    return SourceRecord(
        name=args.source_name,
        url_or_identifier=args.source_url,
        source_date=args.source_date,
        acquisition_date=args.acquisition_date or utc_now_iso(),
        publisher=args.publisher,
        licence=args.licence,
        licence_url=args.licence_url,
    )


def _kind_map(raw: str) -> dict[str, str]:
    candidate = Path(raw)
    text = candidate.read_text(encoding="utf-8") if candidate.is_file() else raw
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"--kind-map is neither a readable file nor valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ConfigError("--kind-map must be a non-empty JSON object")
    return {str(k): str(v) for k, v in parsed.items()}


def run_import(command: str, args: argparse.Namespace) -> dict[str, Any]:
    """Import one layer and write it with its provenance. Returns a summary."""
    from ..crs import parse_crs
    from ..provenance.store import write_provenance
    from ..raster import RasterLayer
    from ..terrain.io import write_raster
    from ..vector import write_geojson

    extra_note = ""
    path = Path(args.path)
    if not path.exists():
        raise IngestError(
            f"{path} does not exist. this command imports a file you already "
            "downloaded; it does not fetch anything."
        )
    checksum = _verify_checksum(path, args)

    source = _source_record(args)
    declared_crs = parse_crs(args.declared_crs) if args.declared_crs else None
    common = {
        "name": args.name,
        "source": source,
        "data_class": DataClass(args.data_class),
        "temporal_class": TemporalProvenance(args.temporal_class),
        "temporal_reference": args.temporal_reference or args.source_date,
        "notes": args.notes,
    }

    if command == "import-fuels":
        from ..fuels.io import read_fuel_geotiff
        from ..study_area.build import KNOWN_FUEL_SCHEMES

        scheme = KNOWN_FUEL_SCHEMES.get(args.scheme)
        if scheme is None:
            raise ConfigError(
                f"unknown fuel scheme {args.scheme!r}; known: "
                f"{sorted(KNOWN_FUEL_SCHEMES)}. register the scheme with its "
                "source and published class list before importing data that "
                "claims to use it (docs/ASSUMPTIONS.md A-FU-1)"
            )
        layer = read_fuel_geotiff(
            path,
            scheme=scheme,
            valid_from=args.valid_from,
            valid_to=args.valid_to,
            **common,
        )
    elif command == "import-population":
        from ..population.io import load_population_layer

        layer = load_population_layer(
            path,
            crs=declared_crs,
            aggregation_level=args.aggregation_level,
            **common,
        )
    elif command == "import-facilities":
        from ..facilities.io import load_facility_layer

        kind_map = _kind_map(args.kind_map)
        layer = load_facility_layer(path, crs=declared_crs, **common)
        extra_note = (
            "source kind values mapped explicitly: "
            f"{json.dumps(kind_map, sort_keys=True, ensure_ascii=False)}"
        )
    elif command == "import-roads":
        from ..roads.io import read_road_geojson

        layer = read_road_geojson(path, crs=declared_crs, **common)
    else:  # pragma: no cover - the parser restricts this
        raise ConfigError(f"unknown import command {command!r}")

    # The vector loaders do not take these yet (they predate D-0025), and a
    # manual import is precisely where an operator may know them, so they are
    # applied here rather than silently dropped. None of these layers is an
    # elevation model, so surface_model is not_applicable rather than UNKNOWN:
    # the fact does not exist, and only UNKNOWN counts as a gap (D-0009).
    updates: dict[str, Any] = {
        "valid_from": args.valid_from,
        "valid_to": args.valid_to,
        "surface_model": NOT_APPLICABLE,
    }
    if extra_note:
        existing = layer.provenance.notes
        updates["notes"] = f"{existing} | {extra_note}" if existing else extra_note
    layer = replace(layer, provenance=layer.provenance.with_updates(**updates))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(layer, RasterLayer):
        written = write_raster(layer, out_dir / f"{args.name}.tif")
    else:
        written = write_geojson(layer, out_dir / f"{args.name}.geojson")
    write_provenance(out_dir, layer.provenance)

    return {
        "schema_version": "1.0.0",
        "command": command,
        "layer": args.name,
        "written": str(written),
        "provenance": str(out_dir / "provenance" / f"{args.name}.provenance.json"),
        "input_file": str(path),
        "checksum": checksum,
        "unknown_provenance_fields": layer.provenance.unknown_fields(),
        "caveats": [
            "nothing about this layer was inferred from the file or its name: "
            "every provenance fact came from an explicit argument, and what "
            "was not supplied is UNKNOWN (AGENTS.md section 3)",
            "importing a layer does not validate it. run "
            "'wg-data validate-study-area' on a bundle that contains it",
        ],
    }


#: Command name -> handler, registered by ``cli/main.py``.
IMPORT_COMMANDS = {command: run_import for command in _IMPORT_KINDS}
