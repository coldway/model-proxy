# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import datetime
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

USAGE_FILE = Path("data/usage_daily.yaml")
BLACKLIST_FILE = Path("data/model_blacklist.yaml")
SAVE_DEBOUNCE_SECONDS = 30


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
        self._save_timer: threading.Timer | None = None
        self._blacklist: dict[str, str] = {}
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

    def _schedule_save(self) -> None:
        """延迟刷盘：合并短时间内的多次写入为一次磁盘操作"""
        if not self._persist:
            return
        self._dirty = True
        if self._save_timer is None or not self._save_timer.is_alive():
            self._save_timer = threading.Timer(SAVE_DEBOUNCE_SECONDS, self._flush_save)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _flush_save(self) -> None:
        """实际执行磁盘写入（由 timer 触发或手动调用 flush）"""
        if not self._dirty:
            return
        self._save()
        self._dirty = False

    def flush(self) -> None:
        """立即刷盘（用于优雅关闭等场景）"""
        if self._save_timer:
            self._save_timer.cancel()
            self._save_timer = None
        self._flush_save()

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
        """记录一次请求，延迟批量持久化（避免每次请求都写磁盘）"""
        key = self._key(provider, model)
        usage = self._usage[key]
        today = self._today()
        usage.reset_if_new_day(today)
        usage.daily_count += 1
        usage.minute_counts.append(time.time())
        self._schedule_save()

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

    @staticmethod
    def _end_of_today() -> float:
        """返回今天 23:59:59 之后的时间戳（即明天 00:00:00）"""
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        return datetime.datetime.combine(tomorrow, datetime.time.min).timestamp()

    def mark_429(self, provider: str, model: str) -> None:
        """将模型标记为 429 限流状态，封禁到当天自然日结束（与厂商每日额度重置对齐）"""
        key = self._key(provider, model)
        expire_at = self._end_of_today()
        self._blacklist[key] = expire_at
        expire_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expire_at))
        logger.warning("模型 %s 标记为 429 限流（将于 %s 恢复）", key, expire_str)
        self._save_blacklist()

    def is_blacklisted(self, provider: str, model: str) -> bool:
        """检查模型是否在 429 黑名单中（超过封禁时长自动恢复）"""
        key = self._key(provider, model)
        if key not in self._blacklist:
            return False
        expire_at = self._blacklist[key]
        if time.time() >= expire_at:
            del self._blacklist[key]
            logger.info("模型 %s 429 封禁已到期，自动恢复", key)
            self._save_blacklist()
            return False
        return True

    def get_blacklist_remaining(self, provider: str, model: str) -> int:
        """返回封禁剩余秒数，未封禁返回 0"""
        key = self._key(provider, model)
        expire_at = self._blacklist.get(key)
        if expire_at is None:
            return 0
        remaining = expire_at - time.time()
        return max(0, int(remaining))

    def clear_blacklist(self, provider: str = "", model: str = "") -> int:
        """清除黑名单。provider+model 都传则清除指定模型；
        仅传 provider 清除该厂商下所有模型；都不传清除全部。
        仅传 model 不做任何操作（避免误删）。返回清除数量。
        """
        if provider and model:
            key = self._key(provider, model)
            if key in self._blacklist:
                del self._blacklist[key]
                self._save_blacklist()
                return 1
            return 0
        if provider:
            prefix = f"{provider}:"
            to_del = [k for k in self._blacklist if k.startswith(prefix)]
            for k in to_del:
                del self._blacklist[k]
            if to_del:
                self._save_blacklist()
            return len(to_del)
        if model:
            return 0
        count = len(self._blacklist)
        self._blacklist.clear()
        if count:
            self._save_blacklist()
        return count

    def get_blacklist(self) -> dict[str, dict[str, Any]]:
        """返回当前黑名单 {key: {expire_at, remaining_seconds}}，自动清理过期记录"""
        now = time.time()
        expired = [k for k, exp in self._blacklist.items() if now >= exp]
        for k in expired:
            del self._blacklist[k]
        if expired:
            self._save_blacklist()
            logger.info("自动清理了 %d 个过期的 429 封禁", len(expired))
        return {
            k: {
                "expire_at": exp,
                "expire_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp)),
                "remaining_seconds": max(0, int(exp - now)),
            }
            for k, exp in self._blacklist.items()
        }

    def _load_blacklist(self) -> None:
        """从磁盘加载 429 黑名单，过滤掉已过期的记录"""
        if not BLACKLIST_FILE.exists():
            return
        try:
            raw = yaml.safe_load(BLACKLIST_FILE.read_text(encoding="utf-8")) or {}
            now = time.time()
            for k, v in raw.items():
                if isinstance(v, (int, float)) and v > now:
                    self._blacklist[k] = float(v)
                elif isinstance(v, str):
                    pass  # 旧格式日期字符串，跳过（视为过期）
            if self._blacklist:
                logger.info("从磁盘恢复了 %d 个模型的 429 封禁", len(self._blacklist))
        except Exception as e:
            logger.warning("加载 429 黑名单失败: %s", e)

    def _save_blacklist(self) -> None:
        """持久化 429 黑名单到磁盘（存储 expire_at 时间戳）"""
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
