# Created by model-proxy on 2026/05/20
# Copyright © 2026

"""
Agent Memory System — L1 Session Memory + L2 Long-term Memory

基于 agent-base/docs/agent_memory_system.md 设计实现：
- L1: 规则化增量提取 (goals/decisions/preferences/tech_context)，注入 system prompt
- L2: 跨会话持久化（JSON 文件），会话开始时检索相关记忆并注入
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MEMORY_DIR = Path("data/memory")


def _compute_bigrams(text: str) -> set[str]:
    """计算字符级 bigram 集合（用于文本相似度）"""
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
    bigrams: set[str] = set()
    for token in tokens:
        for i in range(len(token) - 1):
            bigrams.add(token[i:i+2])
    return bigrams


def _bigram_similarity(a: set[str], b: set[str]) -> float:
    """Dice coefficient — bigram 集合相似度"""
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


@dataclass
class SessionMemoryStore:
    """L1 Session Memory — 单会话期间提取的结构化事实"""

    goals: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    tech_context: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)

    MAX_PER_CATEGORY = 15

    def to_system_section(self) -> str:
        """生成注入 system prompt 的文本"""
        parts = []
        if self.goals:
            parts.append("【当前目标】" + "；".join(self.goals[-5:]))
        if self.decisions:
            parts.append("【已做决策】" + "；".join(self.decisions[-5:]))
        if self.preferences:
            parts.append("【用户偏好】" + "；".join(self.preferences[-5:]))
        if self.tech_context:
            parts.append("【技术上下文】" + "；".join(self.tech_context[-5:]))
        if not parts:
            return ""
        return "## Session Memory\n" + "\n".join(parts)

    def merge(self, new_entries: dict[str, list[str]]) -> None:
        """合并新提取的条目（去重 + 截断）"""
        for key, values in new_entries.items():
            existing: list = getattr(self, key, [])
            for v in values:
                if v not in existing:
                    existing.append(v)
            if len(existing) > self.MAX_PER_CATEGORY:
                existing[:] = existing[-self.MAX_PER_CATEGORY:]
            setattr(self, key, existing)

    def to_dict(self) -> dict:
        return {
            "goals": self.goals,
            "decisions": self.decisions,
            "preferences": self.preferences,
            "tech_context": self.tech_context,
            "topics": self.topics,
        }


class SessionMemoryExtractor:
    """规则化增量提取器 — 从每轮对话中提取 Session Memory"""

    _GOAL_PATTERNS = re.compile(
        r"(?:帮我|请|我想|我要|需要|目标是|任务是|实现|完成|开发|创建|构建|设计|测试|修复|优化|重构)",
    )
    _DECISION_PATTERNS = re.compile(
        r"(?:决定|确定|采用|选择|方案|使用|改为|统一用|不要用|禁止)",
    )
    _PREFERENCE_PATTERNS = re.compile(
        r"(?:不要|别|不需要|改成|偏好|习惯|总是|请用|请不要|风格|格式|语言用|中文|英文)",
    )
    _TECH_PATTERNS = re.compile(
        r"(?:版本|框架|语言|数据库|API|接口|服务|端口|地址|配置|环境)",
    )

    def extract_from_turn(self, user_msg: str, assistant_msg: str) -> dict[str, list[str]]:
        """从单轮对话中提取新的 Memory 条目"""
        entries: dict[str, list[str]] = {}

        if not user_msg:
            return entries

        if self._GOAL_PATTERNS.search(user_msg):
            goal = self._extract_sentence(user_msg, 100)
            if goal:
                entries.setdefault("goals", []).append(goal)

        if self._PREFERENCE_PATTERNS.search(user_msg):
            pref = self._extract_sentence(user_msg, 80)
            if pref:
                entries.setdefault("preferences", []).append(pref)

        if assistant_msg and self._DECISION_PATTERNS.search(assistant_msg):
            for sentence in re.split(r"[。\n]", assistant_msg):
                if self._DECISION_PATTERNS.search(sentence) and len(sentence.strip()) > 5:
                    entries.setdefault("decisions", []).append(sentence.strip()[:120])
                    break

        if self._TECH_PATTERNS.search(user_msg):
            tech = self._extract_sentence(user_msg, 80)
            if tech and len(tech) > 8:
                entries.setdefault("tech_context", []).append(tech)

        topics = self._extract_topics(user_msg)
        if topics:
            entries["topics"] = topics

        return entries

    def _extract_sentence(self, text: str, max_len: int) -> str:
        """提取第一个有意义的句子"""
        text = text.strip()
        for sep in ["。", "\n", "，"]:
            if sep in text[:max_len]:
                return text[:text.index(sep, 0, max_len)].strip()
        return text[:max_len].strip()

    def _extract_topics(self, text: str) -> list[str]:
        """提取关键话题词"""
        topics = []
        keywords = re.findall(r"[a-zA-Z][\w\-\.]*[a-zA-Z0-9]", text)
        for kw in keywords[:5]:
            if len(kw) > 2 and kw.lower() not in ("the", "and", "for", "with", "from"):
                topics.append(kw)
        return topics


@dataclass
class MemoryEntry:
    """L2 长期记忆条目"""

    id: str
    type: str  # "semantic" | "episodic" | "preference"
    content: str
    importance: float  # 0.0 ~ 1.0
    created_at: float
    last_accessed: float
    access_count: int = 0
    tags: list[str] = field(default_factory=list)
    source_session: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "content": self.content,
            "importance": self.importance,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "tags": self.tags,
            "source_session": self.source_session,
        }

    @classmethod
    def from_dict(cls, d: dict) -> MemoryEntry:
        return cls(
            id=d["id"],
            type=d.get("type", "semantic"),
            content=d["content"],
            importance=d.get("importance", 0.5),
            created_at=d.get("created_at", time.time()),
            last_accessed=d.get("last_accessed", time.time()),
            access_count=d.get("access_count", 0),
            tags=d.get("tags", []),
            source_session=d.get("source_session", ""),
        )


class LongTermMemoryStore:
    """L2 Long-term Memory — JSON 文件持久化（线程安全）"""

    def __init__(self, memory_file: Path | None = None):
        self._file = memory_file or (MEMORY_DIR / "long_term.json")
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._entries: list[MemoryEntry] = []
        self._load()

    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            self._entries = [MemoryEntry.from_dict(e) for e in data.get("memories", [])]
            logger.info("已加载 %d 条长期记忆", len(self._entries))
        except Exception as e:
            logger.warning("加载长期记忆失败: %s", e)

    def _save(self) -> None:
        try:
            data = {"memories": [e.to_dict() for e in self._entries], "updated_at": time.time()}
            self._file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error("保存长期记忆失败: %s", e)

    def add(self, entry: MemoryEntry, *, auto_save: bool = True) -> None:
        """添加新记忆（去重：相同内容不重复添加，但提升重要性）"""
        with self._lock:
            for existing in self._entries:
                if self._is_similar(existing.content, entry.content):
                    existing.importance = min(1.0, existing.importance + 0.1)
                    existing.access_count += 1
                    existing.last_accessed = time.time()
                    if auto_save:
                        self._save()
                    return
            self._entries.append(entry)
            if auto_save:
                self._save()

    def retrieve(self, query: str, max_results: int = 10) -> list[MemoryEntry]:
        """基于 n-gram 相似度 + 关键词匹配 + 重要性 + 时效性的混合检索（线程安全）"""
        if not self._entries:
            return []

        query_lower = query.lower()
        query_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", query_lower))
        query_bigrams = _compute_bigrams(query_lower)

        scored: list[tuple[float, MemoryEntry]] = []
        now = time.time()

        for entry in self._entries:
            content_lower = entry.content.lower()
            content_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", content_lower))
            tag_tokens = set(t.lower() for t in entry.tags)

            overlap = query_tokens & (content_tokens | tag_tokens)
            keyword_score = len(overlap) / max(len(query_tokens), 1)

            content_bigrams = _compute_bigrams(content_lower)
            bigram_score = _bigram_similarity(query_bigrams, content_bigrams)

            substring_score = 0.0
            for token in query_tokens:
                if len(token) > 1 and token in content_lower:
                    substring_score += 0.15

            relevance = max(keyword_score, bigram_score) + min(substring_score, 0.3)

            if relevance < 0.05:
                continue

            recency = max(0, 1.0 - (now - entry.last_accessed) / (30 * 86400))
            score = relevance * 0.4 + entry.importance * 0.35 + recency * 0.25

            scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = [entry for _, entry in scored[:max_results]]
        with self._lock:
            for entry in results:
                entry.access_count += 1
                entry.last_accessed = now
            if results:
                self._save()

        return results

    def get_core_memories(self, importance_threshold: float = 0.6) -> list[MemoryEntry]:
        """获取核心记忆：高重要性偏好和语义知识，始终注入 system prompt"""
        with self._lock:
            core = [e for e in self._entries if e.importance >= importance_threshold]
        core.sort(key=lambda e: e.importance, reverse=True)
        return core[:10]

    def get_recent(self, n: int = 5) -> list[MemoryEntry]:
        """获取最近的 N 条记忆"""
        sorted_entries = sorted(self._entries, key=lambda e: e.created_at, reverse=True)
        return sorted_entries[:n]

    def get_all(self) -> list[MemoryEntry]:
        with self._lock:
            return list(self._entries)

    def size(self) -> int:
        return len(self._entries)

    def decay(self, max_entries: int = 200) -> int:
        """衰减清理：当超过上限时，移除最不重要且最久未访问的条目"""
        with self._lock:
            if len(self._entries) <= max_entries:
                return 0
            now = time.time()
            for entry in self._entries:
                age_days = (now - entry.last_accessed) / 86400
                entry.importance *= max(0.1, 1.0 - age_days * 0.005)

            self._entries.sort(key=lambda e: e.importance, reverse=True)
            removed = len(self._entries) - max_entries
            self._entries = self._entries[:max_entries]
            self._save()
            logger.info("记忆衰减清理: 移除 %d 条低重要性记忆", removed)
            return removed

    @staticmethod
    def _is_similar(a: str, b: str) -> bool:
        """简单判断两条记忆是否语义重复"""
        a_set = set(re.findall(r"[\w\u4e00-\u9fff]+", a.lower()))
        b_set = set(re.findall(r"[\w\u4e00-\u9fff]+", b.lower()))
        if not a_set or not b_set:
            return False
        overlap = len(a_set & b_set)
        return overlap / min(len(a_set), len(b_set)) > 0.8


class MemoryConsolidator:
    """记忆巩固器 — 将 Session Memory 中有价值的内容写入 Long-term Memory"""

    CONSOLIDATION_RULES = {
        "preferences": ("preference", 0.8),
        "decisions": ("semantic", 0.6),
        "goals": ("episodic", 0.5),
        "tech_context": ("semantic", 0.4),
    }

    def __init__(self, store: LongTermMemoryStore):
        self._store = store

    def consolidate(self, session_id: str, session_memory: SessionMemoryStore) -> int:
        """将 Session Memory 巩固到 Long-term Memory，返回新增条目数（批量保存）"""
        added = 0
        now = time.time()

        for category, (mem_type, base_importance) in self.CONSOLIDATION_RULES.items():
            items: list[str] = getattr(session_memory, category, [])
            for item in items:
                if len(item) < 5:
                    continue
                entry = MemoryEntry(
                    id=f"{mem_type}_{int(now)}_{added}",
                    type=mem_type,
                    content=item,
                    importance=base_importance,
                    created_at=now,
                    last_accessed=now,
                    tags=session_memory.topics[:3],
                    source_session=session_id,
                )
                self._store.add(entry, auto_save=False)
                added += 1

        if added > 0:
            self._store._save()
            logger.info("记忆巩固: session=%s 写入 %d 条到长期记忆", session_id, added)
            self._store.decay()

        return added


AUTO_CONSOLIDATE_IDLE_SECONDS = 600  # 10 分钟空闲后自动巩固


class MemoryManager:
    """统一 Memory 管理器 — 集成 L1 + L2 + 自动巩固（线程安全）"""

    def __init__(self):
        self._store = LongTermMemoryStore()
        self._consolidator = MemoryConsolidator(self._store)
        self._extractor = SessionMemoryExtractor()
        self._lock = threading.Lock()
        self._session_memories: dict[str, SessionMemoryStore] = {}
        self._session_last_active: dict[str, float] = {}
        self._auto_consolidate_timer: threading.Timer | None = None
        self._start_auto_consolidate()

    def get_session_memory(self, session_id: str) -> SessionMemoryStore:
        with self._lock:
            if session_id not in self._session_memories:
                self._session_memories[session_id] = SessionMemoryStore()
            return self._session_memories[session_id]

    def on_session_start(self, session_id: str, first_message: str) -> str:
        """会话开始 — 加载核心记忆 + 检索相关记忆，返回注入文本"""
        with self._lock:
            self._session_memories[session_id] = SessionMemoryStore()

        core_memories = self._store.get_core_memories()
        relevant = self._store.retrieve(first_message, max_results=5)

        seen_ids = set()
        combined: list[MemoryEntry] = []
        for entry in core_memories + relevant:
            if entry.id not in seen_ids:
                seen_ids.add(entry.id)
                combined.append(entry)

        if not combined:
            return ""

        lines = ["## 用户历史记忆"]
        for entry in combined[:8]:
            prefix = {"preference": "偏好", "semantic": "知识", "episodic": "经验"}.get(entry.type, "记忆")
            lines.append(f"- [{prefix}] {entry.content}")

        return "\n".join(lines)

    def on_turn_complete(self, session_id: str, user_msg: str, assistant_msg: str) -> None:
        """每轮对话后 — 增量提取 Session Memory"""
        with self._lock:
            self._session_last_active[session_id] = time.time()
        new_entries = self._extractor.extract_from_turn(user_msg, assistant_msg)
        if new_entries:
            session_mem = self.get_session_memory(session_id)
            session_mem.merge(new_entries)

    def on_session_end(self, session_id: str) -> int:
        """会话结束 — 巩固到长期记忆"""
        with self._lock:
            session_mem = self._session_memories.get(session_id)
            if not session_mem:
                return 0
            self._session_memories.pop(session_id, None)
        added = self._consolidator.consolidate(session_id, session_mem)
        return added

    async def llm_extract_memories(self, session_id: str, messages: list[dict]) -> dict[str, list[str]]:
        """用 LLM 从对话历史中批量提取结构化记忆（当对话轮次 ≥ 6 时调用效果更好）"""
        conversation_text = ""
        for m in messages[-20:]:
            role = m.get("role", "")
            content = m.get("content", "") or ""
            if role in ("user", "assistant") and content:
                conversation_text += f"[{role}]: {content[:300]}\n"

        if len(conversation_text) < 50:
            return {}

        extraction_prompt = (
            "从以下对话中提取值得长期记住的信息。只输出 JSON，不要解释。\n\n"
            "分类规则：\n"
            "- preferences: 用户明确表达的偏好和习惯（如编程语言、代码风格、沟通方式）\n"
            "- decisions: 已做出的技术/业务决策\n"
            "- tech_context: 项目/技术环境信息\n"
            "- goals: 用户的长期目标（不是单次请求）\n\n"
            "对话历史：\n"
            f"{conversation_text}\n\n"
            '输出格式（每类最多5条，为空则省略该类）：\n'
            '{"preferences":["..."],"decisions":["..."],"tech_context":["..."],"goals":["..."]}'
        )

        try:
            from src.models.schemas import ChatCompletionRequest, ChatMessage, ResponseFormat
            from src.config.manager import get_config_manager

            config_mgr = get_config_manager()
            enabled = config_mgr.get_enabled_models()
            if not enabled:
                return {}

            from src.scheduler.dispatcher import get_dispatcher
            dispatcher = get_dispatcher()

            req = ChatCompletionRequest(
                model="auto",
                messages=[ChatMessage(role="user", content=extraction_prompt)],
                temperature=0.1,
                max_tokens=500,
                response_format=ResponseFormat(type="json_object"),
            )

            _, _, result = await dispatcher.dispatch(req, enabled, trace_id="mem_extract")
            if not result.choices:
                return {}

            raw = result.choices[0].message.content or ""
            import json as json_mod
            data = json_mod.loads(raw)

            extracted: dict[str, list[str]] = {}
            for key in ("preferences", "decisions", "tech_context", "goals"):
                items = data.get(key, [])
                if isinstance(items, list) and items:
                    extracted[key] = [str(i)[:150] for i in items if i]

            if extracted:
                session_mem = self.get_session_memory(session_id)
                session_mem.merge(extracted)
                logger.info("[Memory] LLM 提取完成 session=%s: %s", session_id, {k: len(v) for k, v in extracted.items()})

            return extracted
        except Exception as e:
            logger.warning("[Memory] LLM 提取失败: %s", e)
            return {}

    def _start_auto_consolidate(self) -> None:
        """启动自动巩固定时器"""
        self._auto_consolidate_timer = threading.Timer(60.0, self._check_idle_sessions)
        self._auto_consolidate_timer.daemon = True
        self._auto_consolidate_timer.start()

    def _check_idle_sessions(self) -> None:
        """检查空闲会话并自动巩固"""
        try:
            now = time.time()
            with self._lock:
                idle_sessions = [
                    sid for sid, last_active in list(self._session_last_active.items())
                    if now - last_active > AUTO_CONSOLIDATE_IDLE_SECONDS
                    and sid in self._session_memories
                ]
            for sid in idle_sessions:
                added = self.on_session_end(sid)
                if added > 0:
                    logger.info("[Memory] 自动巩固空闲会话 %s: %d 条", sid, added)
                with self._lock:
                    self._session_last_active.pop(sid, None)
        except Exception as e:
            logger.warning("[Memory] 自动巩固检查异常: %s", e)
        finally:
            self._auto_consolidate_timer = threading.Timer(60.0, self._check_idle_sessions)
            self._auto_consolidate_timer.daemon = True
            self._auto_consolidate_timer.start()

    def get_context_injection(self, session_id: str) -> str:
        """获取当前会话需要注入 system prompt 的完整 memory 文本"""
        parts = []

        session_mem = self._session_memories.get(session_id)
        if session_mem:
            section = session_mem.to_system_section()
            if section:
                parts.append(section)

        return "\n\n".join(parts)

    def get_long_term_stats(self) -> dict[str, Any]:
        """获取长期记忆统计"""
        entries = self._store.get_all()
        type_counts: dict[str, int] = {}
        for e in entries:
            type_counts[e.type] = type_counts.get(e.type, 0) + 1
        return {
            "total_entries": len(entries),
            "by_type": type_counts,
            "active_sessions": len(self._session_memories),
        }


_memory_manager: MemoryManager | None = None


def get_memory_manager() -> MemoryManager:
    """获取全局 MemoryManager 单例"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager
