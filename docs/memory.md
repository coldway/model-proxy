# Agent Memory 系统设计

> 模块路径: `src/scheduler/memory.py`
> 创建日期: 2026-05-20

## 概述

Agent Memory 系统为 model-proxy 的内置聊天助手提供跨会话记忆能力，使模型能够"记住"用户的偏好、决策和技术上下文。采用两层架构：L1 Session Memory（会话级短期记忆）和 L2 Long-term Memory（跨会话持久化长期记忆）。

## 架构

```
┌─────────────────────────────────────────────────┐
│                  MemoryManager                  │  全局单例，统一管理 L1 + L2
│  ┌─────────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ Extractor   │ │ L1 Store │ │ Consolidator │  │
│  │ (规则提取)  │→│ (会话级) │→│ (巩固到 L2)  │  │
│  └─────────────┘ └──────────┘ └──────┬───────┘  │
│                                      ↓          │
│  ┌──────────────────────────────────────────┐   │
│  │        LongTermMemoryStore (L2)          │   │
│  │  JSON 持久化 → data/memory/long_term.json│   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

## L1 Session Memory

**生命周期**: 随会话创建和销毁，不持久化。

### 数据分类

| 分类 | 字段 | 提取来源 | 示例 |
|------|------|----------|------|
| 目标 | `goals` | 用户消息含"帮我/我想/需要/修复/优化"等 | "优化数据库查询性能" |
| 决策 | `decisions` | 助手回复含"决定/采用/选择/使用"等 | "采用 Redis 缓存方案" |
| 偏好 | `preferences` | 用户消息含"不要/请用/风格/格式"等 | "请用中文回答" |
| 技术上下文 | `tech_context` | 用户消息含"版本/框架/API/端口"等 | "使用 Python 3.12 + FastAPI" |
| 话题 | `topics` | 自动提取英文关键词 | ["FastAPI", "Redis", "Docker"] |

### 提取机制

- **规则化增量提取** (`SessionMemoryExtractor`): 每轮对话结束后，通过正则模式匹配用户和助手消息，按分类提取结构化信息
- **LLM 智能提取** (`llm_extract_memories`): 当对话轮次达 6/12/20 时，调用 LLM 批量提取结构化记忆（`response_format=json_object`）
- 每类最多保留 15 条（`MAX_PER_CATEGORY`），超出时丢弃最早的

### System Prompt 注入

L1 记忆通过 `to_system_section()` 生成格式化文本，追加到 system prompt：

```
## Session Memory
【当前目标】优化数据库查询性能
【已做决策】采用 Redis 缓存方案
【用户偏好】请用中文回答
【技术上下文】Python 3.12 + FastAPI
```

## L2 Long-term Memory

**生命周期**: 跨会话永久持久化，存储在 `data/memory/long_term.json`。

### 记忆条目结构

```json
{
  "id": "preference_1716192000_0",
  "type": "preference | semantic | episodic",
  "content": "用户偏好中文回答",
  "importance": 0.8,
  "created_at": 1716192000.0,
  "last_accessed": 1716192000.0,
  "access_count": 3,
  "tags": ["FastAPI", "中文"],
  "source_session": "a1b2c3d4"
}
```

### 记忆类型与基础重要性

| 类型 | type 值 | 巩固时基础重要性 | 说明 |
|------|---------|----------------|------|
| 偏好 | `preference` | 0.8 | 用户明确表达的习惯和要求 |
| 语义知识 | `semantic` | 0.6 (决策) / 0.4 (技术) | 已做决策和技术环境信息 |
| 情景经验 | `episodic` | 0.5 | 用户的长期目标 |

### 检索算法

`retrieve(query)` 使用多维度混合评分：

1. **关键词匹配**: query tokens ∩ (content tokens ∪ tag tokens)
2. **Bigram 相似度**: Dice coefficient（字符级 bigram 集合）
3. **子串匹配**: query 中长度 > 1 的 token 在 content 中的出现次数（单项上限 0.3）
4. **综合评分**: `relevance × 0.4 + importance × 0.35 + recency × 0.25`
   - `relevance` = max(keyword_score, bigram_score) + substring_score
   - `recency` = max(0, 1 - (now - last_accessed) / 30天)

### 去重与衰减

- **去重**: 新增记忆时，若与已有条目词汇重叠率 > 80%（基于 min-set Jaccard），视为重复，仅提升 importance (+0.1)
- **衰减清理**: 条目超过 200 条时触发 `decay()`，按 `importance × (1 - age_days × 0.005)` 重新排序后截断

## 巩固流程

```
L1 Session Memory ──→ MemoryConsolidator ──→ L2 Long-term Memory
```

### 触发时机

1. **会话删除**: `DELETE /api/chat/sessions/{id}` → `on_session_end()`
2. **自动巩固**: 会话空闲 10 分钟后自动触发（60s 轮询检查）
3. **手动触发**: `POST /api/memory/consolidate/{session_id}`

### 巩固规则

按分类遍历 L1 记忆，映射为 L2 条目（类型 + 基础重要性见上表），跳过长度 < 5 的条目，批量写入后统一持久化。

## 会话集成流程

```
1. 会话开始 (首条消息)
   ├── on_session_start() → 检索 L2 核心记忆 + 相关记忆 → 注入 system prompt
   └── "## 用户历史记忆\n- [偏好] ...\n- [知识] ..."

2. 每轮对话
   ├── 规则化提取 → L1 Session Memory
   ├── L1 生成 "## Session Memory" → 注入 system prompt
   └── 轮次 6/12/20 → 异步 LLM 批量提取

3. 会话结束 / 空闲 10min
   └── L1 → MemoryConsolidator → L2 持久化
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/memory/stats` | 长期记忆统计（总数、按类型分布、活跃会话数） |
| GET | `/api/memory/entries?limit=50` | 列出长期记忆条目（按最后访问时间倒序） |
| POST | `/api/memory/add` | 手动添加一条长期记忆（`{content, type}`) |
| DELETE | `/api/memory/entries/{entry_id}` | 删除一条长期记忆 |
| POST | `/api/memory/consolidate/{session_id}` | 手动触发会话记忆巩固 |
| POST | `/api/memory/import` | 批量导入记忆数据 |

## 线程安全

- `LongTermMemoryStore`: `threading.Lock` 保护所有 `_entries` 读写操作
- `MemoryManager`: `threading.Lock` 保护 `_session_memories` 和 `_session_last_active`
- 自动巩固定时器在 daemon 线程运行，通过锁与请求线程安全交互

## 文件存储

```
data/memory/
└── long_term.json    # L2 长期记忆持久化文件
```

JSON 格式：
```json
{
  "memories": [/* MemoryEntry[] */],
  "updated_at": 1716192000.0
}
```
