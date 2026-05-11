# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ModelUsage:
    """单个模型的使用计数"""
    daily_count: int = 0
    minute_counts: list[float] = field(default_factory=list)
    last_reset_day: str = ""

    def reset_if_new_day(self, today: str) -> None:
        if self.last_reset_day != today:
            self.daily_count = 0
            self.minute_counts.clear()
            self.last_reset_day = today

    def clean_minute_window(self) -> None:
        now = time.time()
        self.minute_counts = [t for t in self.minute_counts if now - t < 60]


class RateLimiter:
    """基于滑动窗口的速率限制器"""

    def __init__(self):
        # key: "provider:model_name"
        self._usage: dict[str, ModelUsage] = defaultdict(ModelUsage)

    def _key(self, provider: str, model: str) -> str:
        return f"{provider}:{model}"

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d")

    def can_request(self, provider: str, model: str, rpd: int, rpm: int) -> bool:
        """检查当前模型是否可以发起请求"""
        key = self._key(provider, model)
        usage = self._usage[key]
        today = self._today()
        usage.reset_if_new_day(today)
        usage.clean_minute_window()

        if rpd > 0 and usage.daily_count >= rpd:
            logger.warning(f"{key} 已达每日上限 ({rpd} RPD)")
            return False

        if rpm > 0 and len(usage.minute_counts) >= rpm:
            logger.warning(f"{key} 已达每分钟上限 ({rpm} RPM)")
            return False

        return True

    def record_request(self, provider: str, model: str) -> None:
        """记录一次请求"""
        key = self._key(provider, model)
        usage = self._usage[key]
        today = self._today()
        usage.reset_if_new_day(today)
        usage.daily_count += 1
        usage.minute_counts.append(time.time())

    def get_usage(self, provider: str, model: str) -> tuple[int, int]:
        """返回 (今日请求数, 当前分钟请求数)"""
        key = self._key(provider, model)
        usage = self._usage[key]
        today = self._today()
        usage.reset_if_new_day(today)
        usage.clean_minute_window()
        return usage.daily_count, len(usage.minute_counts)

    def is_exhausted(self, provider: str, model: str, rpd: int) -> bool:
        """判断模型今日额度是否已用尽"""
        key = self._key(provider, model)
        usage = self._usage[key]
        today = self._today()
        usage.reset_if_new_day(today)
        return rpd > 0 and usage.daily_count >= rpd
