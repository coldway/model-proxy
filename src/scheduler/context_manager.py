# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""
上下文管理系统 — Token 监控 + Tool Result 压缩 + LLM 摘要 + 全量归档 + 压缩后恢复

基于 agent-base/docs/agent_long_context_management.md 设计:
- Phase 0: Token 监控 (ContextMonitor)
- Phase 1: Tool Result 压缩 (ToolResultCompactor)
- Phase 2: LLM 摘要压缩 (ConversationCompactor)
- Phase 3: 自动触发 (AutoCompactManager)
- Phase 4: 压缩后恢复 (PostCompactRestorer)
- Phase 6: 全量归档 (FullContextArchive)
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 2
ARCHIVE_DIR = Path("data/archive")
CLEARED_MARKER = "[Tool result cleared to save context]"

COMPACTABLE_TOOLS = {
    "Read", "Shell", "Grep", "Glob", "WebSearch", "WebFetch",
    "read_file", "run_terminal_cmd", "grep_search", "list_dir",
}


# ============================================================
# Phase 0: Token 监控器
# ============================================================

@dataclass
class TurnUsage:
    turn: int
    input_tokens: int
    output_tokens: int
    utilization: float
    cache_hit_rate: float = 0.0
    timestamp: float = field(default_factory=time.time)


class ContextMonitor:
    """上下文监控器 — 追踪 Token 使用率，判断何时需要压缩"""

    def __init__(self, model_context_window: int = 128_000):
        self.context_window = model_context_window
        self.history: list[TurnUsage] = []

    def record_from_response(self, usage_dict: dict[str, int]) -> TurnUsage:
        """从 API 响应的 usage 字段记录"""
        input_tokens = usage_dict.get("prompt_tokens", 0) or usage_dict.get("input_tokens", 0)
        output_tokens = usage_dict.get("completion_tokens", 0) or usage_dict.get("output_tokens", 0)
        cache_hit = usage_dict.get("cache_read_input_tokens", 0)
        total_input = input_tokens + cache_hit

        entry = TurnUsage(
            turn=len(self.history),
            input_tokens=total_input or input_tokens,
            output_tokens=output_tokens,
            utilization=total_input / max(1, self.context_window),
            cache_hit_rate=cache_hit / max(1, total_input) if total_input else 0.0,
        )
        self.history.append(entry)

        if entry.utilization >= 0.75:
            logger.warning(
                "[Context] Turn %d | Input: %d tok (%.1f%%) | Remaining: %d tok",
                entry.turn, entry.input_tokens, entry.utilization * 100,
                self.context_window - entry.input_tokens,
            )
        return entry

    def record_estimated(self, messages: list[dict]) -> TurnUsage:
        """估算 Token 使用率（无 usage 信息时使用）"""
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        est_tokens = total_chars // CHARS_PER_TOKEN
        entry = TurnUsage(
            turn=len(self.history),
            input_tokens=est_tokens,
            output_tokens=0,
            utilization=est_tokens / max(1, self.context_window),
        )
        self.history.append(entry)
        return entry

    def estimate_utilization(self, messages: list[dict]) -> float:
        """估算给定消息列表的 context 利用率"""
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        est_tokens = total_chars // CHARS_PER_TOKEN
        return est_tokens / max(1, self.context_window)

    def should_compact_tool_result(self, threshold: float = 0.55) -> bool:
        if not self.history:
            return False
        return self.history[-1].utilization >= threshold

    def should_compact_full(self, threshold: float = 0.80) -> bool:
        if not self.history:
            return False
        return self.history[-1].utilization >= threshold

    def get_stats(self) -> dict[str, Any]:
        if not self.history:
            return {"turns": 0, "utilization": "0%"}
        latest = self.history[-1]
        return {
            "turns": len(self.history),
            "current_utilization": f"{latest.utilization:.1%}",
            "input_tokens": latest.input_tokens,
            "remaining_tokens": self.context_window - latest.input_tokens,
            "cache_hit_rate": f"{latest.cache_hit_rate:.1%}",
        }

    def update_context_window(self, new_window: int) -> None:
        self.context_window = new_window


# ============================================================
# Phase 1: Tool Result 压缩器
# ============================================================

class ToolResultCompactor:
    """清理旧的 Tool Result，保留最近 N 个"""

    def __init__(self, keep_recent: int = 6):
        self.keep_recent = keep_recent

    def compact(self, messages: list[dict]) -> tuple[list[dict], int]:
        """压缩旧 Tool Result，返回 (新消息列表, 被压缩的数量)"""
        tool_indices: list[int] = []
        for i, msg in enumerate(messages):
            if msg.get("role") == "tool" and msg.get("content") != CLEARED_MARKER:
                tool_indices.append(i)
            elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                pass

        if len(tool_indices) <= self.keep_recent:
            return messages, 0

        indices_to_clear = set(tool_indices[:-self.keep_recent])
        result = []
        cleared_count = 0

        for i, msg in enumerate(messages):
            if i in indices_to_clear:
                result.append({
                    **msg,
                    "content": CLEARED_MARKER,
                    "_compressed": True,
                })
                cleared_count += 1
            else:
                result.append(msg)

        if cleared_count > 0:
            logger.info("[ToolCompact] 清理 %d 条旧 Tool Result", cleared_count)
        return result, cleared_count


# ============================================================
# Phase 2: LLM 摘要压缩器
# ============================================================

COMPACT_PROMPT = """请将以下对话压缩为结构化摘要。

要求：
1. 用户的核心请求和意图
2. 已完成的操作和关键结论
3. 遇到的错误及解决方式
4. 所有用户反馈（逐条保留，非常重要）
5. 当前正在进行的工作
6. 下一步计划

注意：
- 保留精确的文件名、函数名、代码片段
- 用户的反馈和修正指示必须完整保留
- 错误信息必须保留关键字段
- 只输出摘要文本，不要使用任何工具
"""


class ConversationCompactor:
    """LLM 摘要压缩器 — 将过长的对话历史压缩为结构化摘要"""

    def __init__(self, keep_recent: int = 6, summary_max_tokens: int = 2000):
        self.keep_recent = keep_recent
        self.summary_max_tokens = summary_max_tokens
        self._last_summary: str | None = None

    async def compact(
        self,
        messages: list[dict],
        dispatch_fn=None,
    ) -> list[dict]:
        """执行 LLM 摘要压缩

        Args:
            messages: 原始消息列表
            dispatch_fn: async (prompt: str) -> str 用于调用 LLM
        """
        if len(messages) <= self.keep_recent + 2:
            return messages

        old_messages = messages[:-self.keep_recent]
        recent_messages = list(messages[-self.keep_recent:])

        recent_messages = self._fix_tool_pairs(old_messages, recent_messages)

        if not dispatch_fn:
            return self._fallback_compact(old_messages, recent_messages)

        try:
            summary = await self._generate_summary(old_messages, dispatch_fn)
            self._last_summary = summary
            result = [
                {"role": "system", "content": f"[对话摘要]\n{summary}"},
                *recent_messages,
            ]
            logger.info(
                "[Compact] LLM 摘要完成: %d条消息 → 摘要(%d字) + %d条保留",
                len(old_messages), len(summary), len(recent_messages),
            )
            return result
        except Exception as e:
            logger.warning("[Compact] LLM 摘要失败，使用 fallback: %s", e)
            return self._fallback_compact(old_messages, recent_messages)

    def _fallback_compact(self, old: list[dict], recent: list[dict]) -> list[dict]:
        """Fallback：提取关键信息做简单摘要"""
        key_parts = []
        for msg in old:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            if role == "user" and len(content) > 5:
                key_parts.append(f"[用户] {content[:150]}")
            elif role == "assistant" and len(content) > 20:
                key_parts.append(f"[助手] {content[:100]}")

        summary = "\n".join(key_parts[-15:])
        return [
            {"role": "system", "content": f"[对话摘要(简化版)]\n{summary}"},
            *recent,
        ]

    def _fix_tool_pairs(self, old: list[dict], recent: list[dict]) -> list[dict]:
        """确保 recent 中的 tool result 都有对应的 tool_calls"""
        first_tool = None
        for i, msg in enumerate(recent):
            if msg.get("role") == "tool":
                first_tool = i
                break

        if first_tool is not None and first_tool == 0:
            for msg in reversed(old):
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    recent.insert(0, msg)
                    break
        return recent

    async def _generate_summary(self, messages: list[dict], dispatch_fn) -> str:
        """调用 LLM 生成摘要"""
        conversation_text = ""
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            if role in ("user", "assistant", "system") and content:
                conversation_text += f"[{role}]: {content[:500]}\n"

        prompt = f"{COMPACT_PROMPT}\n\n对话历史：\n{conversation_text[-8000:]}"
        summary = await dispatch_fn(prompt)
        return summary[:3000]

    @property
    def last_summary(self) -> str | None:
        return self._last_summary


# ============================================================
# Phase 3: 自动压缩管理器
# ============================================================

class AutoCompactManager:
    """自动上下文管理器 — 整合 Tool Result 压缩 + LLM 摘要"""

    def __init__(
        self,
        context_window: int = 128_000,
        tool_compact_threshold: float = 0.55,
        full_compact_threshold: float = 0.80,
        max_failures: int = 3,
    ):
        self.monitor = ContextMonitor(context_window)
        self.tool_compactor = ToolResultCompactor(keep_recent=6)
        self.conversation_compactor = ConversationCompactor(keep_recent=6)
        self.tool_compact_threshold = tool_compact_threshold
        self.full_compact_threshold = full_compact_threshold
        self.consecutive_failures = 0
        self.max_failures = max_failures
        self.total_compactions = 0

    async def maybe_compact(
        self,
        messages: list[dict],
        dispatch_fn=None,
    ) -> list[dict]:
        """每轮 API 调用前检查是否需要压缩"""
        if self.consecutive_failures >= self.max_failures:
            return messages

        utilization = self.monitor.estimate_utilization(messages)

        if utilization >= self.tool_compact_threshold:
            messages, cleared = self.tool_compactor.compact(messages)
            if cleared > 0:
                utilization = self.monitor.estimate_utilization(messages)
                self.total_compactions += 1

        if utilization >= self.full_compact_threshold and dispatch_fn:
            try:
                messages = await self.conversation_compactor.compact(messages, dispatch_fn)
                self.consecutive_failures = 0
                self.total_compactions += 1
            except Exception as e:
                self.consecutive_failures += 1
                logger.warning(
                    "[AutoCompact] 压缩失败 (%d/%d): %s",
                    self.consecutive_failures, self.max_failures, e,
                )

        return messages

    def record_usage(self, usage_dict: dict[str, int]) -> TurnUsage:
        return self.monitor.record_from_response(usage_dict)

    def get_stats(self) -> dict:
        stats = self.monitor.get_stats()
        stats["total_compactions"] = self.total_compactions
        stats["failures"] = self.consecutive_failures
        return stats

    def update_context_window(self, new_window: int) -> None:
        self.monitor.update_context_window(new_window)


# ============================================================
# Phase 4: 压缩后恢复器
# ============================================================

class PostCompactRestorer:
    """压缩后上下文恢复 — 追踪最近操作的文件/关键结论"""

    def __init__(self, max_files: int = 5, max_chars_per_file: int = 4000):
        self.max_files = max_files
        self.max_chars_per_file = max_chars_per_file
        self._recent_files: dict[str, dict] = {}
        self._key_conclusions: list[str] = []

    def track_file_read(self, path: str, content: str) -> None:
        """记录文件读取"""
        self._recent_files[path] = {
            "content": content[:self.max_chars_per_file],
            "timestamp": time.time(),
        }
        if len(self._recent_files) > self.max_files * 2:
            sorted_files = sorted(
                self._recent_files.items(),
                key=lambda x: x[1]["timestamp"],
                reverse=True,
            )
            self._recent_files = dict(sorted_files[:self.max_files])

    def track_conclusion(self, conclusion: str) -> None:
        """记录关键结论"""
        self._key_conclusions.append(conclusion[:200])
        if len(self._key_conclusions) > 20:
            self._key_conclusions = self._key_conclusions[-15:]

    def get_restore_context(self) -> str:
        """生成压缩后的恢复文本"""
        parts: list[str] = []

        if self._key_conclusions:
            parts.append("## 关键结论")
            for c in self._key_conclusions[-5:]:
                parts.append(f"- {c}")

        sorted_files = sorted(
            self._recent_files.items(),
            key=lambda x: x[1]["timestamp"],
            reverse=True,
        )[:self.max_files]

        if sorted_files:
            parts.append("\n## 最近操作的文件")
            for path, info in sorted_files:
                preview = info["content"][:500]
                parts.append(f"### {path}\n```\n{preview}\n```")

        return "\n".join(parts) if parts else ""

    def clear(self) -> None:
        self._recent_files.clear()
        self._key_conclusions.clear()


# ============================================================
# Phase 6: 全量上下文归档
# ============================================================

@dataclass
class ArchivedTurn:
    """归档的单轮对话"""
    turn_id: int
    timestamp: float
    user_msg: str
    assistant_msg: str
    tool_names: list[str] = field(default_factory=list)
    file_paths: list[str] = field(default_factory=list)
    key_entities: list[str] = field(default_factory=list)
    token_count: int = 0
    summary: str = ""
    was_compacted: bool = False


class FullContextArchive:
    """全量上下文归档 — JSONL 逐轮追加"""

    def __init__(self, session_id: str):
        self._dir = ARCHIVE_DIR / session_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / "turns.jsonl"
        self._turn_count = 0
        self._load_count()

    def _load_count(self) -> None:
        if self._file.exists():
            with open(self._file, "r", encoding="utf-8") as f:
                self._turn_count = sum(1 for _ in f)

    def archive_turn(
        self,
        user_msg: str,
        assistant_msg: str,
        tool_names: list[str] | None = None,
        file_paths: list[str] | None = None,
    ) -> int:
        """归档一轮对话，返回 turn_id"""
        turn = ArchivedTurn(
            turn_id=self._turn_count,
            timestamp=time.time(),
            user_msg=user_msg[:2000],
            assistant_msg=assistant_msg[:2000],
            tool_names=tool_names or [],
            file_paths=file_paths or [],
            key_entities=self._extract_entities(user_msg + " " + assistant_msg),
            token_count=(len(user_msg) + len(assistant_msg)) // CHARS_PER_TOKEN,
        )
        with open(self._file, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")
        self._turn_count += 1
        return turn.turn_id

    def search(self, query: str, limit: int = 5) -> list[ArchivedTurn]:
        """关键词检索归档"""
        if not self._file.exists():
            return []

        query_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", query.lower()))
        scored: list[tuple[float, ArchivedTurn]] = []

        with open(self._file, "r", encoding="utf-8") as f:
            for line in f:
                data = json.loads(line)
                turn = ArchivedTurn(**data)
                text = (turn.user_msg + " " + turn.assistant_msg).lower()
                text_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", text))
                overlap = len(query_tokens & text_tokens)
                if overlap > 0:
                    score = overlap / max(len(query_tokens), 1)
                    scored.append((score, turn))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [turn for _, turn in scored[:limit]]

    def get_total_turns(self) -> int:
        return self._turn_count

    def get_recent(self, n: int = 3) -> list[ArchivedTurn]:
        """获取最近 N 轮"""
        if not self._file.exists():
            return []
        turns: list[ArchivedTurn] = []
        with open(self._file, "r", encoding="utf-8") as f:
            for line in f:
                turns.append(ArchivedTurn(**json.loads(line)))
        return turns[-n:]

    @staticmethod
    def _extract_entities(text: str) -> list[str]:
        """简单实体提取（函数名/类名/变量名/文件路径）"""
        entities: list[str] = []
        code_patterns = re.findall(r"[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)+", text)
        entities.extend(code_patterns[:5])
        path_patterns = re.findall(r"[/\\][\w\-./\\]+\.\w+", text)
        entities.extend(path_patterns[:3])
        return entities[:8]
