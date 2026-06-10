# Cursor Agent CLI 集成指南

## 1. 概览

model-proxy 支持 Cursor Agent CLI 的三种执行模式：

| 模式 | 说明 | 权限 | 使用场景 |
|------|------|------|----------|
| **agent** | 默认模式 | 读写文件、执行命令 | 复杂编码任务、代码重构 |
| **plan** | 规划模式 | 只读 | 设计方案、分析架构、制定计划 |
| **ask** | 问答模式 | 只读 | 代码解释、问题回答 |

> `mode` 参数仅对 Cursor provider 生效，其他 provider 会忽略。

---

## 2. API 使用

### 2.1 请求格式

在 `/v1/chat/completions` 请求中添加 `mode` 字段：

```json
{
  "model": "cursor-agent",
  "messages": [{"role": "user", "content": "分析这个项目的架构并提出重构方案"}],
  "mode": "plan"
}
```

### 2.2 Python SDK 示例

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused")

# Plan 模式：只分析不修改
response = client.chat.completions.create(
    model="cursor-agent",
    messages=[{"role": "user", "content": "分析 auth 模块并提出重构方案"}],
    extra_body={"mode": "plan"}
)

# Ask 模式：问答
response = client.chat.completions.create(
    model="cursor-agent",
    messages=[{"role": "user", "content": "这段代码的性能瓶颈在哪里？"}],
    extra_body={"mode": "ask"}
)

# Agent 模式（默认）
response = client.chat.completions.create(
    model="cursor-agent",
    messages=[{"role": "user", "content": "重构 auth 模块为 JWT 认证"}]
)

# 流式输出
stream = client.chat.completions.create(
    model="cursor-agent",
    messages=[{"role": "user", "content": "分析这个项目的技术债务"}],
    stream=True,
    extra_body={"mode": "plan"}
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### 2.3 cURL 示例

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "cursor-agent", "messages": [{"role": "user", "content": "分析项目架构"}], "mode": "plan"}'
```

---

## 3. 高级功能

### 3.1 扩展参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `mode` | string | `agent` / `plan` / `ask` |
| `force` | bool | 强制执行（仅 agent 模式生效） |
| `sandbox` | string | `enabled` / `disabled` |
| `workspace_path` | string | 覆盖默认工作区路径 |
| `cursor_session_id` | string | 恢复指定会话（`--resume`） |
| `cursor_continue` | bool | 继续上次会话（`--continue`） |
| `worktree_name` | string | Git worktree 名称（`-w`） |
| `worktree_base` | string | 基准分支（`--worktree-base`） |
| `skip_worktree_setup` | bool | 跳过 worktree 设置脚本 |
| `approve_mcps` | bool | 自动批准 MCP 服务器 |

### 3.2 典型工作流

**计划 → 执行：**

```python
plan = client.chat.completions.create(
    model="cursor-agent",
    messages=[{"role": "user", "content": "分析 auth 模块重构为 JWT 的方案"}],
    extra_body={"mode": "plan"}
)
# 确认后执行
exec_response = client.chat.completions.create(
    model="cursor-agent",
    messages=[
        {"role": "user", "content": "分析 auth 模块重构为 JWT 的方案"},
        {"role": "assistant", "content": plan.choices[0].message.content},
        {"role": "user", "content": "按照上述方案执行重构"}
    ]
)
```

**会话恢复：**

```python
response = client.chat.completions.create(
    model="cursor-agent",
    messages=[{"role": "user", "content": "继续之前的任务"}],
    extra_body={"cursor_session_id": "chat-abc123"}
)
```

**Git 隔离开发：**

```python
response = client.chat.completions.create(
    model="cursor-agent",
    messages=[{"role": "user", "content": "实现新 API"}],
    extra_body={"worktree_name": "feature-new-api", "worktree_base": "develop"}
)
```

---

## 4. 使用建议

| 场景 | 推荐模式 |
|------|----------|
| 大型重构前分析架构 | plan |
| 技术选型、方案对比 | plan |
| 代码审查、文档生成 | plan |
| 快速理解代码功能 | ask |
| 诊断 bug 原因 | ask |
| 实际编码、文件修改 | agent |
| 自动化批量重构 | agent |
| CI/CD 流水线 | plan + `approve_mcps` |

---

## 5. 日志系统

### 5.1 日志位置

- LaunchD 管理时: `logs/launchd.stdout.log`
- 直接运行时: 标准输出

### 5.2 日志格式

| 阶段 | 格式 | 级别 |
|------|------|------|
| 请求参数 | `[Cursor] 调用参数: model={model} timeout={timeout}s {cursor_params} tools={N}` | INFO |
| CLI 成功 | `[Cursor] CLI 成功: elapsed={time}s output_size={bytes} stderr={summary}` | INFO |
| CLI 失败 | `[Cursor] CLI 失败: returncode={code} elapsed={time}s stderr={full}` | ERROR |
| 响应内容 | `[Cursor] 响应: 长度={N} finish={reason} 内容预览={preview}` | INFO |
| 命令详情 | `[Cursor] 执行命令: cursor agent ...` | DEBUG |

### 5.3 日志查看

```bash
# 实时监听 Cursor 日志
tail -f logs/launchd.stdout.log | grep "\[Cursor\]"

# 查看 CLI 失败
grep "CLI 失败" logs/launchd.stdout.log

# 查看有 stderr 的请求
grep "stderr=" logs/launchd.stdout.log | grep -v "stderr=无"
```

### 5.4 截断限制

| 字段 | 截断长度 |
|------|---------|
| workspace 路径 | 50 字符 |
| session_id | 16 字符 |
| 内容预览 | 100 字符 |
| stderr（成功） | 前 5 行 |
| stderr（失败） | 前 1000 字符 |
| 命令行 | 300 字符 |

---

## 6. 配置

```yaml
# conf/config.yaml
providers:
  cursor:
    api_key: ""  # 可选，留空则使用 CURSOR_API_KEY 环境变量

# conf/providers_catalog.yaml
providers:
  cursor:
    enabled: true
    priority: 10
    models:
      - id: "cursor-agent"
        enabled: true
        priority: 1
```

---

## 7. 故障排查

| 问题 | 可能原因 | 解决方案 |
|------|---------|---------|
| 看不到 Cursor 日志 | 日志路径错误/服务未运行 | 检查 `launchctl list \| grep model-proxy` |
| stderr 显示为"无" | CLI 输出到 stdout / 仅 ANSI 序列 | 启用 DEBUG 查看完整命令 |
| 无效 mode 参数 | 拼写错误 | 回退到默认 agent 模式 |
| Session not found | 会话 ID 无效 | 刷新会话列表或创建新会话 |
| Worktree already exists | 名称冲突 | 使用不同的 worktree 名称 |

## 8. 相关文件

- Provider 实现: `src/providers/cursor.py`
- Schema 定义: `src/models/schemas.py`
- 日志配置: `main.py`
- LaunchD 配置: `~/Library/LaunchAgents/com.yuanrui.model-proxy.plist`
