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
        )
        self._records.append(rec)

        if self._persist:
            self._append_to_file(rec)

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取最近 N 条记录"""
        records = list(self._records)[-limit:]
        return [r.to_dict() for r in reversed(records)]

    def get_stats(self) -> dict[str, Any]:
        """获取汇总统计"""
        if not self._records:
            return {"total": 0, "success_rate": 0, "avg_latency_ms": 0, "by_provider": {}}

        total = len(self._records)
        success = sum(1 for r in self._records if r.success)
        avg_latency = sum(r.latency_ms for r in self._records) / total

        by_provider: dict[str, dict[str, Any]] = {}
        for r in self._records:
            key = r.provider
            if key not in by_provider:
                by_provider[key] = {"total": 0, "success": 0, "total_latency": 0}
            by_provider[key]["total"] += 1
            if r.success:
                by_provider[key]["success"] += 1
            by_provider[key]["total_latency"] += r.latency_ms

        for prov in by_provider.values():
            prov["avg_latency_ms"] = round(prov["total_latency"] / prov["total"], 1)
            prov["success_rate"] = round(prov["success"] / prov["total"] * 100, 1)
            del prov["total_latency"]

        return {
            "total": total,
            "success_rate": round(success / total * 100, 1),
            "avg_latency_ms": round(avg_latency, 1),
            "by_provider": by_provider,
        }

    def _append_to_file(self, record: RequestRecord) -> None:
        try:
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"写入历史记录失败: {e}")
