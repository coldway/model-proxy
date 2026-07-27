# Cursor Agent CLI 全功能集成指南

本文档说明 `model-proxy` 已完整集成的 Cursor Agent CLI 功能。

## 已实现功能总览

### P0 功能（核心能力）✅
- ✅ **多模式支持**：agent（编码）/ plan（规划）/ ask（问答）
- ✅ **工作区管理**：`--workspace` 指定项目路径
- ✅ **会话管理**：
  - `--resume <chat_id>` 恢复指定会话
  - `--continue` 快速恢复最近会话
  - `create-chat` 预创建会话
  - `ls` 列出历史会话

### P1 功能（高级能力）✅
- ✅ **Git 隔离**：
  - `--worktree` / `-w` 在独立 worktree 中运行
  - `--worktree-base` 指定基准分支
  - `--skip-worktree-setup` 跳过设置脚本
- ✅ **自动化支持**：`--approve-mcps` 自动批准 MCP 服务器
- ✅ **沙箱模式**：`sandbox=enabled|disabled`
- ✅ **强制执行**：`force=true`（agent 模式专用）

---

## 1. API 接口扩展

### 1.1 聊天补全接口

**端点**：`POST /v1/chat/completions`

**新增请求参数**（针对 Cursor provider）：

```json
{
  "model": "auto",  // 或 "cursor-agent"
  "messages": [...],
  "stream": true,
  
  // === 基础参数 ===
  "mode": "agent|plan|ask",
  "force": true,
  "sandbox": "enabled|disabled",
  
  // === P0: 工作区与会话 ===
  "workspace_path": "/path/to/project",
  "cursor_session_id": "chat-abc123...",
  "cursor_continue": true,
  
  // === P1: Git 隔离 ===
  "worktree_name": "feature-branch",
  "worktree_base": "main",
  "skip_worktree_setup": false,
  
  // === P1: 自动化 ===
  "approve_mcps": true
}
```

**参数说明**：

| 参数 | 类型 | 说明 | 默认值 |
|-----|------|------|-------|
| `mode` | string | 执行模式：agent（编码）/ plan（规划）/ ask（问答） | "agent" |
| `force` | boolean | 强制执行，跳过确认（仅 agent 模式） | false |
| `sandbox` | string | 沙箱隔离："enabled" / "disabled" / "" | "" |
| `workspace_path` | string | 工作区目录，覆盖默认 cwd | null |
| `cursor_session_id` | string | 恢复指定会话（`--resume`） | null |
| `cursor_continue` | boolean | 恢复最近会话（`--continue`） | false |
| `worktree_name` | string | Git worktree 名称（`-w`） | null |
| `worktree_base` | string | Worktree 基准分支 | null |
| `skip_worktree_setup` | boolean | 跳过 worktree 设置脚本 | false |
| `approve_mcps` | boolean | 自动批准 MCP 服务器 | false |

### 1.2 Cursor 专用接口

#### 创建会话

```bash
POST /api/cursor/sessions/create
Authorization: Bearer {api_key}

# 响应
{
  "session_id": "chat-abc123..."
}
```

#### 列出会话

```bash
GET /api/cursor/sessions
Authorization: Bearer {api_key}

# 响应
{
  "sessions": [
    {"id": "chat-abc123...", "title": "项目重构"},
    {"id": "chat-def456...", "title": "Bug 修复"}
  ]
}
```

---

## 2. Web UI 使用指南

### 2.1 基础模式选择

在 Chat 页面，选择 Cursor 模型后，会显示模式选择器：

- **Agent（编码）**：完整代码修改，需确认
- **Plan（规划）**：只分析不修改，生成方案
- **Ask（问答）**：快速问答，不修改代码

![模式选择](https://example.com/mode-selector.png)

### 2.2 高级选项

点击"高级 ▼"按钮展开高级选项面板：

#### 工作区路径
```
/path/to/project
```
留空则使用默认 cwd。用于多项目场景。

#### 会话管理
- **选择会话**：从下拉框选择历史会话
- **➕ 新建会话**：预创建会话 ID
- **🔄 刷新列表**：重新加载会话列表
- **☑ 继续上次**：快速恢复最近会话（`--continue`）

#### Git Worktree
- **worktree-name**：独立分支名称
- **基准分支**：如 `main` / `develop`
- **☑ 跳过设置**：跳过 `.cursor/worktrees.json` 设置脚本

#### MCP 选项
- **☑ 自动批准 MCP**：跳过 MCP 服务器确认（自动化场景）

---

## 3. 前端集成示例

### 3.1 纯 JavaScript

```javascript
async function sendCursorRequest(message, options = {}) {
  const response = await fetch('http://localhost:8009/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer your-api-key'
    },
    body: JSON.stringify({
      model: 'auto',
      messages: [{ role: 'user', content: message }],
      stream: true,
      
      // 基础参数
      mode: options.mode || 'agent',
      force: options.force || false,
      sandbox: options.sandbox || undefined,
      
      // 工作区与会话
      workspace_path: options.workspacePath,
      cursor_session_id: options.sessionId,
      cursor_continue: options.continue || false,
      
      // Git worktree
      worktree_name: options.worktreeName,
      worktree_base: options.worktreeBase,
      skip_worktree_setup: options.skipWorktreeSetup || false,
      
      // 自动化
      approve_mcps: options.approveMcps || false
    })
  });

  // 处理流式响应
  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    
    const chunk = decoder.decode(value);
    const lines = chunk.split('\n').filter(line => line.trim());
    
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6);
        if (data === '[DONE]') break;
        
        const parsed = JSON.parse(data);
        const content = parsed.choices[0]?.delta?.content;
        if (content) {
          console.log(content); // 输出内容
        }
      }
    }
  }
}

// 使用示例
sendCursorRequest('重构 auth 模块', {
  mode: 'plan',  // 规划模式
  workspacePath: '/path/to/project',
  sessionId: 'chat-abc123...',  // 恢复会话
  worktreeName: 'refactor-auth',  // Git 隔离
  worktreeBase: 'main'
});
```

### 3.2 React + TypeScript

```typescript
import { useState } from 'react';

interface CursorOptions {
  mode?: 'agent' | 'plan' | 'ask';
  force?: boolean;
  sandbox?: 'enabled' | 'disabled';
  workspacePath?: string;
  sessionId?: string;
  continue?: boolean;
  worktreeName?: string;
  worktreeBase?: string;
  skipWorktreeSetup?: boolean;
  approveMcps?: boolean;
}

function CursorChat() {
  const [message, setMessage] = useState('');
  const [response, setResponse] = useState('');
  const [options, setOptions] = useState<CursorOptions>({ mode: 'agent' });

  const sendMessage = async () => {
    const res = await fetch('http://localhost:8009/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${process.env.REACT_APP_API_KEY}`
      },
      body: JSON.stringify({
        model: 'auto',
        messages: [{ role: 'user', content: message }],
        stream: true,
        ...options
      })
    });

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let accumulated = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value);
      const lines = chunk.split('\n').filter(l => l.trim());

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data === '[DONE]') break;

          const parsed = JSON.parse(data);
          const content = parsed.choices[0]?.delta?.content;
          if (content) {
            accumulated += content;
            setResponse(accumulated);
          }
        }
      }
    }
  };

  return (
    <div>
      <select value={options.mode} onChange={e => setOptions({...options, mode: e.target.value as any})}>
        <option value="agent">Agent（编码）</option>
        <option value="plan">Plan（规划）</option>
        <option value="ask">Ask（问答）</option>
      </select>
      
      <input
        placeholder="工作区路径"
        value={options.workspacePath || ''}
        onChange={e => setOptions({...options, workspacePath: e.target.value})}
      />
      
      <input
        placeholder="Worktree 名称"
        value={options.worktreeName || ''}
        onChange={e => setOptions({...options, worktreeName: e.target.value})}
      />

      <label>
        <input
          type="checkbox"
          checked={options.approveMcps}
          onChange={e => setOptions({...options, approveMcps: e.target.checked})}
        />
        自动批准 MCP
      </label>

      <textarea value={message} onChange={e => setMessage(e.target.value)} />
      <button onClick={sendMessage}>发送</button>
      
      <div>{response}</div>
    </div>
  );
}
```

---

## 4. 使用场景与最佳实践

### 4.1 多项目管理

```json
{
  "workspace_path": "/path/to/backend",
  "mode": "agent"
}
```

### 4.2 会话恢复

**方式 1：恢复指定会话**
```json
{
  "cursor_session_id": "chat-abc123...",
  "mode": "agent"
}
```

**方式 2：快速恢复最近会话**
```json
{
  "cursor_continue": true,
  "mode": "agent"
}
```

### 4.3 隔离开发（Git Worktree）

```json
{
  "worktree_name": "feature-new-api",
  "worktree_base": "develop",
  "mode": "agent"
}
```

### 4.4 CI/CD 自动化

```json
{
  "mode": "plan",
  "approve_mcps": true,
  "workspace_path": "/workspace"
}
```

### 4.5 只分析不修改（Plan 模式）

```json
{
  "mode": "plan",
  "workspace_path": "/path/to/project"
}
```

---

## 5. 参数优先级与冲突处理

### 5.1 会话恢复优先级

1. `cursor_session_id` (最高优先级) - 恢复指定会话
2. `cursor_continue` - 恢复最近会话
3. 两者都不设置 - 新建会话

```python
if resume_chat_id:
    cmd_args.extend(["--resume", resume_chat_id])
elif continue_session:
    cmd_args.append("--continue")
```

### 5.2 Force 参数限制

`force=true` 仅在 `mode="agent"` 时有效：

```javascript
// UI 自动禁用
if (mode === 'plan' || mode === 'ask') {
  forceCheckbox.checked = false;
  forceCheckbox.disabled = true;
}
```

---

## 6. 错误处理

### 6.1 CLI 执行失败

```python
try:
    result = await provider.chat_completion(...)
except RuntimeError as e:
    # "Cursor CLI failed with exit code 1: ..."
    raise HTTPException(status_code=500, detail=str(e))
```

### 6.2 会话不存在

```json
{
  "error": "Session not found: chat-abc123..."
}
```

### 6.3 Worktree 冲突

```json
{
  "error": "Worktree 'feature-x' already exists"
}
```

---

## 7. 配置与环境变量

### 7.1 Cursor CLI 路径

默认使用系统 PATH 中的 `cursor`。如需自定义：

```python
# src/providers/cursor.py
CURSOR_CLI = os.getenv("CURSOR_CLI_PATH", "cursor")
```

### 7.2 默认工作区

```python
DEFAULT_WORKSPACE = os.getenv("CURSOR_DEFAULT_WORKSPACE", os.getcwd())
```

---

## 8. 测试验证

### 8.1 单元测试

```bash
pytest tests/providers/test_cursor.py -v
```

### 8.2 端到端测试

```bash
python tests/test_cursor_e2e.py
```

### 8.3 手动测试

```bash
curl -X POST http://localhost:8009/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-key" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "分析 auth.py"}],
    "mode": "plan",
    "workspace_path": "/path/to/project",
    "worktree_name": "analysis",
    "approve_mcps": true
  }'
```

---

## 9. 限制与注意事项

1. **Cursor CLI 必须已安装**：系统 PATH 中需有 `cursor` 命令
2. **会话 ID 格式**：必须是 `chat-` 开头的有效 ID
3. **Worktree 需 Git 仓库**：`--worktree` 仅在 Git 仓库中可用
4. **MCP 自动批准风险**：`approve_mcps=true` 会跳过安全确认
5. **并发限制**：同一会话不支持并发请求

---

## 10. 版本与兼容性

| model-proxy 版本 | Cursor CLI 版本 | 功能支持 |
|-----------------|----------------|---------|
| v1.0.0 | >= 0.43.x | 基础模式 |
| v2.0.0 | >= 0.43.x | 全功能集成 |

---

## 11. 参考资料

- [Cursor Agent CLI 官方文档](https://cursor.com/docs)
- [model-proxy README](../README.md)
- [Web UI 使用指南](./ui.md)
- [前端集成示例](./frontend-integration-examples.md)
