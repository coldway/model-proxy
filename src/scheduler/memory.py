# Created by model-proxy on 2026/05/20
# Copyright © 2026

"""
Agent Memory System — L1 Session Memory + L2 Long-term Memory + Constitution + Procedural

基于 agent-base/docs/agent_memory_system.md 设计实现：
- L1: 规则化增量提取 (goals/decisions/preferences/tech_context)，注入 system prompt
- L2: 跨会话持久化（JSON 文件），会话开始时检索相关记忆并注入
- Constitution: 记忆宪法 — 定义什么该记、什么不记
- Procedural Memory: 程序记忆 — 操作流程模板
- Entity Graph: 实体关系图谱 — 增强跨话题检索
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MEMORY_DIR = Path("data/memory")


# ============================================================
# Memory Constitution — 记忆宪法
# ============================================================

@dataclass
class MemoryConstitution:
    """记忆宪法：定义 Agent 的记忆行为原则（源自 Letta）"""

    identity_principles: list[str] = field(default_factory=lambda: [
        "保持跨会话的身份连续性：记得之前的交互和用户偏好",
        "用户偏好和项目知识应被视为核心记忆，优先保留",
        "历史决策和设计选择应被视为情景记忆，按重要性保留",
    ])

    storage_constraints: list[str] = field(default_factory=lambda: [
        "不存储敏感信息（密码、Token、API Key）",
        "不存储临时调试信息（单次查询结果、中间变量值）",
        "不存储可从代码/文档中直接获取的信息",
        "工具调用的完整输出应压缩为摘要而非原文存储",
    ])

    priority_rules: list[str] = field(default_factory=lambda: [
        "用户显式纠正 > 历史推断",
        "最新偏好 > 旧偏好",
        "多次重复出现的模式 > 单次提及",
        "决策原因 > 决策结果",
    ])

    def to_system_section(self) -> str:
        """生成注入 system prompt 的宪法文本"""
        sections = ["## Memory Constitution"]
        sections.append("### 记忆原则")
        for p in self.identity_principles:
            sections.append(f"- {p}")
        sections.append("### 存储约束")
        for c in self.storage_constraints:
            sections.append(f"- {c}")
        sections.append("### 优先级")
        for r in self.priority_rules:
            sections.append(f"- {r}")
        return "\n".join(sections)

    def should_store(self, content: str) -> bool:
        """判断内容是否应该存储（简单规则过滤）"""
        sensitive_patterns = [
            r"(?:api[_-]?key|token|password|secret)\s*[:=]\s*\S+",
            r"sk-[a-zA-Z0-9]{20,}",
            r"(?:bearer|authorization)\s+\S{20,}",
        ]
        for pattern in sensitive_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return False
        return True


# ============================================================
# Procedural Memory — 程序记忆
# ============================================================

@dataclass
class ProceduralMemoryEntry:
    """一条程序记忆 — 操作流程模板"""
    id: str
    name: str
    trigger: str
    steps: list[str]
    tools_used: list[str] = field(default_factory=list)
    success_count: int = 0
    fail_count: int = 0
    last_used: float = 0.0
    created_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        return self.success_count / total if total > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "trigger": self.trigger,
            "steps": self.steps,
            "tools_used": self.tools_used,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "last_used": self.last_used,
            "created_at": self.created_at,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProceduralMemoryEntry:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ProceduralMemoryStore:
    """程序记忆存储 — 操作流程模板库"""

    def __init__(self, file_path: Path | None = None):
        self._file = file_path or (MEMORY_DIR / "procedural.json")
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._entries: list[ProceduralMemoryEntry] = []
        self._load()

    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            self._entries = [ProceduralMemoryEntry.from_dict(e) for e in data.get("procedures", [])]
        except Exception as e:
            logger.warning("加载程序记忆失败: %s", e)

    def _save(self) -> None:
        try:
            data = {"procedures": [e.to_dict() for e in self._entries]}
            content = json.dumps(data, ensure_ascii=False, indent=2)
            self._file.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=str(self._file.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(self._file))
        except Exception as e:
            logger.error("保存程序记忆失败: %s", e)

    def add(self, entry: ProceduralMemoryEntry) -> None:
        with self._lock:
            for existing in self._entries:
                if existing.name == entry.name:
                    existing.steps = entry.steps
                    existing.tools_used = entry.tools_used
                    existing.last_used = time.time()
                    self._save()
                    return
            self._entries.append(entry)
            self._save()

    def find_by_trigger(self, query: str, limit: int = 3) -> list[ProceduralMemoryEntry]:
        """根据触发条件检索相关的程序记忆"""
        query_lower = query.lower()
        query_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", query_lower))

        scored: list[tuple[float, ProceduralMemoryEntry]] = []
        for entry in self._entries:
            trigger_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", entry.trigger.lower()))
            name_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", entry.name.lower()))
            tag_tokens = set(t.lower() for t in entry.tags)
            all_tokens = trigger_tokens | name_tokens | tag_tokens

            overlap = len(query_tokens & all_tokens)
            if overlap == 0:
                continue
            score = overlap / max(len(query_tokens), 1) + entry.success_rate * 0.2
            scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:limit]]

    def record_usage(self, entry_id: str, success: bool) -> None:
        with self._lock:
            for entry in self._entries:
                if entry.id == entry_id:
                    if success:
                        entry.success_count += 1
                    else:
                        entry.fail_count += 1
                    entry.last_used = time.time()
                    self._save()
                    return

    def get_all(self) -> list[ProceduralMemoryEntry]:
        with self._lock:
            return list(self._entries)

    def size(self) -> int:
        return len(self._entries)


# ============================================================
# Entity Graph — 实体关系图谱
# ============================================================

@dataclass
class EntityRelation:
    """实体关系"""
    subject: str
    predicate: str
    obj: str
    weight: float = 1.0
    source_memory_id: str = ""


class EntityGraph:
    """简易实体关系图谱 — 增强记忆检索"""

    def __init__(self):
        self._lock = threading.Lock()
        self._entities: dict[str, set[str]] = {}
        self._relations: list[EntityRelation] = []

    def add_entity(self, entity: str, memory_ids: list[str]) -> None:
        with self._lock:
            key = entity.lower()
            if key not in self._entities:
                self._entities[key] = set()
            self._entities[key].update(memory_ids)

    def add_relation(self, relation: EntityRelation) -> None:
        with self._lock:
            self._relations.append(relation)

    def find_related_memories(self, query: str, limit: int = 10) -> set[str]:
        """通过实体匹配找到相关记忆 ID"""
        query_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", query.lower()))
        memory_ids: set[str] = set()
        with self._lock:
            for token in query_tokens:
                if token in self._entities:
                    memory_ids.update(self._entities[token])
                for entity_key in self._entities:
                    if token in entity_key or entity_key in token:
                        memory_ids.update(self._entities[entity_key])
        return set(list(memory_ids)[:limit])

    def extract_and_index(self, text: str, memory_id: str) -> list[str]:
        """从文本中提取实体并建立索引"""
        entities: list[str] = []
        code_ids = re.findall(r"[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)+", text)
        entities.extend(code_ids[:5])
        cn_terms = re.findall(r"[\u4e00-\u9fff]{2,6}", text)
        entities.extend(cn_terms[:5])
        tech_terms = re.findall(r"\b(?:gRPC|Redis|Kafka|PostgreSQL|HTTP|API|Docker)\b", text, re.IGNORECASE)
        entities.extend(tech_terms[:3])

        for entity in entities:
            self.add_entity(entity, [memory_id])
        return entities

    def size(self) -> tuple[int, int]:
        """返回 (实体数, 关系数)"""
        return len(self._entities), len(self._relations)


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

    def save(self) -> None:
        """持久化长期记忆到磁盘（原子写入）"""
        self._save()

    def _save(self) -> None:
        try:
            data = {"memories": [e.to_dict() for e in self._entries], "updated_at": time.time()}
            content = json.dumps(data, ensure_ascii=False, indent=2)
            self._file.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=str(self._file.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(self._file))
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
        with self._lock:
            snapshot = list(self._entries)
        if not snapshot:
            return []

        query_lower = query.lower()
        query_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", query_lower))
        query_bigrams = _compute_bigrams(query_lower)

        scored: list[tuple[float, MemoryEntry]] = []
        now = time.time()

        for entry in snapshot:
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

        result_ids = {id(entry) for _, entry in scored[:max_results]}
        results = [entry for _, entry in scored[:max_results]]
        with self._lock:
            for entry in self._entries:
                if id(entry) in result_ids:
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
        with self._lock:
            sorted_entries = sorted(self._entries, key=lambda e: e.created_at, reverse=True)
        return sorted_entries[:n]

    def get_all(self) -> list[MemoryEntry]:
        with self._lock:
            return list(self._entries)

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def decay(self, max_entries: int = 200, half_life_days: float = 7.0) -> int:
        """指数衰减清理（源自 CrewAI Recency Decay + Half-life 公式）

        effective_score = importance * exp(-0.693 * days / half_life)
        低于 min_score 阈值或超出上限的条目被移除。
        """
        with self._lock:
            if len(self._entries) <= max_entries:
                return 0
            now = time.time()
            for entry in self._entries:
                days_ago = (now - entry.last_accessed) / 86400
                recency = math.exp(-0.693 * days_ago / half_life_days)
                entry.importance = entry.importance * recency
                if entry.access_count > 10:
                    entry.importance = max(entry.importance, 0.3)

            self._entries.sort(key=lambda e: e.importance, reverse=True)
            removed = len(self._entries) - max_entries
            self._entries = self._entries[:max_entries]
            self._save()
            logger.info("记忆衰减清理: 移除 %d 条低重要性记忆 (half_life=%.1fd)", removed, half_life_days)
            return removed

    def remove_by_id(self, entry_id: str) -> bool:
        """按 ID 删除记忆条目（线程安全）"""
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.id != entry_id]
            if len(self._entries) < before:
                self._save()
                return True
            return False

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
                    id=f"{mem_type}_{uuid.uuid4().hex[:8]}",
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
            self._store.save()
            logger.info("记忆巩固: session=%s 写入 %d 条到长期记忆", session_id, added)
            self._store.decay()

        return added


AUTO_CONSOLIDATE_IDLE_SECONDS = 1800  # 30 分钟空闲后自动巩固（原 10 分钟过短）


class MemoryManager:
    """统一 Memory 管理器 — 集成 L1 + L2 + Constitution + Procedural + EntityGraph（线程安全）"""

    def __init__(self):
        self._store = LongTermMemoryStore()
        self._consolidator = MemoryConsolidator(self._store)
        self._extractor = SessionMemoryExtractor()
        self._constitution = MemoryConstitution()
        self._procedural = ProceduralMemoryStore()
        self._entity_graph = EntityGraph()
        self._lock = threading.Lock()
        self._closed = False
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
        """会话开始 — 加载核心记忆 + 检索相关记忆 + 程序记忆，返回注入文本"""
        with self._lock:
            self._session_memories[session_id] = SessionMemoryStore()

        core_memories = self._store.get_core_memories()
        relevant = self._store.retrieve(first_message, max_results=5)

        entity_memory_ids = self._entity_graph.find_related_memories(first_message)
        entity_entries: list[MemoryEntry] = []
        if entity_memory_ids:
            all_entries = self._store.get_all()
            entity_entries = [e for e in all_entries if e.id in entity_memory_ids][:3]

        seen_ids = set()
        combined: list[MemoryEntry] = []
        for entry in core_memories + relevant + entity_entries:
            if entry.id not in seen_ids:
                seen_ids.add(entry.id)
                combined.append(entry)

        parts: list[str] = []

        if combined:
            lines = ["## 用户历史记忆"]
            for entry in combined[:8]:
                prefix = {"preference": "偏好", "semantic": "知识", "episodic": "经验"}.get(entry.type, "记忆")
                lines.append(f"- [{prefix}] {entry.content}")
            parts.append("\n".join(lines))

        procedures = self._procedural.find_by_trigger(first_message, limit=2)
        if procedures:
            proc_lines = ["## 相关操作流程"]
            for proc in procedures:
                proc_lines.append(f"### {proc.name} (成功率:{proc.success_rate:.0%})")
                for step in proc.steps[:5]:
                    proc_lines.append(f"  {step}")
            parts.append("\n".join(proc_lines))

        return "\n\n".join(parts) if parts else ""

    def on_turn_complete(self, session_id: str, user_msg: str, assistant_msg: str) -> None:
        """每轮对话后 — 增量提取 Session Memory + 实体索引"""
        with self._lock:
            self._session_last_active[session_id] = time.time()
        new_entries = self._extractor.extract_from_turn(user_msg, assistant_msg)
        if new_entries:
            session_mem = self.get_session_memory(session_id)
            session_mem.merge(new_entries)

        combined_text = user_msg + " " + (assistant_msg or "")
        if len(combined_text) > 20:
            mem_id = f"turn_{session_id}_{int(time.time())}"
            self._entity_graph.extract_and_index(combined_text, mem_id)

    def on_session_end(self, session_id: str) -> int:
        """会话结束 — 巩固到长期记忆"""
        with self._lock:
            session_mem = self._session_memories.get(session_id)
            if not session_mem:
                return 0
            self._session_memories.pop(session_id, None)
            self._session_last_active.pop(session_id, None)
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
            from src.api.routes import _deps

            if not _deps.config_manager or not _deps.dispatcher:
                logger.debug("[Memory] LLM 提取跳过: 依赖未初始化")
                return {}
            enabled = _deps.config_manager.get_enabled_models()
            if not enabled:
                return {}

            dispatcher = _deps.dispatcher

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
        """检查空闲会话并自动巩固（巩固后保留记忆，不清除 session_memories）"""
        try:
            now = time.time()
            with self._lock:
                idle_sessions = [
                    sid for sid, last_active in list(self._session_last_active.items())
                    if now - last_active > AUTO_CONSOLIDATE_IDLE_SECONDS
                    and sid in self._session_memories
                ]
            for sid in idle_sessions:
                with self._lock:
                    session_mem = self._session_memories.get(sid)
                    if not session_mem:
                        continue
                added = self._consolidator.consolidate(sid, session_mem)
                if added > 0:
                    logger.info("[Memory] 自动巩固空闲会话 %s: %d 条（记忆保留）", sid, added)
                with self._lock:
                    self._session_last_active.pop(sid, None)
        except Exception as e:
            logger.warning("[Memory] 自动巩固检查异常: %s", e)
        finally:
            if not self._closed:
                self._auto_consolidate_timer = threading.Timer(60.0, self._check_idle_sessions)
                self._auto_consolidate_timer.daemon = True
                self._auto_consolidate_timer.start()

    def close(self) -> None:
        """停止自动巩固定时器（用于优雅关闭）"""
        self._closed = True
        if self._auto_consolidate_timer:
            self._auto_consolidate_timer.cancel()
            self._auto_consolidate_timer = None

    def retrieve_for_turn(self, session_id: str, user_msg: str, max_results: int = 3) -> str:
        """每轮检索相关长期记忆（轻量，仅在消息足够长时触发），返回注入文本"""
        if len(user_msg) < 10:
            return ""
        relevant = self._store.retrieve(user_msg, max_results=max_results)
        if not relevant:
            return ""
        lines = ["[相关记忆]"]
        for entry in relevant:
            prefix = {"preference": "偏好", "semantic": "知识", "episodic": "经验"}.get(entry.type, "记忆")
            lines.append(f"- [{prefix}] {entry.content}")
        return "\n".join(lines)

    def get_context_injection(self, session_id: str, user_msg: str = "") -> str:
        """获取当前会话需要注入 system prompt 的完整 memory 文本（含 L1 + L2 每轮检索）"""
        with self._lock:
            session_mem = self._session_memories.get(session_id)

        parts: list[str] = []
        if session_mem:
            section = session_mem.to_system_section()
            if section:
                parts.append(section)

        if user_msg:
            turn_memory = self.retrieve_for_turn(session_id, user_msg)
            if turn_memory:
                parts.append(turn_memory)

        return "\n\n".join(parts) if parts else ""

    def get_long_term_stats(self) -> dict[str, Any]:
        """获取长期记忆统计"""
        entries = self._store.get_all()
        type_counts: dict[str, int] = {}
        for e in entries:
            type_counts[e.type] = type_counts.get(e.type, 0) + 1
        entity_count, relation_count = self._entity_graph.size()
        return {
            "total_entries": len(entries),
            "by_type": type_counts,
            "active_sessions": len(self._session_memories),
            "procedural_count": self._procedural.size(),
            "entity_count": entity_count,
            "relation_count": relation_count,
        }

    @property
    def constitution(self) -> MemoryConstitution:
        return self._constitution

    @property
    def procedural(self) -> ProceduralMemoryStore:
        return self._procedural

    @property
    def entity_graph(self) -> EntityGraph:
        return self._entity_graph

    def should_store_memory(self, content: str) -> bool:
        """使用 Constitution 判断内容是否应该存储"""
        return self._constitution.should_store(content)


_memory_manager: MemoryManager | None = None
_memory_manager_lock = threading.Lock()


def get_memory_manager() -> MemoryManager:
    """获取全局 MemoryManager 单例（线程安全双重检查锁）"""
    global _memory_manager
    if _memory_manager is None:
        with _memory_manager_lock:
            if _memory_manager is None:
                _memory_manager = MemoryManager()
    return _memory_manager
