from __future__ import annotations
import re
from typing import Any
from app.common.text import normalize_text, split_tokens


def split_path_full(path_full: Any) -> list[str]:
    if path_full is None:
        return []
    raw = str(path_full).strip()
    if not raw:
        return []
    parts = re.split(r"[\\/]+", raw)
    return [p for p in (normalize_text(x) for x in parts) if p]


def parse_path_full(path_full: Any) -> dict:
    segments = split_path_full(path_full)
    tokens: set[str] = set()
    for segment in segments:
        tokens.add(segment)
        tokens.update(split_tokens(segment))

    return {
        "raw_path_full": path_full,
        "path_segments": segments,
        "path_tokens": sorted(tokens),
        "normalized_path": " ".join(segments),
        "app_code_from_path": segments[0].upper() if segments else None,
        "leaf_name": segments[-1] if segments else None,
        "parent_name": segments[-2] if len(segments) >= 2 else None,
        "path_depth": len(segments),
    }


def build_embedding_text(row: dict) -> str:
    parts = [
        row.get("entity_level"),
        row.get("app_code_from_path"),
        row.get("normalized_path"),
        row.get("leaf_name"),
        row.get("parent_name"),
        " ".join(row.get("path_tokens") or []),
    ]
    return " | ".join(str(p) for p in parts if p)
