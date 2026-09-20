"""Roads: graph ingestion and topology QA.

Topology QA of the input data only -- never routing, never a safety claim.
See ``docs/SCOPE.md`` and ``docs/DECISIONS.md`` D-0007, D-0008.
"""

from __future__ import annotations

from .graph import DEFAULT_SNAP_TOLERANCE_M, RoadGraph, build_road_graph
from .io import read_road_geojson, read_road_vector
from .qa import (
    DEFAULT_BOUNDARY_TOLERANCE_M,
    DEFAULT_SETTLEMENT_SNAP_M,
    ROAD_ATTRIBUTES_OF_INTEREST,
    SAFETY_DISCLAIMER,
    ExitNodeSet,
    RoadNetworkQA,
    SettlementNodeSet,
    articulation_points,
    assess_road_network,
    attribute_availability,
    bridge_edges,
    critical_links,
    crossings_without_node,
    duplicate_geometry_edges,
    grade_separation_edges,
    identify_exit_nodes,
    identify_settlement_nodes,
    network_density,
)

__all__ = [
    "RoadGraph",
    "build_road_graph",
    "DEFAULT_SNAP_TOLERANCE_M",
    "read_road_geojson",
    "read_road_vector",
    "assess_road_network",
    "RoadNetworkQA",
    "ExitNodeSet",
    "SettlementNodeSet",
    "identify_exit_nodes",
    "identify_settlement_nodes",
    "articulation_points",
    "bridge_edges",
    "critical_links",
    "crossings_without_node",
    "SAFETY_DISCLAIMER",
    "DEFAULT_BOUNDARY_TOLERANCE_M",
    "DEFAULT_SETTLEMENT_SNAP_M",
    "attribute_availability",
    "duplicate_geometry_edges",
    "grade_separation_edges",
    "network_density",
    "ROAD_ATTRIBUTES_OF_INTEREST",
]
