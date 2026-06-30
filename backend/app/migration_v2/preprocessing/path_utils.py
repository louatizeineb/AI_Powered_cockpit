from __future__ import annotations

import hashlib


def stable_path_hash(path_full: str | None) -> str | None:
    """Return a stable hash for a DataGalaxy path."""

    if not path_full:
        return None
    return hashlib.sha256(path_full.encode("utf-8")).hexdigest()
