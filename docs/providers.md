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

#### Step 2: `main.py` — 注册 Provider 工厂 ⚠️ 最易遗漏

在 `create_app()` 的 `provider_factories` 字典中添加（约第 119 行）：

```python
provider_factories = {
    ...,
    "new_provider": lambda key: create_openai_provider("new_provider", key),
    ...
}
```

**不做这一步，Dispatcher 不会创建 Provider 实例，请求会直接报 "厂商 xxx 未注册"。**

#### Step 3: `conf/providers_catalog.yaml` — 配置厂商和模型

```yaml
  new_provider:
    name: 厂商中文名 (EnglishName)
    enabled: false          # 默认不启用，用户手动开启
    priority: 10
    billing_type: paid      # 付费厂商必须标记
    url: https://厂商官网
    description: 简要描述
    api_key_guide: API Key 获取方式说明
    models:
    - id: model-id          # 必须与厂商 API 中的 model ID 一致
      name: 模型显示名
      description: 模型简介
      category: 通用
      tool_calling: false
      chinese: true
      streaming: true
      enabled: true
      priority: 1
```

#### Step 4: `src/api/static/ui.html` — 添加 UI 元数据

在 `providerMeta` 对象中添加条目（约第 1300 行）：

```javascript
new_provider: {
    name: '中文名',
    icon: 'X',              // 单字符图标
    color: 'icon-google',   // CSS 颜色类
    desc: '简要描述',
    url: 'https://厂商官网/',
    guide: '获取 API Key 的指引...'
},
```

付费厂商的 `guide` 应以 `⚠️ 付费厂商` 开头。

### 情况 B：自定义协议厂商

在情况 A 的基础上，额外需要：

1. 创建 `src/providers/new_provider.py`，继承 `BaseProvider`，实现 `chat_completion`、`stream_chat_completion`、`list_models`
2. 使用 `@register_provider("new_provider")` 装饰器注册
3. `main.py` 中的工厂方法改为直接构造：`lambda key: NewProvider(key)`

### 验证 Checklist

添加完成后，按以下顺序验证：

- [ ] `curl http://localhost:8000/v1/models` — 新厂商模型出现在列表中
- [ ] `curl -X POST http://localhost:8000/v1/messages -d '{"model":"模型ID","max_tokens":50,"messages":[{"role":"user","content":"hi"}]}'` — 返回正常响应
- [ ] 访问 `/ui` — 厂商卡片正常显示，付费标记和配置指引正确
- [ ] 如果是付费厂商 — 点击"测试能力"时弹出确认对话框

### 文件修改速查表

| 步骤 | 文件 | 作用 | 遗漏后果 |
|------|------|------|----------|
| 1 | `src/providers/openai_compat.py` | base_url 映射 | `ValueError: 未知的 OpenAI 兼容厂商` |
| 2 | `main.py` | Provider 工厂注册 | **`"厂商 xxx 未注册"` 请求直接失败** |
| 3 | `conf/providers_catalog.yaml` | 模型列表和元数据 | UI 不显示、模型不可选 |
| 4 | `src/api/static/ui.html` | 前端展示 | 厂商卡片无图标/描述/指引 |
