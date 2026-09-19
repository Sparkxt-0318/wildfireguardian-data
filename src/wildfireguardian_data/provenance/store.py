"""Reading and writing provenance sidecars.

One JSON file per layer, under ``<bundle>/provenance/<layer>.provenance.json``
(``docs/INTERFACES.md``). Sidecars rather than embedded metadata so that
provenance is reviewable in a diff and survives a format change in the data
file it describes.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..errors import ProvenanceError
from .models import ProvenanceRecord

__all__ = [
    "provenance_path",
    "write_provenance",
    "read_provenance",
    "read_all_provenance",
]


def provenance_path(bundle_dir: str | Path, layer_name: str) -> Path:
    """Path of the provenance sidecar for ``layer_name`` inside a bundle."""
    return Path(bundle_dir) / "provenance" / f"{layer_name}.provenance.json"


def write_provenance(
    bundle_dir: str | Path, record: ProvenanceRecord, *, overwrite: bool = True
) -> Path:
    """Write one provenance sidecar; returns the path written."""
    path = provenance_path(bundle_dir, record.layer_name)
    if path.exists() and not overwrite:
        raise ProvenanceError(f"provenance already exists and overwrite=False: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8")
    return path


def read_provenance(bundle_dir: str | Path, layer_name: str) -> ProvenanceRecord:
    """Read one provenance sidecar, failing loudly if absent or unreadable."""
    path = provenance_path(bundle_dir, layer_name)
    if not path.exists():
        raise ProvenanceError(
            f"no provenance sidecar for layer {layer_name!r} at {path}. "
            "a layer without provenance is not usable in this package "
            "(AGENTS.md §7)."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProvenanceError(f"provenance sidecar {path} is not valid JSON: {exc}") from exc
    return ProvenanceRecord.from_dict(payload)


def read_all_provenance(bundle_dir: str | Path) -> dict[str, ProvenanceRecord]:
    """Read every provenance sidecar in a bundle, keyed by layer name."""
    directory = Path(bundle_dir) / "provenance"
    if not directory.is_dir():
        return {}
    out: dict[str, ProvenanceRecord] = {}
    for path in sorted(directory.glob("*.provenance.json")):
        layer = path.name[: -len(".provenance.json")]
        record = read_provenance(bundle_dir, layer)
        if record.layer_name != layer:
            raise ProvenanceError(
                f"provenance sidecar {path.name} declares layer_name "
                f"{record.layer_name!r}; filename and record must agree."
            )
        out[layer] = record
    return out
