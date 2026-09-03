"""Redis 缓存。只用于 LLM 补全结果缓存;连接失败时静默降级(缓存不影响主流程)。"""

import contextlib
import json
from typing import Any

import redis

from python_backend.settings import settings


class RedisCache:
    def __init__(self, host: str = settings.redis_host, port: int = settings.redis_port) -> None:
        self._client = redis.Redis(
            host=host,
            port=port,
            decode_responses=True,
            socket_connect_timeout=1.5,
            socket_timeout=1.5,
        )

    def get_json(self, key: str) -> Any | None:
        try:
            raw = self._client.get(key)
            return json.loads(raw) if raw is not None else None
        except Exception:
            return None

    def set_json(self, key: str, value: Any, ttl: int) -> None:
        with contextlib.suppress(Exception):
            self._client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)


redis_cache = RedisCache()
