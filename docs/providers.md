# 厂商适配器

## BaseProvider 接口

```python
class BaseProvider(ABC):
    async def chat_completion(self, model: str, request) -> ChatCompletionResponse: ...
    async def stream_chat_completion(self, model: str, request) -> AsyncIterator[str]: ...
    async def list_models(self) -> list[str]: ...
    async def health_check(self) -> bool: ...
```

## 已接入厂商

| 厂商 | 文件 | API 格式 | 特点 |
|------|------|----------|------|
| Google AI Studio | `google.py` | Generative AI REST | Gemini 系列，流式 SSE |
| Groq | OpenAI 兼容 | OpenAI | LPU 极速推理 |
| GitHub Models | OpenAI 兼容 | OpenAI | 使用 GitHub PAT |
| Cursor Agent CLI | `cursor.py` | 本地 CLI 子进程 | plan/ask/agent 三种模式 |
| Cerebras | OpenAI 兼容 | OpenAI | ~2000 tok/s |
| SambaNova | OpenAI 兼容 | OpenAI | 支持 405B/DeepSeek |
| OpenRouter | OpenAI 兼容 | OpenAI | 聚合平台 `:free` 后缀 |
| Mistral AI | OpenAI 兼容 | OpenAI | Codestral 代码模型 |
| Cloudflare | OpenAI 兼容 | OpenAI | Workers AI |
| HuggingFace | OpenAI 兼容 | OpenAI | 数千开源模型 |
| NVIDIA NIM | OpenAI 兼容 | OpenAI | 123+ 模型 |
| Cohere | OpenAI 兼容 | OpenAI | Command 系列 |
| 阿里百炼 | OpenAI 兼容 | OpenAI | 通义千问全系列 |
| 讯飞星火 | `spark.py` | OpenAI 兼容 | DeepSeek V4/Qwen/Spark |
| Ollama | `ollama.py` | 本地 OpenAI 兼容 | 自动发现本地模型 |
| SenseNova | OpenAI 兼容 | OpenAI | 商汤日日新 |

## 添加新厂商

**如果 API 兼容 OpenAI 格式**（大部分情况）：

1. 在 `src/providers/openai_compat.py` 的 `PROVIDER_CONFIGS` 中添加配置
2. 在 `main.py` 的 `provider_factories` 中注册
3. 在 `conf/providers_catalog.yaml` 中添加模型

**如果 API 格式不同**（如 Google、Cursor）：

1. 创建 `src/providers/new_provider.py`，继承 `BaseProvider`
2. 在 `main.py` 注册
3. 更新 catalog

## 流式输出

| 类型 | 厂商 |
|------|------|
| 原生 SSE | Google、Groq、GitHub、大部分 OpenAI 兼容 |
| 非流式回退 | Cloudflare、HuggingFace |
| CLI stdout | Cursor |
