# Created by model-proxy on 2026/05/21
# Copyright © 2026

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class SessionBindingMixin:
    """会话模型绑定相关方法"""

    _SESSION_BIND_MAX = 10000
    _SESSION_BIND_FILE = Path("data/session_bindings.yaml")
    _SESSION_BIND_SAVE_DEBOUNCE = 30

    def _load_session_bindings(self) -> None:
        if not self._SESSION_BIND_FILE.exists():
            return
        try:
            raw = yaml.safe_load(self._SESSION_BIND_FILE.read_text(encoding="utf-8")) or {}
            now = time.time()
            loaded = 0
            for sid, entry in raw.items():
                if not isinstance(entry, dict):
                    continue
                ts = entry.get("ts", 0)
                if now - ts > self._session_bind_ttl:
                    continue
                self._session_bindings[sid] = (entry["provider"], entry["model"], ts)
                loaded += 1
            if loaded:
                logger.info("从磁盘恢复了 %d 个会话绑定", loaded)
        except Exception as e:
            logger.warning("加载会话绑定文件失败: %s", e)

    def _schedule_bindings_save(self) -> None:
        with self._session_lock:
            if self._bind_save_timer is None or not self._bind_save_timer.is_alive():
                self._bind_save_timer = threading.Timer(self._SESSION_BIND_SAVE_DEBOUNCE, self._persist_bindings)
                self._bind_save_timer.daemon = True
                self._bind_save_timer.start()

    def _persist_bindings(self) -> None:
        try:
            with self._session_lock:
                snapshot = {
                    sid: {"provider": prov, "model": model, "ts": ts}
                    for sid, (prov, model, ts) in self._session_bindings.items()
                }
            self._SESSION_BIND_FILE.parent.mkdir(parents=True, exist_ok=True)
            content = yaml.dump(snapshot, allow_unicode=True, default_flow_style=False)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._SESSION_BIND_FILE.parent), suffix=".tmp",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            try:
                os.replace(tmp_path, str(self._SESSION_BIND_FILE))
            except OSError:
                try:
                    os.remove(str(self._SESSION_BIND_FILE))
                except FileNotFoundError:
                    pass
                os.rename(tmp_path, str(self._SESSION_BIND_FILE))
        except Exception as e:
            logger.warning("保存会话绑定文件失败: %s", e)

    def flush_session_bindings(self) -> None:
        if self._bind_save_timer:
            self._bind_save_timer.cancel()
        self._persist_bindings()

    def bind_session(self, session_id: str, provider: str, model: str) -> None:
        with self._session_lock:
            if len(self._session_bindings) >= self._SESSION_BIND_MAX and session_id not in self._session_bindings:
                self._evict_expired_sessions()
                if len(self._session_bindings) >= self._SESSION_BIND_MAX:
                    oldest_sid = min(self._session_bindings, key=lambda k: self._session_bindings[k][2])
                    del self._session_bindings[oldest_sid]
            self._session_bindings[session_id] = (provider, model, time.time())
        self._schedule_bindings_save()
        logger.info("会话绑定: %s → %s:%s", session_id, provider, model)

    def _evict_expired_sessions(self) -> int:
        now = time.time()
        expired = [sid for sid, (_, _, ts) in self._session_bindings.items() if now - ts > self._session_bind_ttl]
        for sid in expired:
            del self._session_bindings[sid]
        return len(expired)

    def get_session_binding(self, session_id: str) -> tuple[str, str] | None:
        with self._session_lock:
            if session_id not in self._session_bindings:
                return None
            provider, model, ts = self._session_bindings[session_id]
            if time.time() - ts > self._session_bind_ttl:
                del self._session_bindings[session_id]
                logger.info("会话绑定过期: %s", session_id)
                return None
            return provider, model

    def get_all_session_bindings(self) -> dict[str, dict[str, Any]]:
        with self._session_lock:
            return self._get_all_session_bindings_unlocked()

    def _get_all_session_bindings_unlocked(self) -> dict[str, dict[str, Any]]:
        now = time.time()
        result = {}
        expired = []
        for sid, (prov, model, ts) in self._session_bindings.items():
            remaining = self._session_bind_ttl - (now - ts)
            if remaining <= 0:
                expired.append(sid)
                continue
            result[sid] = {
                "provider": prov,
                "model": model,
                "remaining_seconds": round(remaining),
            }
        for sid in expired:
            del self._session_bindings[sid]
        return result

    def clear_session_binding(self, session_id: str = "") -> int:
        with self._session_lock:
            if session_id:
                return 1 if self._session_bindings.pop(session_id, None) else 0
            count = len(self._session_bindings)
            self._session_bindings.clear()
            return count
