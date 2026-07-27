# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""多 Key 轮转器 — 当厂商返回 429 时自动切换到下一个可用 Key"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_BACKOFF_SECONDS = 60


class KeyRotator:
    """管理单个 Provider 的多个 API Key 轮转

    - 正常情况按 round-robin 轮转
    - 遇到 429 时将当前 key 加入冷却，自动跳到下一个
    - 冷却 60 秒后 key 自动恢复
    """

    def __init__(self, provider: str, keys: list[str]):
        self._provider = provider
        self._keys = [k for k in keys if k.strip()]
        self._index = 0
        self._lock = threading.Lock()
        self._cooldowns: dict[int, float] = {}

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def get_key(self) -> str:
        """获取当前可用的 Key（跳过冷却中的 Key）"""
        if not self._keys:
            return ""
        with self._lock:
            now = time.time()
            for _ in range(len(self._keys)):
                idx = self._index % len(self._keys)
                cooldown_until = self._cooldowns.get(idx, 0)
                if now >= cooldown_until:
                    return self._keys[idx]
                self._index += 1
            logger.warning("[%s] 所有 %d 个 Key 均在冷却中，返回第一个", self._provider, len(self._keys))
            return self._keys[0]

    def rotate(self) -> str:
        """轮转到下一个 Key 并返回"""
        if not self._keys:
            return ""
        with self._lock:
            self._index = (self._index + 1) % len(self._keys)
            return self.get_key()

    def mark_rate_limited(self) -> None:
        """将当前 Key 标记为限流，冷却 60 秒并自动切换"""
        if not self._keys:
            return
        with self._lock:
            idx = self._index % len(self._keys)
            self._cooldowns[idx] = time.time() + _BACKOFF_SECONDS
            logger.info(
                "[%s] Key #%d 被限流，冷却 %ds，切换到下一个",
                self._provider, idx, _BACKOFF_SECONDS,
            )
            self._index = (self._index + 1) % len(self._keys)

    def get_status(self) -> dict:
        """返回所有 Key 的状态"""
        now = time.time()
        statuses = []
        for i, key in enumerate(self._keys):
            masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
            cooldown_until = self._cooldowns.get(i, 0)
            is_cooling = now < cooldown_until
            statuses.append({
                "index": i,
                "key_masked": masked,
                "is_current": i == (self._index % len(self._keys)),
                "cooling_down": is_cooling,
                "cool_remaining_s": max(0, int(cooldown_until - now)) if is_cooling else 0,
            })
        return {
            "provider": self._provider,
            "total_keys": len(self._keys),
            "keys": statuses,
        }
