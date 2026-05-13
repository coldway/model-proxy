# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

USAGE_FILE = Path("data/usage_daily.yaml")
BLACKLIST_FILE = Path("data/model_blacklist.yaml")


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
    """基于滑动窗口的速率限制器，支持每日使用量持久化和 429 黑名单"""

    def __init__(self, persist: bool = True):
        self._usage: dict[str, ModelUsage] = defaultdict(ModelUsage)
        self._persist = persist
        self._dirty = False
        self._blacklist: dict[str, str] = {}  # key → 加入日期
        if persist:
            self._load()
            self._load_blacklist()

    def _key(self, provider: str, model: str) -> str:
        return f"{provider}:{model}"

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d")

    def _load(self) -> None:
        """从磁盘加载每日使用量"""
        if not USAGE_FILE.exists():
            return
        try:
            raw: dict[str, Any] = yaml.safe_load(USAGE_FILE.read_text(encoding="utf-8")) or {}
            today = self._today()
            for key, data in raw.items():
                if not isinstance(data, dict):
                    continue
                stored_day = data.get("last_reset_day", "")
                if stored_day != today:
                    continue
                usage = ModelUsage(
                    daily_count=data.get("daily_count", 0),
                    last_reset_day=stored_day,
                )
                self._usage[key] = usage
            loaded = sum(1 for u in self._usage.values() if u.daily_count > 0)
            if loaded:
                logger.info("从磁盘恢复了 %d 个模型的今日使用量", loaded)
        except Exception as e:
            logger.warning("加载使用量持久化文件失败: %s", e)

    def _save(self) -> None:
        """将每日使用量持久化到磁盘（仅保存 daily_count 和 last_reset_day）"""
        if not self._persist:
            return
        try:
            USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            snapshot: dict[str, dict[str, Any]] = {}
            today = self._today()
            for key, usage in self._usage.items():
                if usage.last_reset_day != today or usage.daily_count == 0:
                    continue
                snapshot[key] = {
                    "daily_count": usage.daily_count,
                    "last_reset_day": usage.last_reset_day,
                }
            USAGE_FILE.write_text(
                yaml.dump(snapshot, allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("保存使用量持久化文件失败: %s", e)

    def can_request(self, provider: str, model: str, rpd: int, rpm: int) -> bool:
        """检查当前模型是否可以发起请求（含 429 黑名单检查）"""
        key = self._key(provider, model)

        if self.is_blacklisted(provider, model):
            return False

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
        """记录一次请求并持久化"""
        key = self._key(provider, model)
        usage = self._usage[key]
        today = self._today()
        usage.reset_if_new_day(today)
        usage.daily_count += 1
        usage.minute_counts.append(time.time())
        self._save()

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

    # --- 429 黑名单管理 ---

    def mark_429(self, provider: str, model: str) -> None:
        """将模型标记为 429 限流状态（当天有效，次日自动恢复）"""
        key = self._key(provider, model)
        today = self._today()
        self._blacklist[key] = today
        logger.warning("模型 %s 标记为 429 限流（次日恢复）", key)
        self._save_blacklist()

    def is_blacklisted(self, provider: str, model: str) -> bool:
        """检查模型是否在 429 黑名单中（跨天自动恢复）"""
        key = self._key(provider, model)
        if key not in self._blacklist:
            return False
        blacklist_day = self._blacklist[key]
        today = self._today()
        if blacklist_day != today:
            del self._blacklist[key]
            logger.info("模型 %s 429 状态已过期，自动恢复", key)
            self._save_blacklist()
            return False
        return True

    def clear_blacklist(self, provider: str = "", model: str = "") -> int:
        """清除黑名单。不传参数清除全部，传参清除指定模型。返回清除数量"""
        if provider and model:
            key = self._key(provider, model)
            if key in self._blacklist:
                del self._blacklist[key]
                self._save_blacklist()
                return 1
            return 0
        count = len(self._blacklist)
        self._blacklist.clear()
        self._save_blacklist()
        return count

    def get_blacklist(self) -> dict[str, str]:
        """返回当前黑名单 {key: 加入日期}，自动清理过期记录"""
        today = self._today()
        expired = [k for k, d in self._blacklist.items() if d != today]
        for k in expired:
            del self._blacklist[k]
        if expired:
            self._save_blacklist()
        return dict(self._blacklist)

    def _load_blacklist(self) -> None:
        """从磁盘加载 429 黑名单"""
        if not BLACKLIST_FILE.exists():
            return
        try:
            raw = yaml.safe_load(BLACKLIST_FILE.read_text(encoding="utf-8")) or {}
            today = self._today()
            self._blacklist = {k: d for k, d in raw.items() if d == today}
            if self._blacklist:
                logger.info("从磁盘恢复了 %d 个模型的 429 黑名单", len(self._blacklist))
        except Exception as e:
            logger.warning("加载 429 黑名单失败: %s", e)

    def _save_blacklist(self) -> None:
        """持久化 429 黑名单到磁盘"""
        if not self._persist:
            return
        try:
            BLACKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
            BLACKLIST_FILE.write_text(
                yaml.dump(dict(self._blacklist), allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("保存 429 黑名单失败: %s", e)
