# Created by model-proxy on 2026/05/13
# Copyright © 2026

"""聊天会话管理器 — 多会话、上下文窗口控制、持久化"""

from __future__ import annotations

import logging
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
    """单个聊天会话"""

    def __init__(
        self,
        session_id: str | None = None,
        title: str = "新对话",
        model: str = "auto",
        system_prompt: str = SYSTEM_PROMPT,
    ):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.title = title
        self.model = model
        self.system_prompt = system_prompt
        self.messages: list[dict[str, str]] = []
        self.created_at: float = time.time()
        self.updated_at: float = time.time()
        self.total_tokens_est: int = 0

    def add_message(self, role: str, content: str, model: str | None = None) -> None:
        msg: dict[str, Any] = {"role": role, "content": content}
        if model:
            msg["model"] = model
        self.messages.append(msg)
        self.updated_at = time.time()
        self._update_token_estimate()

    def _update_token_estimate(self) -> None:
        total_chars = len(self.system_prompt)
        for m in self.messages:
            total_chars += len(m.get("content") or "")
        self.total_tokens_est = total_chars // CHARS_PER_TOKEN

    def get_context_messages(self) -> list[dict[str, str]]:
        """获取用于 API 调用的消息列表（含系统提示、自动截断）"""
        result = [{"role": "system", "content": self.system_prompt}]
        budget = MAX_CONTEXT_TOKENS - RESERVED_SYSTEM_TOKENS
        selected: list[dict[str, str]] = []
        consumed = 0

        for msg in reversed(self.messages):
            msg_tokens = len(msg.get("content") or "") // CHARS_PER_TOKEN
            if consumed + msg_tokens > budget:
                break
            selected.append(msg)
            consumed += msg_tokens

        selected.reverse()

        if selected and selected[0]["role"] == "assistant":
            selected = selected[1:]

        result.extend({"role": m["role"], "content": m["content"]} for m in selected)
        return result

    def auto_title(self) -> None:
        """从第一条用户消息自动生成标题"""
        for m in self.messages:
            if m["role"] == "user":
                text = m["content"].strip()
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
    def from_dict(cls, data: dict) -> ChatSession:
        s = cls(
            session_id=data["id"],
            title=data.get("title", "新对话"),
            model=data.get("model", "auto"),
            system_prompt=data.get("system_prompt", SYSTEM_PROMPT),
        )
        s.messages = data.get("messages", [])
        s.created_at = data.get("created_at", time.time())
        s.updated_at = data.get("updated_at", time.time())
        s._update_token_estimate()
        return s


class SessionManager:
    """管理所有聊天会话"""

    def __init__(self, *, max_context_tokens: int = MAX_CONTEXT_TOKENS):
        self._sessions: dict[str, ChatSession] = {}
        self._max_context_tokens = max_context_tokens
        self._load()

    def create(self, model: str = "auto", title: str = "新对话") -> ChatSession:
        session = ChatSession(title=title, model=model)
        self._sessions[session.id] = session
        self._save()
        return session

    def get(self, session_id: str) -> ChatSession | None:
        return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            self._save()
            return True
        return False

    def list_sessions(self) -> list[dict]:
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
        s = self._sessions.get(session_id)
        if s:
            s.title = title
            self._save()
            return True
        return False

    def save(self) -> None:
        self._save()

    def _save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = {sid: s.to_dict() for sid, s in self._sessions.items()}
        try:
            SESSION_FILE.write_text(
                yaml.dump(data, allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("保存会话数据失败: %s", e)

    def _load(self) -> None:
        if not SESSION_FILE.exists():
            return
        try:
            data = yaml.safe_load(SESSION_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            for sid, sdata in data.items():
                self._sessions[sid] = ChatSession.from_dict(sdata)
            logger.info("已加载 %d 个聊天会话", len(self._sessions))
        except Exception as e:
            logger.error("加载会话数据失败: %s", e)
