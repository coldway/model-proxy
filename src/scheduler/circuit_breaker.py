# Created by model-proxy on 2026/05/17
# Copyright © 2026

"""厂商级熔断器

在滑动窗口（120s）内连续失败达到阈值时触发熔断，
冷却期间该厂商所有模型不可用，冷却结束后自动恢复。
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 3
_DEFAULT_COOLDOWN = 300
_WINDOW_SECONDS = 120

# 不应触发熔断的 HTTP 状态码（客户端请求自身问题，非厂商故障）
CLIENT_ERROR_NO_BREAKER = frozenset({400, 404, 413, 414, 415, 422})


def should_trigger_breaker(status_code: int) -> bool:
    """判断 HTTP 状态码是否应触发熔断计数。"""
    return status_code not in CLIENT_ERROR_NO_BREAKER


class CircuitBreaker:
    """厂商级熔断器"""

    def __init__(
        self,
        *,
        threshold: int = _DEFAULT_THRESHOLD,
        cooldown: int = _DEFAULT_COOLDOWN,
    ):
        self._threshold = threshold
        self._cooldown = cooldown
        self._failures: dict[str, list[float]] = {}
        self._breaker: dict[str, float] = {}

    def record_failure(self, provider: str) -> None:
        """记录厂商失败，达到阈值时触发熔断"""
        now = time.time()
        if provider not in self._failures:
            self._failures[provider] = []
        fails = self._failures[provider]
        fails.append(now)
        self._failures[provider] = [t for t in fails if now - t < _WINDOW_SECONDS]

        if len(self._failures[provider]) >= self._threshold:
            self._breaker[provider] = now + self._cooldown
            self._failures[provider] = []
            logger.warning(
                "厂商 %s 连续失败 %d 次，触发熔断 %d 秒",
                provider, self._threshold, self._cooldown,
            )

    def record_success(self, provider: str) -> None:
        """记录厂商成功，清除失败计数"""
        self._failures.pop(provider, None)

    def is_open(self, provider: str) -> bool:
        """检查厂商是否处于熔断状态（True = 熔断中，不可用）"""
        if provider not in self._breaker:
            return False
        if time.time() > self._breaker[provider]:
            del self._breaker[provider]
            logger.info("厂商 %s 熔断已恢复", provider)
            return False
        return True

    def get_status(self) -> dict[str, Any]:
        """获取所有厂商的熔断状态"""
        now = time.time()
        result = {}
        for prov, expire in list(self._breaker.items()):
            if now > expire:
                del self._breaker[prov]
                continue
            result[prov] = {
                "broken": True,
                "remaining_seconds": round(expire - now),
                "failures": len(self._failures.get(prov, [])),
            }
        return result

    def clear(self, provider: str = "") -> int:
        """手动清除熔断状态并重置失败计数。返回清除的厂商数量。"""
        if provider:
            count = 0
            if provider in self._breaker:
                del self._breaker[provider]
                count += 1
            self._failures.pop(provider, None)
            if count:
                logger.info("手动清除厂商 %s 的熔断状态", provider)
            return count
        count = len(self._breaker)
        self._breaker.clear()
        self._failures.clear()
        if count:
            logger.info("手动清除全部 %d 个厂商的熔断状态", count)
        return count

    @property
    def cooldown(self) -> int:
        return self._cooldown
