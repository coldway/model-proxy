# Cursor Provider 日志完整性指南

## 概述

`model-proxy` 的 Cursor Provider 已实现完整的日志记录，包括请求参数、执行过程和响应内容。

## 日志位置

- **LaunchD 管理时**: `/Users/yuanrui/go/src/gitlab.p1staff.com/new/AI-Coding/workspace/AI-agent/model-proxy/logs/launchd.stdout.log`
- **直接运行时**: 标准输出或配置的日志文件

## 日志内容详解

### 1. 请求参数日志

**格式**：
```
[Cursor] 调用参数: model={model} timeout={timeout}s {cursor_params} tools={tool_count}
```

**包含信息**：
- `model`: 使用的模型名称（如 `auto`、`composer-2.5`）
- `timeout`: 超时时间（秒）
- `cursor_params`: Cursor 特有参数：
  - `mode=agent/plan/ask` - 运行模式
  - `workspace=/path/to/workspace...` - 工作区路径（截断显示）
  - `session_id=abc123...` - 会话 ID（如使用 `--resume`）
  - `continue=True` - 是否继续上次会话
  - `worktree=branch-name` - Git worktree 名称
  - `approve_mcps=True` - 是否自动批准 MCP 服务器
- `tools`: 工具数量

**示例**：
```
2026-05-27 17:53:26,773 [INFO] src.providers.cursor: [Cursor] 调用参数: model=auto timeout=600s mode=ask workspace=/Users/yuanrui/go/src/gitlab.p1staff.com/new/AI-Co... tools=0
```

### 2. CLI 执行日志

#### 成功场景

**格式**：
```
[Cursor] CLI 成功: elapsed={time}s output_size={bytes} stderr={stderr_summary}
```

**包含信息**：
- `elapsed`: 执行耗时（秒，精确到 0.1s）
- `output_size`: stdout 输出大小（字节）
- `stderr`: stderr 内容摘要：
  - `无` - 没有 stderr 输出
  - `无实质内容（仅控制字符）` - 仅包含控制字符/ANSI 转义序列
  - `{N}行: line1 | line2 | ... (共{N}行)` - 有实质内容时显示前5行+总行数

**示例**：
```
2026-05-27 18:08:22,213 [INFO] src.providers.cursor: [Cursor] CLI 成功: elapsed=58.7s output_size=686 stderr=无
```

**带 stderr 的示例**：
```
[Cursor] CLI 成功: elapsed=43.9s output_size=898 stderr=3行: Warning: workspace not clean | Using fallback model | Done (共3行)
```

#### 失败场景

**格式**：
```
[Cursor] CLI 失败: returncode={code} elapsed={time}s stderr={full_stderr}
```

**包含信息**：
- `returncode`: 退出码（非0）
- `elapsed`: 执行耗时
- `stderr`: 完整的 stderr 输出（截断到前1000字符）

**示例**：
```
2026-05-27 16:30:48,168 [ERROR] src.providers.cursor: [Cursor] CLI 失败: returncode=1 elapsed=5.2s stderr=Error: failed to connect to agent server
```

### 3. 响应内容日志

**格式**：
```
[Cursor] 响应: 长度={length} finish={reason}{tool_info} 内容预览={preview}
```

**包含信息**：
- `长度`: 响应内容长度（字符数）
- `finish`: 结束原因（stop/length/tool_calls）
- `tool_info`: 如果有 tool_calls，显示 `tool_calls={N}个`
- `内容预览`: 响应内容前100字符（`\n` 转义为 `\\n`）

**示例**：
```
2026-05-27 18:08:22,214 [INFO] src.providers.cursor: [Cursor] 响应: 长度=686 finish=stop 内容预览=**简短说明：如何验证 stderr 日志**\n\n项目里有两层「stderr」：\n\n1. **Cursor CLI 子进程的 stderr**（`src/providers/cursor.py`）
```

**带 tool_calls 的示例**：
```
[Cursor] 响应: 长度=234 finish=tool_calls tool_calls=2个 内容预览=正在调用 read_file 和 grep 工具...
```

### 4. DEBUG 级别日志

**命令行日志**（需启用 DEBUG 级别）：
```
[Cursor] 执行命令: cursor agent --workspace /path/to/workspace --mode ask ...
```

包含完整的命令行参数（长参数会被截断）。

## 日志级别

| 级别 | 场景 | 示例 |
|-----|------|------|
| DEBUG | CLI 命令行（详细） | `[Cursor] 执行命令: cursor agent ...` |
| INFO | 正常流程 | 调用参数、CLI 成功、响应 |
| WARNING | 非致命问题 | 未知 mode、未知 sandbox 值 |
| ERROR | 执行失败 | CLI 失败、超时 |

## stderr 输出处理

### 自动清理

- 去除 ANSI 转义序列（如颜色码、光标控制）
- 去除纯控制字符行
- 去除空行

### 格式化

- 按行分割
- 成功时显示前5行摘要+总行数
- 失败时显示前1000字符

### 典型 stderr 内容

Cursor CLI 的 stderr 可能包含：
- 进度信息（如 "Analyzing workspace..."）
- 警告信息（如 "Warning: using cached model"）
- MCP 服务器日志
- 调试信息（如 "--verbose" 模式）

## 查看日志

### 实时监听 Cursor 相关日志

```bash
tail -f /Users/yuanrui/go/src/gitlab.p1staff.com/new/AI-Coding/workspace/AI-agent/model-proxy/logs/launchd.stdout.log | grep "\[Cursor\]"
```

### 查看特定 trace 的完整日志

```bash
grep "trace=abc123" /path/to/launchd.stdout.log
```

### 查看所有 CLI 失败

```bash
grep "CLI 失败" /path/to/launchd.stdout.log
```

### 查看带 stderr 的请求

```bash
grep "stderr=" /path/to/launchd.stdout.log | grep -v "stderr=无"
```

## 完整调用示例

```
2026-05-27 17:53:26,769 [INFO] src.api.routes_pkg.chat_completions: [API] trace=816432a8fa84 /v1/chat/completions | model=auto stream=False 消息数=1
2026-05-27 17:53:26,772 [INFO] src.scheduler.dispatcher_mixins.provider_call: [推理] trace=816432a8fa84 推理请求 cursor:auto | 消息数=1 payload=0.1KB temp=0.7 [user×1] 最新用户消息: 'hello, 这是一个测试请求，请简单回答'
2026-05-27 17:53:26,773 [INFO] src.providers.cursor: [Cursor] 调用参数: model=auto timeout=600s mode=ask workspace=/Users/yuanrui/go/src/gitlab.p1staff.com/new/AI-Co... tools=0
2026-05-27 17:54:30,037 [INFO] src.providers.cursor: [Cursor] CLI 成功: elapsed=63.3s output_size=38 stderr=无
2026-05-27 17:54:30,038 [INFO] src.providers.cursor: [Cursor] 响应: 长度=38 finish=stop 内容预览=你好，测试请求已收到。\n\n我这边工作正常，可以继续提问或说明你想了解的内容。
2026-05-27 17:54:30,039 [INFO] src.scheduler.dispatcher_mixins.provider_call: [推理] trace=816432a8fa84 推理响应 cursor:auto | 耗时=63265ms tokens(prompt=0,completion=0,total=0) 响应长度=38 finish_reason=stop
2026-05-27 17:54:30,039 [INFO] src.api.routes_pkg.chat_completions: [API] trace=816432a8fa84 完成 | provider=cursor model=auto 耗时=63268ms
```

## 故障排查

### 问题：看不到 Cursor 日志

**检查**：
1. 确认日志文件路径正确（LaunchD 配置中的 StandardOutPath）
2. 确认服务正在运行：`launchctl list | grep model-proxy`
3. 确认日志级别未设置为 WARNING 或更高

### 问题：stderr 显示为"无"但应该有内容

**可能原因**：
1. Cursor CLI 将信息输出到 stdout 而非 stderr
2. 内容仅为 ANSI 转义序列，已被清理

**检查**：
- 启用 DEBUG 级别查看完整命令
- 手动执行相同的 Cursor CLI 命令查看原始输出

### 问题：日志被截断

**当前限制**：
- workspace 路径：截断到50字符
- session_id：截断到16字符
- 内容预览：100字符
- stderr（成功）：前5行
- stderr（失败）：前1000字符
- 命令行：300字符

**如需完整日志**：修改 `src/providers/cursor.py` 中的相应截断长度。

## 配置建议

### 生产环境

- 日志级别：INFO
- 日志轮转：建议配置（防止日志文件过大）
- 保留时间：7-30天

### 开发/调试环境

- 日志级别：DEBUG
- 保留全部 stderr 输出（修改代码去除截断）
- 实时查看：`tail -f logs/launchd.stdout.log`

## 相关文件

- 实现：`src/providers/cursor.py`
- 日志配置：`main.py` 中的 `logging.basicConfig()`
- LaunchD 配置：`/Users/yuanrui/Library/LaunchAgents/com.yuanrui.model-proxy.plist`

## 更新历史

- 2026-05-27: 初始版本，包含调用参数、CLI执行、响应内容完整日志
- 2026-05-27: 增强 stderr 处理，包括非错误信息的详细记录
