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
    SAFETY_DISCLAIMER,
    ExitNodeSet,
    RoadNetworkQA,
    SettlementNodeSet,
    articulation_points,
    assess_road_network,
    bridge_edges,
    critical_links,
    crossings_without_node,
    identify_exit_nodes,
    identify_settlement_nodes,
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
]
