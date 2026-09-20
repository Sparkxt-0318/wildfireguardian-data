"""Phase 2 road work: shared-vertex noding, exit semantics, and QA extensions.

The shared-vertex tests pin D-0020, which is the fix for the Uljin fragmentation
audited in `reports/ULJIN_ROAD_AUDIT.md`. Every expected topology below is
hand-derived from the geometry, not read off the implementation.
"""

from __future__ import annotations

import pytest
from helpers import make_provenance
from shapely.geometry import LineString, Point

from wildfireguardian_data.bounds import Bounds
from wildfireguardian_data.roads import (
    assess_road_network,
    attribute_availability,
    bridge_edges,
    build_road_graph,
    crossings_without_node,
    duplicate_geometry_edges,
    identify_exit_nodes,
)
from wildfireguardian_data.vector import Feature, VectorLayer

CRS = "EPSG:5187"
BOX = Bounds(0.0, 0.0, 3000.0, 3000.0, crs=CRS)


def layer(segments, name="roads"):
    """``[(id, [(x, y), ...], {tags})]`` -> VectorLayer."""
    features = []
    for entry in segments:
        segment_id, coords = entry[0], entry[1]
        tags = entry[2] if len(entry) > 2 else {}
        features.append(Feature(LineString(coords), {"segment_id": segment_id, **tags}))
    return VectorLayer(
        name=name,
        features=tuple(features),
        crs=CRS,
        provenance=make_provenance(name, value_unit="not_applicable"),
        feature_kind="road segment",
    )


# --------------------------------------------------------------------------- #
# D-0020 — shared-vertex noding
# --------------------------------------------------------------------------- #
def test_t_junction_at_an_interior_vertex_is_a_junction():
    """The Uljin defect, minimised.

    A side road meeting the *middle* of a through road is how OSM encodes a
    T-junction: both ways reference the same node, which is interior to the
    through road. Endpoint-only connection sees two components; the source says
    one.
    """
    through = [(0, 1000), (1000, 1000), (2000, 1000)]  # vertex at (1000, 1000)
    side = [(1000, 1000), (1000, 2000)]  # starts at that same vertex

    endpoint_only = build_road_graph(
        layer([("through", through), ("side", side)]), node_shared_vertices=False
    )
    assert endpoint_only.edge_count == 2
    import networkx as nx

    assert nx.number_connected_components(endpoint_only.graph) == 2  # the bug

    noded = build_road_graph(layer([("through", through), ("side", side)]))
    # The through road is split at the shared vertex: 3 edges, 4 nodes, 1 piece.
    assert noded.edge_count == 3
    assert nx.number_connected_components(noded.graph) == 1
    assert noded.shared_vertices_found == 1
    assert noded.shared_vertex_splits == 1
    # Total length is unchanged: topology only, no geometry invented.
    assert noded.total_length_m == pytest.approx(endpoint_only.total_length_m)


def test_shared_vertex_noding_preserves_total_length_exactly():
    segments = [
        ("a", [(0, 0), (500, 0), (1000, 0), (1500, 0)]),
        ("b", [(500, 0), (500, 800)]),
        ("c", [(1000, 0), (1000, -800)]),
    ]
    plain = build_road_graph(layer(segments), node_shared_vertices=False)
    noded = build_road_graph(layer(segments))
    assert noded.total_length_m == pytest.approx(plain.total_length_m)
    assert noded.edge_count > plain.edge_count


def test_crossing_without_a_shared_vertex_stays_unconnected():
    """D-0007 is not reopened: OSM encodes a bridge exactly this way."""
    import networkx as nx

    # The crossing point (500, 500) is in neither coordinate list.
    segments = [
        ("under", [(0, 500), (1000, 500)]),
        ("over", [(500, 0), (500, 1000)], {"bridge": "yes", "layer": "1"}),
    ]
    graph = build_road_graph(layer(segments))
    assert graph.shared_vertices_found == 0
    assert nx.number_connected_components(graph.graph) == 2
    diagnostic = crossings_without_node(graph)
    assert diagnostic["count"] == 1
    # The tags that let a reader tell grade separation from a missing junction.
    assert "yes" in diagnostic["samples"][0]["bridge_tags"]


def test_crossing_diagnostic_finds_a_bridge_that_rejoins_the_same_road():
    """The case the old diagnostic skipped, and the clearest real one.

    A bridge that crosses a road and then rejoins it *shares a node* with it, so
    a diagnostic that skipped node-sharing pairs missed exactly the grade
    separations it existed to find.
    """
    segments = [
        ("main", [(0, 500), (1000, 500), (2000, 500)]),
        # Leaves main at (2000,500), flies back west over it, lands north.
        ("flyover", [(2000, 500), (1000, 300), (0, 700)], {"bridge": "yes"}),
    ]
    graph = build_road_graph(layer(segments))
    diagnostic = crossings_without_node(graph)
    assert diagnostic["count"] >= 1
    assert any("yes" in sample["bridge_tags"] for sample in diagnostic["samples"])


def test_shared_vertex_comparison_matches_the_audited_uljin_numbers():
    """Pins the audit's headline result against the committed bundle."""
    from wildfireguardian_data.study_area import read_bundle

    bundle = read_bundle("data/study_areas/uljin_real_v1")
    roads = bundle.roads.layer

    plain = build_road_graph(roads, node_shared_vertices=False)
    noded = build_road_graph(roads)
    import networkx as nx

    assert nx.number_connected_components(plain.graph) == 22
    assert nx.number_connected_components(noded.graph) == 2
    assert noded.shared_vertices_found == 41
    assert noded.total_length_m == pytest.approx(plain.total_length_m)


# --------------------------------------------------------------------------- #
# D-0021 — exit semantics
# --------------------------------------------------------------------------- #
def test_a_road_crossing_the_boundary_is_an_exit_even_with_no_node_there():
    # The under-count: with clip_mode=intersects a leaving road has no node
    # near the boundary, so node proximity alone found almost none.
    graph = build_road_graph(
        layer([("leaving", [(1500, 1500), (1500, 2900), (1500, 3500)])])
    )
    exits = identify_exit_nodes(graph, study_bounds=BOX)
    assert len(exits.from_boundary_crossing) == 1
    assert exits.boundary_crossing_edges  # recorded for inspection
    # The departure node is the one INSIDE the study area.
    inside = next(iter(exits.from_boundary_crossing))
    x, y = graph.node_xy(inside)
    assert BOX.contains_point(x, y)


def test_a_tagged_exit_edge_contributes_one_exit_not_two():
    # The over-count: marking both endpoints would let a single tagged
    # through-road hide a genuinely single-egress component.
    graph = build_road_graph(
        layer(
            [
                ("spur", [(1000, 1500), (2000, 1500)], {"leaves_area": "yes"}),
                ("lane", [(2000, 1500), (2000, 2000)]),
            ]
        )
    )
    exits = identify_exit_nodes(graph, study_bounds=BOX, exit_property="leaves_area")
    assert len(exits.from_source_tags) == 2  # both endpoints are inside the box
    # ...but when one endpoint lies outside, only the inside one departs.
    graph2 = build_road_graph(
        layer([("out", [(1500, 1500), (1500, 3500)], {"leaves_area": "yes"})])
    )
    exits2 = identify_exit_nodes(graph2, study_bounds=BOX, exit_property="leaves_area")
    assert len(exits2.from_source_tags) == 1


def test_an_edge_merely_touching_the_boundary_is_not_a_crossing():
    # `crosses`, not `intersects`: touching the ring at an endpoint has not left
    # the area, and that endpoint is already covered by node proximity.
    graph = build_road_graph(layer([("access", [(0, 1500), (1000, 1500)])]))
    exits = identify_exit_nodes(graph, study_bounds=BOX)
    assert exits.from_boundary_crossing == frozenset()
    assert len(exits.from_boundary) == 1
    assert len(exits.all_exits) == 1


def test_exit_categories_are_reported_separately():
    graph = build_road_graph(
        layer(
            [
                ("touching", [(0, 1000), (800, 1000)]),
                ("crossing", [(2000, 1500), (2000, 3500)]),
            ]
        )
    )
    payload = identify_exit_nodes(graph, study_bounds=BOX).to_dict()
    assert payload["from_boundary"] and payload["from_boundary_crossing"]
    assert payload["clip_boundary_warning"] is True


# --------------------------------------------------------------------------- #
# Item 31 — adversarial critical-link cases, every expectation hand-derived
# --------------------------------------------------------------------------- #
def qa_for(segments, settlements):
    graph = build_road_graph(layer(segments))
    return graph, assess_road_network(
        graph, study_bounds=BOX, settlements=settlements, settlements_crs=CRS
    )


def test_critical_link_settlement_initially_disconnected():
    # No exit in its component -> cannot be "cut off" by any removal.
    _, qa = qa_for(
        [
            ("boundary", [(0, 1500), (1000, 1500)]),
            ("island", [(2000, 800), (2000, 1200)]),
        ],
        [("lonely", Point(2000, 1200))],
    )
    assert qa.critical_link_list == ()
    assert len(qa.no_egress_components) == 1


def test_critical_link_settlement_with_one_real_bridge():
    # Chain from a boundary-touching node to a hamlet: every chain edge is
    # critical. 2 edges -> 2 critical links.
    graph, qa = qa_for(
        [
            ("access", [(0, 1500), (1200, 1500)]),
            ("spur", [(1200, 1500), (1200, 2200)]),
        ],
        [("hamlet", Point(1200, 2200))],
    )
    assert len(qa.critical_link_list) == 2
    node = qa.settlements.node_by_settlement["hamlet"]
    for link in qa.critical_link_list:
        assert node in link["settlement_nodes_cut_off"]


def test_critical_link_two_parallel_bridges_are_not_critical():
    # Two distinct roads between the same junctions are two ways out.
    graph, qa = qa_for(
        [
            ("access", [(0, 1500), (1000, 1500)]),
            ("route_a", [(1000, 1500), (1500, 1800), (2000, 1500)]),
            ("route_b", [(1000, 1500), (1500, 1200), (2000, 1500)]),
            ("village", [(2000, 1500), (2200, 1500)]),
        ],
        [("hamlet", Point(2200, 1500))],
    )
    critical_edges = {tuple(link["edge"]) for link in qa.critical_link_list}
    parallel = {tuple(pair["edge"]) for pair in qa.parallel_edge_pairs}
    assert parallel, "the two routes must register as parallel edges"
    # Neither parallel edge may be critical: removing one leaves the other.
    assert not (critical_edges & parallel)
    # The access road and the village stub still are.
    assert len(qa.critical_link_list) == 2


def test_critical_link_dangling_irrelevant_bridge_is_not_reported():
    # A bridge in a part of the network no settlement depends on.
    _, qa = qa_for(
        [
            ("access", [(0, 1500), (1000, 1500)]),
            ("village", [(1000, 1500), (1000, 2000)]),
            ("dangling", [(1000, 1500), (1800, 1500)]),  # leads nowhere
        ],
        [("hamlet", Point(1000, 2000))],
    )
    critical = {tuple(link["edge"]) for link in qa.critical_link_list}
    assert len(critical) == 2  # access + village stub, not the dangling spur
    assert len(bridge_edges(_)) == 3  # all three ARE bridges...
    # ...but only two of them cut the settlement off, which is the distinction.


def test_critical_link_unrelated_disconnected_component_is_not_reported():
    _, qa = qa_for(
        [
            ("access", [(0, 1500), (1000, 1500)]),
            ("village", [(1000, 1500), (1000, 2000)]),
            ("far_a", [(2500, 500), (2700, 500)]),
            ("far_b", [(2700, 500), (2700, 700)]),
        ],
        [("hamlet", Point(1000, 2000))],
    )
    critical = {tuple(link["edge"]) for link in qa.critical_link_list}
    assert len(critical) == 2
    # No edge from the far component appears.
    for link in qa.critical_link_list:
        for node in link["edge"]:
            x, y = _.node_xy(node)
            assert x < 2000


def test_critical_link_via_a_boundary_crossing_egress():
    """Exactly one edge is critical here, and the reason matters.

    Nodes: A=(1500,3400) outside the box, B=(1500,2600) inside, C=(1500,2000)
    the hamlet. Edge ``out`` is A-B and crosses the boundary, so **B** is the
    exit -- the departure node, the endpoint inside the area (D-0021), not A.
    Removing ``link`` (B-C) cuts the hamlet off from B, so it is critical.
    Removing ``out`` (A-B) does not: C is still connected to B, which is itself
    the exit. So one critical link, not two.
    """
    graph, qa = qa_for(
        [
            ("out", [(1500, 2600), (1500, 3400)]),
            ("link", [(1500, 2600), (1500, 2000)]),
        ],
        [("hamlet", Point(1500, 2000))],
    )
    assert len(qa.exits.from_boundary_crossing) == 1
    exit_node = next(iter(qa.exits.from_boundary_crossing))
    assert BOX.contains_point(*graph.node_xy(exit_node))

    critical = {tuple(link["edge"]) for link in qa.critical_link_list}
    assert len(critical) == 1
    hamlet_node = qa.settlements.node_by_settlement["hamlet"]
    assert critical == {tuple(sorted((exit_node, hamlet_node)))}


def test_directionality_does_not_affect_this_undirected_diagnostic():
    # A-RD-1: the QA graph is undirected. A oneway tag changes nothing here, and
    # that is a documented limitation rather than a silent one.
    plain = qa_for(
        [
            ("access", [(0, 1500), (1000, 1500)]),
            ("spur", [(1000, 1500), (1000, 2000)]),
        ],
        [("hamlet", Point(1000, 2000))],
    )[1]
    oneway = qa_for(
        [
            ("access", [(0, 1500), (1000, 1500)], {"oneway": "yes"}),
            ("spur", [(1000, 1500), (1000, 2000)], {"oneway": "-1"}),
        ],
        [("hamlet", Point(1000, 2000))],
    )[1]
    assert len(plain.critical_link_list) == len(oneway.critical_link_list)
    # The tags are still carried, so a directed consumer can use them.
    availability = oneway.attribute_availability_report
    assert availability["oneway"]["edges_with_value"] == 2


# --------------------------------------------------------------------------- #
# QA extensions
# --------------------------------------------------------------------------- #
def test_attribute_availability_reports_absence_without_defaulting():
    graph = build_road_graph(
        layer([("a", [(0, 0), (100, 0)], {"highway": "track", "surface": "gravel"})])
    )
    availability = attribute_availability(graph)
    assert availability["surface"]["edges_with_value"] == 1
    assert availability["surface"]["values"] == {"gravel": 1}
    # Absent stays absent: no per-class default is invented.
    assert availability["lanes"]["edges_with_value"] == 0
    assert availability["lanes"]["values"] == {}
    assert "D-0022" in availability["_note"]


def test_duplicate_geometry_is_distinguished_from_parallel_edges():
    # Same road entered twice, the second time digitised backwards.
    graph = build_road_graph(
        layer(
            [
                ("once", [(0, 0), (500, 100), (1000, 0)]),
                ("twice", [(1000, 0), (500, 100), (0, 0)]),
            ]
        )
    )
    duplicates = duplicate_geometry_edges(graph)
    # Both copies get split at (500, 100) -- a vertex they share -- so the pair
    # becomes two duplicated halves. Two groups of multiplicity 2, not one of 2.
    assert len(duplicates) == 2
    assert all(group["multiplicity"] == 2 for group in duplicates)

    # Two genuinely different roads between the same junctions are NOT duplicates.
    distinct = build_road_graph(
        layer(
            [
                ("north", [(0, 0), (500, 200), (1000, 0)]),
                ("south", [(0, 0), (500, -200), (1000, 0)]),
            ]
        )
    )
    assert duplicate_geometry_edges(distinct) == []


def test_largest_component_fraction_is_reported():
    _, qa = qa_for(
        [
            ("main_a", [(0, 1500), (1000, 1500)]),
            ("main_b", [(1000, 1500), (2000, 1500)]),
            ("orphan", [(2500, 500), (2600, 500)]),
        ],
        [],
    )
    largest = qa.largest_component
    assert largest["edge_count"] == 2
    assert largest["edge_fraction"] == pytest.approx(2 / 3, abs=1e-4)


def test_density_reports_an_empty_quadrant_without_explaining_it():
    _, qa = qa_for([("only_ne", [(1600, 1600), (2900, 2900)])], [])
    density = qa.density
    assert density["available"] is True
    assert set(density["empty_quadrants"]) == {"north_west", "south_west", "south_east"}
    assert "cannot distinguish" in density["note"]


def test_grade_separation_lists_tagged_bridges_and_tunnels():
    _, qa = qa_for(
        [
            ("br", [(0, 1500), (500, 1500)], {"bridge": "yes", "layer": "1"}),
            ("tu", [(500, 1500), (900, 1500)], {"tunnel": "yes", "layer": "-1"}),
            ("plain", [(900, 1500), (1400, 1500)]),
        ],
        [],
    )
    assert qa.grade_separation["bridge_count"] == 1
    assert qa.grade_separation["tunnel_count"] == 1


def test_build_diagnostics_expose_how_the_graph_was_made():
    _, qa = qa_for(
        [("a", [(0, 1000), (1000, 1000), (2000, 1000)]), ("b", [(1000, 1000), (1000, 2000)])],
        [],
    )
    diagnostics = qa.build_diagnostics
    assert diagnostics["shared_vertices_found"] == 1
    assert diagnostics["shared_vertex_splits"] == 1
    assert "D-0020" in diagnostics["note"]
