# Created by AI Assistant on 2026/05/17
# Copyright © 2026

"""动态 Payload 上限追踪器

当厂商返回 413 Payload Too Large 时，记录触发 413 的 payload 大小
作为该模型的 payload 上限。后续请求根据此上限过滤模型，避免发送
注定失败的请求。上限会逐步收敛到模型的真实 payload 限制。

持久化到 data/payload_limits.yaml，服务重启后自动加载。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from src.models.schemas import ChatCompletionRequest

logger = logging.getLogger(__name__)

_DATA_DIR = Path("data")
_LIMITS_FILE = _DATA_DIR / "payload_limits.yaml"


def estimate_payload_bytes(request: ChatCompletionRequest) -> int:
    """估算请求 payload 的字节大小（序列化 messages + tools 为 JSON）"""
    parts: list[Any] = []
    for msg in request.messages:
        if hasattr(msg, "model_dump"):
            parts.append(msg.model_dump(exclude_none=True))
        elif isinstance(msg, dict):
            parts.append(msg)
    payload = {"messages": parts}
    if request.tools:
        payload["tools"] = [t.model_dump() for t in request.tools]
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


class PayloadTracker:
    """追踪每个 provider/model 的 payload 上限（字节）

    - record_413(key, size): 收到 413 时记录该 size 为上限
    - get_limit(key): 获取已知上限，None 表示无限制
    - can_accept(key, size): 判断 payload 是否在上限内
    """

    def __init__(self, path: Path = _LIMITS_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._limits: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._limits = raw
                logger.info("已加载 %d 个模型的 payload 上限记录", len(self._limits))
        except Exception as e:
            logger.warning("加载 payload 上限文件失败: %s", e)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                yaml.dump(self._limits, allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("保存 payload 上限文件失败: %s", e)

    @staticmethod
    def _key(provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def record_413(self, provider: str, model: str, payload_bytes: int) -> None:
        """收到 413 时，将 payload_bytes 设为上限（取更小值以逐步收敛）"""
        key = self._key(provider, model)
        with self._lock:
            current = self._limits.get(key)
            if current and current["max_bytes"] <= payload_bytes:
                return
            self._limits[key] = {
                "max_bytes": payload_bytes,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "hit_count": (current["hit_count"] + 1) if current else 1,
            }
            logger.warning(
                "模型 %s payload 上限更新为 %d bytes (%.1f KB)",
                key, payload_bytes, payload_bytes / 1024,
            )
            self._save()

    def record_success(self, provider: str, model: str, payload_bytes: int) -> None:
        """请求成功时，如果 payload 大于已知上限，清除过时的限制"""
        key = self._key(provider, model)
        with self._lock:
            current = self._limits.get(key)
            if current and payload_bytes >= current["max_bytes"]:
                logger.info(
                    "模型 %s 成功处理 %d bytes payload（≥已知上限 %d），清除限制",
                    key, payload_bytes, current["max_bytes"],
                )
                del self._limits[key]
                self._save()

    def get_limit(self, provider: str, model: str) -> int | None:
        """获取已知 payload 上限（字节），None 表示无限制"""
        key = self._key(provider, model)
        entry = self._limits.get(key)
        return entry["max_bytes"] if entry else None

    def can_accept(self, provider: str, model: str, payload_bytes: int) -> bool:
        """判断模型是否能接受给定大小的 payload"""
        limit = self.get_limit(provider, model)
        if limit is None:
            return True
        return payload_bytes < limit

    def get_all_limits(self) -> dict[str, dict[str, Any]]:
        """获取所有模型的 payload 上限记录"""
        with self._lock:
            return dict(self._limits)

    def clear(self, provider: str = "", model: str = "") -> int:
        """清除 payload 上限记录。返回清除的数量。"""
        with self._lock:
            if not provider:
                count = len(self._limits)
                self._limits.clear()
            elif model:
                key = self._key(provider, model)
                count = 1 if self._limits.pop(key, None) else 0
            else:
                keys = [k for k in self._limits if k.startswith(f"{provider}/")]
                for k in keys:
                    del self._limits[k]
                count = len(keys)
            if count:
                self._save()
            return count
