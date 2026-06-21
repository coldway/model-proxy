# Cursor Agent CLI 高级功能前端示例

本文档展示如何在前端调用 Model Proxy 的 Cursor Agent CLI 高级功能，包括工作区管理、会话管理和 Git Worktree。

## 完整功能演示 (Pure JavaScript)

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>Model Proxy - Cursor 高级功能</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
        h1 { color: #1e293b; }
        .panel { background: white; padding: 20px; margin: 16px 0; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        .panel h3 { margin-top: 0; color: #3b82f6; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; }
        .field { margin: 12px 0; }
        .field label { display: inline-block; width: 150px; font-weight: 600; color: #475569; }
        .field input[type="text"], .field select { width: calc(100% - 160px); padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 14px; }
        .field input[type="checkbox"] { margin-right: 8px; }
        .btn { padding: 10px 20px; margin: 4px; background: #3b82f6; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600; transition: background 0.2s; }
        .btn:hover { background: #2563eb; }
        .btn-secondary { background: #64748b; }
        .btn-secondary:hover { background: #475569; }
        .btn-success { background: #10b981; }
        .btn-success:hover { background: #059669; }
        textarea { width: 100%; padding: 12px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 14px; font-family: inherit; resize: vertical; }
        #output { white-space: pre-wrap; background: #1e293b; color: #e2e8f0; padding: 20px; border-radius: 8px; font-family: 'Monaco', 'Menlo', monospace; font-size: 13px; min-height: 200px; max-height: 500px; overflow-y: auto; }
        .info { background: #e0f2fe; border-left: 4px solid #0284c7; padding: 12px; margin: 12px 0; border-radius: 4px; font-size: 13px; }
    </style>
</head>
<body>
    <h1>🎯 Cursor Agent CLI - 高级功能演示</h1>
    
    <div class="info">
        <strong>提示：</strong>本页面展示 Model Proxy 的 Cursor Agent CLI 全功能集成，包括工作区管理、会话恢复、Git Worktree 隔离等高级能力。
    </div>

    <!-- 基础配置 -->
    <div class="panel">
        <h3>⚙️ 基础配置</h3>
        <div class="field">
            <label>执行模式:</label>
            <select id="mode">
                <option value="agent">Agent（编码）</option>
                <option value="plan">Plan（规划）</option>
                <option value="ask">Ask（问答）</option>
            </select>
        </div>
        <div class="field">
            <label>Force:</label>
            <input type="checkbox" id="force">
            <span style="color: #64748b; font-size: 12px;">强制执行（仅 Agent 模式）</span>
        </div>
        <div class="field">
            <label>Sandbox:</label>
            <select id="sandbox">
                <option value="">默认</option>
                <option value="enabled">启用</option>
                <option value="disabled">禁用</option>
            </select>
        </div>
    </div>

    <!-- 工作区管理 -->
    <div class="panel">
        <h3>📁 工作区管理</h3>
        <div class="field">
            <label>工作区路径:</label>
            <input type="text" id="workspace" placeholder="/path/to/your/project">
        </div>
        <p style="color: #64748b; font-size: 13px; margin-left: 160px;">
            留空则使用默认 cwd。指定路径覆盖默认工作目录，支持多项目场景。
        </p>
    </div>

    <!-- 会话管理 -->
    <div class="panel">
        <h3>💬 会话管理</h3>
        <div class="field">
            <label>选择会话:</label>
            <select id="session-select">
                <option value="">新会话</option>
            </select>
            <button class="btn btn-secondary" onclick="loadSessions()">🔄 刷新列表</button>
            <button class="btn btn-success" onclick="createCursorSession()">➕ 新建会话</button>
        </div>
        <div class="field">
            <label>快速恢复:</label>
            <input type="checkbox" id="continue">
            <span style="color: #64748b; font-size: 12px;">继续上次会话（--continue）</span>
        </div>
        <p style="color: #64748b; font-size: 13px; margin-left: 160px;">
            <strong>优先级：</strong>选择会话 &gt; 继续上次 &gt; 新会话
        </p>
    </div>

    <!-- Git Worktree -->
    <div class="panel">
        <h3>🌳 Git Worktree 隔离</h3>
        <div class="field">
            <label>Worktree 名称:</label>
            <input type="text" id="worktree" placeholder="feature-branch-name">
        </div>
        <div class="field">
            <label>基准分支:</label>
            <input type="text" id="worktree-base" placeholder="main">
        </div>
        <div class="field">
            <label>跳过设置脚本:</label>
            <input type="checkbox" id="skip-worktree-setup">
            <span style="color: #64748b; font-size: 12px;">跳过 .cursor/worktrees.json 中的设置脚本</span>
        </div>
        <p style="color: #64748b; font-size: 13px; margin-left: 160px;">
            在独立的 Git worktree 中运行，实现完全隔离的开发环境。
        </p>
    </div>

    <!-- 其他选项 -->
    <div class="panel">
        <h3>🔧 其他选项</h3>
        <div class="field">
            <label>自动批准 MCP:</label>
            <input type="checkbox" id="approve-mcps">
            <span style="color: #64748b; font-size: 12px;">跳过 MCP 服务器确认（自动化场景）</span>
        </div>
    </div>

    <!-- 发送请求 -->
    <div class="panel">
        <h3>📤 发送请求</h3>
        <textarea id="message" rows="4" placeholder="输入你的消息...&#10;&#10;示例：&#10;- 分析 auth.py 的安全性&#10;- 重构 database 模块&#10;- 添加单元测试"></textarea>
        <br><br>
        <button class="btn" onclick="sendAdvancedRequest()">🚀 发送请求</button>
        <button class="btn btn-secondary" onclick="clearOutput()">🗑️ 清空输出</button>
    </div>

    <!-- 输出区域 -->
    <div class="panel">
        <h3>📋 响应输出</h3>
        <div id="output">等待请求...</div>
    </div>

    <script>
        const API_BASE = 'http://localhost:8009';
        const API_KEY = 'your-api-key';  // 🔑 替换为你的 API Key

        // 创建 Cursor 会话
        async function createCursorSession() {
            const output = document.getElementById('output');
            output.textContent = '正在创建会话...\n';
            
            try {
                const res = await fetch(`${API_BASE}/api/cursor/sessions/create`, {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${API_KEY}` }
                });
                
                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
                }
                
                const data = await res.json();
                output.textContent = `✅ 会话创建成功！\n\nSession ID: ${data.session_id}\n\n已自动选中该会话。`;
                
                // 刷新列表并选中
                await loadSessions();
                document.getElementById('session-select').value = data.session_id;
            } catch (err) {
                output.textContent = `❌ 创建失败: ${err.message}`;
            }
        }

        // 加载会话列表
        async function loadSessions() {
            try {
                const res = await fetch(`${API_BASE}/api/cursor/sessions`, {
                    headers: { 'Authorization': `Bearer ${API_KEY}` }
                });
                
                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}`);
                }
                
                const data = await res.json();
                const select = document.getElementById('session-select');
                const currentVal = select.value;
                
                select.innerHTML = '<option value="">新会话</option>';
                data.sessions.forEach(s => {
                    const opt = document.createElement('option');
                    opt.value = s.id;
                    opt.textContent = s.title || `会话 ${s.id.slice(0, 8)}...`;
                    select.appendChild(opt);
                });
                
                // 恢复选中
                if (currentVal) select.value = currentVal;
                
                console.log(`已加载 ${data.sessions.length} 个会话`);
            } catch (err) {
                console.error('加载会话失败:', err);
            }
        }

        // 发送高级请求
        async function sendAdvancedRequest() {
            const message = document.getElementById('message').value.trim();
            if (!message) {
                alert('请输入消息');
                return;
            }

            const output = document.getElementById('output');
            output.textContent = '⏳ 正在发送请求...\n';

            // 构建请求参数
            const requestBody = {
                model: 'auto',
                messages: [{ role: 'user', content: message }],
                stream: true,
                
                // 基础参数
                mode: document.getElementById('mode').value,
                force: document.getElementById('force').checked,
                sandbox: document.getElementById('sandbox').value || undefined,
                
                // 工作区与会话
                workspace_path: document.getElementById('workspace').value || undefined,
                cursor_session_id: document.getElementById('session-select').value || undefined,
                cursor_continue: document.getElementById('continue').checked,
                
                // Git Worktree
                worktree_name: document.getElementById('worktree').value || undefined,
                worktree_base: document.getElementById('worktree-base').value || undefined,
                skip_worktree_setup: document.getElementById('skip-worktree-setup').checked,
                
                // 其他
                approve_mcps: document.getElementById('approve-mcps').checked
            };

            // 显示请求详情
            output.textContent = `📝 请求配置：\n${JSON.stringify(requestBody, null, 2)}\n\n${'='.repeat(60)}\n\n`;

            try {
                const res = await fetch(`${API_BASE}/v1/chat/completions`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${API_KEY}`
                    },
                    body: JSON.stringify(requestBody)
                });

                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
                }

                // 处理流式响应
                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                
                output.textContent += '💬 AI 响应：\n\n';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    const chunk = decoder.decode(value);
                    const lines = chunk.split('\n').filter(line => line.trim());

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            const data = line.slice(6);
                            if (data === '[DONE]') {
                                output.textContent += '\n\n✅ 完成';
                                output.scrollTop = output.scrollHeight;
                                return;
                            }

                            try {
                                const parsed = JSON.parse(data);
                                const content = parsed.choices[0]?.delta?.content || '';
                                if (content) {
                                    output.textContent += content;
                                    output.scrollTop = output.scrollHeight;
                                }
                            } catch (e) {
                                console.warn('解析失败:', data);
                            }
                        }
                    }
                }
            } catch (err) {
                output.textContent += `\n\n❌ 错误: ${err.message}`;
            }
        }

        // 清空输出
        function clearOutput() {
            document.getElementById('output').textContent = '等待请求...';
        }

        // 监听模式变化，自动禁用/启用 force
        document.getElementById('mode').addEventListener('change', (e) => {
            const forceCheckbox = document.getElementById('force');
            const isAgent = e.target.value === 'agent';
            forceCheckbox.disabled = !isAgent;
            if (!isAgent) forceCheckbox.checked = false;
        });

        // 页面加载时自动加载会话列表
        window.addEventListener('DOMContentLoaded', () => {
            loadSessions();
        });
    </script>
</body>
</html>
```

## 使用说明

### 1. 替换 API Key

将代码中的 `your-api-key` 替换为你的实际 API Key：

```javascript
const API_KEY = 'sk-your-actual-api-key-here';
```

### 2. 功能说明

#### 工作区路径
指定项目目录路径，留空则使用默认 cwd。适用于：
- 多项目管理
- 切换不同代码库
- CI/CD 环境

#### 会话管理
- **选择会话**：从下拉框选择历史会话（`--resume`）
- **新建会话**：预创建会话 ID
- **刷新列表**：重新加载会话列表
- **继续上次**：快速恢复最近会话（`--continue`）

**优先级**：选择会话 > 继续上次 > 新会话

#### Git Worktree
在独立的 Git worktree 中运行，实现完全隔离：
- **Worktree 名称**：独立分支名（`-w`）
- **基准分支**：如 `main` / `develop`（`--worktree-base`）
- **跳过设置**：跳过 `.cursor/worktrees.json` 设置脚本

### 3. 跨域问题

如果前端和 Model Proxy 不在同一域，需要配置 CORS。在 Model Proxy 启动时添加环境变量：

```bash
export CORS_ORIGINS="http://localhost:3000,https://your-domain.com"
python main.py
```

或在代码中配置（`main.py`）：

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 4. 最佳实践

#### 场景 1：多项目管理
```javascript
{
  workspace_path: "/path/to/backend",
  mode: "agent"
}
```

#### 场景 2：会话恢复
```javascript
{
  cursor_session_id: "chat-abc123...",  // 恢复指定会话
  mode: "agent"
}
```

或

```javascript
{
  cursor_continue: true,  // 快速恢复最近会话
  mode: "agent"
}
```

#### 场景 3：隔离开发
```javascript
{
  worktree_name: "feature-new-api",
  worktree_base: "develop",
  mode: "agent"
}
```

#### 场景 4：CI/CD 自动化
```javascript
{
  mode: "plan",
  approve_mcps: true,
  workspace_path: "/workspace"
}
```

#### 场景 5：只分析不修改
```javascript
{
  mode: "plan",
  workspace_path: "/path/to/project"
}
```

### 5. 错误处理

常见错误及解决方案：

| 错误 | 原因 | 解决方案 |
|-----|------|---------|
| `HTTP 401` | API Key 无效 | 检查 API_KEY 配置 |
| `HTTP 404` | 端点不存在 | 确认 API_BASE 正确 |
| `Session not found` | 会话 ID 无效 | 刷新会话列表或创建新会话 |
| `Worktree already exists` | Worktree 名称冲突 | 使用不同的名称或删除现有 worktree |
| `CORS error` | 跨域限制 | 配置 CORS 允许来源 |

### 6. 调试技巧

#### 查看请求详情
请求发送前会在输出区域显示完整配置：

```json
{
  "model": "auto",
  "messages": [...],
  "mode": "plan",
  "workspace_path": "/path/to/project",
  ...
}
```

#### 浏览器开发者工具
按 F12 打开开发者工具，查看 Network 标签页：
- 检查请求 Headers
- 查看 Request Payload
- 查看 Response 内容
- 检查状态码

#### Console 日志
代码中已包含关键日志：
- `console.log('已加载 N 个会话')`
- `console.warn('解析失败:', data)`
- `console.error('加载会话失败:', err)`

---

## 进阶：集成到现有项目

### React 项目

```tsx
// hooks/useCursorChat.ts
import { useState } from 'react';

export interface CursorOptions {
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

export function useCursorChat(apiKey: string) {
  const [response, setResponse] = useState('');
  const [loading, setLoading] = useState(false);

  const sendMessage = async (message: string, options: CursorOptions = {}) => {
    setLoading(true);
    setResponse('');

    const requestBody = {
      model: 'auto',
      messages: [{ role: 'user', content: message }],
      stream: true,
      ...options
    };

    try {
      const res = await fetch('http://localhost:8009/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify(requestBody)
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

            try {
              const parsed = JSON.parse(data);
              const content = parsed.choices[0]?.delta?.content || '';
              if (content) {
                accumulated += content;
                setResponse(accumulated);
              }
            } catch (e) {
              console.warn('Parse error:', e);
            }
          }
        }
      }
    } catch (err) {
      console.error('Request failed:', err);
      setResponse(`Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  return { response, loading, sendMessage };
}
```

---

## 完整 API 参考

详见主文档：[CURSOR_PLAN_MODE_INTEGRATION.md](../CURSOR_PLAN_MODE_INTEGRATION.md)
