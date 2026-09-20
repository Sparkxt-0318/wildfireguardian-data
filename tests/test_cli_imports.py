"""``wg-data import-*``: the manual-ingest path (Phase 2 item 19).

These commands exist because the best Korean sources cannot be fetched from
code. That makes them the place where a plausible-looking wrong provenance
claim is most likely to enter the repository, so the tests here are mostly
about what the commands *refuse*.
"""

from __future__ import annotations

import json

import pytest

from wildfireguardian_data.cli.main import EXIT_DATA_ERROR, EXIT_OK, EXIT_USAGE, main
from wildfireguardian_data.provenance.checksum import sha256_file

REQUIRED = [
    "--source-name",
    "a source somebody actually named",
    "--source-url",
    "https://example.invalid/product/page",
    "--source-date",
    "2022",
    "--licence",
    "UNKNOWN",
    "--temporal-class",
    "annual",
]


@pytest.fixture
def facilities_file(tmp_path):
    from wildfireguardian_data.fixtures import make_fixture
    from wildfireguardian_data.vector import write_geojson

    path = tmp_path / "src" / "facilities.geojson"
    path.parent.mkdir(parents=True)
    write_geojson(make_fixture("korean_valley_facilities"), path)
    return path


def _run(tmp_path, facilities_file, *extra, expect=EXIT_OK):
    out = tmp_path / "out"
    argv = [
        "import-facilities",
        str(facilities_file),
        "--out",
        str(out),
        "--kind-map",
        '{"shelter": "shelter"}',
        *REQUIRED,
        *extra,
    ]
    assert main(argv) == expect
    return out


# --------------------------------------------------------------------------- #
# Checksums: never optional, only explicitly waived
# --------------------------------------------------------------------------- #
def test_a_checksum_decision_is_mandatory(tmp_path, facilities_file):
    # Neither flag: argparse refuses. The operator has to decide rather than
    # drift past the question -- a manually downloaded file could be a
    # truncated transfer, a different edition, or the wrong county.
    with pytest.raises(SystemExit) as excinfo:
        _run(tmp_path, facilities_file)
    assert excinfo.value.code == EXIT_USAGE


def test_a_wrong_checksum_refuses_the_import(tmp_path, facilities_file):
    out = _run(
        tmp_path,
        facilities_file,
        "--expect-sha256",
        "0" * 64,
        expect=EXIT_DATA_ERROR,
    )
    # And nothing was written: a refused import leaves no half-ingested layer.
    assert not (out / "facilities.geojson").exists()


def test_a_matching_checksum_is_recorded_as_verified(tmp_path, facilities_file, capsys):
    digest = sha256_file(facilities_file)
    out = _run(tmp_path, facilities_file, "--expect-sha256", digest, "--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["checksum"]["sha256"] == digest
    assert payload["checksum"]["verified_against_published_value"] is True
    assert (out / "facilities.geojson").exists()
    assert (out / "provenance" / "facilities.provenance.json").exists()


def test_waiving_the_checksum_records_that_it_was_unverified(
    tmp_path, facilities_file, capsys
):
    # The distinction that matters: the file's own digest is still computed, so
    # the record says what was received -- it just does not claim it is what
    # was published.
    _run(tmp_path, facilities_file, "--no-expected-checksum", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["checksum"]["sha256"] == sha256_file(facilities_file)
    assert payload["checksum"]["verified_against_published_value"] is False
    assert "no published checksum" in payload["checksum"]["note"]


# --------------------------------------------------------------------------- #
# Nothing is inferred from the file
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "drop", ["--source-name", "--source-url", "--source-date", "--licence", "--temporal-class"]
)
def test_every_provenance_fact_is_required_not_inferred(
    tmp_path, facilities_file, drop
):
    # A file on disk does not state where it came from, what period it
    # describes, or under what licence it may be used. None of these has a
    # default, because a default here is a fabricated scientific claim.
    index = REQUIRED.index(drop)
    remaining = REQUIRED[:index] + REQUIRED[index + 2 :]
    argv = [
        "import-facilities",
        str(facilities_file),
        "--out",
        str(tmp_path / "out"),
        "--kind-map",
        '{"shelter": "shelter"}',
        "--no-expected-checksum",
        *remaining,
    ]
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == EXIT_USAGE


def test_what_was_not_supplied_stays_unknown_and_is_reported(
    tmp_path, facilities_file, capsys
):
    _run(tmp_path, facilities_file, "--no-expected-checksum", "--json")
    payload = json.loads(capsys.readouterr().out)
    unknown = payload["unknown_provenance_fields"]
    # The licence was explicitly declared unread, so it is UNKNOWN -- an
    # importer that recorded the licence printed on the page would be asserting
    # something nobody confirmed. This is the 임상도 case exactly.
    assert "sources[0].licence" in unknown
    # But a vector layer's cell size and vertical datum are not_applicable, not
    # UNKNOWN: those facts do not exist, and conflating the two would dilute
    # the incompleteness metric (D-0009).
    for not_a_gap in ("resolution_unit", "vertical_datum", "nodata_representation"):
        assert not_a_gap not in unknown


def test_validity_is_recorded_when_given_and_unknown_when_not(
    tmp_path, facilities_file, capsys
):
    from wildfireguardian_data.provenance.store import read_provenance

    out = _run(tmp_path, facilities_file, "--no-expected-checksum")
    record = read_provenance(out, "facilities")
    assert record.valid_from == "UNKNOWN"

    out2 = _run(
        tmp_path / "second",
        facilities_file,
        "--no-expected-checksum",
        "--valid-from",
        "2022",
        "--valid-to",
        "2022-12",
    )
    record2 = read_provenance(out2, "facilities")
    assert record2.valid_from == "2022"
    # Precision is preserved, not normalised to a day.
    assert record2.valid_to == "2022-12"


def test_the_import_summary_says_nothing_was_inferred(
    tmp_path, facilities_file, capsys
):
    _run(tmp_path, facilities_file, "--no-expected-checksum", "--json")
    caveats = " ".join(json.loads(capsys.readouterr().out)["caveats"]).lower()
    assert "was inferred from the file" in caveats
    assert "does not validate it" in caveats


# --------------------------------------------------------------------------- #
# Per-kind required arguments
# --------------------------------------------------------------------------- #
def test_importing_fuels_requires_a_registered_scheme(tmp_path, facilities_file, capsys):
    # A raster of integers with no legend has no meaning, and reading it
    # through the wrong legend relabels every cell (A-FU-1, A-FU-2). A scheme
    # name nobody registered is a bad *argument*, so it is a usage error (2),
    # not a data error (3) -- the file was never reached.
    argv = [
        "import-fuels",
        str(facilities_file),
        "--out",
        str(tmp_path / "out"),
        "--no-expected-checksum",
        "--scheme",
        "a_scheme_nobody_registered",
        *REQUIRED,
    ]
    assert main(argv) == EXIT_USAGE
    # The error names the schemes that do exist, rather than only refusing.
    assert "synthetic_demo_v1" in capsys.readouterr().err


def test_importing_population_requires_its_aggregation_level(tmp_path, facilities_file):
    # Without the geographic support, a consumer cannot judge whether a count
    # is disclosive (D-0011).
    argv = [
        "import-population",
        str(facilities_file),
        "--out",
        str(tmp_path / "out"),
        "--no-expected-checksum",
        *REQUIRED,
    ]
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == EXIT_USAGE


def test_importing_facilities_requires_an_explicit_kind_map(tmp_path, facilities_file):
    # A facility's role is mapped from the source's own tag, never inferred
    # from its spelling (A-FAC-2).
    argv = [
        "import-facilities",
        str(facilities_file),
        "--out",
        str(tmp_path / "out"),
        "--no-expected-checksum",
        *REQUIRED,
    ]
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == EXIT_USAGE


def test_a_missing_input_file_is_a_clean_error(tmp_path):
    argv = [
        "import-roads",
        str(tmp_path / "nope.geojson"),
        "--out",
        str(tmp_path / "out"),
        "--no-expected-checksum",
        *REQUIRED,
    ]
    assert main(argv) == EXIT_DATA_ERROR
