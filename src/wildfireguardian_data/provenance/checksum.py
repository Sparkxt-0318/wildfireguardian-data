"""SHA-256 checksums for files, arrays, and JSON-serialisable structures.

Checksums are how this package makes "the same inputs reproduce the same
outputs" checkable rather than asserted (``docs/ASSUMPTIONS.md`` A-REP-2/3).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

__all__ = [
    "CHECKSUM_ALGORITHM",
    "sha256_file",
    "sha256_bytes",
    "sha256_array",
    "sha256_json",
]

CHECKSUM_ALGORITHM = "sha256"

_CHUNK = 1024 * 1024


def sha256_bytes(payload: bytes) -> str:
    """Hex SHA-256 of a byte string."""
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Hex SHA-256 of a file's bytes, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_array(array: Any) -> str:
    """Hex SHA-256 of a NumPy array's dtype, shape, and buffer.

    ``dtype`` and ``shape`` are hashed alongside the bytes because identical
    bytes can mean different data: a ``(4, 4)`` float32 array and an ``(8, 8)``
    uint8 array can share a buffer, and so can a transposed view. Hashing the
    buffer alone would call those equal.

    ``NaN`` is handled by hashing raw bytes, so two ``NaN``-containing arrays
    with the same bit patterns hash equal even though ``NaN != NaN``. Distinct
    ``NaN`` payloads (rare, but produced by some GDAL paths) hash differently,
    which is the honest answer: the files genuinely differ.
    """
    import numpy as np

    arr = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(arr.dtype.str).encode("utf-8"))
    digest.update(b"|")
    digest.update(str(arr.shape).encode("utf-8"))
    digest.update(b"|")
    digest.update(arr.tobytes(order="C"))
    return digest.hexdigest()


def sha256_json(payload: Any) -> str:
    """Hex SHA-256 of a JSON-serialisable structure, key-order independent.

    ``sort_keys=True`` makes the digest independent of dict insertion order, so
    a manifest that merely reordered its keys does not read as changed content.
    """
    text = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return sha256_bytes(text.encode("utf-8"))
