# Agent Memory 系统

> 模块: `src/scheduler/memory.py`

## 架构

两层记忆系统，为内置聊天助手提供跨会话记忆。

```
L1 Session Memory（会话级，不持久化）
    ↓ 巩固（会话结束/空闲 10min）
L2 Long-term Memory（跨会话，JSON 持久化）
```

## L1 — 会话记忆

自动提取用户对话中的：目标、决策、偏好、技术上下文、话题关键词。
每轮对话通过正则匹配提取，轮次 6/12/20 时调用 LLM 批量提取。

注入方式：追加到 system prompt 的 `## Session Memory` 段落。

## L2 — 长期记忆

持久化文件：`data/memory/long_term.json`

检索评分：`relevance × 0.4 + importance × 0.35 + recency × 0.25`

去重：词汇重叠 >80% 时视为重复，仅提升 importance。
衰减：超 200 条时按 `importance × (1 - age_days × 0.005)` 截断。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/memory/stats` | 记忆统计 |
| GET | `/api/memory/entries` | 列出条目 |
| POST | `/api/memory/add` | 手动添加 |
| DELETE | `/api/memory/entries/{id}` | 删除 |
| POST | `/api/memory/consolidate/{session_id}` | 手动巩固 |
