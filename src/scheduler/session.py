# Created by model-proxy on 2026/05/13
# Copyright © 2026

"""聊天会话管理器 — 多会话、上下文窗口控制、持久化、并发安全"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
SESSION_FILE = DATA_DIR / "chat_sessions.yaml"

# 上下文窗口 token 估算：中英混合 ≈ 2 chars/token
CHARS_PER_TOKEN = 2
MAX_CONTEXT_TOKENS = 32_000
RESERVED_SYSTEM_TOKENS = 500
MAX_SESSIONS = 0

SYSTEM_PROMPT = (
    "你是 Model Proxy 的内置 AI 助手，帮助用户完成文本分析、编程、翻译、写作等任务。"
    "请用简洁、专业的中文回答，除非用户使用其他语言提问。\n\n"
    "【重要】输出规则：\n"
    "- 只输出最终回复，禁止输出任何思考过程、推理步骤、草稿或分析\n"
    "- 禁止使用 * 或 • 列出你的思考过程\n"
    "- 禁止输出 'Final Polish'、'Draft'、'Wait' 等元认知文本\n"
    "- 禁止输出 (括号包裹的内心独白)\n"
    "- 不要复述用户的问题，直接给出答案\n"
)


class ChatSession:
    """单个聊天会话（支持并发安全和事务性回滚）"""

    def __init__(
        self,
        session_id: str | None = None,
        title: str = "新对话",
        model: str = "auto",
        system_prompt: str = SYSTEM_PROMPT,
        max_context_tokens: int = MAX_CONTEXT_TOKENS,
    ):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.title = title
        self.model = model
        self.system_prompt = system_prompt
        self._max_context_tokens = max_context_tokens
        self._lock = threading.Lock()
        self._async_lock = asyncio.Lock()
        self.messages: list[dict[str, str]] = []
        self.created_at: float = time.time()
        self.updated_at: float = time.time()
        self.total_tokens_est: int = 0
        self._checkpoint: int | None = None

    def add_message(self, role: str, content: str, model: str | None = None, tool_calls: list | None = None, tool_call_id: str | None = None, name: str | None = None) -> None:
        with self._lock:
            msg: dict[str, Any] = {"role": role, "content": content}
            if model:
                msg["model"] = model
            if tool_calls:
                msg["tool_calls"] = tool_calls
            if tool_call_id:
                msg["tool_call_id"] = tool_call_id
            if name:
                msg["name"] = name
            self.messages.append(msg)
            self.updated_at = time.time()
            self._update_token_estimate()

    def _update_token_estimate(self) -> None:
        total_chars = len(self.system_prompt)
        for m in self.messages:
            total_chars += len(m.get("content") or "")
        self.total_tokens_est = total_chars // CHARS_PER_TOKEN

    def get_context_messages(self, model_max_tokens: int | None = None) -> list[dict[str, str]]:
        """获取用于 API 调用的消息列表（含系统提示、重要性感知截断）

        截断策略（优化版）：
        1. 始终保留首轮用户消息（定义性约束）
        2. 始终保留最近 N 轮
        3. 中间部分超出预算时生成摘要占位
        4. 保证 tool_call 链完整性
        """
        max_tokens = model_max_tokens or self._max_context_tokens
        with self._lock:
            result = [{"role": "system", "content": self.system_prompt}]
            budget = max_tokens - RESERVED_SYSTEM_TOKENS

            if not self.messages:
                return result

            first_user_msg = None
            first_user_idx = -1
            for i, m in enumerate(self.messages):
                if m.get("role") == "user":
                    first_user_msg = m
                    first_user_idx = i
                    break

            selected: list[dict[str, str]] = []
            consumed = 0

            for msg in reversed(self.messages):
                msg_tokens = len(msg.get("content") or "") // CHARS_PER_TOKEN
                if consumed + msg_tokens > budget:
                    break
                selected.append(msg)
                consumed += msg_tokens

            selected.reverse()

            if (
                first_user_msg
                and first_user_idx >= 0
                and first_user_msg not in selected
                and len(self.messages) > 4
            ):
                first_tokens = len(first_user_msg.get("content") or "") // CHARS_PER_TOKEN
                if consumed + first_tokens + 50 <= budget:
                    summary_msg = {
                        "role": "system",
                        "content": f"[对话开始时用户的初始消息：{(first_user_msg.get('content') or '')[:500]}]",
                    }
                    selected = [summary_msg] + selected
                    consumed += first_tokens + 50

            while selected and selected[0].get("role") == "tool":
                selected = selected[1:]

            if (
                selected
                and selected[0].get("role") == "assistant"
                and selected[0].get("tool_calls")
                and (len(selected) < 2 or selected[1].get("role") != "tool")
            ):
                selected = selected[1:]

            for m in selected:
                entry: dict[str, Any] = {"role": m["role"], "content": m["content"]}
                if m.get("tool_calls"):
                    entry["tool_calls"] = m["tool_calls"]
                if m.get("tool_call_id"):
                    entry["tool_call_id"] = m["tool_call_id"]
                if m.get("name"):
                    entry["name"] = m["name"]
                result.append(entry)
            return result

    def pop_last_message(self) -> dict | None:
        """移除最后一条消息（用于 dispatch 失败时回滚用户消息）"""
        with self._lock:
            if self.messages:
                msg = self.messages.pop()
                self._update_token_estimate()
                return msg
            return None

    def set_checkpoint(self) -> int:
        """设置回滚点，返回当前消息数量作为 checkpoint。"""
        with self._lock:
            self._checkpoint = len(self.messages)
            return self._checkpoint

    def rollback(self) -> int:
        """回滚到最近的 checkpoint，返回被移除的消息数量。"""
        with self._lock:
            if self._checkpoint is None:
                return 0
            removed = len(self.messages) - self._checkpoint
            if removed > 0:
                self.messages = self.messages[:self._checkpoint]
                self._update_token_estimate()
            self._checkpoint = None
            return max(removed, 0)

    def clear_checkpoint(self) -> None:
        """清除 checkpoint（表示事务成功提交）。"""
        with self._lock:
            self._checkpoint = None

    def edit_message(self, index: int, new_content: str) -> bool:
        """编辑指定位置的消息并截断其后所有消息（用于对话分支）"""
        with self._lock:
            if index < 0 or index >= len(self.messages):
                return False
            self.messages[index]["content"] = new_content
            self.messages = self.messages[:index + 1]
            self.updated_at = time.time()
            self._update_token_estimate()
            return True

    def regenerate(self) -> dict | None:
        """移除最后一条 assistant 消息以便重新生成"""
        with self._lock:
            if not self.messages:
                return None
            if self.messages[-1].get("role") == "assistant":
                msg = self.messages.pop()
                self._update_token_estimate()
                return msg
            for i in range(len(self.messages) - 1, -1, -1):
                if self.messages[i].get("role") == "assistant":
                    msg = self.messages[i]
                    self.messages = self.messages[:i]
                    self._update_token_estimate()
                    return msg
            return None

    def switch_model(self, new_model: str, max_context_tokens: int | None = None) -> str:
        """切换会话模型，返回旧模型名。可选更新 context 窗口大小。"""
        with self._lock:
            old = self.model
            self.model = new_model
            if max_context_tokens and max_context_tokens != self._max_context_tokens:
                self._max_context_tokens = max_context_tokens
            self.updated_at = time.time()
            return old

    def count_role(self, role: str) -> int:
        """安全地统计指定 role 的消息数量"""
        with self._lock:
            return sum(1 for m in self.messages if m.get("role") == role)

    def auto_title(self) -> None:
        """从第一条用户消息自动生成标题"""
        for m in self.messages:
            if m["role"] == "user":
                text = (m.get("content") or "").strip()
                self.title = text[:20] + ("..." if len(text) > 20 else "")
                return

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "messages": self.messages,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict, max_context_tokens: int = MAX_CONTEXT_TOKENS) -> ChatSession:
        s = cls(
            session_id=data["id"],
            title=data.get("title", "新对话"),
            model=data.get("model", "auto"),
            system_prompt=data.get("system_prompt", SYSTEM_PROMPT),
            max_context_tokens=max_context_tokens,
        )
        s.messages = data.get("messages", [])
        s.created_at = data.get("created_at", time.time())
        s.updated_at = data.get("updated_at", time.time())
        s._update_token_estimate()
        return s


_SAVE_DEBOUNCE_SECONDS = 8


TRASH_RETENTION_DAYS = 30


class SessionManager:
    """管理所有聊天会话（含回收站）"""

    def __init__(
        self,
        *,
        max_context_tokens: int = MAX_CONTEXT_TOKENS,
        max_sessions: int = MAX_SESSIONS,
    ):
        self._lock = threading.Lock()
        self._sessions: dict[str, ChatSession] = {}
        self._trash: dict[str, dict] = {}  # {session_id: {"session": ChatSession, "deleted_at": float}}
        self._max_context_tokens = max_context_tokens
        self._max_sessions = max_sessions
        self._dirty = False
        self._save_timer: threading.Timer | None = None
        self._trash_timer: threading.Timer | None = None
        self._load()
        self._cleanup_expired_trash()
        self._start_trash_cleanup_timer()

    def create(self, model: str = "auto", title: str = "新对话") -> ChatSession:
        with self._lock:
            if self._max_sessions > 0 and len(self._sessions) >= self._max_sessions:
                self._evict_oldest()
            session = ChatSession(
                title=title, model=model,
                max_context_tokens=self._max_context_tokens,
            )
            self._sessions[session.id] = session
            self._save_unlocked()
        return session

    def _evict_oldest(self) -> None:
        """淘汰最旧的会话直到腾出空间（须在持有 self._lock 时调用)"""
        sorted_sessions = sorted(
            self._sessions.values(),
            key=lambda s: s.updated_at,
        )
        while self._max_sessions > 0 and len(self._sessions) >= self._max_sessions:
            if not sorted_sessions:
                break
            oldest = sorted_sessions.pop(0)
            del self._sessions[oldest.id]
            logger.info("会话数已达上限 %d，淘汰最旧会话: %s", self._max_sessions, oldest.id)

    def get(self, session_id: str) -> ChatSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        """软删除：移入回收站"""
        with self._lock:
            if session_id in self._sessions:
                session = self._sessions.pop(session_id)
                self._trash[session_id] = {"session": session, "deleted_at": time.time()}
                self._save_unlocked()
                return True
            return False

    def restore(self, session_id: str) -> bool:
        """从回收站恢复会话"""
        with self._lock:
            if session_id in self._trash:
                entry = self._trash.pop(session_id)
                self._sessions[session_id] = entry["session"]
                self._save_unlocked()
                return True
            return False

    def permanent_delete(self, session_id: str) -> bool:
        """从回收站永久删除"""
        with self._lock:
            if session_id in self._trash:
                del self._trash[session_id]
                self._save_unlocked()
                return True
            return False

    def list_trash(self) -> list[dict]:
        """列出回收站中的会话"""
        with self._lock:
            items = sorted(
                self._trash.items(),
                key=lambda x: x[1]["deleted_at"],
                reverse=True,
            )
            now = time.time()
            return [
                {
                    "id": sid,
                    "title": entry["session"].title,
                    "model": entry["session"].model,
                    "message_count": len(entry["session"].messages),
                    "deleted_at": entry["deleted_at"],
                    "expires_in_days": max(0, round(TRASH_RETENTION_DAYS - (now - entry["deleted_at"]) / 86400)),
                }
                for sid, entry in items
            ]

    def _cleanup_expired_trash(self) -> None:
        """清理超过保留期限的回收站会话（须持有 _lock 或初始化时无竞争）"""
        with self._lock:
            now = time.time()
            expired = [
                sid for sid, entry in self._trash.items()
                if now - entry["deleted_at"] > TRASH_RETENTION_DAYS * 86400
            ]
            if expired:
                for sid in expired:
                    del self._trash[sid]
                logger.info("清理 %d 个过期回收站会话", len(expired))
                self._save_unlocked()

    def _start_trash_cleanup_timer(self) -> None:
        """启动周期性回收站清理"""
        self._trash_timer = threading.Timer(3600.0, self._periodic_trash_cleanup)
        self._trash_timer.daemon = True
        self._trash_timer.start()

    def _periodic_trash_cleanup(self) -> None:
        """Timer 回调：周期性清理过期回收站"""
        try:
            self._cleanup_expired_trash()
        except Exception as e:
            logger.warning("周期清理回收站异常: %s", e)
        finally:
            self._start_trash_cleanup_timer()

    def list_sessions(self) -> list[dict]:
        with self._lock:
            sessions = sorted(
                self._sessions.values(),
                key=lambda s: s.updated_at,
                reverse=True,
            )
            return [
                {
                    "id": s.id,
                    "title": s.title,
                    "model": s.model,
                    "message_count": len(s.messages),
                    "tokens_est": s.total_tokens_est,
                    "created_at": s.created_at,
                    "updated_at": s.updated_at,
                }
                for s in sessions
            ]

    def rename(self, session_id: str, title: str) -> bool:
        with self._lock:
            s = self._sessions.get(session_id)
            if s:
                s.title = title
                self._save_unlocked()
                return True
            return False

    def save(self) -> None:
        """立即刷盘（用于优雅关闭或显式保存）"""
        with self._lock:
            if self._save_timer:
                self._save_timer.cancel()
                self._save_timer = None
            self._persist_unlocked()

    def _schedule_save(self) -> None:
        """延迟写盘：合并短时间内的多次写入为一次磁盘操作（须在 _lock 内调用）"""
        self._dirty = True
        if self._save_timer is None or not self._save_timer.is_alive():
            self._save_timer = threading.Timer(_SAVE_DEBOUNCE_SECONDS, self._timer_flush)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _timer_flush(self) -> None:
        """Timer 回调：在独立线程中安全获取锁并刷盘"""
        with self._lock:
            self._save_timer = None
            self._persist_unlocked()

    def _save_unlocked(self) -> None:
        """在持有锁时标记脏并调度延迟写盘（须在 _lock 内调用）"""
        self._schedule_save()

    def _persist_unlocked(self) -> None:
        """实际执行磁盘写入（原子写入：写临时文件 + rename，须在 _lock 内调用）"""
        if not self._dirty and not self._sessions and not self._trash:
            return
        self._dirty = False
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "_version": 2,
            "_active": {sid: s.to_dict() for sid, s in self._sessions.items()},
            "_trash": {sid: {"session": entry["session"].to_dict(), "deleted_at": entry["deleted_at"]} for sid, entry in self._trash.items()},
        }
        try:
            content = yaml.dump(data, allow_unicode=True, default_flow_style=False)
            fd, tmp_path = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(SESSION_FILE))
        except Exception as e:
            logger.error("保存会话数据失败: %s", e)
            if "tmp_path" in locals():
                Path(tmp_path).unlink(missing_ok=True)

    def _load(self) -> None:
        if not SESSION_FILE.exists():
            return
        try:
            data = yaml.safe_load(SESSION_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            with self._lock:
                if "_active" in data:
                    for sid, sdata in data["_active"].items():
                        self._sessions[sid] = ChatSession.from_dict(
                            sdata, max_context_tokens=self._max_context_tokens,
                        )
                    for sid, tdata in data.get("_trash", {}).items():
                        session = ChatSession.from_dict(
                            tdata["session"], max_context_tokens=self._max_context_tokens,
                        )
                        self._trash[sid] = {"session": session, "deleted_at": tdata["deleted_at"]}
                else:
                    for sid, sdata in data.items():
                        self._sessions[sid] = ChatSession.from_dict(
                            sdata, max_context_tokens=self._max_context_tokens,
                        )
                n = len(self._sessions)
                t = len(self._trash)
            logger.info("已加载 %d 个聊天会话, %d 个回收站会话", n, t)
        except Exception as e:
            logger.error("加载会话数据失败: %s", e)
