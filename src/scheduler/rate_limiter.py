# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

USAGE_FILE = Path("data/usage_daily.yaml")
BLACKLIST_FILE = Path("data/model_blacklist.yaml")
SAVE_DEBOUNCE_SECONDS = 30


_BLACKLIST_BASE_COOLDOWN = 120
_BLACKLIST_MAX_COOLDOWN = 1800


@dataclass
class ModelUsage:
    """单个模型的使用计数（含 token 维度）"""
    daily_count: int = 0
    minute_counts: deque = field(default_factory=deque)
    last_reset_day: str = ""
    daily_tokens: int = 0
    minute_token_entries: deque = field(default_factory=deque)

    def reset_if_new_day(self, today: str) -> None:
        if self.last_reset_day != today:
            self.daily_count = 0
            self.daily_tokens = 0
            self.minute_counts.clear()
            self.minute_token_entries.clear()
            self.last_reset_day = today

    def clean_minute_window(self) -> None:
        cutoff = time.time() - 60
        while self.minute_counts and self.minute_counts[0] < cutoff:
            self.minute_counts.popleft()
        while self.minute_token_entries and self.minute_token_entries[0][0] < cutoff:
            self.minute_token_entries.popleft()

    @property
    def minute_tokens(self) -> int:
        return sum(n for _, n in self.minute_token_entries)


class RateLimiter:
    """基于滑动窗口的速率限制器，支持每日使用量持久化和 429 黑名单。

    使用 threading.Lock 而非 asyncio.Lock：所有临界区操作为 O(RPM) 的列表
    过滤（RPM 通常 < 100），实测持锁时间 < 50μs，在 async 环境中不会造成
    可感知的事件循环阻塞。
    """

    def __init__(self, persist: bool = True):
        self._usage: dict[str, ModelUsage] = defaultdict(ModelUsage)
        self._persist = persist
        self._dirty = False
        self._save_timer: threading.Timer | None = None
        self._blacklist: dict[str, float] = {}
        self._blacklist_consecutive: dict[str, int] = {}
        self._blacklist_dirty = False
        self._blacklist_save_timer: threading.Timer | None = None
        self._lock = threading.Lock()
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
                    daily_tokens=data.get("daily_tokens", 0),
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
        if self._blacklist_save_timer:
            self._blacklist_save_timer.cancel()
            self._blacklist_save_timer = None
        self._flush_blacklist()

    def _save(self) -> None:
        """将每日使用量持久化到磁盘（原子写入：先写临时文件再 rename）"""
        import os
        import tempfile
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
                    "daily_tokens": usage.daily_tokens,
                    "last_reset_day": usage.last_reset_day,
                }
            content = yaml.dump(snapshot, allow_unicode=True, default_flow_style=False)
            fd, tmp_path = tempfile.mkstemp(dir=str(USAGE_FILE.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(USAGE_FILE))
        except Exception as e:
            logger.warning("保存使用量持久化文件失败: %s", e)
            if "tmp_path" in locals():
                Path(tmp_path).unlink(missing_ok=True)

    def _can_request_unlocked(
        self, provider: str, model: str,
        rpd: int, rpm: int, tpm: int = 0, tpd: int = 0,
    ) -> bool:
        """在已持有 ``_lock`` 的前提下检查是否可再记一笔请求（含黑名单与额度）"""
        key = self._key(provider, model)

        if self._is_blacklisted_unlocked(provider, model):
            return False

        usage = self._usage[key]
        today = self._today()
        usage.reset_if_new_day(today)
        usage.clean_minute_window()

        if rpd > 0 and usage.daily_count >= rpd:
            logger.warning("%s 已达每日请求上限 (%s RPD)", key, rpd)
            return False

        if rpm > 0 and len(usage.minute_counts) >= rpm:
            logger.warning("%s 已达每分钟请求上限 (%s RPM)", key, rpm)
            return False

        if tpm > 0 and usage.minute_tokens >= tpm:
            logger.warning("%s 已达每分钟 token 上限 (%s/%s TPM)", key, usage.minute_tokens, tpm)
            return False

        if tpd > 0 and usage.daily_tokens >= tpd:
            logger.warning("%s 已达每日 token 上限 (%s TPD)", key, tpd)
            return False

        return True

    def can_request(
        self, provider: str, model: str,
        rpd: int, rpm: int, tpm: int = 0, tpd: int = 0,
    ) -> bool:
        """检查当前模型是否可以发起请求（含 429 黑名单 + token 维度检查）"""
        with self._lock:
            return self._can_request_unlocked(provider, model, rpd, rpm, tpm, tpd)

    def batch_can_request(
        self, checks: list[tuple[str, str, int, int, int, int]],
    ) -> list[bool]:
        """批量检查多个模型是否可请求（单次获取锁，减少争用）"""
        with self._lock:
            return [
                self._can_request_unlocked(prov, model, rpd, rpm, tpm, tpd)
                for prov, model, rpd, rpm, tpm, tpd in checks
            ]

    def _record_request_unlocked(self, provider: str, model: str, tokens: int = 0) -> None:
        """在已持有 ``_lock`` 的前提下增加请求计数与可选 token"""
        key = self._key(provider, model)
        usage = self._usage[key]
        today = self._today()
        usage.reset_if_new_day(today)
        usage.daily_count += 1
        now = time.time()
        usage.minute_counts.append(now)
        if tokens > 0:
            usage.daily_tokens += tokens
            usage.minute_token_entries.append((now, tokens))
        self._schedule_save()

    def try_record_request(
        self,
        provider: str,
        model: str,
        rpd: int,
        rpm: int,
        tpm: int = 0,
        tpd: int = 0,
        *,
        tokens: int = 0,
    ) -> bool:
        """原子地「校验额度并记一笔请求」；未通过校验则不扣减。用于消除 check-then-act 竞态。"""
        with self._lock:
            if not self._can_request_unlocked(provider, model, rpd, rpm, tpm, tpd):
                return False
            self._record_request_unlocked(provider, model, tokens)
            return True

    def record_request(self, provider: str, model: str, tokens: int = 0) -> None:
        """记录一次请求及其 token 用量，延迟批量持久化"""
        with self._lock:
            self._record_request_unlocked(provider, model, tokens)

    def record_tokens(self, provider: str, model: str, tokens: int) -> None:
        """补充记录 token 用量（用于流式响应完成后追加）"""
        if tokens <= 0:
            return
        with self._lock:
            key = self._key(provider, model)
            usage = self._usage[key]
            today = self._today()
            usage.reset_if_new_day(today)
            usage.daily_tokens += tokens
            usage.minute_token_entries.append((time.time(), tokens))
            self._schedule_save()

    def get_usage(self, provider: str, model: str) -> tuple[int, int, int, int]:
        """返回 (今日请求数, 分钟请求数, 今日token数, 分钟token数)"""
        with self._lock:
            key = self._key(provider, model)
            usage = self._usage[key]
            today = self._today()
            usage.reset_if_new_day(today)
            usage.clean_minute_window()
            return usage.daily_count, len(usage.minute_counts), usage.daily_tokens, usage.minute_tokens

    def is_exhausted(self, provider: str, model: str, rpd: int) -> bool:
        """判断模型今日额度是否已用尽"""
        with self._lock:
            key = self._key(provider, model)
            usage = self._usage[key]
            today = self._today()
            usage.reset_if_new_day(today)
            return rpd > 0 and usage.daily_count >= rpd

    # --- 429 黑名单管理（短期冷却 + 指数退避） ---

    def mark_429(self, provider: str, model: str) -> None:
        """将模型标记为 429 限流状态，使用指数退避冷却（120s→240s→…→1800s 上限）

        相比旧版封禁到午夜，新策略更精准：
        - 首次 429：冷却 120 秒（覆盖 Groq TPM 重置周期）
        - 连续 429：每次翻倍，最长 30 分钟
        - 成功请求后重置退避计数器
        """
        with self._lock:
            key = self._key(provider, model)
            consecutive = self._blacklist_consecutive.get(key, 0) + 1
            self._blacklist_consecutive[key] = consecutive
            cooldown = min(
                _BLACKLIST_BASE_COOLDOWN * (2 ** (consecutive - 1)),
                _BLACKLIST_MAX_COOLDOWN,
            )
            expire_at = time.time() + cooldown
            self._blacklist[key] = expire_at
            expire_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expire_at))
            logger.warning(
                "模型 %s 标记为 429 限流（第 %d 次，冷却 %ds，将于 %s 恢复）",
                key, consecutive, cooldown, expire_str,
            )
        self._save_blacklist()

    def _is_blacklisted_unlocked(self, provider: str, model: str) -> bool:
        """内部无锁版本，须在持有 _lock 时调用。

        仅标记 dirty 而非调度 Timer，避免在持有锁时创建回调线程。
        """
        key = self._key(provider, model)
        if key not in self._blacklist:
            return False
        expire_at = self._blacklist[key]
        if time.time() >= expire_at:
            del self._blacklist[key]
            logger.info("模型 %s 429 封禁已到期，自动恢复", key)
            self._blacklist_dirty = True
            return False
        return True

    def is_blacklisted(self, provider: str, model: str) -> bool:
        """检查模型是否在 429 黑名单中（超过封禁时长自动恢复）"""
        with self._lock:
            return self._is_blacklisted_unlocked(provider, model)

    def clear_429_backoff(self, provider: str, model: str) -> None:
        """模型请求成功后调用，重置其 429 退避计数器"""
        with self._lock:
            key = self._key(provider, model)
            if key in self._blacklist_consecutive:
                del self._blacklist_consecutive[key]

    def get_blacklist_remaining(self, provider: str, model: str) -> int:
        """返回封禁剩余秒数，未封禁返回 0"""
        with self._lock:
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
        with self._lock:
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
        with self._lock:
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

    def _schedule_blacklist_save(self) -> None:
        """标记黑名单为脏并调度延迟写盘"""
        if not self._persist:
            return
        self._blacklist_dirty = True
        if self._blacklist_save_timer is None or not self._blacklist_save_timer.is_alive():
            self._blacklist_save_timer = threading.Timer(SAVE_DEBOUNCE_SECONDS, self._flush_blacklist)
            self._blacklist_save_timer.daemon = True
            self._blacklist_save_timer.start()

    def _flush_blacklist(self) -> None:
        """实际执行黑名单写盘（原子写入）"""
        import os
        import tempfile
        if not self._blacklist_dirty:
            return
        self._blacklist_dirty = False
        try:
            BLACKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                snapshot = dict(self._blacklist)
            content = yaml.dump(snapshot, allow_unicode=True, default_flow_style=False)
            fd, tmp_path = tempfile.mkstemp(dir=str(BLACKLIST_FILE.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(BLACKLIST_FILE))
        except Exception as e:
            logger.warning("保存 429 黑名单失败: %s", e)
            if "tmp_path" in locals():
                Path(tmp_path).unlink(missing_ok=True)

    def _save_blacklist(self) -> None:
        """标记黑名单需持久化（延迟写盘，可在锁内安全调用）"""
        self._schedule_blacklist_save()
