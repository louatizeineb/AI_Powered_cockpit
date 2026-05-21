from __future__ import annotations
import re
import unicodedata
from typing import Any


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = strip_accents(text)
    text = text.replace("[", "").replace("]", "")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or None


def split_tokens(value: Any) -> list[str]:
    norm = normalize_text(value)
    if not norm:
        return []
    tokens = set([norm])
    tokens.update(t for t in re.split(r"[_\s]+", norm) if t)
    return sorted(tokens)
