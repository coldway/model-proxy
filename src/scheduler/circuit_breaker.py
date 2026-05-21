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
    """滑动窗口失败计数；支持 CLOSED → OPEN → HALF_OPEN → CLOSED 三态转换。

    - CLOSED: 正常放行
    - OPEN: 冷却期内拒绝所有请求
    - HALF_OPEN: 冷却期刚结束，允许单个探测请求通过
      - 探测成功 → CLOSED
      - 探测失败 → 重新 OPEN（冷却期翻倍，上限 2x 原始 cooldown）
    """

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
        self._half_open: set[str] = set()
        self._lock = threading.Lock()

    def record_failure(self, provider: str, model: str | None = None) -> None:
        """记录失败。半开状态下失败立即重新熔断（冷却时间 1.5x）。"""
        key = _breaker_key(provider, model)
        now = time.time()
        with self._lock:
            if key in self._half_open:
                self._half_open.discard(key)
                extended_cooldown = int(self._cooldown * 1.5)
                self._breaker[key] = now + extended_cooldown
                self._failures[key] = []
                logger.warning(
                    "半开探测失败，重新熔断: %s，冷却 %d 秒",
                    key, extended_cooldown,
                )
                return

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
        """记录成功。半开状态下成功则回到 CLOSED，清除熔断和失败计数。"""
        key = _breaker_key(provider, model)
        with self._lock:
            if key in self._half_open:
                self._half_open.discard(key)
                self._breaker.pop(key, None)
                logger.info("半开探测成功，熔断恢复: %s", key)
            self._failures.pop(key, None)

    def is_open(self, provider: str, model: str | None = None) -> bool:
        """是否处于熔断（OPEN=True, HALF_OPEN/CLOSED=False）。

        冷却期结束时进入 HALF_OPEN 状态（允许单个探测请求），
        由 record_success/record_failure 决定是否回到 CLOSED 或重新 OPEN。
        """
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
                    if key not in self._half_open:
                        self._half_open.add(key)
                        logger.info("熔断进入半开状态: %s（允许探测请求）", key)
                    return False
                return True
        return False

    def is_half_open(self, provider: str, model: str | None = None) -> bool:
        """是否处于半开探测状态"""
        key = _breaker_key(provider, model)
        with self._lock:
            return key in self._half_open

    def get_status(self) -> dict[str, Any]:
        """获取所有熔断/半开状态的键"""
        now = time.time()
        result = {}
        with self._lock:
            for key, expire in list(self._breaker.items()):
                if now > expire:
                    if key in self._half_open:
                        result[key] = {
                            "state": "half_open",
                            "remaining_seconds": 0,
                            "failures": len(self._failures.get(key, [])),
                        }
                    continue
                result[key] = {
                    "state": "open",
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
                self._half_open.clear()
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
                self._half_open.discard(target)
                if removed:
                    logger.info("手动清除熔断状态: %s", target)
                return removed

            to_remove = [k for k in self._breaker if k == provider or k.startswith(f"{provider}:")]
            for k in to_remove:
                del self._breaker[k]
                removed += 1
                self._failures.pop(k, None)
                self._half_open.discard(k)

            if removed:
                logger.info("手动清除厂商 %s 的 %d 条熔断状态", provider, removed)
            return removed

    @property
    def cooldown(self) -> int:
        return self._cooldown
