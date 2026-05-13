# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HISTORY_FILE = Path("data/request_history.jsonl")
MAX_MEMORY_RECORDS = 500


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
    """请求历史记录器，支持内存队列 + 文件持久化"""

    def __init__(self, persist: bool = True):
        self._records: deque[RequestRecord] = deque(maxlen=MAX_MEMORY_RECORDS)
        self._persist = persist
        if persist:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

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

        if self._persist:
            self._append_to_file(rec)

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取最近 N 条记录"""
        records = list(self._records)[-limit:]
        return [r.to_dict() for r in reversed(records)]

    def get_stats(self) -> dict[str, Any]:
        """获取汇总统计（含 token 用量和路由策略分布）"""
        if not self._records:
            return {"total": 0, "success_rate": 0, "avg_latency_ms": 0, "by_provider": {}, "tokens": {}, "route_strategies": {}}

        total = len(self._records)
        success = sum(1 for r in self._records if r.success)
        avg_latency = sum(r.latency_ms for r in self._records) / total

        total_prompt = sum(r.prompt_tokens for r in self._records)
        total_completion = sum(r.completion_tokens for r in self._records)

        by_provider: dict[str, dict[str, Any]] = {}
        by_model_tokens: dict[str, dict[str, int]] = {}
        route_strategies: dict[str, int] = {}

        for r in self._records:
            key = r.provider
            if key not in by_provider:
                by_provider[key] = {"total": 0, "success": 0, "total_latency": 0, "prompt_tokens": 0, "completion_tokens": 0}
            by_provider[key]["total"] += 1
            if r.success:
                by_provider[key]["success"] += 1
            by_provider[key]["total_latency"] += r.latency_ms
            by_provider[key]["prompt_tokens"] += r.prompt_tokens
            by_provider[key]["completion_tokens"] += r.completion_tokens

            model_key = f"{r.provider}:{r.model}"
            if model_key not in by_model_tokens:
                by_model_tokens[model_key] = {"prompt": 0, "completion": 0, "total": 0}
            by_model_tokens[model_key]["prompt"] += r.prompt_tokens
            by_model_tokens[model_key]["completion"] += r.completion_tokens
            by_model_tokens[model_key]["total"] += r.prompt_tokens + r.completion_tokens

            if r.route_strategy:
                route_strategies[r.route_strategy] = route_strategies.get(r.route_strategy, 0) + 1

        for prov in by_provider.values():
            prov["avg_latency_ms"] = round(prov["total_latency"] / prov["total"], 1)
            prov["success_rate"] = round(prov["success"] / prov["total"] * 100, 1)
            del prov["total_latency"]

        return {
            "total": total,
            "success_rate": round(success / total * 100, 1),
            "avg_latency_ms": round(avg_latency, 1),
            "by_provider": by_provider,
            "tokens": {
                "total_prompt": total_prompt,
                "total_completion": total_completion,
                "total": total_prompt + total_completion,
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

    def _append_to_file(self, record: RequestRecord) -> None:
        try:
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"写入历史记录失败: {e}")
