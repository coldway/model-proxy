# Cursor Agent CLI 能力梳理 - 待接入 model-proxy

## 当前已实现功能 ✅

| 功能 | CLI 参数 | model-proxy 实现位置 | 说明 |
|------|---------|---------------------|------|
| 模型选择 | `--model` | `_build_cmd_args()` | 支持指定模型 |
| 非交互式输出 | `--print` | `_build_cmd_args()` | 脚本模式，固定启用 |
| 输出格式 | `--output-format` | `_build_cmd_args()` | text/json/stream-json |
| 流式增量 | `--stream-partial-output` | `_build_cmd_args()` | 流式输出时启用 |
| 执行模式 | `--mode` | `_build_cmd_args(mode)` | plan/ask 模式 |
| Plan 快捷方式 | `--plan` | `_build_cmd_args(mode='plan')` | plan 模式快捷参数 |
| API Key | `--api-key` | `_build_cmd_args()` | 认证支持 |
| 强制执行 | `--force` | `_build_cmd_args(force)` | 跳过确认 |
| 沙箱模式 | `--sandbox` | `_build_cmd_args(sandbox)` | enabled/disabled |
| 信任工作区 | `--trust` | `_build_cmd_args()` | 固定启用 |
| 列出模型 | `--list-models` | `list_models()` | 动态拉取可用模型 |

---

## 待接入功能清单 🚀

### 一、会话管理（高价值 ⭐⭐⭐）

#### 1.1 会话恢复 `--resume [chatId]`
**价值**：允许用户跨请求继续之前的对话，保持上下文连续性
**实现难度**：⭐⭐
**用户体验提升**：非常显著，支持长期对话

**接入设计**：
```python
# API 参数扩展
class ChatCompletionRequest:
    cursor_session_id: str | None = None  # Cursor 原生会话 ID
    
# CursorProvider 实现
def _build_cmd_args(..., resume_chat_id: str | None = None):
    if resume_chat_id:
        cmd_args.extend(["--resume", resume_chat_id])
```

**API 使用示例**：
```json
{
  "model": "auto",
  "messages": [...],
  "cursor_session_id": "abc123"
}
```

#### 1.2 继续上次会话 `--continue`
**价值**：快速恢复最近一次会话
**实现难度**：⭐
**用户体验提升**：简化恢复操作

**接入设计**：
```python
class ChatCompletionRequest:
    cursor_continue: bool = False
    
def _build_cmd_args(..., continue_session: bool = False):
    if continue_session:
        cmd_args.append("--continue")
```

#### 1.3 创建空会话 `create-chat`
**价值**：预创建会话 ID，用于后续 resume
**实现难度**：⭐⭐
**用户体验提升**：中等

**接入设计**：
```python
# 新增 API 端点
POST /api/cursor/sessions/create
→ { "session_id": "xxx" }

# CursorProvider 新增方法
async def create_session(self) -> str:
    cmd = ["cursor", "agent", "create-chat"]
    # 解析输出获取 session_id
```

#### 1.4 列出会话 `ls`
**价值**：查看所有历史会话
**实现难度**：⭐⭐
**用户体验提升**：中等

---

### 二、工作区隔离（高价值 ⭐⭐⭐⭐）

#### 2.1 指定工作区 `--workspace <path>`
**价值**：支持多项目并行，不干扰默认工作区
**实现难度**：⭐
**用户体验提升**：非常显著，多团队/多项目场景必需

**接入设计**：
```python
class ChatCompletionRequest:
    workspace_path: str | None = None
    
def _build_cmd_args(..., workspace: str | None = None):
    if workspace:
        cmd_args.extend(["--workspace", workspace])
```

**使用场景**：
- 允许前端传入不同项目路径
- model-proxy 可管理多个项目的独立工作区

#### 2.2 Git Worktree 隔离 `-w, --worktree [name]`
**价值**：在隔离的 git worktree 中运行，避免污染主分支
**实现难度**：⭐⭐
**用户体验提升**：高，适合实验性修改

**接入设计**：
```python
class ChatCompletionRequest:
    worktree_name: str | None = None
    worktree_base: str | None = None
    skip_worktree_setup: bool = False
```

---

### 三、MCP 服务器管理（中等价值 ⭐⭐）

#### 3.1 自动批准 MCP `--approve-mcps`
**价值**：跳过 MCP 服务器确认，自动化脚本场景
**实现难度**：⭐
**用户体验提升**：中等

**接入设计**：
```python
class ChatCompletionRequest:
    approve_mcps: bool = False
```

#### 3.2 MCP 管理子命令 `cursor agent mcp`
**价值**：查看/启用/禁用 MCP 服务器
**实现难度**：⭐⭐⭐
**用户体验提升**：低（管理功能，非核心聊天能力）

---

### 四、高级功能（低优先级 ⭐）

#### 4.1 自定义请求头 `-H, --header`
**价值**：支持自定义 HTTP Header
**实现难度**：⭐
**用户体验提升**：低，特殊场景

#### 4.2 插件目录 `--plugin-dir`
**价值**：加载本地插件
**实现难度**：⭐⭐
**用户体验提升**：低

#### 4.3 认证状态查询 `status` / `whoami`
**价值**：查看当前登录状态
**实现难度**：⭐
**用户体验提升**：低

#### 4.4 系统信息 `about`
**价值**：显示版本/系统信息
**实现难度**：⭐
**用户体验提升**：低

#### 4.5 自动更新 `update`
**价值**：更新 Cursor CLI
**实现难度**：⭐
**用户体验提升**：低（运维功能）

#### 4.6 生成规则 `generate-rule`
**价值**：交互式生成 Cursor 规则
**实现难度**：⭐⭐⭐
**用户体验提升**：低（非实时对话功能）

#### 4.7 Shell 集成 `install-shell-integration`
**价值**：安装 Shell 快捷命令
**实现难度**：⭐
**用户体验提升**：无（不适合 API 代理）

#### 4.8 登录/登出 `login` / `logout`
**价值**：管理认证凭据
**实现难度**：⭐⭐
**用户体验提升**：低（通过 API Key 已解决）

#### 4.9 Worker 模式 `worker`
**价值**：启动私有云 worker
**实现难度**：⭐⭐⭐⭐
**用户体验提升**：无（与 API 代理场景不符）

---

## 优先级推荐

### 🔥 P0（强烈建议实现）

| 功能 | 价值 | 难度 | ROI |
|------|-----|-----|-----|
| **指定工作区** `--workspace` | ⭐⭐⭐⭐ | ⭐ | 🔥🔥🔥 |
| **会话恢复** `--resume` | ⭐⭐⭐ | ⭐⭐ | 🔥🔥🔥 |
| **继续会话** `--continue` | ⭐⭐⭐ | ⭐ | 🔥🔥 |

**理由**：
- `--workspace` 是多项目支持的核心，实现简单价值极高
- `--resume` 和 `--continue` 解决上下文连续性问题，显著提升用户体验

---

### 📌 P1（建议实现）

| 功能 | 价值 | 难度 | ROI |
|------|-----|-----|-----|
| **创建会话** `create-chat` | ⭐⭐ | ⭐⭐ | 🔥🔥 |
| **Git Worktree** `--worktree` | ⭐⭐⭐ | ⭐⭐ | 🔥 |
| **自动批准 MCP** `--approve-mcps` | ⭐⭐ | ⭐ | 🔥 |

**理由**：
- `create-chat` 配合 `--resume` 使用，完善会话管理
- `--worktree` 适合实验性修改场景
- `--approve-mcps` 简化自动化流程

---

### 🔖 P2（可选实现）

| 功能 | 价值 | 难度 | ROI |
|------|-----|-----|-----|
| **列出会话** `ls` | ⭐⭐ | ⭐⭐ | 🔥 |
| **自定义请求头** `-H` | ⭐ | ⭐ | 低 |
| **认证状态** `status` | ⭐ | ⭐ | 低 |

---

### ❌ P3（不建议实现）

| 功能 | 说明 |
|------|------|
| `worker` | 与 API 代理场景不符 |
| `install-shell-integration` | 不适合 API 化 |
| `login` / `logout` | API Key 已解决认证 |
| `update` | 运维功能，非核心能力 |
| `generate-rule` | 交互式工具，不适合 API |

---

## 实现工作量估算

### 快速实现（1-2 小时）
- ✅ `--workspace` - 参数传递
- ✅ `--continue` - 参数传递
- ✅ `--approve-mcps` - 参数传递
- ✅ `-H, --header` - 参数传递

### 中等实现（3-5 小时）
- 🔧 `--resume [chatId]` - 参数传递 + 会话 ID 管理
- 🔧 `create-chat` - 新增 CLI 调用 + 解析输出
- 🔧 `--worktree` - 参数传递 + 文档说明

### 复杂实现（1 天以上）
- ⚠️ `ls` 命令 - 需解析列表输出 + 新增 API 端点
- ⚠️ `mcp` 子命令 - 完整的 MCP 管理功能

---

## 建议实施路径

### 阶段 1：核心会话管理（3-4 小时）
1. `--workspace` - 支持多项目
2. `--resume` + `--continue` - 会话连续性
3. 文档更新 + 测试

### 阶段 2：高级工作区（2-3 小时）
1. `--worktree` - Git 隔离
2. `create-chat` - 预创建会话
3. 前端 UI 集成示例

### 阶段 3：辅助功能（按需）
1. `--approve-mcps` - 自动化支持
2. `-H, --header` - 高级场景
3. `ls` / `status` - 查询类功能

---

## 下一步

请选择你希望优先实现的功能：

**A. 快速上线（1-2 小时）**
- `--workspace` + `--continue` + `--approve-mcps`

**B. 完整会话管理（3-4 小时）**
- `--workspace` + `--resume` + `--continue` + `create-chat`

**C. 全功能集成（1-2 天）**
- 所有 P0 + P1 功能 + 前端 UI + 文档

**D. 自定义选择**
- 从上述列表中挑选你需要的功能

请告诉我你的选择，我会立即开始实现！
