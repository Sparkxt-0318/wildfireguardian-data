"""CRS safeguards: mismatch raises, nothing is reprojected implicitly.

These tests encode ``docs/DECISIONS.md`` D-0002 and D-0010. A change that makes
any of them pass by coercing a CRS is a scientific change and needs a decision
entry.
"""

from __future__ import annotations

import pytest
from helpers import make_raster, planar_surface

from wildfireguardian_data.bounds import Bounds
from wildfireguardian_data.crs import (
    KOREAN_CRS_NOTES,
    authority_axis_order_is_xy,
    check_korean_crs_notes,
    crs_axis_length_unit,
    crs_equal,
    crs_to_string,
    parse_crs,
    require_projected_metre_crs,
    require_same_crs,
)
from wildfireguardian_data.errors import (
    CRSMismatchError,
    GeographicCRSError,
    UnknownCRSError,
)
from wildfireguardian_data.units import LengthUnit


def test_documented_korean_crs_facts_match_the_proj_database():
    # Documentation about a CRS is checked against PROJ rather than trusted, so
    # a PROJ upgrade that renames a CRS fails here instead of drifting silently.
    assert check_korean_crs_notes() == []


def test_korean_crs_notes_cover_the_expected_codes():
    assert {"EPSG:5179", "EPSG:5186", "EPSG:5187", "EPSG:5174", "EPSG:32652", "EPSG:4326"} <= set(
        KOREAN_CRS_NOTES
    )


def test_two_unknown_crss_are_not_equal():
    # Two layers of unknown CRS are not known to agree; treating them as equal
    # is the silent error this package exists to prevent.
    assert crs_equal(None, None) is False


def test_equivalent_crs_spellings_compare_equal():
    assert crs_equal("EPSG:4326", parse_crs("EPSG:4326").to_wkt())


def test_legacy_korean_datum_is_not_equated_with_the_modern_one():
    # EPSG:5174 (Korean 1985) vs EPSG:5186 (KGD2002): a 100-200 m datum shift.
    # Older Korean layers are frequently mislabelled between the two.
    assert not crs_equal("EPSG:5174", "EPSG:5186")


def test_korean_belt_systems_declare_northing_first():
    # The trap: EPSG:5179/5186/5187 abbreviate their axes X and Y, but X is the
    # NORTHING. Detection must use axis direction, not the abbreviation.
    for code in ("EPSG:5179", "EPSG:5186", "EPSG:5187"):
        assert authority_axis_order_is_xy(code) is False
    assert authority_axis_order_is_xy("EPSG:32652") is True


def test_require_same_crs_raises_on_mismatch_and_never_reprojects():
    with pytest.raises(CRSMismatchError) as excinfo:
        require_same_crs(["EPSG:5187", "EPSG:4326"], context="stacking layers")
    assert "never reprojects implicitly" in str(excinfo.value)


def test_require_same_crs_raises_on_unknown():
    with pytest.raises(UnknownCRSError):
        require_same_crs(["EPSG:5187", None])


def test_geographic_crs_refused_for_length_work():
    with pytest.raises(GeographicCRSError):
        require_projected_metre_crs("EPSG:4326", context="slope")
    with pytest.raises(GeographicCRSError):
        crs_axis_length_unit("EPSG:4326")


def test_projected_metre_crs_accepted():
    assert crs_axis_length_unit("EPSG:5187") is LengthUnit.METRE
    assert require_projected_metre_crs("EPSG:5187") is not None


def test_unknown_crs_renders_as_the_unknown_literal():
    assert crs_to_string(None) == "UNKNOWN"


def test_slope_refuses_a_geographic_grid():
    from wildfireguardian_data.terrain import slope

    layer = make_raster(planar_surface(), crs="EPSG:4326", cell_size_m=1 / 3600)
    with pytest.raises(GeographicCRSError):
        slope(layer)


def test_clip_refuses_bounds_in_a_different_crs():
    from wildfireguardian_data.terrain import clip_raster

    layer = make_raster(planar_surface())
    with pytest.raises(CRSMismatchError):
        clip_raster(layer, Bounds(129.3, 36.9, 129.4, 37.0, crs="EPSG:4326"))


def test_bounds_operations_refuse_mixed_crs():
    left = Bounds(0, 0, 100, 100, crs="EPSG:5187")
    right = Bounds(0, 0, 100, 100, crs="EPSG:32652")
    with pytest.raises(CRSMismatchError):
        left.intersects(right)


def test_road_graph_refuses_a_geographic_crs():
    from helpers import make_provenance
    from shapely.geometry import LineString

    from wildfireguardian_data.roads import build_road_graph
    from wildfireguardian_data.vector import Feature, VectorLayer

    layer = VectorLayer(
        name="degrees_roads",
        features=(Feature(LineString([(129.30, 36.90), (129.31, 36.91)]), {}),),
        crs="EPSG:4326",
        provenance=make_provenance("degrees_roads", value_unit="not_applicable"),
    )
    with pytest.raises(GeographicCRSError):
        build_road_graph(layer)
