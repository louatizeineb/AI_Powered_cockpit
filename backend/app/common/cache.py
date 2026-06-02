from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.config import get_settings

try:
    from redis import Redis
except ImportError:  # Redis stays optional until the local service is enabled.
    Redis = None


@lru_cache(maxsize=1)
def _client():
    settings = get_settings()
    if Redis is None or not settings.REDIS_URL:
        return None
    return Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=0.15,
        socket_timeout=0.15,
    )


def get_json(key: str) -> Any | None:
    client = _client()
    if client is None:
        return None
    try:
        payload = client.get(key)
        return json.loads(payload) if payload is not None else None
    except Exception:
        return None


def set_json(key: str, value: Any, ttl_seconds: int) -> bool:
    client = _client()
    if client is None:
        return False
    try:
        client.setex(key, ttl_seconds, json.dumps(value, ensure_ascii=False))
        return True
    except Exception:
        return False
