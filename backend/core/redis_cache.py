"""Shared Redis for cached display data. Optional: with no REDIS_URL, or when Redis is down,
reads miss and writes are skipped, so callers always fall back to the database."""
import json
from typing import Any, Optional

import redis.asyncio as redis

from core.config import settings
from core.logging import get_structured_logger

logger = get_structured_logger(__name__)

_client: Optional[redis.Redis] = None


def key(name: str) -> str:
    """Namespaced key, so dev and production can share one Redis without reading each other's data."""
    return f"banwee:{settings.ENVIRONMENT}:{name}"


def _get_client() -> Optional[redis.Redis]:
    global _client
    if _client is None and settings.REDIS_URL:
        # Short timeouts: a slow or unreachable Redis must never be slower than the database fallback.
        _client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1, decode_responses=True)
    return _client


async def get_json(name: str) -> Optional[Any]:
    client = _get_client()
    if client is None:
        return None
    try:
        raw = await client.get(key(name))
        return json.loads(raw) if raw is not None else None
    except Exception as e:
        logger.warning(f"Redis read of {name} failed: {e}")
        return None


async def set_json(name: str, value: Any, ttl_seconds: Optional[int] = None) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        await client.set(key(name), json.dumps(value), ex=ttl_seconds)
        return True
    except Exception as e:
        logger.warning(f"Redis write of {name} failed: {e}")
        return False


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
