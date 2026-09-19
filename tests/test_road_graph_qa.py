"""Road-graph construction and topology QA on networks with known topology.

Every network here is small enough that its component count, degrees, bridges
and cuts can be worked out by hand, which is what makes these correctness tests
rather than snapshots.
"""

from __future__ import annotations

import pytest
from shapely.geometry import LineString, MultiLineString, Point

from helpers import make_provenance
from wildfireguardian_data.bounds import Bounds
from wildfireguardian_data.errors import GraphError
from wildfireguardian_data.roads import (
    articulation_points,
    assess_road_network,
    bridge_edges,
    build_road_graph,
    critical_links,
    crossings_without_node,
    identify_exit_nodes,
    identify_settlement_nodes,
)
from wildfireguardian_data.vector import Feature, VectorLayer

CRS = "EPSG:5187"
BOX = Bounds(0.0, 0.0, 3000.0, 3000.0, crs=CRS)


def road_layer(segments, name="roads"):
    """Build a road layer from ``[(id, [(x, y), ...]), ...]``."""
    features = tuple(
        Feature(LineString(coords), {"segment_id": segment_id})
        for segment_id, coords in segments
    )
    return VectorLayer(
        name=name,
        features=features,
        crs=CRS,
        provenance=make_provenance(name, value_unit="not_applicable"),
        feature_kind="road segment",
    )


# --------------------------------------------------------------------------- #
# Graph construction
# --------------------------------------------------------------------------- #
def test_shared_endpoints_become_one_node():
    layer = road_layer(
        [("a", [(0, 0), (100, 0)]), ("b", [(100, 0), (200, 0)])]
    )
    graph = build_road_graph(layer)
    assert graph.node_count == 3
    assert graph.edge_count == 2
    assert graph.total_length_m == pytest.approx(200.0)


def test_endpoints_within_tolerance_are_snapped_and_beyond_it_are_not():
    segments = [("a", [(0, 0), (100, 0)]), ("b", [(100.5, 0), (200, 0)])]
    snapped = build_road_graph(road_layer(segments), snap_tolerance_m=1.0)
    assert snapped.node_count == 3

    exact = build_road_graph(road_layer(segments), snap_tolerance_m=0.0)
    assert exact.node_count == 4


def test_transitive_snapping_is_reported_when_it_exceeds_the_tolerance():
    # A chain of endpoints each 0.9 m from the next merges into one node
    # spanning 1.8 m. That has to be visible, not inferred.
    segments = [
        ("a", [(0, 0), (100.0, 0)]),
        ("b", [(100.9, 0), (200, 0)]),
        ("c", [(101.8, 0), (300, 0)]),
    ]
    graph = build_road_graph(road_layer(segments), snap_tolerance_m=1.0)
    assert graph.max_intra_cluster_distance_m == pytest.approx(1.8)
    qa = assess_road_network(graph, study_bounds=BOX)
    assert any("transitive" in note for note in qa.notes)


def test_crossing_lines_are_not_connected_by_default():
    # docs/DECISIONS.md D-0007: a mid-segment crossing is often a bridge or an
    # underpass, and fabricating a junction there would destroy the egress count.
    layer = road_layer(
        [("ew", [(0, 500), (1000, 500)]), ("ns", [(500, 0), (500, 1000)])]
    )
    graph = build_road_graph(layer)
    assert graph.node_count == 4
    assert graph.edge_count == 2
    qa = assess_road_network(graph, study_bounds=BOX)
    assert qa.crossing_diagnostic["count"] == 1


def test_crossing_noding_is_available_as_an_explicit_opt_in():
    layer = road_layer(
        [("ew", [(0, 500), (1000, 500)]), ("ns", [(500, 0), (500, 1000)])]
    )
    graph = build_road_graph(layer, node_crossings=True)
    assert graph.node_crossings_added == 2
    assert graph.edge_count == 4
    assert graph.node_count == 5
    parameters = graph.provenance.transformations[-1].parameters
    assert parameters["node_crossings"] is True


def test_multilinestrings_are_exploded_and_counted():
    features = (
        Feature(
            MultiLineString([[(0, 0), (100, 0)], [(500, 0), (600, 0)]]),
            {"segment_id": "multi"},
        ),
    )
    layer = VectorLayer(
        name="roads",
        features=features,
        crs=CRS,
        provenance=make_provenance("roads", value_unit="not_applicable"),
    )
    graph = build_road_graph(layer)
    assert graph.exploded_multilinestrings == 1
    assert graph.edge_count == 2


def test_non_line_geometry_is_refused():
    layer = VectorLayer(
        name="roads",
        features=(Feature(Point(0, 0), {}),),
        crs=CRS,
        provenance=make_provenance("roads", value_unit="not_applicable"),
    )
    with pytest.raises(GraphError) as excinfo:
        build_road_graph(layer)
    assert "not a LineString" in str(excinfo.value)


def test_empty_layer_is_refused():
    layer = VectorLayer(
        name="roads",
        features=(),
        crs=CRS,
        provenance=make_provenance("roads", value_unit="not_applicable"),
    )
    with pytest.raises(GraphError):
        build_road_graph(layer)


# --------------------------------------------------------------------------- #
# Topology with known answers
# --------------------------------------------------------------------------- #
def single_exit_graph():
    """Boundary -> A -> B -> hamlet. One exit, three edges, all critical."""
    return build_road_graph(
        road_layer(
            [
                ("access", [(0, 1500), (1000, 1500)]),
                ("valley", [(1000, 1500), (2000, 1500)]),
                ("lane", [(2000, 1500), (2000, 1900)]),
            ]
        )
    )


def two_exit_graph():
    """A loop touching the boundary twice; hamlet on the loop."""
    return build_road_graph(
        road_layer(
            [
                ("west", [(0, 1500), (1000, 1500)]),
                ("north", [(1000, 1500), (1500, 2000)]),
                ("south", [(1000, 1500), (1500, 1000)]),
                ("east_north", [(1500, 2000), (3000, 1750)]),
                ("east_south", [(1500, 1000), (3000, 1250)]),
                ("link", [(1500, 2000), (1500, 1000)]),
            ]
        )
    )


def test_single_exit_network_has_one_exit_and_every_edge_is_critical():
    graph = single_exit_graph()
    settlements = identify_settlement_nodes(graph, [("hamlet", Point(2000, 1900))])
    exits = identify_exit_nodes(graph, study_bounds=BOX, boundary_tolerance_m=30.0)

    assert len(exits.all_exits) == 1
    assert exits.clip_boundary_warning is True

    links = critical_links(
        graph, exit_nodes=exits.all_exits, settlement_nodes=settlements.all_nodes
    )
    assert len(links) == 3  # every edge in the chain cuts the hamlet off

    qa = assess_road_network(
        graph, study_bounds=BOX, settlements=[("hamlet", Point(2000, 1900))]
    )
    assert qa.component_count == 1
    assert len(qa.single_egress_candidates) == 1
    assert qa.single_egress_candidates[0]["settlements"] == ["hamlet"]


def test_two_exit_network_has_no_critical_link_for_the_hamlet():
    graph = two_exit_graph()
    hamlet = Point(1500, 2000)
    settlements = identify_settlement_nodes(graph, [("hamlet", hamlet)])
    exits = identify_exit_nodes(graph, study_bounds=BOX, boundary_tolerance_m=30.0)

    assert len(exits.all_exits) >= 2
    links = critical_links(
        graph, exit_nodes=exits.all_exits, settlement_nodes=settlements.all_nodes
    )
    # The counterpart to the single-exit case: a redundant network has no single
    # edge whose loss isolates the hamlet. A critical-link finder that merely
    # reported every bridge would fail here.
    assert links == []

    qa = assess_road_network(graph, study_bounds=BOX, settlements=[("hamlet", hamlet)])
    assert qa.single_egress_candidates == ()


def test_disconnected_network_components_and_isolated_segments():
    graph = build_road_graph(
        road_layer(
            [
                ("main", [(0, 1500), (1500, 1500)]),
                ("side", [(1500, 1500), (1500, 2200)]),
                ("orphan", [(2500, 400), (2700, 400)]),
            ]
        )
    )
    qa = assess_road_network(graph, study_bounds=BOX)
    assert qa.component_count == 2
    assert len(qa.isolated_segment_edges) == 1
    assert qa.isolated_segment_edges[0]["length_m"] == pytest.approx(200.0)


def test_settlement_in_a_component_with_no_exit_is_reported_not_hidden():
    graph = build_road_graph(
        road_layer(
            [
                ("main", [(0, 1500), (1500, 1500)]),
                ("orphan_a", [(2400, 400), (2600, 400)]),
                ("orphan_b", [(2600, 400), (2600, 600)]),
            ]
        )
    )
    qa = assess_road_network(
        graph, study_bounds=BOX, settlements=[("stranded", Point(2600, 600))]
    )
    assert len(qa.no_egress_components) == 1
    assert qa.no_egress_components[0]["settlements"] == ["stranded"]
    assert "clipped too tightly" in qa.no_egress_components[0]["note"]


def test_unmatched_settlement_is_a_finding_not_a_silent_drop():
    graph = single_exit_graph()
    result = identify_settlement_nodes(
        graph, [("far_away", Point(2900, 2900))], snap_distance_m=100.0
    )
    assert result.unmatched_settlements == ("far_away",)
    assert result.all_nodes == frozenset()


def test_dead_ends_exclude_exit_nodes():
    graph = single_exit_graph()
    exits = identify_exit_nodes(graph, study_bounds=BOX)
    qa = assess_road_network(graph, study_bounds=BOX)
    for node in qa.dead_end_nodes:
        assert node not in exits.all_exits
    # The hamlet end is a dead end; the boundary end is an exit, not a dead end.
    assert len(qa.dead_end_nodes) == 1


def test_degree_histogram_counts_multigraph_degree():
    graph = single_exit_graph()
    qa = assess_road_network(graph, study_bounds=BOX)
    # Chain of 3 edges: two ends of degree 1, two middles of degree 2.
    assert qa.node_degree_histogram == {1: 2, 2: 2}


def test_parallel_edges_are_not_reported_as_bridges():
    # Two distinct roads between the same junctions are two ways out. Collapsing
    # them would make each look like a bridge whose loss disconnects the network.
    graph = build_road_graph(
        road_layer(
            [
                ("route_a", [(0, 0), (500, 100), (1000, 0)]),
                ("route_b", [(0, 0), (500, -100), (1000, 0)]),
            ]
        )
    )
    assert graph.edge_count == 2
    assert graph.edge_multiplicity(0, 1) == 2
    assert bridge_edges(graph) == []
    assert articulation_points(graph) == []


def test_articulation_points_of_a_chain():
    graph = single_exit_graph()
    # The two interior junctions of a 4-node chain are cut vertices.
    assert len(articulation_points(graph)) == 2


def test_source_tagged_exits_are_distinguished_from_boundary_exits():
    layer = VectorLayer(
        name="roads",
        features=(
            Feature(LineString([(1000, 1500), (2000, 1500)]), {"leaves_area": "yes"}),
            Feature(LineString([(2000, 1500), (2000, 1900)]), {"leaves_area": "no"}),
        ),
        crs=CRS,
        provenance=make_provenance("roads", value_unit="not_applicable"),
    )
    graph = build_road_graph(layer)
    exits = identify_exit_nodes(graph, exit_property="leaves_area")
    assert len(exits.from_source_tags) == 2
    assert exits.from_boundary == frozenset()
    assert exits.clip_boundary_warning is False


def test_qa_report_carries_the_safety_disclaimer_in_band():
    from wildfireguardian_data.roads import SAFETY_DISCLAIMER

    qa = assess_road_network(single_exit_graph(), study_bounds=BOX)
    payload = qa.to_dict()
    assert payload["safety_disclaimer"] == SAFETY_DISCLAIMER
    assert "NOT statements about whether a road is passable" in payload["safety_disclaimer"]
    # And no field anywhere claims safety.
    import json

    text = json.dumps(payload).lower()
    for forbidden in ('"safe"', '"is_safe"', '"passable"', '"trapped"'):
        assert forbidden not in text


def test_qa_report_is_json_serialisable():
    import json

    qa = assess_road_network(
        single_exit_graph(), study_bounds=BOX, settlements=[("hamlet", Point(2000, 1900))]
    )
    json.dumps(qa.to_dict())


def test_edge_lengths_are_planar_metres():
    graph = build_road_graph(road_layer([("diag", [(0, 0), (300, 400)])]))
    assert graph.total_length_m == pytest.approx(500.0)
    assert graph.distance_unit.value == "m"
