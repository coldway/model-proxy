# Created by model-proxy on 2026/05/14
# Copyright © 2026

from __future__ import annotations

import logging
import re
import time
import threading
from collections import deque
from typing import Any

_MASK_PATTERNS: list[tuple[re.Pattern, str]] = [
    # URL 中的 key= 参数（Google API 等）
    (re.compile(r'([?&]key=)[A-Za-z0-9_\-]{10,}'), r'\1***'),
    # Bearer Token
    (re.compile(r'(Bearer\s+)[A-Za-z0-9_\-\.]{10,}'), r'\1***'),
    # Authorization header value
    (re.compile(r'(Authorization["\']?\s*:\s*["\']?Bearer\s+)[A-Za-z0-9_\-\.]{10,}'), r'\1***'),
    # 常见 API Key 格式（sk-xxx, ghp_xxx, ghu_xxx, AIza 开头等）
    (re.compile(r'\b(sk-[A-Za-z0-9]{5})[A-Za-z0-9]{10,}'), r'\1***'),
    (re.compile(r'\b(ghp_[A-Za-z0-9]{4})[A-Za-z0-9]{10,}'), r'\1***'),
    (re.compile(r'\b(ghu_[A-Za-z0-9]{4})[A-Za-z0-9]{10,}'), r'\1***'),
    (re.compile(r'\b(AIza[A-Za-z0-9]{4})[A-Za-z0-9]{20,}'), r'\1***'),
    (re.compile(r'\b(gsk_[A-Za-z0-9]{4})[A-Za-z0-9]{10,}'), r'\1***'),
    (re.compile(r'\b(hf_[A-Za-z0-9]{4})[A-Za-z0-9]{10,}'), r'\1***'),
]


def _mask_sensitive(text: str) -> str:
    """对日志文本中的敏感信息进行脱敏"""
    for pattern, repl in _MASK_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class MaskingFormatter(logging.Formatter):
    """对 format 后的日志文本自动脱敏（隐藏 API Key 等敏感信息）。

    替代默认 Formatter 使用，适用于所有 handler（控制台、文件、缓冲区），
    确保子 logger（如 httpx）的消息也被脱敏。
    """
    def format(self, record: logging.LogRecord) -> str:
        result = super().format(record)
        return _mask_sensitive(result)


class BufferedLogHandler(logging.Handler):
    """内存环形缓冲日志 Handler，保留最近 max_records 条日志。

    线程安全，供 /api/logs 接口读取。支持增量拉取（通过 cursor）。
    """

    def __init__(self, max_records: int = 2000, level: int = logging.DEBUG):
        super().__init__(level)
        self._buf: deque[dict[str, Any]] = deque(maxlen=max_records)
        self._seq: int = 0
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "seq": 0,
                "ts": record.created,
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
                       + f",{int(record.msecs):03d}",
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
            with self._lock:
                self._seq += 1
                entry["seq"] = self._seq
                self._buf.append(entry)
        except Exception:
            self.handleError(record)

    def get_logs(self, after_seq: int = 0, limit: int = 200) -> tuple[list[dict], int]:
        """获取 seq > after_seq 的日志条目（增量拉取）。

        返回 (entries, latest_seq)。
        """
        with self._lock:
            if after_seq <= 0:
                entries = list(self._buf)[-limit:]
            else:
                entries = [e for e in self._buf if e["seq"] > after_seq]
                entries = entries[-limit:]
            latest = self._seq
        return entries, latest

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()


_instance: BufferedLogHandler | None = None


def install(max_records: int = 2000) -> BufferedLogHandler:
    """安装缓冲日志 handler 到 root logger，并将所有 handler 的 Formatter 替换为脱敏版本（单例）。"""
    global _instance
    if _instance is not None:
        return _instance
    root = logging.getLogger()

    for h in root.handlers:
        original_fmt = h.formatter
        if original_fmt and not isinstance(original_fmt, MaskingFormatter):
            masking_fmt = MaskingFormatter(original_fmt._fmt, original_fmt.datefmt)
            h.setFormatter(masking_fmt)
        elif not original_fmt:
            h.setFormatter(MaskingFormatter())

    handler = BufferedLogHandler(max_records=max_records)
    handler.setFormatter(MaskingFormatter("%(message)s"))
    root.addHandler(handler)
    _instance = handler
    return handler


def _tail_lines(filepath, n: int, chunk_size: int = 8192) -> list[str]:
    """从文件末尾高效读取最后 n 行，避免全量加载。"""
    with open(filepath, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if size == 0:
            return []
        buf = b""
        pos = size
        lines_found = 0
        while pos > 0 and lines_found <= n:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + buf
            lines_found = buf.count(b"\n")
        return buf.decode("utf-8", errors="replace").splitlines()[-n:]


def preload_from_file(path: str | Path, max_lines: int = 2000) -> int:
    """从日志文件及其轮转备份预加载最近的日志条目到缓冲区，用于重启后恢复 UI 日志。

    会自动扫描同目录下 ``app.log.YYYY-MM-DD`` 格式的轮转文件，
    按日期从旧到新加载，最终加载当前 ``app.log``。
    返回实际加载的行数。
    """
    if _instance is None:
        return 0
    from pathlib import Path as _P
    p = _P(path)
    log_dir = p.parent
    stem = p.name

    rotated = sorted(
        f for f in log_dir.iterdir()
        if f.is_file() and f.name.startswith(stem + ".") and f.name != stem
    )

    files_newest_first: list[_P] = []
    if p.is_file():
        files_newest_first.append(p)
    files_newest_first.extend(reversed(rotated))

    recent: list[str] = []
    for rf in files_newest_first:
        if len(recent) >= max_lines:
            break
        remaining = max_lines - len(recent)
        try:
            tail_lines = _tail_lines(rf, remaining)
            recent = tail_lines + recent
        except Exception:
            continue
    if not recent:
        return 0
    loaded = 0
    for line in recent:
        if not line.strip():
            continue
        record = logging.LogRecord(
            name="(file)", level=logging.INFO,
            pathname="", lineno=0, msg=line,
            args=None, exc_info=None,
        )
        _instance.emit(record)
        loaded += 1
    return loaded


def get_instance() -> BufferedLogHandler | None:
    return _instance
