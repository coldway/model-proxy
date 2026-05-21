# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""状态后端抽象层

当前使用本地内存+文件持久化（单 Worker 模式）。
切换为 Redis 后端即可支持多 Worker 水平扩展。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StateBackend(ABC):
    """状态存储抽象基类

    实现者负责管理以下状态：
    - 速率限制计数器（RateLimiter）
    - 会话绑定（session_bindings）
    - 路由缓存（route_cache）
    - 成本追踪（cost_tracking）
    """

    @abstractmethod
    def get(self, namespace: str, key: str) -> Any:
        ...

    @abstractmethod
    def set(self, namespace: str, key: str, value: Any, ttl: int = 0) -> None:
        ...

    @abstractmethod
    def delete(self, namespace: str, key: str) -> bool:
        ...

    @abstractmethod
    def incr(self, namespace: str, key: str, amount: int = 1) -> int:
        ...

    @abstractmethod
    def get_all(self, namespace: str) -> dict[str, Any]:
        ...


class LocalMemoryBackend(StateBackend):
    """本地内存后端（当前默认，单进程安全）"""

    def __init__(self):
        self._store: dict[str, dict[str, Any]] = {}

    def get(self, namespace: str, key: str) -> Any:
        return self._store.get(namespace, {}).get(key)

    def set(self, namespace: str, key: str, value: Any, ttl: int = 0) -> None:
        self._store.setdefault(namespace, {})[key] = value

    def delete(self, namespace: str, key: str) -> bool:
        ns = self._store.get(namespace, {})
        if key in ns:
            del ns[key]
            return True
        return False

    def incr(self, namespace: str, key: str, amount: int = 1) -> int:
        ns = self._store.setdefault(namespace, {})
        ns[key] = ns.get(key, 0) + amount
        return ns[key]

    def get_all(self, namespace: str) -> dict[str, Any]:
        return dict(self._store.get(namespace, {}))


class RedisBackend(StateBackend):
    """Redis 后端（多 Worker 模式）

    TODO: 实现 Redis 连接和操作
    需要安装: pip install redis[hiredis]

    使用方式:
        backend = RedisBackend(url="redis://localhost:6379/0")
        rate_limiter = RateLimiter(backend=backend)
    """

    def __init__(self, url: str = "redis://localhost:6379/0", prefix: str = "mp:"):
        self._url = url
        self._prefix = prefix
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            try:
                import redis
                self._client = redis.Redis.from_url(self._url, decode_responses=True)
            except ImportError:
                raise RuntimeError("需要安装 redis: pip install redis[hiredis]")

    def _full_key(self, namespace: str, key: str) -> str:
        return f"{self._prefix}{namespace}:{key}"

    def get(self, namespace: str, key: str) -> Any:
        self._ensure_client()
        import json
        raw = self._client.get(self._full_key(namespace, key))
        return json.loads(raw) if raw else None

    def set(self, namespace: str, key: str, value: Any, ttl: int = 0) -> None:
        self._ensure_client()
        import json
        full_key = self._full_key(namespace, key)
        self._client.set(full_key, json.dumps(value, ensure_ascii=False))
        if ttl > 0:
            self._client.expire(full_key, ttl)

    def delete(self, namespace: str, key: str) -> bool:
        self._ensure_client()
        return bool(self._client.delete(self._full_key(namespace, key)))

    def incr(self, namespace: str, key: str, amount: int = 1) -> int:
        self._ensure_client()
        return self._client.incrby(self._full_key(namespace, key), amount)

    def get_all(self, namespace: str) -> dict[str, Any]:
        self._ensure_client()
        import json
        pattern = f"{self._prefix}{namespace}:*"
        result = {}
        for full_key in self._client.scan_iter(pattern, count=100):
            short_key = full_key.replace(f"{self._prefix}{namespace}:", "", 1)
            raw = self._client.get(full_key)
            result[short_key] = json.loads(raw) if raw else None
        return result
