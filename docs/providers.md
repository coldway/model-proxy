# 厂商适配器

所有厂商适配器继承自 `BaseProvider` 抽象基类，实现三个核心方法。

## BaseProvider 接口

```python
class BaseProvider(ABC):
    def __init__(self, api_key: str): ...

    async def chat_completion(self, model: str, request: ChatCompletionRequest) -> ChatCompletionResponse: ...
    async def stream_chat_completion(self, model: str, request: ChatCompletionRequest) -> AsyncIterator[str]: ...
    async def list_models(self) -> list[str]: ...
    async def health_check(self) -> bool: ...
```

| 方法 | 说明 |
|------|------|
| `chat_completion` | 发送聊天补全请求，返回标准化响应 |
| `stream_chat_completion` | 流式聊天补全，逐块 yield 文本。默认回退到非流式调用 |
| `list_models` | 列出该厂商支持的远程模型 ID |
| `health_check` | 健康检查，验证 API Key 有效性 |

### 流式输出实现

所有主要厂商均实现了原生 SSE 流式输出：

| 厂商 | 流式方式 | 说明 |
|------|----------|------|
| Google | `streamGenerateContent?alt=sse` | 使用 Gemini 专用流式端点 |
| Groq / GitHub / OpenAI 兼容 | `stream: true` | 标准 OpenAI SSE 格式 |
| Cloudflare / HuggingFace / Cursor | 默认回退 | 调用非流式后一次性返回 |

流式解析中对空 `choices` 列表做了容错处理，避免上游返回不完整 chunk 时崩溃。

## 已实现的适配器

### 1. Google AI Studio (`google.py`)

| 项目 | 值 |
|------|---|
| API 格式 | Google Generative AI REST API |
| Base URL | `https://generativelanguage.googleapis.com/v1beta` |
| 认证方式 | URL 参数 `?key=API_KEY` |
| 默认模型 | gemini-2.5-pro, gemini-2.5-flash, gemini-2.0-flash, gemma-4-26b-a4b-it |

### 2. Groq (`groq.py`)

| 项目 | 值 |
|------|---|
| API 格式 | OpenAI 兼容 |
| Base URL | `https://api.groq.com/openai/v1` |
| 认证方式 | `Authorization: Bearer API_KEY` |
| 特点 | 推理速度极快，基于 LPU 硬件加速 |

### 3. GitHub Models (`github.py`)

| 项目 | 值 |
|------|---|
| API 格式 | OpenAI 兼容 |
| Base URL | `https://models.inference.ai.azure.com` |
| 认证方式 | `Authorization: Bearer GITHUB_TOKEN` |
| 特点 | 使用 GitHub Personal Access Token |

### 4. Cursor Agent CLI (`cursor.py`)

| 项目 | 值 |
|------|---|
| API 格式 | 本地 CLI 子进程 |
| 依赖 | `cursor` 或 `agent` 命令在 PATH 中 |
| 认证方式 | 无需 API Key，依赖 Cursor IDE 登录状态 |
| 特点 | 通过 subprocess 调用 `cursor agent --print` 命令行工具 |

#### 特殊能力：三种执行模式

Cursor provider 是唯一支持**执行模式**的 provider，通过 `mode` 参数控制：

| 模式 | CLI 参数 | 权限 | 使用场景 |
|------|----------|------|----------|
| **agent** | (默认) | 读写文件、执行命令 | 完整编码、重构、执行任务 |
| **plan** | `--plan` | 只读 | 架构分析、方案设计、规划 |
| **ask** | `--mode ask` | 只读 | 代码问答、解释说明 |

#### 扩展参数

| 参数 | 说明 | CLI 参数 | 限制 |
|------|------|----------|------|
| `force` | 强制执行命令 | `--force` | plan/ask 模式下自动忽略 |
| `sandbox` | 沙箱模式 | `--sandbox enabled\|disabled` | 控制命令执行环境 |

#### 使用示例

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused")

# 1. Plan 模式 - 只读分析
plan = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "分析 auth 模块架构"}],
    extra_body={"mode": "plan"}
)

# 2. Ask 模式 - 快速问答
ask = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "这个函数做什么?"}],
    extra_body={"mode": "ask"}
)

# 3. Agent 模式 - 完整执行
agent = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "重构 auth 为 JWT"}],
    extra_body={"mode": "agent", "force": True, "sandbox": "enabled"}
)
```

#### 典型工作流

```python
# 步骤1: 用 plan 模式先分析
plan_response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "分析重构方案"}],
    extra_body={"mode": "plan"}
)

# 步骤2: 确认后用 agent 模式执行
agent_response = client.chat.completions.create(
    model="auto",
    messages=[
        {"role": "user", "content": "分析重构方案"},
        {"role": "assistant", "content": plan_response.choices[0].message.content},
        {"role": "user", "content": "执行上述方案"}
    ]
    # mode 默认为 "agent"
)
```

#### 实现细节

- **Tool Calling**: 通过 prompt 注入实现（无原生 OpenAI tools 协议）
- **流式输出**: plan/ask/agent 模式均支持 `stream=True`
- **超时设置**: Tool calling 场景默认 300s，普通场景 240s
- **命令构建**: `cursor agent --print --trust --model X [mode/force/sandbox参数] "prompt"`

详见 [Cursor Plan 模式完整指南](cursor-plan-mode.md)。

### 5. OpenAI 兼容通用适配器 (`openai_compat.py`)

统一适配所有兼容 OpenAI API 格式的厂商，只需指定不同的 `base_url`。

| 厂商 | Base URL | 特点 |
|------|----------|------|
| Cerebras | `https://api.cerebras.ai/v1` | ~2000 tok/s 极速推理 |
| SambaNova | `https://api.sambanova.ai/v1` | 支持 405B 超大模型 |
| OpenRouter | `https://openrouter.ai/api/v1` | 聚合平台，`:free` 后缀免费 |
| Mistral | `https://api.mistral.ai/v1` | 官方 Mistral 模型 |
| Agnes AI | `https://apihub.agnes-ai.com/v1` | 免费全模态（文本/图像/视频） |

**工厂方法**：

```python
from src.providers.openai_compat import create_openai_provider

provider = create_openai_provider("cerebras", api_key="sk-...")
```

内部通过 `PROVIDER_BASE_URLS` 字典映射厂商名到 base_url，使用 HTTPX AsyncClient 调用 `/chat/completions`、`/images/generations`、`/videos` 和 `/models` 端点。

#### 图像和视频能力

`OpenAICompatibleProvider` 额外实现了图像和视频生成方法（`BaseProvider` 中默认抛出 NotImplementedError）：

| 方法 | 端点 | 说明 |
|------|------|------|
| `image_generation(model, **kwargs)` | `POST {base_url}/images/generations` | 同步图像生成 |
| `create_video(model, **kwargs)` | `POST {base_url}/videos` | 创建异步视频任务 |
| `poll_video(task_id)` | `GET {base_url}/videos/{task_id}` | 查询视频任务状态 |

并非所有 OpenAI 兼容厂商都支持这些端点（如 Cerebras 只有文本），调用时若上游返回 404/405 则自动抛出 HTTP 错误。

### 6. Agnes AI (`openai_compat.py` — agnes)

| 项目 | 值 |
|------|---|
| API 格式 | OpenAI 兼容（全模态） |
| Base URL | `https://apihub.agnes-ai.com/v1` |
| 认证方式 | `Authorization: Bearer API_KEY` |
| 计费 | 免费（无限期，Sapiens AI 推出） |
| 特点 | 支持文本（agnes-2.0-flash）、图像（agnes-image-2.1-flash）、视频（agnes-video-v2.0） |

#### 文本模型

通过标准 `/v1/chat/completions` 调用，支持 tool calling 和 thinking 模式：

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"agnes-2.0-flash","messages":[{"role":"user","content":"你好"}]}'
```

#### 图像生成

通过 `/v1/images/generations` 调用：

```bash
curl -X POST http://localhost:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agnes-image-2.1-flash",
    "prompt": "a cute cat, digital art",
    "size": "1024x1024",
    "n": 1
  }'
```

返回：

```json
{
  "created": 1718000000,
  "data": [{"url": "https://..."}],
  "proxy_info": {"provider": "agnes", "trace_id": "...", "latency_ms": 3200}
}
```

支持参数：`prompt`（必填）、`n`（1-10）、`size`（如 1024x1024、1024x768）、`seed`（复现用）、`response_format`（url/b64_json）。

#### 视频生成（异步任务）

视频生成为异步模式，需两步：创建任务 + 轮询状态。

**创建任务**：

```bash
curl -X POST http://localhost:8000/v1/videos \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agnes-video-v2.0",
    "prompt": "A cat walking on the beach at sunset",
    "width": 1152,
    "height": 768,
    "num_frames": 121,
    "frame_rate": 24
  }'
```

返回：

```json
{
  "id": "task_abc123",
  "status": "queued",
  "provider_id": "agnes",
  "proxy_info": {"provider": "agnes", "trace_id": "...", "latency_ms": 500}
}
```

**轮询状态**（使用返回的 `provider_id`）：

```bash
curl "http://localhost:8000/v1/videos/task_abc123?provider_id=agnes"
```

返回（完成后）：

```json
{
  "id": "task_abc123",
  "status": "completed",
  "video_url": "https://...",
  "proxy_info": {"provider": "agnes", "trace_id": "...", "latency_ms": 200}
}
```

视频参数约束：`num_frames` 必须满足 `8n+1` 且 <= 441，允许值为 81、121、161、241、441。视频时长 = `num_frames / frame_rate`。

### 7. Cloudflare Workers AI (`cloudflare.py`)

| 项目 | 值 |
|------|---|
| API 格式 | Cloudflare REST API |
| Base URL | `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run` |
| 认证方式 | `Authorization: Bearer API_TOKEN` |
| API Key 格式 | `account_id:api_token`（冒号分隔） |
| 特点 | 模型 ID 以 `@cf/` 开头 |

### 7. HuggingFace Inference API (`huggingface.py`)

| 项目 | 值 |
|------|---|
| API 格式 | OpenAI 兼容 + 旧版推理 API |
| Base URL | `https://api-inference.huggingface.co` |
| 认证方式 | `Authorization: Bearer HF_TOKEN` |
| 特点 | 优先使用 `/v1/chat/completions`，对不支持的模型回退到旧版 API |

## 添加新厂商（完整 Checklist）

新增厂商分两种情况：**OpenAI 兼容厂商**（如讯飞星火、阿里百炼、Cerebras 等）和**自定义协议厂商**（如 Google Gemini、Cloudflare）。

> **教训**：2026-06 集成讯飞星火时，前 4 步都做了但遗漏了第 2 步（main.py 注册），导致 `"厂商 spark 未注册"` 错误。以下 checklist 每一步都必须完成。

### 情况 A：OpenAI 兼容厂商（推荐，最常见）

无需创建新 Provider 类文件，共需修改 **4 个文件**：

#### Step 1: `src/providers/openai_compat.py` — 注册 base_url

在 `PROVIDER_BASE_URLS` 字典中添加条目：

```python
PROVIDER_BASE_URLS = {
    ...,
    "new_provider": "https://api.new-provider.com/v1",
}
```

如果是**付费厂商**，还需加入跳过列表：

```python
SKIP_CAPABILITY_TEST_PROVIDERS = {"dashscope", "spark", "new_provider"}
```

**只需修改 `conf/providers_catalog.yaml` 一个文件。**

在 `providers:` 下添加厂商定义，关键是 `type: openai_compat` 和 `base_url` 两个字段：

```yaml
  new_provider:
    name: 厂商中文名 (EnglishName)
    type: openai_compat              # 标记为 OpenAI 兼容，启动时自动发现并注册
    base_url: https://api.xxx.com/v1 # API 端点
    enabled: true                    # 是否默认启用
    priority: 10                     # 越小越优先
    billing_type: free               # free / paid
    url: https://厂商官网/
    description: 简要描述
    api_key_guide: API Key 获取方式说明
    models:
    - id: model-id                   # 必须与厂商 API 中的 model ID 一致
      name: 模型显示名
      description: 模型简介
      category: 通用                  # 通用/推理/快速/多模态/图像生成/开源 等
      tool_calling: false
      chinese: true
      streaming: true
      enabled: true
      priority: 1
```

#### 自动发现机制

`main.py` 在启动时会自动扫描 catalog 中所有 `type: openai_compat` 的厂商，根据 `base_url` 创建 `OpenAICompatibleProvider` 实例并注册到 Dispatcher。无需在 `main.py`、`openai_compat.py` 或 `ui.html` 中添加任何代码。

#### 付费厂商额外步骤

如果厂商是付费的，除了在 catalog 中设置 `billing_type: paid`，还需要：

- `src/providers/openai_compat.py`: 将厂商名加入 `SKIP_CAPABILITY_TEST_PROVIDERS` 集合（避免测试消耗 tokens）

#### 可选：增强 UI 展示

在 `src/api/static/ui.html` 的 `providerMeta` 中添加条目可获得自定义图标和接入指南：

```javascript
new_provider: {
    name: '中文名',
    icon: 'X',              // 单字符图标
    color: 'icon-google',   // CSS 颜色类
    desc: '简要描述',
    url: 'https://厂商官网/',
    guide: '获取 API Key 的详细指引...'
},
```

不添加时，UI 会从 catalog 的 `name`、`description` 自动派生基本展示信息。

### 情况 B：自定义协议厂商

API 不兼容 OpenAI 格式时（如 Google Gemini、Cloudflare），需要额外：

1. 创建 `src/providers/new_provider.py`，继承 `BaseProvider`，实现 `chat_completion`、`stream_chat_completion`、`list_models`
2. 使用 `@register_provider("new_provider")` 装饰器注册
3. `main.py` 的 `provider_factories` 中添加工厂方法：`"new_provider": lambda key: NewProvider(key)`
4. `conf/providers_catalog.yaml` 中添加厂商和模型定义（不需要 `type` 和 `base_url`）

### 验证 Checklist

添加完成后，按以下顺序验证：

- [ ] `curl http://localhost:8000/v1/models` — 新厂商模型出现在列表中
- [ ] `curl -X POST http://localhost:8000/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"模型ID","max_tokens":50,"messages":[{"role":"user","content":"hi"}]}'` — 返回正常响应
- [ ] 访问 `/ui` — 厂商卡片正常显示，付费标记和配置指引正确
- [ ] 如果是付费厂商 — 点击"测试能力"时弹出确认对话框

### 文件修改速查表

| 场景 | 必须修改 | 可选修改 |
|------|----------|----------|
| OpenAI 兼容厂商 | `conf/providers_catalog.yaml`（加 `type` + `base_url`） | `ui.html`（自定义图标） |
| 付费 OpenAI 兼容 | 上 + `openai_compat.py`（`SKIP_CAPABILITY_TEST_PROVIDERS`） | 同上 |
| 自定义协议厂商 | `src/providers/xxx.py` + `main.py` + `providers_catalog.yaml` | `ui.html` |

---

## Rapid-MLX 多实例管理系统

### 概述

Rapid-MLX 是 Apple Silicon 本地推理引擎。由于每个 `rapid-mlx serve` 进程只能加载一个模型，运行多个模型需要多个进程监听不同端口。

Model Proxy 提供了完整的多实例管理系统 (`RapidMLXManager`)，自动处理：

- 进程启动/停止/重启
- 端口自动分配（范围 8001-8099）
- 按模型名路由请求到正确实例
- 周期性健康检查
- 配置持久化（`conf/rapid_mlx_instances.yaml`）
- 外部实例自动发现（通过 `rapid-mlx ps`）

### 架构

```
┌─────────────────────────────────────────┐
│           Model Proxy (port 8000)        │
│                                          │
│  RapidMLXProvider                        │
│    ├─ _resolve_base_url(model)           │
│    │   → 查询 Manager.get_model_url_map  │
│    │   → 路由到对应实例端口              │
│    │                                      │
│  RapidMLXManager                         │
│    ├─ 实例 A: Qwen3-8B (port 8001)      │
│    ├─ 实例 B: kokoro   (port 8002)       │
│    └─ 实例 C: gemma    (port 8003)       │
└──────────────┬───────────────────────────┘
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
 rapid-mlx  rapid-mlx  rapid-mlx
 :8001      :8002      :8003
 Qwen3-8B   kokoro     gemma
```

### 配置文件

`conf/rapid_mlx_instances.yaml`:

```yaml
port_range:
  start: 8001
  end: 8099

instances:
- model: mlx-community/Qwen3-8B-4bit
  port: 8001
  enabled: true
  auto_start: true
  extra_args: ["--enable-auto-tool-choice", "--tool-call-parser", "qwen3"]

- model: kokoro
  port: 8002
  enabled: true
  auto_start: false  # 外部启动，不自动管理
```

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/rapid-mlx/instances` | 列出所有实例及状态 |
| POST | `/api/rapid-mlx/instances/start` | 启动新实例 |
| POST | `/api/rapid-mlx/instances/stop` | 停止实例 |
| POST | `/api/rapid-mlx/instances/restart` | 重启实例 |
| DELETE | `/api/rapid-mlx/instances/{model}?port=N` | 移除实例配置 |
| POST | `/api/rapid-mlx/instances/start-all` | 启动所有已配置实例 |
| POST | `/api/rapid-mlx/instances/stop-all` | 停止所有实例 |
| POST | `/api/rapid-mlx/discover` | 发现外部运行的实例 |
| GET | `/api/rapid-mlx/health` | 健康检查 |
| GET | `/api/rapid-mlx/model-urls` | 模型→URL映射 |
| POST | `/api/rapid-mlx/reload` | 重载配置文件 |

### 使用示例

```bash
# 启动新模型实例（自动分配端口）
curl -X POST http://localhost:8000/api/rapid-mlx/instances/start \
  -H 'Content-Type: application/json' \
  -d '{"model": "mlx-community/Qwen3-8B-4bit", "extra_args": ["--enable-auto-tool-choice"]}'

# 查看运行状态
curl http://localhost:8000/api/rapid-mlx/instances

# 停止实例
curl -X POST http://localhost:8000/api/rapid-mlx/instances/stop \
  -H 'Content-Type: application/json' \
  -d '{"model": "mlx-community/Qwen3-8B-4bit", "port": 8001}'
```

### 生命周期

1. **启动时**: Manager 加载配置 → `discover_external()` 发现已运行的外部实例 → `start_all_enabled()` 启动配置中 `auto_start: true` 的实例 → Provider `discover_and_register()` 注册模型到 Catalog
2. **运行中**: 周期性健康检查（30s 间隔）→ 自动更新实例状态 → 宕机实例自动重启
3. **关闭时**: Manager 取消健康检查任务 → 关闭 HTTP 客户端（不主动杀进程，除非手动 stop）

### 自动重启（Auto Recovery）

健康检查循环集成了自动重启机制，当检测到实例宕掉时自动拉起：

| 参数 | 值 | 说明 |
|------|---|------|
| 检查间隔 | 30s | 每 30 秒探测一次所有实例 |
| 最大重启次数 | 3 次 / 5 分钟 | 超限后暂停重启，避免重启风暴 |
| 冷却期 | 300s (5分钟) | 冷却期过后重启计数器清零 |

**触发条件**（全部满足才会重启）：
- 实例状态变为 `error`
- `config.enabled = true`
- `config.auto_start = true`
- 5 分钟内重启次数未超过 3 次

**日志示例**：
```
16:17:35 [INFO] 检测到实例 kokoro@8002 宕机，尝试自动重启 (#1)
16:17:35 [INFO] 重启 Rapid-MLX: rapid-mlx serve kokoro --port 8002
16:17:48 [INFO] 实例已恢复: kokoro (port=8002, pid=52482)
```

**API 响应中的重启信息**：
```json
{
  "model": "kokoro",
  "status": "running",
  "restart_count": 1,
  "pid": 52482
}
```

### 模型路由策略

`RapidMLXProvider._resolve_base_url(model)` 负责将请求的模型名映射到正确实例端口：

1. **精确匹配**：`model` 直接命中 `get_model_url_map()` 中的 key（config.model 或 served_model_name）
2. **子串匹配**：处理 HuggingFace 完整 ID 与短别名的映射。例如请求 `mlx-community/Kokoro-82M-bf16` 时，通过 `"kokoro" in "mlx-community/kokoro-82m-bf16"` 匹配到 kokoro 实例
3. **回退**：未匹配到任何实例时使用默认 `_base_url`

TTS 音频路由（`audio.py`）使用类似的子串匹配策略，在 `get_model_url_map()` 中查找包含 "kokoro" 的 key。

### Binary 查找策略

`_find_rapid_mlx_bin()` 按以下优先级查找 `rapid-mlx` 可执行文件：

| 优先级 | 路径 | 说明 |
|--------|------|------|
| 1 | `.venv/bin/rapid-mlx` | 项目 venv 内（依赖最隔离） |
| 2 | `~/.pyenv/shims/rapid-mlx` | pyenv 管理的版本（通常有 `[audio]` extra） |
| 3 | `shutil.which("rapid-mlx")` | 系统 PATH（launchd 环境可能只有 `/opt/homebrew/bin`） |

> **注意**：launchd 环境的 PATH 不含 pyenv shims。如果需要 `rapid-mlx[audio]`（TTS 模型需要），必须确保优先级 1 或 2 的路径可用。推荐在 model-proxy venv 中 `pip install "rapid-mlx[audio]"`。

### Catalog 模型注册策略

`RapidMLXProvider.discover_and_register()` 在自动注册模型时遵循以下规则：

- **只注册完整 HuggingFace ID**（包含 `/` 的 model ID，如 `mlx-community/Kokoro-82M-bf16`）
- **跳过短别名**（如 `kokoro`），避免同一模型在 Catalog 中出现重复条目
- 已存在的模型不会重复注册

### 模型市场（Model Catalog）

UI 和 API 提供了 rapid-mlx 官方模型目录的浏览和一键启动功能。

#### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/rapid-mlx/catalog` | 获取所有可用模型（158 文本 + 30 音频） |
| GET | `/api/rapid-mlx/catalog?type=text` | 仅文本模型 |
| GET | `/api/rapid-mlx/catalog?type=audio` | 仅音频模型 |
| GET | `/api/rapid-mlx/cached` | 本地已下载的模型 |

#### 一键启动流程

1. 用户在模型市场浏览/搜索模型
2. 点击"启动"按钮
3. 系统自动分配空闲端口（8001-8099 范围）
4. 调用 `rapid-mlx serve <alias> --port <port>` 启动进程
5. 如果模型未下载，`rapid-mlx` 会自动从 HuggingFace 下载
6. 等待健康检查通过后标记为 RUNNING
7. 实例自动加入 `get_model_url_map()`，后续请求可直接路由

#### UI 状态标记

模型卡片会根据当前状态显示不同标记：

| 标记 | 颜色 | 含义 |
|------|------|------|
| 运行中 | 绿色 | 该模型有实例正在运行 |
| 已缓存 | 灰色 | 模型已下载到本地但未启动 |
| （无标记） | — | 需要下载后才能启动 |
