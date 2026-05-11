# 厂商适配器

所有厂商适配器继承自 `BaseProvider` 抽象基类，实现三个核心方法。

## BaseProvider 接口

```python
class BaseProvider(ABC):
    def __init__(self, api_key: str): ...

    async def chat_completion(self, model: str, request: ChatCompletionRequest) -> ChatCompletionResponse: ...
    async def list_models(self) -> list[str]: ...
    async def health_check(self) -> bool: ...
```

| 方法 | 说明 |
|------|------|
| `chat_completion` | 发送聊天补全请求，返回标准化响应 |
| `list_models` | 列出该厂商支持的远程模型 ID |
| `health_check` | 健康检查，验证 API Key 有效性 |

## 已实现的适配器

### 1. Google AI Studio (`google.py`)

| 项目 | 值 |
|------|---|
| API 格式 | Google Generative AI REST API |
| Base URL | `https://generativelanguage.googleapis.com/v1beta` |
| 认证方式 | URL 参数 `?key=API_KEY` |
| 默认模型 | gemini-2.5-pro, gemini-2.5-flash, gemini-2.0-flash, gemma-4-27b |

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
| 依赖 | `cursor` 命令在 PATH 中 |
| 认证方式 | 无需 API Key，依赖 Cursor IDE 登录状态 |
| 特点 | 通过 subprocess 调用 `cursor` 命令行工具 |

### 5. OpenAI 兼容通用适配器 (`openai_compat.py`)

统一适配所有兼容 OpenAI API 格式的厂商，只需指定不同的 `base_url`。

| 厂商 | Base URL | 特点 |
|------|----------|------|
| Cerebras | `https://api.cerebras.ai/v1` | ~2000 tok/s 极速推理 |
| SambaNova | `https://api.sambanova.ai/v1` | 支持 405B 超大模型 |
| OpenRouter | `https://openrouter.ai/api/v1` | 聚合平台，`:free` 后缀免费 |
| Mistral | `https://api.mistral.ai/v1` | 官方 Mistral 模型 |

**工厂方法**：

```python
from src.providers.openai_compat import create_openai_provider

provider = create_openai_provider("cerebras", api_key="sk-...")
```

内部通过 `PROVIDER_BASE_URLS` 字典映射厂商名到 base_url，使用 HTTPX AsyncClient 调用 `/chat/completions` 和 `/models` 端点。

### 6. Cloudflare Workers AI (`cloudflare.py`)

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

## 添加新厂商

1. 创建 `src/providers/your_provider.py`
2. 继承 `BaseProvider` 并实现三个抽象方法
3. 在 `main.py` 的 `provider_factories` 中注册
4. 在 `conf/providers_catalog.yaml` 中添加厂商信息和模型
5. 在 UI 的 `providerMeta` 对象中添加厂商元数据

如果新厂商兼容 OpenAI API，无需创建新文件。只需在 `openai_compat.py` 的 `PROVIDER_BASE_URLS` 中添加：

```python
PROVIDER_BASE_URLS = {
    ...,
    "new_provider": "https://api.new-provider.com/v1",
}
```
