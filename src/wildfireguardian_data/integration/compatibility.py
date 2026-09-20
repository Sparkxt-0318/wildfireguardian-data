"""Readiness of a bundle for a named consumer — completeness, not validity.

Phase 2 items 22, 23, 24 and 28. Three consumers are described, as **adapter
contracts only**: what inputs each needs from a landscape-data foundation, and
what this repository will not supply.

The one thing this module is careful never to do
------------------------------------------------
``READY`` means *every input the consumer named is present, and carries the
provenance needed to interpret it*. It does **not** mean the data is accurate,
the resolution is adequate, the sources are authoritative, or that any result
computed from it would be correct. A bundle of explicitly synthetic fixtures can
be ``READY`` for a consumer — that is the point of a fixture, and it is why
``data_classes_present`` and ``planner_legal`` sit next to the verdict.

Phase 2 item 28 puts it directly: report readiness "without claiming scientific
validity". So the report states what is present and what is missing, and stops.
"""

from __future__ import annotations

from typing import Any

from ..provenance.models import UNKNOWN, DataClass, governance_class_name
from ..study_area.bundle import StudyAreaBundle

__all__ = [
    "CONSUMER_PROFILES",
    "ReadinessLevel",
    "build_compatibility_report",
]


class ReadinessLevel:
    """How far a bundle gets towards one consumer's declared input list."""

    #: Every required input is present with interpretable provenance.
    READY = "READY"
    #: Every required input is present, but at least one carries a limitation
    #: the consumer should read before using it.
    READY_WITH_LIMITATIONS = "READY_WITH_LIMITATIONS"
    #: At least one required input is absent. Named, not counted.
    INCOMPLETE = "INCOMPLETE"
    #: The consumer needs something this repository will not provide at all.
    OUT_OF_SCOPE_FOR_THIS_REPOSITORY = "OUT_OF_SCOPE_FOR_THIS_REPOSITORY"


#: What each consumer needs from a *landscape data* foundation, and what it must
#: bring itself. The ``not_supplied_here`` lists are the load-bearing half:
#: they are how this repository says no to scope creep in a machine-readable
#: way, instead of in a paragraph somebody has to find.
CONSUMER_PROFILES: dict[str, dict[str, Any]] = {
    "FORECAST_VALUE": {
        "description": (
            "the main WildfireGuardian repository's forecast-value experiment "
            "(Phase 2 item 22). consumes immutable landscape inputs for one "
            "study area."
        ),
        "requires": ("terrain", "roads", "fuels", "facilities", "population"),
        "requires_detail": {
            "terrain": "elevation, plus slope and aspect if it does not compute them",
            "roads": "line geometry and a graph with topology counts",
            "fuels": "a categorical vegetation/land-cover raster and its class scheme",
            "facilities": "candidate destination points, existence only",
            "population": "aggregate counts by settlement, never per-household",
        },
        "not_supplied_here": (
            "weather, fuel moisture, ignition points, fire perimeters, any "
            "forecast, and any crosswalk from land cover to a fire-behaviour "
            "fuel model (docs/DECISIONS.md D-0024)",
            "the study-area boundary as a *decision*: the bounds are what the "
            "config asked for, and egress counts depend on where it was cut "
            "(docs/ASSUMPTIONS.md A-RD-4)",
        ),
    },
    "OSSE": {
        "description": (
            "observing-system simulation experiments (Phase 2 item 23). "
            "consumes the immutable landscape only."
        ),
        "requires": ("terrain", "fuels"),
        "requires_detail": {
            "terrain": "elevation on a stated canonical grid",
            "fuels": "a categorical raster co-registered to that same grid",
        },
        "not_supplied_here": (
            "the nature model's dynamic state -- fire spread, fuel moisture "
            "evolution, smoke, or anything that changes during a simulated "
            "event. an OSSE's truth run is the consumer's, not this "
            "repository's (docs/SCOPE.md)",
            "observation operators, sensor models, and observation error "
            "covariances",
            "any ORACLE_ONLY quantity. this repository emits none, and refuses "
            "to label one planner-legal (docs/DECISIONS.md D-0023)",
        ),
    },
    "ASSISTED_DISPATCH": {
        "description": (
            "assisted-dispatch work (Phase 2 item 24). contract only: this "
            "repository supplies the static network and facility existence."
        ),
        "requires": ("roads", "facilities"),
        "requires_detail": {
            "roads": (
                "line geometry, a graph, and per-edge source attributes where "
                "the source had them -- absent where it did not, never "
                "defaulted by road class (D-0022)"
            ),
            "facilities": (
                "responder bases and candidate destinations, as existence and "
                "location. capabilities are left unknown where unknown"
            ),
        },
        "not_supplied_here": (
            "mission logic of any kind: routing, rescue assignment, dispatch "
            "timing, vehicle models, or travel-time estimates. edge lengths "
            "here are planar geometry, not travel distances "
            "(docs/ASSUMPTIONS.md A-RD-5)",
            "per-class defaults for lanes, surface, width or maxspeed. those "
            "are modelling parameters and belong in the consumer's own "
            "declared parameter layer (D-0022)",
            "any statement that a road is passable or a facility is a viable "
            "refuge. this repository reports topology and existence, and "
            "nothing downstream of them (AGENTS.md section 5)",
        ),
    },
}

#: Limitation tags that a consumer must read but that do not make a bundle
#: unusable. Distinguished from the rest so ``READY_WITH_LIMITATIONS`` means
#: something specific rather than "there was a warning somewhere".
_MATERIAL_LIMITATIONS = frozenset(
    {
        "SURFACE_MODEL_NOT_TERRAIN",
        "TEMPORAL_VALIDITY_NOT_ESTABLISHED",
        "LICENCE_UNKNOWN",
        "NO_NODATA_DECLARED",
        "NO_SOURCE_RECORDED",
    }
)
# Deliberately NOT material: ``REPROJECTED_ONTO_ITS_OWN_GRID``. It is a true
# fact and worth recording per layer, but it is *expected* of the layer that
# defines the canonical grid -- the DEM is reprojected first and every sibling
# is then warped onto it (D-0018). Treating it as material would flag every
# bundle for doing the right thing. What actually matters is whether the
# rasters ended up on the same grid, and the manifest's ``canonical_grid``
# answers that directly, so that is what is checked below instead.

#: Profiles that index two or more rasters by the same (row, col) and therefore
#: depend on them sharing a grid.
_CO_REGISTRATION_SENSITIVE = frozenset({"FORECAST_VALUE", "OSSE"})


def _slot_report(
    profile_name: str, slot: str, manifest_layers: dict[str, Any]
) -> dict[str, Any]:
    entry = manifest_layers[slot]
    if entry["status"] != "PRESENT":
        return {
            "slot": slot,
            "status": "ABSENT",
            "reason": entry.get("reason", UNKNOWN),
            "limitations": [],
        }
    limitations = sorted(
        {
            tag
            for layer in entry["layers"]
            for tag in layer.get("limitations", ())
        }
    )
    out = {
        "slot": slot,
        "status": "PRESENT",
        "reason": "not_applicable",
        "layer_names": [layer["name"] for layer in entry["layers"]],
        "limitations": limitations,
        "material_limitations": sorted(set(limitations) & _MATERIAL_LIMITATIONS),
    }
    # Fuels has one extra requirement no other slot has: integers with no
    # scheme are meaningless, so a missing scheme makes the slot unusable even
    # though the raster is present.
    if slot == "fuels":
        scheme = entry.get("class_scheme", {})
        out["class_scheme_present"] = scheme.get("status") == "PRESENT"
        out["class_scheme_kind"] = scheme.get("scheme_kind", UNKNOWN)
        if not out["class_scheme_present"]:
            out["status"] = "PRESENT_BUT_UNUSABLE"
            out["reason"] = (
                "fuel raster has no class scheme, so its integers have no "
                "meaning (validation BND-005)"
            )
    return out


def build_compatibility_report(
    bundle: StudyAreaBundle, manifest: dict[str, Any]
) -> dict[str, Any]:
    """Report, per consumer profile, what is present and what is missing.

    Takes the canonical manifest rather than re-deriving the facts, so the
    readiness verdict and the contract a consumer reads cannot disagree.
    """
    manifest_layers = manifest["layers"]
    profiles: dict[str, Any] = {}
    for name, profile in CONSUMER_PROFILES.items():
        slots = [
            _slot_report(name, slot, manifest_layers) for slot in profile["requires"]
        ]
        missing = [s["slot"] for s in slots if s["status"] != "PRESENT"]
        material = sorted(
            {tag for s in slots for tag in s.get("material_limitations", ())}
        )
        # A grid mismatch is a bundle-level fact, not a per-layer one: no
        # single raster is at fault, and only a consumer that indexes two of
        # them together is affected.
        grid_status = manifest.get("canonical_grid", {}).get("status")
        if name in _CO_REGISTRATION_SENSITIVE and grid_status == "MISMATCHED":
            material = sorted({*material, "CANONICAL_GRID_MISMATCHED"})
        if missing:
            level = ReadinessLevel.INCOMPLETE
        elif material:
            level = ReadinessLevel.READY_WITH_LIMITATIONS
        else:
            level = ReadinessLevel.READY
        profiles[name] = {
            "description": profile["description"],
            "readiness": level,
            "required_inputs": list(profile["requires"]),
            "input_detail": dict(profile["requires_detail"]),
            "inputs": slots,
            "missing_inputs": missing,
            "material_limitations": material,
            "not_supplied_here": list(profile["not_supplied_here"]),
        }

    classes = sorted(
        {governance_class_name(r.data_class) for r in bundle.provenance.values()}
    )
    return {
        "bundle_schema_version": manifest["bundle_schema_version"],
        "bundle_id": manifest["bundle_id"],
        "profiles": profiles,
        "governance": {
            "data_classes_present": classes,
            "all_layers_planner_legal": all(
                r.data_class.planner_legal for r in bundle.provenance.values()
            ),
            "not_planner_legal": sorted(
                {
                    r.layer_name
                    for r in bundle.provenance.values()
                    if not r.data_class.planner_legal
                }
            ),
            "contains_synthetic": any(
                r.data_class is DataClass.SYNTHETIC
                for r in bundle.provenance.values()
            ),
        },
        "caveats": [
            "READY means every input the consumer named is present with "
            "interpretable provenance. it does NOT mean the data is accurate, "
            "the resolution adequate, the sources authoritative, or any result "
            "computed from it correct. a bundle of explicitly synthetic "
            "fixtures can be READY, which is what a fixture is for",
            "readiness is a statement about completeness. this repository "
            "makes no claim of scientific validity for any bundle "
            "(Phase 2 item 28)",
            "the not_supplied_here lists are part of the contract, not a "
            "to-do list. those items belong to the consumer or to another "
            "repository (docs/SCOPE.md)",
        ],
    }
