"""The CLI contract: commands, exit codes, and the --json keys.

The CLI is this repository's one stable interface (``docs/INTERFACES.md``), so
these tests pin its exit codes and top-level JSON keys.
"""

from __future__ import annotations

import json

from wildfireguardian_data.cli.main import (
    EXIT_DATA_ERROR,
    EXIT_NETWORK,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VALIDATION_FAILED,
    main,
)

CONFIG = "configs/uljin_valley_synthetic.yaml"


def test_version_command(capsys):
    assert main(["version"]) == EXIT_OK
    assert capsys.readouterr().out.strip()


def test_list_fixtures_includes_the_eight_required_fixtures(capsys):
    assert main(["list-fixtures"]) == EXIT_OK
    names = set(capsys.readouterr().out.split())
    required = {
        "tilted_plane",
        "flat_terrain",
        "single_exit_network",
        "two_exit_network",
        "disconnected_network",
        "incompatible_crs_pair",
        "missing_cells_raster",
        "village_shelter_station",
    }
    assert required <= names


def test_build_validate_summarize_end_to_end(tmp_path, capsys):
    out = tmp_path / "bundle"
    assert main(["build-study-area", CONFIG, "--out", str(out)]) == EXIT_OK
    build_output = capsys.readouterr().out
    assert "built uljin_valley_synthetic_v1" in build_output
    assert (out / "manifest.json").exists()
    assert (out / "validation" / "report.json").exists()

    assert main(["validate-study-area", str(out)]) == EXIT_OK
    assert "status: pass" in capsys.readouterr().out

    assert main(["summarize-study-area", str(out)]) == EXIT_OK
    summary_output = capsys.readouterr().out
    assert "topology QA only" in summary_output
    assert "EPSG:5187" in summary_output


def test_build_json_output_has_the_documented_top_level_keys(tmp_path, capsys):
    out = tmp_path / "bundle"
    assert main(["build-study-area", CONFIG, "--out", str(out), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert {"schema_version", "status", "findings", "summary"} <= set(payload)
    assert payload["status"] in {"pass", "pass_with_warnings"}


def test_validate_json_output_has_the_documented_top_level_keys(tmp_path, capsys):
    out = tmp_path / "bundle"
    main(["build-study-area", CONFIG, "--out", str(out)])
    capsys.readouterr()
    assert main(["validate-study-area", str(out), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert {"schema_version", "target", "status", "counts", "findings"} <= set(payload)


def test_strict_mode_fails_on_warnings(tmp_path, capsys):
    out = tmp_path / "bundle"
    main(["build-study-area", CONFIG, "--out", str(out)])
    capsys.readouterr()
    # The example bundle has deliberate warnings, so --strict must fail it.
    assert main(["validate-study-area", str(out), "--strict"]) == EXIT_VALIDATION_FAILED


def test_validate_of_a_non_bundle_directory_fails(tmp_path, capsys):
    assert main(["validate-study-area", str(tmp_path)]) == EXIT_VALIDATION_FAILED
    assert "BND-010" in capsys.readouterr().out


def test_missing_config_is_a_usage_error(capsys):
    assert main(["build-study-area", "configs/does_not_exist.yaml"]) == EXIT_USAGE
    assert "config" in capsys.readouterr().err


def test_unknown_config_key_is_a_usage_error(tmp_path, capsys):
    config = tmp_path / "bad.yaml"
    config.write_text(
        "study_area_id: x_synthetic\ncrs: EPSG:5187\n"
        "bounds: [0, 0, 100, 100]\nterain: {}\n"  # typo: 'terain'
    )
    assert main(["build-study-area", str(config)]) == EXIT_USAGE
    assert "unrecognised key" in capsys.readouterr().err


def test_geographic_analysis_crs_is_a_usage_error(tmp_path, capsys):
    config = tmp_path / "geographic.yaml"
    config.write_text(
        "study_area_id: x_synthetic\ncrs: EPSG:4326\n"
        "bounds: [129.0, 36.0, 129.1, 36.1]\n"
    )
    assert main(["build-study-area", str(config)]) == EXIT_USAGE
    assert "geographic" in capsys.readouterr().err


def test_network_source_without_the_flag_exits_five(tmp_path, capsys):
    config = tmp_path / "network.yaml"
    config.write_text(
        "study_area_id: uljin_real_test\n"
        "crs: EPSG:5187\n"
        "bounds: [226000.0, 477000.0, 228000.0, 479000.0]\n"
        "terrain:\n"
        "  source:\n"
        "    kind: copernicus_dem_glo30\n"
        "  target_resolution_m: 30.0\n"
    )
    assert main(["build-study-area", str(config)]) == EXIT_NETWORK
    assert "allow-network" in capsys.readouterr().err


def test_build_refuses_to_overwrite_without_force(tmp_path, capsys):
    out = tmp_path / "bundle"
    main(["build-study-area", CONFIG, "--out", str(out)])
    capsys.readouterr()
    assert main(["build-study-area", CONFIG, "--out", str(out)]) == EXIT_DATA_ERROR
    assert "--force" in capsys.readouterr().err
    assert main(["build-study-area", CONFIG, "--out", str(out), "--force"]) == EXIT_OK


def test_npz_build_needs_no_gdal_reader(tmp_path, capsys):
    out = tmp_path / "npz_bundle"
    assert main(["build-study-area", CONFIG, "--out", str(out), "--npz"]) == EXIT_OK
    capsys.readouterr()
    manifest = json.loads((out / "manifest.json").read_text())
    rasters = [e for e in manifest["layers"] if e["type"] == "raster"]
    assert rasters and all(entry["format"] == "npz" for entry in rasters)


def test_make_fixtures_writes_every_fixture(tmp_path, capsys):
    out = tmp_path / "fixtures"
    assert main(["make-fixtures", str(out), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["written"]
    # Every written fixture is labelled synthetic in its provenance.
    assert {entry["data_class"] for entry in payload["written"]} == {"synthetic"}
    assert (out / "synthetic_tilted_plane.tif").exists()


def test_make_fixtures_rejects_an_unknown_name(tmp_path, capsys):
    assert main(["make-fixtures", str(tmp_path), "--only", "nope"]) == EXIT_USAGE
    assert "unknown fixture" in capsys.readouterr().err
