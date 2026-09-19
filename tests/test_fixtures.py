"""The synthetic fixtures are what they claim to be.

The phase-1 plan requires eight specific fixtures; this file asserts each one
exists, is labelled synthetic, and has the topology or the analytical property
it is meant to have.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from shapely.geometry import Point

from wildfireguardian_data import DataClass
from wildfireguardian_data.bounds import Bounds
from wildfireguardian_data.errors import CRSMismatchError, GeographicCRSError
from wildfireguardian_data.fixtures import (
    disconnected_network,
    fixture_names,
    flat_terrain,
    incompatible_crs_pair,
    korean_valley_bounds,
    korean_valley_dem,
    korean_valley_fuels,
    korean_valley_roads,
    make_fixture,
    missing_cells_raster,
    single_exit_network,
    tilted_plane,
    two_exit_network,
    village_shelter_station,
)

REQUIRED_FIXTURES = (
    "tilted_plane",
    "flat_terrain",
    "single_exit_network",
    "two_exit_network",
    "disconnected_network",
    "incompatible_crs_pair",
    "missing_cells_raster",
    "village_shelter_station",
)


def test_every_required_fixture_is_registered():
    assert set(REQUIRED_FIXTURES) <= set(fixture_names())


def test_unknown_fixture_name_raises():
    with pytest.raises(KeyError):
        make_fixture("no_such_fixture")


def test_tilted_plane_records_its_own_analytical_answer():
    layer = tilted_plane(dz_dx=0.3, dz_dy=0.4)
    parameters = layer.provenance.transformations[0].parameters
    assert parameters["expected_slope_deg"] == pytest.approx(
        math.degrees(math.atan(0.5))
    )
    # And the recorded answer is what the estimator actually produces.
    from wildfireguardian_data.terrain import slope

    interior = slope(layer).data[1:-1, 1:-1]
    assert interior == pytest.approx(parameters["expected_slope_deg"], abs=1e-4)


def test_flat_terrain_is_constant():
    layer = flat_terrain(elevation_m=412.0)
    assert np.all(layer.data == 412.0)


def test_missing_cells_fixture_returns_two_equivalent_representations():
    nan_layer, sentinel_layer = missing_cells_raster()
    assert nan_layer.missing_count == sentinel_layer.missing_count
    assert np.isnan(nan_layer.nodata)
    assert sentinel_layer.nodata == -9999.0
    # Same cells missing in both.
    assert np.array_equal(nan_layer.valid_mask(), sentinel_layer.valid_mask())


def test_single_exit_and_two_exit_networks_differ_in_critical_links():
    from wildfireguardian_data.fixtures import SYNTHETIC_ORIGIN
    from wildfireguardian_data.roads import (
        assess_road_network,
        build_road_graph,
    )

    box = Bounds(
        SYNTHETIC_ORIGIN[0],
        SYNTHETIC_ORIGIN[1],
        SYNTHETIC_ORIGIN[0] + 3000,
        SYNTHETIC_ORIGIN[1] + 3000,
        crs="EPSG:5187",
    )
    hamlet = Point(SYNTHETIC_ORIGIN[0] + 2000, SYNTHETIC_ORIGIN[1] + 1900)

    single = assess_road_network(
        build_road_graph(single_exit_network()),
        study_bounds=box,
        settlements=[("hamlet", hamlet)],
    )
    two = assess_road_network(
        build_road_graph(two_exit_network()),
        study_bounds=box,
        settlements=[("hamlet", Point(SYNTHETIC_ORIGIN[0] + 1500, SYNTHETIC_ORIGIN[1] + 2000))],
    )
    assert len(single.critical_link_list) == 3
    assert len(two.critical_link_list) == 0
    assert len(single.single_egress_candidates) == 1
    assert len(two.single_egress_candidates) == 0


def test_disconnected_network_has_multiple_components_and_a_crossing():
    from wildfireguardian_data.fixtures import SYNTHETIC_ORIGIN
    from wildfireguardian_data.roads import assess_road_network, build_road_graph

    box = Bounds(
        SYNTHETIC_ORIGIN[0],
        SYNTHETIC_ORIGIN[1],
        SYNTHETIC_ORIGIN[0] + 3000,
        SYNTHETIC_ORIGIN[1] + 3000,
        crs="EPSG:5187",
    )
    qa = assess_road_network(build_road_graph(disconnected_network()), study_bounds=box)
    assert qa.component_count >= 2
    assert qa.crossing_diagnostic["count"] == 1


def test_incompatible_crs_pair_cannot_be_combined():
    pair = incompatible_crs_pair()
    from wildfireguardian_data.crs import require_same_crs

    with pytest.raises(CRSMismatchError):
        require_same_crs([pair.projected.crs, pair.geographic.crs])
    # And the geographic raster is refused by slope outright.
    from wildfireguardian_data.terrain import slope

    with pytest.raises(GeographicCRSError):
        slope(pair.geographic_raster)


def test_village_scene_has_aggregate_population_only():
    scene = village_shelter_station()
    from wildfireguardian_data.population import check_privacy

    check_privacy(scene.villages.property_keys)
    properties = scene.villages.features[0].properties
    assert properties["population_total"] == 42
    assert (
        properties["pop_0_14"] + properties["pop_15_64"] + properties["pop_65_plus"] == 42
    )


def test_village_scene_facilities_declare_no_capacity_or_status():
    scene = village_shelter_station()
    for feature in scene.facilities:
        assert feature.properties["capacity_persons"] is None
        assert feature.properties["operational_status"] == "UNKNOWN"


def test_every_fixture_layer_is_labelled_synthetic():
    for name in fixture_names():
        produced = make_fixture(name)
        candidates = []
        if hasattr(produced, "provenance"):
            candidates = [produced]
        elif isinstance(produced, (list, tuple)):
            candidates = list(produced)
        elif hasattr(produced, "__dataclass_fields__"):
            candidates = [getattr(produced, f) for f in produced.__dataclass_fields__]
        for layer in candidates:
            if hasattr(layer, "provenance"):
                assert layer.provenance.data_class is DataClass.SYNTHETIC, name
                assert "SYNTHETIC" in layer.provenance.notes, name


def test_korean_valley_dem_is_deterministic_for_a_given_seed():
    first = korean_valley_dem(seed=123)
    second = korean_valley_dem(seed=123)
    third = korean_valley_dem(seed=124)
    assert np.array_equal(first.data, second.data)
    assert not np.array_equal(first.data, third.data)
    assert first.provenance.random_seed == 123


def test_korean_valley_dem_is_in_a_plausible_korean_elevation_range():
    # Plausibility matters only for making the example legible; the layer is
    # still synthetic. But an implausible surface (2000 m hills inland of Uljin)
    # would invite a reader to treat it as real.
    dem = korean_valley_dem()
    values = dem.data[dem.valid_mask()]
    assert 100.0 < float(values.min()) < 400.0
    assert 400.0 < float(values.max()) < 1200.0


def test_korean_valley_dem_covers_the_study_bounds_with_a_margin():
    dem = korean_valley_dem()
    bounds = korean_valley_bounds()
    assert dem.bounds.min_x < bounds.min_x
    assert dem.bounds.max_x > bounds.max_x


def test_korean_valley_fuels_share_the_dem_grid_and_have_a_nodata_block():
    dem = korean_valley_dem()
    fuels = korean_valley_fuels(template=dem)
    assert fuels.transform == dem.transform
    assert fuels.missing_count >= 225
    assert set(np.unique(fuels.data)) <= {1, 2, 3, 4, 5, 6, 255}


def test_korean_valley_roads_include_the_intended_pathologies():
    layer = korean_valley_roads()
    ids = {feature.properties["segment_id"] for feature in layer}
    assert {"orphan_spur", "forest_track", "spur_a1", "loop_b1"} <= ids
