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

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_LIMITS_FILE = _DATA_DIR / "payload_limits.yaml"

# 客户端估算与网关实际限制可能有偏差，比较与 413 记录统一使用加缓冲后的估算值
_PAYLOAD_ESTIMATE_BUFFER = 1.1


_BASE64_PREFIX = "data:"
_BASE64_OVERHEAD_RATIO = 1.37


def estimate_payload_bytes_raw(request: ChatCompletionRequest) -> int:
    """原始 payload 字节估算（无安全余量）

    对 base64 图片 URL 直接计入其字符长度（因为 base64 编码后的字节数
    就是它在 JSON payload 中占用的大小）。
    """
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


def estimate_payload_bytes(request: ChatCompletionRequest) -> int:
    """估算请求 payload 字节大小，返回含 10% 安全余量（与网关判定对齐更保守）"""
    raw = estimate_payload_bytes_raw(request)
    if raw <= 0:
        return 0
    return max(1, int(raw * _PAYLOAD_ESTIMATE_BUFFER))


_FLUSH_INTERVAL_SECONDS = 60


class PayloadTracker:
    """追踪每个 provider/model 的 payload 上限（字节）

    - record_413(key, size): 收到 413 时记录该 size 为上限
    - get_limit(key): 获取已知上限，None 表示无限制
    - can_accept(key, size): 判断 payload 是否在上限内

    自动以 60s 间隔将脏数据持久化到磁盘。
    """

    def __init__(self, path: Path = _LIMITS_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._limits: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._flush_timer: threading.Timer | None = None
        self._load()
        self._schedule_flush()

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
        """标记为脏，由内部定时器自动持久化"""
        self._dirty = True

    def _persist(self) -> None:
        """实际写入磁盘（原子写入：先写临时文件再 rename）"""
        import os
        import tempfile
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            content = yaml.dump(self._limits, allow_unicode=True, default_flow_style=False)
            fd, tmp_path = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(self._path))
        except Exception as e:
            logger.warning("保存 payload 上限文件失败: %s", e)
            if "tmp_path" in locals():
                Path(tmp_path).unlink(missing_ok=True)

    def _schedule_flush(self) -> None:
        """启动周期性自动 flush 定时器"""
        if self._flush_timer is not None and self._flush_timer.is_alive():
            return
        self._flush_timer = threading.Timer(_FLUSH_INTERVAL_SECONDS, self._periodic_flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _periodic_flush(self) -> None:
        """定时器回调：flush 脏数据并重新调度"""
        try:
            self.flush()
        finally:
            self._schedule_flush()

    def flush(self) -> None:
        """将内存状态持久化到磁盘（由定时器自动调用或关停钩子显式调用）"""
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            self._persist()

    def close(self) -> None:
        """停止定时器并执行最终 flush（用于优雅关闭）"""
        if self._flush_timer:
            self._flush_timer.cancel()
            self._flush_timer = None
        self.flush()

    @staticmethod
    def _key(provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def record_413(self, provider: str, model: str, payload_bytes: int) -> None:
        """收到 413 时，将 payload_bytes（已含估算缓冲）设为上限（取更小值以逐步收敛）

        同时记录 raw_estimate_bytes（倒推的原始估算）与 adjusted_limit_bytes（实际采用的
        比较上限），便于与网关行为对照。
        """
        key = self._key(provider, model)
        raw_est = max(0, int(round(payload_bytes / _PAYLOAD_ESTIMATE_BUFFER))) if payload_bytes else 0
        adjusted = payload_bytes
        with self._lock:
            current = self._limits.get(key)
            if current and current["max_bytes"] <= payload_bytes:
                return
            self._limits[key] = {
                "max_bytes": adjusted,
                "raw_estimate_bytes": raw_est,
                "adjusted_limit_bytes": adjusted,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "hit_count": (current["hit_count"] + 1) if current else 1,
            }
            logger.warning(
                "模型 %s payload 上限更新: 原始估算=%d bytes | 调整后限制=%d bytes (%.1f KB)",
                key, raw_est, adjusted, adjusted / 1024,
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
