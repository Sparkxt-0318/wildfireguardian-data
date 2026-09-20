"""The second real geography (Phase 2 item 21).

These tests do not re-check the pipeline -- the rest of the suite does that.
They assert the things that make ``naju_real_v1`` worth committing: that it is
genuinely different from Uljin, and that three code paths Uljin never reached
are exercised here on real data.

A test that merely rebuilt the bundle and compared it to itself would be
worthless, so nothing here is a snapshot of the whole layer. Each assertion is
either a property of the geography or a rule from ``docs/DECISIONS.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from wildfireguardian_data.study_area import read_bundle

NAJU = Path("data/study_areas/naju_real_v1")
ULJIN = Path("data/study_areas/uljin_real_v1")


@pytest.fixture(scope="module")
def naju():
    if not NAJU.exists():
        pytest.skip("naju_real_v1 is not committed in this checkout")
    return read_bundle(NAJU)


@pytest.fixture(scope="module")
def uljin():
    if not ULJIN.exists():
        pytest.skip("uljin_real_v1 is not committed in this checkout")
    return read_bundle(ULJIN)


def test_the_two_geographies_are_actually_different(naju, uljin):
    """Otherwise item 21 has been answered by building Uljin twice."""
    assert naju.crs.to_string() == "EPSG:5186"
    assert uljin.crs.to_string() == "EPSG:5187"

    def relief(bundle):
        data = bundle.terrain.dem.data[bundle.terrain.dem.valid_mask()]
        return float(data.max() - data.min())

    def mean_slope(bundle):
        slope = bundle.terrain.slope
        return float(slope.data[slope.valid_mask()].mean())

    # An order of magnitude in both, which is what makes the comparison mean
    # something. Loose bounds, because the point is the ratio, not the value.
    assert relief(uljin) > 5 * relief(naju)
    assert mean_slope(uljin) > 5 * mean_slope(naju)
    assert naju.roads.qa["graph"]["edges"] > 10 * uljin.roads.qa["graph"]["edges"]


def test_flat_ground_aspect_is_nan_on_real_data_never_a_direction(naju, uljin):
    """D-0006, exercised on real ground for the first time.

    Uljin contains **no** flat cells, so until this bundle existed the rule was
    only tested against a synthetic flat fixture. Naju has hundreds. A consumer
    that read aspect ``0`` as "north-facing" would mis-sign every one of them.
    """
    slope, aspect = naju.terrain.slope, naju.terrain.aspect
    slope_valid, aspect_valid = slope.valid_mask(), aspect.valid_mask()

    # Flat = slope is defined and exactly zero, so aspect has no direction.
    flat = slope_valid & ~aspect_valid
    assert int(flat.sum()) > 100, "expected real flat ground on a river plain"
    # Every such cell has slope exactly 0, not merely small: the flat test is
    # not a tolerance band that happened to catch gentle slopes.
    assert np.all(slope.data[flat] == 0.0)
    assert np.all(np.isnan(aspect.data[flat]))

    # And the values this repository refuses to use for "undefined".
    defined = aspect.data[aspect_valid]
    assert not np.any(defined == 0.0)
    assert not np.any(defined < 0.0)
    assert float(defined.min()) > 0.0
    assert float(defined.max()) <= 360.0

    # The contrast that makes this test necessary rather than redundant.
    uljin_flat = uljin.terrain.slope.valid_mask() & ~uljin.terrain.aspect.valid_mask()
    assert int(uljin_flat.sum()) == 0


def test_worldcover_is_ingested_and_every_code_is_in_the_published_scheme(naju):
    from wildfireguardian_data.raster import RasterKind

    fuels = naju.fuels.layer
    assert fuels.kind is RasterKind.CATEGORICAL
    scheme = naju.fuels.scheme
    name = scheme["name"] if isinstance(scheme, dict) else scheme.name
    assert name == "esa_worldcover_v200"

    codes = scheme["codes"] if isinstance(scheme, dict) else scheme.codes
    defined = {int(c) for c in codes}
    present = {int(v) for v in np.unique(fuels.data[fuels.valid_mask()])}
    assert present <= defined, f"undefined class codes present: {present - defined}"
    # A river plain: cropland should dominate. Asserted loosely, as a sanity
    # bound on the ingestion rather than a claim about the ground -- nobody has
    # checked these classes against reality.
    values, counts = np.unique(fuels.data[fuels.valid_mask()], return_counts=True)
    shares = dict(
        zip(values.tolist(), (counts / counts.sum()).tolist(), strict=True)
    )
    assert shares.get(40, 0.0) > 0.25, "expected cropland to dominate a river plain"


def test_worldcover_is_co_registered_with_the_dem(naju):
    # D-0018 / A-RD-5: warped onto the DEM's grid, so a consumer indexing both
    # by the same (row, col) reads the same ground.
    assert naju.fuels.layer.shape == naju.terrain.dem.shape
    assert naju.fuels.layer.transform.to_dict() == naju.terrain.dem.transform.to_dict()
    manifest = json.loads((NAJU / "bundle_manifest.json").read_text())
    assert manifest["canonical_grid"]["status"] == "SHARED"


def test_worldcover_was_not_resampled_by_averaging(naju):
    # A-FU-3: the average of class codes 2 and 4 is class 3, a different
    # category. The provenance must show nearest-neighbour, and the
    # co-registration record must show the layer was treated as categorical.
    record = naju.provenance["fuels"]
    warps = [
        transformation
        for transformation in record.transformations
        if transformation.operation == "reproject_raster"
    ]
    assert warps, "the WorldCover layer arrives in EPSG:4326 and must be warped"
    for warp in warps:
        assert warp.parameters["resampling"] == "nearest"
        assert warp.parameters["categorical_or_continuous"] == "categorical"
        # Warped onto a supplied grid, not onto one derived from its own extent.
        assert warp.parameters["target_grid_supplied"] is True


def test_najus_unnoded_crossings_are_tagged_grade_separation(naju, uljin):
    """The reverse of the Uljin audit's finding, which is the point.

    Uljin's apparent crossings were missing junction nodes, and shared-vertex
    noding (D-0020) connected them: it now reports zero. Naju's are real
    bridges over a railway and a river. Same check, opposite correct answers.

    The diagnostic deliberately does not decide which it is looking at
    (D-0007); it reports the crossing and the tags, so a reader can tell.
    """
    naju_crossings = naju.roads.qa["crossings_without_node"]
    assert naju_crossings["count"] > 0
    samples = naju_crossings["samples"]
    assert samples, "a non-zero count must come with samples to check"
    for sample in samples:
        tags = [
            *sample["bridge_tags"],
            *sample["tunnel_tags"],
            *sample["layer_tags"],
        ]
        assert any(tag for tag in tags), (
            "an unnoded crossing with no bridge/tunnel/layer tag on either side "
            f"would be a suspected missing junction, not grade separation: {sample}"
        )

    # Uljin's went to zero once noding was fixed.
    assert uljin.roads.qa["crossings_without_node"]["count"] == 0


def test_najus_network_is_dominated_by_one_component(naju):
    # Not the Uljin pre-fix situation, where the largest component held a
    # minority of the network. reports/SECOND_GEOGRAPHY.md is explicit that the
    # six small components are NOT audited and may still be clip artifacts.
    qa = naju.roads.qa
    components = qa["connectivity"]["components"]
    largest = max(components, key=lambda c: c["edge_count"])
    assert largest["edge_count"] / qa["graph"]["edges"] > 0.95


def test_the_second_bundle_still_declares_its_missing_layers_as_absent(naju):
    # Item 21 is answered for terrain, land cover, roads and CRS handling. It is
    # NOT answered for population or facilities, because neither geography has
    # ever carried real data for those. What IS tested is that their absence is
    # represented rather than implied by a missing key.
    manifest = json.loads((NAJU / "bundle_manifest.json").read_text())
    for slot in ("population", "facilities"):
        entry = manifest["layers"][slot]
        assert entry["status"] == "ABSENT"
        assert entry["layers"] == []


def test_the_second_bundle_validates_with_no_errors():
    from wildfireguardian_data.validation import validate_bundle_directory

    if not NAJU.exists():
        pytest.skip("naju_real_v1 is not committed in this checkout")
    report = validate_bundle_directory(NAJU)
    assert not report.has_errors, report.to_text()
