# Cursor CLI 全功能集成 - 实施计划

## 目标
实现所有 P0+P1 功能，包括会话管理、工作区隔离、MCP 自动化，并提供完整的 UI 和文档。

---

## 阶段 1：快速参数扩展（1 小时）⚡

### 1.1 后端参数定义
**文件**: `src/models/schemas.py`

```python
class ChatCompletionRequest(BaseModel):
    # ... 现有字段 ...
    
    # 工作区相关
    workspace_path: str | None = Field(
        default=None,
        description="工作区目录路径（覆盖默认 cwd）",
    )
    
    # 会话管理相关
    cursor_session_id: str | None = Field(
        default=None,
        description="Cursor 原生会话 ID（用于 --resume）",
    )
    cursor_continue: bool = Field(
        default=False,
        description="继续上次会话（--continue）",
    )
    
    # Git Worktree 相关
    worktree_name: str | None = Field(
        default=None,
        description="Git worktree 名称（-w/--worktree）",
    )
    worktree_base: str | None = Field(
        default=None,
        description="Worktree 基准分支（--worktree-base）",
    )
    skip_worktree_setup: bool = Field(
        default=False,
        description="跳过 worktree 设置脚本（--skip-worktree-setup）",
    )
    
    # MCP 相关
    approve_mcps: bool = Field(
        default=False,
        description="自动批准所有 MCP 服务器（--approve-mcps）",
    )
```

### 1.2 CursorProvider 扩展
**文件**: `src/providers/cursor.py`

```python
def _build_cmd_args(
    self,
    model: str,
    prompt: str,
    *,
    stream: bool = False,
    json_output: bool = False,
    mode: str | None = None,
    force: bool = False,
    sandbox: str | None = None,
    workspace: str | None = None,
    resume_chat_id: str | None = None,
    continue_session: bool = False,
    worktree_name: str | None = None,
    worktree_base: str | None = None,
    skip_worktree_setup: bool = False,
    approve_mcps: bool = False,
) -> list[str]:
    cmd_args = [
        "cursor", "agent",
        "--print", "--trust",
        "--model", self._resolve_cli_model(model),
    ]
    
    # ... 现有参数 ...
    
    # 工作区
    if workspace:
        cmd_args.extend(["--workspace", workspace])
    
    # 会话管理
    if resume_chat_id:
        cmd_args.extend(["--resume", resume_chat_id])
    elif continue_session:
        cmd_args.append("--continue")
    
    # Git Worktree
    if worktree_name:
        cmd_args.extend(["-w", worktree_name])
        if worktree_base:
            cmd_args.extend(["--worktree-base", worktree_base])
        if skip_worktree_setup:
            cmd_args.append("--skip-worktree-setup")
    
    # MCP
    if approve_mcps:
        cmd_args.append("--approve-mcps")
    
    cmd_args.append(prompt)
    return cmd_args
```

### 1.3 参数传递
**文件**: `src/api/routes_pkg/chat_pipeline.py` & `chat_sessions.py`

```python
return ChatCompletionRequest(
    # ... 现有字段 ...
    workspace_path=self.request.workspace_path,
    cursor_session_id=self.request.cursor_session_id,
    cursor_continue=self.request.cursor_continue,
    worktree_name=self.request.worktree_name,
    worktree_base=self.request.worktree_base,
    skip_worktree_setup=self.request.skip_worktree_setup,
    approve_mcps=self.request.approve_mcps,
)
```

---

## 阶段 2：会话管理 API（1.5 小时）

### 2.1 创建会话端点
**文件**: `src/api/routes.py`（新增路由）

```python
@router.post("/api/cursor/sessions/create")
async def create_cursor_session(api_key: str | None = None):
    """创建新的 Cursor 会话并返回 session_id"""
    provider = CursorProvider(api_key=api_key or "")
    session_id = await provider.create_session()
    return {"session_id": session_id}
```

### 2.2 CursorProvider 新增方法
**文件**: `src/providers/cursor.py`

```python
async def create_session(self) -> str:
    """调用 cursor agent create-chat 创建新会话"""
    cmd = ["cursor", "agent", "create-chat"]
    if self._api_key:
        cmd.extend(["--api-key", self._api_key])
    
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    
    if proc.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"创建 Cursor 会话失败: {error_msg}")
    
    # 解析输出获取 session_id
    output = stdout.decode("utf-8", errors="replace").strip()
    # 预期格式：Created chat: <session_id>
    match = re.search(r"Created chat:\s*(\S+)", output)
    if match:
        return match.group(1)
    
    # 如果没有匹配，返回完整输出作为 session_id
    return output
```

### 2.3 列出会话端点（可选）
**文件**: `src/api/routes.py`

```python
@router.get("/api/cursor/sessions")
async def list_cursor_sessions(api_key: str | None = None):
    """列出所有 Cursor 会话"""
    provider = CursorProvider(api_key=api_key or "")
    sessions = await provider.list_sessions()
    return {"sessions": sessions}
```

---

## 阶段 3：UI 集成（2-3 小时）

### 3.1 Chat 页面增加控件
**文件**: `src/api/static/ui.html`

```html
<!-- 工作区选择器 -->
<div class="workspace-panel" style="...">
    <label>工作区路径:</label>
    <input type="text" id="cursor-workspace" placeholder="/path/to/project" />
    <button onclick="browseWorkspace()">浏览...</button>
</div>

<!-- 会话管理 -->
<div class="session-panel" style="...">
    <label>会话管理:</label>
    <select id="cursor-session">
        <option value="">新会话</option>
        <!-- 动态加载历史会话 -->
    </select>
    <button onclick="createCursorSession()">创建新会话</button>
    <label>
        <input type="checkbox" id="cursor-continue">
        继续上次会话
    </label>
</div>

<!-- Git Worktree -->
<div class="worktree-panel" style="...">
    <label>Git Worktree:</label>
    <input type="text" id="cursor-worktree" placeholder="worktree-name" />
    <input type="text" id="cursor-worktree-base" placeholder="base-branch" />
    <label>
        <input type="checkbox" id="cursor-skip-worktree-setup">
        跳过设置脚本
    </label>
</div>

<!-- MCP 自动批准 -->
<div class="mcp-panel" style="...">
    <label>
        <input type="checkbox" id="cursor-approve-mcps">
        自动批准 MCP 服务器
    </label>
</div>
```

### 3.2 JavaScript 函数
```javascript
function buildCursorChatExtras() {
    if (!isCursorChatModel(document.getElementById('chat-model')?.value)) return {};
    
    return {
        // 现有参数
        mode: currentCursorMode,
        force: document.getElementById('cursor-force')?.checked || false,
        sandbox: document.getElementById('cursor-sandbox')?.value || undefined,
        
        // 新增参数
        workspace_path: document.getElementById('cursor-workspace')?.value || undefined,
        cursor_session_id: document.getElementById('cursor-session')?.value || undefined,
        cursor_continue: document.getElementById('cursor-continue')?.checked || false,
        worktree_name: document.getElementById('cursor-worktree')?.value || undefined,
        worktree_base: document.getElementById('cursor-worktree-base')?.value || undefined,
        skip_worktree_setup: document.getElementById('cursor-skip-worktree-setup')?.checked || false,
        approve_mcps: document.getElementById('cursor-approve-mcps')?.checked || false,
    };
}

async function createCursorSession() {
    try {
        const res = await apiFetch(API + '/api/cursor/sessions/create', {
            method: 'POST',
        });
        const data = await res.json();
        const select = document.getElementById('cursor-session');
        const option = document.createElement('option');
        option.value = data.session_id;
        option.textContent = `会话 ${data.session_id.slice(0, 8)}`;
        select.appendChild(option);
        select.value = data.session_id;
        toast('会话创建成功', 'success');
    } catch (err) {
        toast('创建会话失败: ' + err.message, 'error');
    }
}

async function loadCursorSessions() {
    try {
        const res = await apiFetch(API + '/api/cursor/sessions');
        const data = await res.json();
        const select = document.getElementById('cursor-session');
        select.innerHTML = '<option value="">新会话</option>';
        data.sessions.forEach(s => {
            const option = document.createElement('option');
            option.value = s.id;
            option.textContent = `${s.title || s.id.slice(0, 8)}`;
            select.appendChild(option);
        });
    } catch (err) {
        console.warn('加载会话列表失败:', err);
    }
}
```

---

## 阶段 4：文档更新（1-2 小时）

### 4.1 API 参考文档
**文件**: `docs/api-reference.md`

增加完整的参数说明：
- `workspace_path`
- `cursor_session_id` & `cursor_continue`
- `worktree_name` & `worktree_base` & `skip_worktree_setup`
- `approve_mcps`

### 4.2 使用指南
**文件**: `docs/cursor-advanced-features.md`（新建）

```markdown
# Cursor Agent CLI 高级功能指南

## 1. 多项目工作区

### 使用场景
- 团队管理多个项目
- 不同项目使用不同的 Cursor 规则
- 避免工作区配置相互干扰

### API 使用
\`\`\`json
{
  "model": "auto",
  "messages": [...],
  "workspace_path": "/Users/me/projects/my-app"
}
\`\`\`

## 2. 会话管理

### 2.1 创建会话
\`\`\`bash
POST /api/cursor/sessions/create
→ { "session_id": "abc123" }
\`\`\`

### 2.2 恢复会话
\`\`\`json
{
  "model": "auto",
  "messages": [...],
  "cursor_session_id": "abc123"
}
\`\`\`

### 2.3 继续上次会话
\`\`\`json
{
  "model": "auto",
  "messages": [...],
  "cursor_continue": true
}
\`\`\`

## 3. Git Worktree 隔离

### 使用场景
- 实验性修改不影响主分支
- 并行开发多个特性
- A/B 测试代码变更

### API 使用
\`\`\`json
{
  "model": "auto",
  "messages": [...],
  "worktree_name": "feature-x",
  "worktree_base": "main"
}
\`\`\`

## 4. MCP 自动化

### 使用场景
- CI/CD 自动化脚本
- 批量任务处理
- 跳过 MCP 服务器确认

### API 使用
\`\`\`json
{
  "model": "auto",
  "messages": [...],
  "approve_mcps": true
}
\`\`\`
```

### 4.3 前端集成示例
**文件**: `docs/frontend-integration-examples.md`（更新）

增加新参数的使用示例（React/Vue/JS）

---

## 阶段 5：测试验证（1-2 小时）

### 5.1 单元测试
**文件**: `tests/test_cursor_advanced.py`（新建）

```python
class TestCursorAdvancedFeatures:
    def test_workspace_param(self):
        """测试 --workspace 参数构建"""
        provider = CursorProvider()
        cmd_args = provider._build_cmd_args(
            "sonnet-4",
            "test",
            workspace="/tmp/test-workspace"
        )
        assert "--workspace" in cmd_args
        assert "/tmp/test-workspace" in cmd_args
    
    def test_resume_param(self):
        """测试 --resume 参数构建"""
        provider = CursorProvider()
        cmd_args = provider._build_cmd_args(
            "sonnet-4",
            "test",
            resume_chat_id="abc123"
        )
        assert "--resume" in cmd_args
        assert "abc123" in cmd_args
    
    # ... 其他测试用例
```

### 5.2 端到端测试
**文件**: `scripts/test_cursor_advanced.py`（新建）

```python
async def test_workspace():
    """测试工作区功能"""
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{API_BASE}/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "当前工作区路径是?"}],
                "workspace_path": "/tmp/test-workspace",
            }
        )
        assert res.status_code == 200

async def test_session_management():
    """测试会话管理"""
    # 1. 创建会话
    res1 = await client.post(f"{API_BASE}/api/cursor/sessions/create")
    session_id = res1.json()["session_id"]
    
    # 2. 使用会话
    res2 = await client.post(
        f"{API_BASE}/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "cursor_session_id": session_id,
        }
    )
    assert res2.status_code == 200
    
    # 3. 继续会话
    res3 = await client.post(
        f"{API_BASE}/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "继续"}],
            "cursor_continue": true,
        }
    )
    assert res3.status_code == 200
```

---

## 进度追踪

| 阶段 | 预估时间 | 状态 |
|------|---------|-----|
| ✅ 阶段 1：参数扩展 | 1 小时 | ⏸️ 待开始 |
| ✅ 阶段 2：会话 API | 1.5 小时 | ⏸️ 待开始 |
| ✅ 阶段 3：UI 集成 | 2-3 小时 | ⏸️ 待开始 |
| ✅ 阶段 4：文档更新 | 1-2 小时 | ⏸️ 待开始 |
| ✅ 阶段 5：测试验证 | 1-2 小时 | ⏸️ 待开始 |
| **总计** | **8-11 小时** | **0%** |

---

## 下一步

按照计划分阶段实施，每完成一个阶段：
1. ✅ 本地测试验证
2. ✅ 更新文档
3. ✅ Git 提交
4. ✅ 进入下一阶段

准备好了就告诉我，我们从阶段 1 开始！🚀
