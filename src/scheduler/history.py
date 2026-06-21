# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HISTORY_FILE = Path("data/request_history.jsonl")
DEFAULT_MAX_MEMORY_RECORDS = 2000
FLUSH_INTERVAL_SECONDS = 5
RETENTION_DAYS = 30


@dataclass
class RequestRecord:
    """单次请求记录"""
    timestamp: float
    provider: str
    model: str
    success: bool
    latency_ms: float
    error: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    route_strategy: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RequestHistory:
    """请求历史记录器，支持内存队列 + 文件持久化 + 增量统计"""

    def __init__(self, persist: bool = True, *, max_memory_records: int | None = None):
        cap = max_memory_records if max_memory_records is not None else DEFAULT_MAX_MEMORY_RECORDS
        cap = max(1, int(cap))
        self._records: deque[RequestRecord] = deque(maxlen=cap)
        self._persist = persist
        self._pending: list[RequestRecord] = []
        self._pending_lock = threading.Lock()
        self._flush_timer: threading.Timer | None = None
        self._stats_lock = threading.Lock()
        self._total = 0
        self._success = 0
        self._total_latency = 0.0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._by_provider: dict[str, dict[str, Any]] = {}
        self._by_model_tokens: dict[str, dict[str, int]] = {}
        self._route_strategies: dict[str, int] = {}
        if persist:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._load_and_cleanup()

    def _update_incremental_stats(self, rec: RequestRecord) -> None:
        """增量更新统计计数器（O(1)）"""
        with self._stats_lock:
            self._total += 1
            if rec.success:
                self._success += 1
            self._total_latency += rec.latency_ms
            self._total_prompt_tokens += rec.prompt_tokens
            self._total_completion_tokens += rec.completion_tokens
            prov = rec.provider
            if prov not in self._by_provider:
                self._by_provider[prov] = {"total": 0, "success": 0, "total_latency": 0.0, "prompt_tokens": 0, "completion_tokens": 0}
            pd = self._by_provider[prov]
            pd["total"] += 1
            if rec.success:
                pd["success"] += 1
            pd["total_latency"] += rec.latency_ms
            pd["prompt_tokens"] += rec.prompt_tokens
            pd["completion_tokens"] += rec.completion_tokens
            model_key = f"{rec.provider}:{rec.model}"
            if model_key not in self._by_model_tokens:
                self._by_model_tokens[model_key] = {"prompt": 0, "completion": 0, "total": 0}
            mt = self._by_model_tokens[model_key]
            mt["prompt"] += rec.prompt_tokens
            mt["completion"] += rec.completion_tokens
            mt["total"] += rec.prompt_tokens + rec.completion_tokens
            if rec.route_strategy:
                self._route_strategies[rec.route_strategy] = self._route_strategies.get(rec.route_strategy, 0) + 1

    def record(
        self,
        provider: str,
        model: str,
        success: bool,
        latency_ms: float,
        error: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        route_strategy: str = "",
    ) -> None:
        """记录一次请求"""
        rec = RequestRecord(
            timestamp=time.time(),
            provider=provider,
            model=model,
            success=success,
            latency_ms=latency_ms,
            error=error,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            route_strategy=route_strategy,
        )
        self._records.append(rec)
        self._update_incremental_stats(rec)

        if self._persist:
            with self._pending_lock:
                self._pending.append(rec)
            self._schedule_flush()

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取最近 N 条记录"""
        records = list(self._records)[-limit:]
        return [r.to_dict() for r in reversed(records)]

    def get_stats(self) -> dict[str, Any]:
        """获取汇总统计（O(1) 增量计数器 + 按需扫描 model token 分布）"""
        with self._stats_lock:
            total = self._total
            if total == 0:
                return {"total": 0, "success_rate": 0, "avg_latency_ms": 0, "by_provider": {}, "tokens": {}, "route_strategies": {}}

            avg_latency = self._total_latency / total
            by_provider = {}
            for prov, pd in self._by_provider.items():
                by_provider[prov] = {
                    "total": pd["total"],
                    "success": pd["success"],
                    "avg_latency_ms": round(pd["total_latency"] / pd["total"], 1) if pd["total"] else 0,
                    "success_rate": round(pd["success"] / pd["total"] * 100, 1) if pd["total"] else 0,
                    "prompt_tokens": pd["prompt_tokens"],
                    "completion_tokens": pd["completion_tokens"],
                }
            route_strategies = dict(self._route_strategies)
            by_model_tokens = {k: dict(v) for k, v in self._by_model_tokens.items()}

        return {
            "total": total,
            "success_rate": round(self._success / total * 100, 1),
            "avg_latency_ms": round(avg_latency, 1),
            "by_provider": by_provider,
            "tokens": {
                "total_prompt": self._total_prompt_tokens,
                "total_completion": self._total_completion_tokens,
                "total": self._total_prompt_tokens + self._total_completion_tokens,
                "by_model": by_model_tokens,
            },
            "route_strategies": route_strategies,
        }

    def get_model_health(self, window_minutes: int = 60) -> dict[str, dict[str, Any]]:
        """计算每个模型在指定时间窗口内的健康指标

        返回 {provider:model: {success_rate, avg_latency, total, recent_errors}}
        """
        cutoff = time.time() - window_minutes * 60
        model_data: dict[str, dict[str, Any]] = {}

        for r in self._records:
            if r.timestamp < cutoff:
                continue
            key = f"{r.provider}:{r.model}"
            if key not in model_data:
                model_data[key] = {
                    "total": 0, "success": 0, "total_latency": 0.0,
                    "recent_errors": 0, "last_error": "",
                }
            d = model_data[key]
            d["total"] += 1
            if r.success:
                d["success"] += 1
                d["total_latency"] += r.latency_ms
            else:
                d["recent_errors"] += 1
                d["last_error"] = r.error

        result = {}
        for key, d in model_data.items():
            success_rate = d["success"] / d["total"] if d["total"] > 0 else 0
            avg_latency = d["total_latency"] / d["success"] if d["success"] > 0 else 99999
            result[key] = {
                "success_rate": round(success_rate, 3),
                "avg_latency_ms": round(avg_latency, 1),
                "total": d["total"],
                "recent_errors": d["recent_errors"],
                "last_error": d["last_error"],
            }
        return result

    def _load_and_cleanup(self) -> None:
        """启动时单次扫描 JSONL 文件：加载有效记录到内存 + 清理过期记录"""
        if not HISTORY_FILE.exists():
            return
        cutoff = time.time() - RETENTION_DAYS * 86400
        kept_lines: list[str] = []
        loaded = 0
        removed = 0
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("timestamp", 0) < cutoff:
                        removed += 1
                        continue
                    kept_lines.append(line)
                    try:
                        rec = RequestRecord(
                            timestamp=data["timestamp"],
                            provider=data["provider"],
                            model=data["model"],
                            success=data["success"],
                            latency_ms=data["latency_ms"],
                            error=data.get("error", ""),
                            prompt_tokens=data.get("prompt_tokens", 0),
                            completion_tokens=data.get("completion_tokens", 0),
                            route_strategy=data.get("route_strategy", ""),
                        )
                        self._records.append(rec)
                        self._update_incremental_stats(rec)
                        loaded += 1
                    except KeyError:
                        continue
            if removed:
                with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                    for line in kept_lines:
                        f.write(line + "\n")
                logger.info("已清理 %d 条超过 %d 天的路由决策记录", removed, RETENTION_DAYS)
            if loaded:
                logger.info("已从历史文件加载 %d 条路由决策记录", loaded)
        except Exception as e:
            logger.warning("加载/清理路由决策历史失败: %s", e)

    def _schedule_flush(self) -> None:
        if self._flush_timer is None or not self._flush_timer.is_alive():
            self._flush_timer = threading.Timer(FLUSH_INTERVAL_SECONDS, self._flush)
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _flush(self) -> None:
        with self._pending_lock:
            if not self._pending:
                return
            batch = self._pending[:]
            self._pending.clear()
        try:
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                for rec in batch:
                    f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("批量写入历史记录失败（%d 条将在下次重试）: %s", len(batch), e)
            with self._pending_lock:
                self._pending = batch + self._pending

    def flush(self) -> None:
        """立即刷盘（用于优雅关闭）"""
        if self._flush_timer:
            self._flush_timer.cancel()
            self._flush_timer = None
        self._flush()
