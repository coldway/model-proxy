# Created by model-proxy on 2026/05/17
# Copyright © 2026

"""熔断器（默认按 provider:model 粒度；可仅传 provider 以兼容旧行为）"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 3
_DEFAULT_COOLDOWN = 300
_DEFAULT_WINDOW = 120

# 不应触发熔断的 HTTP 状态码（客户端请求自身问题，非厂商故障）
CLIENT_ERROR_NO_BREAKER = frozenset({400, 404, 413, 414, 415, 422})


def should_trigger_breaker(status_code: int) -> bool:
    """判断 HTTP 状态码是否应触发熔断计数。"""
    return status_code not in CLIENT_ERROR_NO_BREAKER


def _breaker_key(provider: str, model: str | None) -> str:
    """构造熔断器键：有 model 时为 provider:model，否则为厂商级（向后兼容）。"""
    if model:
        return f"{provider}:{model}"
    return provider


class CircuitBreaker:
    """滑动窗口失败计数；冷却期内对应键不可用。"""

    def __init__(
        self,
        *,
        threshold: int = _DEFAULT_THRESHOLD,
        cooldown: int = _DEFAULT_COOLDOWN,
        window: int = _DEFAULT_WINDOW,
    ):
        self._threshold = threshold
        self._cooldown = cooldown
        self._window = window
        self._failures: dict[str, list[float]] = {}
        self._breaker: dict[str, float] = {}
        self._lock = threading.Lock()

    def record_failure(self, provider: str, model: str | None = None) -> None:
        """记录失败。传入 model 时仅计入该模型，不传时保持厂商级熔断（兼容旧调用）。"""
        key = _breaker_key(provider, model)
        now = time.time()
        with self._lock:
            if key not in self._failures:
                self._failures[key] = []
            fails = self._failures[key]
            fails.append(now)
            self._failures[key] = [t for t in fails if now - t < self._window]

            if len(self._failures[key]) >= self._threshold:
                self._breaker[key] = now + self._cooldown
                self._failures[key] = []
                logger.warning(
                    "熔断触发: %s 连续失败 %d 次，冷却 %d 秒",
                    key, self._threshold, self._cooldown,
                )

    def record_success(self, provider: str, model: str | None = None) -> None:
        """记录成功，清除该键的失败计数。"""
        key = _breaker_key(provider, model)
        with self._lock:
            self._failures.pop(key, None)

    def is_open(self, provider: str, model: str | None = None) -> bool:
        """是否处于熔断。传入 model 时同时尊重旧的厂商级熔断键（向后兼容）。"""
        keys_to_check: list[str] = []
        if model:
            keys_to_check.append(_breaker_key(provider, model))
        keys_to_check.append(_breaker_key(provider, None))

        now = time.time()
        with self._lock:
            for key in keys_to_check:
                if key not in self._breaker:
                    continue
                if now > self._breaker[key]:
                    del self._breaker[key]
                    logger.info("熔断已恢复: %s", key)
                    continue
                return True
        return False

    def get_status(self) -> dict[str, Any]:
        """获取所有熔断中的键状态"""
        now = time.time()
        result = {}
        with self._lock:
            for key, expire in list(self._breaker.items()):
                if now > expire:
                    del self._breaker[key]
                    continue
                result[key] = {
                    "broken": True,
                    "remaining_seconds": round(expire - now),
                    "failures": len(self._failures.get(key, [])),
                }
        return result

    def clear(self, provider: str = "", model: str | None = None) -> int:
        """清除熔断。model 有值时只清该 provider:model；仅 provider 时清除该厂商全部键；空则清除全部。"""
        with self._lock:
            if not provider:
                count = len(self._breaker)
                self._breaker.clear()
                self._failures.clear()
                if count:
                    logger.info("手动清除全部 %d 条熔断状态", count)
                return count

            removed = 0
            if model:
                target = _breaker_key(provider, model)
                if target in self._breaker:
                    del self._breaker[target]
                    removed += 1
                self._failures.pop(target, None)
                if removed:
                    logger.info("手动清除熔断状态: %s", target)
                return removed

            to_remove = [k for k in self._breaker if k == provider or k.startswith(f"{provider}:")]
            for k in to_remove:
                del self._breaker[k]
                removed += 1
                self._failures.pop(k, None)

            if removed:
                logger.info("手动清除厂商 %s 的 %d 条熔断状态", provider, removed)
            return removed

    @property
    def cooldown(self) -> int:
        return self._cooldown
