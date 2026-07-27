# API 接口参考

所有接口兼容 OpenAI SDK 格式。服务默认监听 `http://127.0.0.1:8000`。

## 快速接入

### 交互式文档（Swagger）

服务启动后可访问以下地址获取交互式 API 文档：

| 文档 | 地址 | 说明 |
|------|------|------|
| Swagger UI | http://127.0.0.1:8000/docs | 交互式调试，支持 "Try it out" |
| ReDoc | http://127.0.0.1:8000/redoc | 阅读友好的文档布局 |
| OpenAPI JSON | http://127.0.0.1:8000/openapi.json | 原始 Schema，可导入 Postman/Apifox |

> 文档页面无需认证即可访问。

### 支持的协议

| 协议 | 端点 | SDK |
|------|------|-----|
| **OpenAI** | `POST /v1/chat/completions` | `openai` Python/Node SDK |
| **Anthropic** | `POST /v1/messages` | `anthropic` Python/Node SDK |

两种协议共享同一套调度引擎和模型池，选择哪种协议取决于你的项目已使用哪个 SDK。

### OpenAI SDK 接入

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="your-api-token",  # conf/config.yaml 中的 api_token
)

resp = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True,
)
for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="")
```

### Anthropic SDK 接入

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="http://127.0.0.1:8000/v1",
    api_key="your-api-token",
)

message = client.messages.create(
    model="auto",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
)
print(message.content[0].text)
```

**Anthropic 流式调用**:

```python
with client.messages.stream(
    model="auto",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="")
```

### 认证

如果 `conf/config.yaml` 中配置了 `api_token`，所有 `/v1/*` 请求需要携带 Bearer Token：

```
Authorization: Bearer your-api-token
```

---

## 推理接口

### POST /v1/chat/completions

聊天补全请求，支持自动调度和流式输出。

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | 否 | "auto" | 模型名称，"auto" 时自动选择 |
| messages | array | 是 | - | 消息列表，格式 `[{role, content}]` |
| temperature | float | 否 | 0.7 | 温度参数 |
| max_tokens | int | 否 | null | 最大输出 token 数 |
| stream | bool | 否 | false | 是否启用流式输出 |
| mode | string | 否 | null | **Cursor 专用**: 执行模式 (`plan`/`ask`/`agent`) |
| force | bool | 否 | false | **Cursor 专用**: 强制执行命令(plan/ask模式下无效) |
| sandbox | string | 否 | null | **Cursor 专用**: 沙箱模式 (`enabled`/`disabled`) |

#### Cursor Agent CLI 特性

当使用 Cursor provider 时，支持以下扩展参数：

| 参数 | 说明 | 可选值 | CLI参数 |
|------|------|--------|---------|
| `mode` | 执行模式 | `plan`(只读规划)、`ask`(只读问答)、`agent`(默认,全权限) | `--plan` / `--mode ask` |
| `force` | 强制执行命令 | `true`/`false`，在plan/ask模式下自动忽略 | `--force` |
| `sandbox` | 沙箱模式 | `"enabled"`/`"disabled"` | `--sandbox enabled` |

**Cursor 模式说明：**

- **plan 模式**: 只读分析，适合架构设计、方案规划
- **ask 模式**: 只读问答，适合代码解释、快速问答
- **agent 模式**: 默认模式，具有文件修改和命令执行权限

详见 [Cursor Plan 模式使用指南](cursor-plan-mode.md)。

**Cursor 请求示例**

```json
{
  "model": "auto",
  "messages": [
    {"role": "user", "content": "分析这个项目的架构"}
  ],
  "mode": "plan",
  "sandbox": "enabled"
}
```

**请求示例**

```json
{
  "model": "auto",
  "messages": [
    {"role": "system", "content": "你是一个有帮助的助手"},
    {"role": "user", "content": "Hello"}
  ],
  "temperature": 0.7
}
```

**响应体**

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1715000000,
  "model": "gemini-2.5-flash",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "你好！"},
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 5,
    "total_tokens": 15
  }
}
```

**流式响应**（`stream: true`）

以 SSE（Server-Sent Events）格式返回，每个 chunk：

```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","model":"gemini-2.5-flash","choices":[{"index":0,"delta":{"content":"你"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","model":"gemini-2.5-flash","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

**错误码**

| 状态码 | 含义 | 说明 |
|--------|------|------|
| 429 | 模型速率限制 | 本地限速器拦截或厂商 API 返回 429，提示建议使用 auto 模式 |
| 404 | 指定模型未找到 | 模型 ID 不存在或未在配置中启用 |
| 503 | 所有模型不可用 | auto 模式下遍历全部候选均失败（限流/异常） |
| 500 | 内部错误 | 厂商 API 非 429 的其他异常（如 503 临时不可用） |

> 指定具体模型时，若厂商返回 429 限流，将返回明确的 429 错误并建议切换到 auto 模式。auto 模式下 429 会被静默跳过并自动尝试下一个候选模型。

### POST /v1/messages (Anthropic 协议)

兼容 Anthropic Messages API，使用 Anthropic SDK 直接调用。内部将请求转换为 OpenAI 格式调用 dispatcher，再将响应转换回 Anthropic 格式。

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | 是 | - | 模型名称，`"auto"` 自动选择 |
| messages | array | 是 | - | 消息列表，Anthropic 格式 `[{role, content}]` |
| max_tokens | int | 是 | - | 最大输出 token 数 |
| system | string/array | 否 | null | 系统提示词 |
| temperature | float | 否 | 1.0 | 温度参数 |
| stream | bool | 否 | false | 是否启用流式 SSE |
| tools | array | 否 | null | 工具定义列表 |
| stop_sequences | array | 否 | null | 停止词列表 |
| metadata | object | 否 | null | 扩展元数据（Cursor 参数等） |

**请求示例**

```json
{
  "model": "auto",
  "max_tokens": 1024,
  "messages": [
    {"role": "user", "content": "用 Python 写一个快速排序"}
  ],
  "system": "你是一个高级程序员"
}
```

**响应体**

```json
{
  "id": "msg_abc123",
  "type": "message",
  "role": "assistant",
  "content": [{"type": "text", "text": "以下是快速排序..."}],
  "model": "gemini-2.5-flash",
  "stop_reason": "end_turn",
  "usage": {"input_tokens": 15, "output_tokens": 120}
}
```

**流式响应**（`stream: true`）

以 SSE 格式返回 Anthropic 标准事件序列：

```
event: message_start
data: {"type":"message_start","message":{"id":"msg_abc","type":"message","role":"assistant","model":"gemini-2.5-flash","content":[],"usage":{"input_tokens":15,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"以下是"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":120}}

event: message_stop
data: {"type":"message_stop"}
```

**Tool Use 支持**

Anthropic 风格的 tool_use 会被自动转换为 OpenAI function calling 格式调用，响应中的 tool_calls 会转换回 Anthropic tool_use content block。

### POST /v1/images/generations

图像生成接口，透传到支持图像生成的厂商（如 Agnes AI）。

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | 是 | - | 图像模型 ID，如 `agnes-image-2.1-flash` |
| prompt | string | 是 | - | 图像描述 |
| n | int | 否 | 1 | 生成数量（1-10） |
| size | string | 否 | "1024x1024" | 图像尺寸，如 `1024x1024`、`1024x768` |
| seed | int | 否 | null | 随机种子（用于复现） |
| response_format | string | 否 | "url" | 返回格式：`url` 或 `b64_json` |

**请求示例**

```json
{
  "model": "agnes-image-2.1-flash",
  "prompt": "a cute cat, digital art",
  "size": "1024x1024",
  "n": 1
}
```

**响应体**

```json
{
  "created": 1718000000,
  "data": [{"url": "https://..."}],
  "proxy_info": {"provider": "agnes", "trace_id": "abc123", "latency_ms": 3200}
}
```

**错误码**

| 状态码 | 含义 |
|--------|------|
| 404 | 模型未找到或厂商未注册 |
| 501 | 厂商不支持图像生成 |
| 502 | 上游图像生成失败 |

### POST /v1/videos

创建视频生成任务（异步），返回 task_id 供客户端轮询。

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | 是 | - | 视频模型 ID，如 `agnes-video-v2.0` |
| prompt | string | 是 | - | 视频描述 |
| width | int | 否 | 1152 | 视频宽度（像素） |
| height | int | 否 | 768 | 视频高度（像素） |
| num_frames | int | 否 | 121 | 总帧数（需满足 8n+1 且 <= 441） |
| frame_rate | int | 否 | 24 | 帧率 |
| image_url | string | 否 | null | 参考图片 URL（图生视频模式） |

**请求示例**

```json
{
  "model": "agnes-video-v2.0",
  "prompt": "A cat walking on the beach at sunset",
  "width": 1152,
  "height": 768,
  "num_frames": 121,
  "frame_rate": 24
}
```

**响应体**

```json
{
  "id": "task_abc123",
  "status": "queued",
  "provider_id": "agnes",
  "proxy_info": {"provider": "agnes", "trace_id": "def456", "latency_ms": 500}
}
```

### GET /v1/videos/{task_id}

查询视频生成任务状态。

**查询参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| provider_id | string | 是 | 厂商 ID，从创建响应的 `provider_id` 字段获取 |

**请求示例**

```
GET /v1/videos/task_abc123?provider_id=agnes
```

**响应体**（任务完成时）

```json
{
  "id": "task_abc123",
  "status": "completed",
  "video_url": "https://...",
  "proxy_info": {"provider": "agnes", "trace_id": "ghi789", "latency_ms": 200}
}
```

**status 值**

| 状态 | 说明 |
|------|------|
| queued | 排队中 |
| processing | 生成中 |
| completed | 已完成，`video_url` 字段包含下载地址 |
| failed | 失败，`error` 字段包含原因 |

### GET /v1/models

列出所有已配置并启用的模型。

**响应**

```json
{
  "models": [
    {
      "id": "gemini-2.5-pro",
      "provider": "google",
      "enabled": true,
      "priority": 1,
      "rate_limit": {"rpd": 25, "rpm": 5}
    }
  ]
}
```

### GET /v1/usage

获取各模型的实时使用量统计。

**响应**

```json
{
  "stats": [
    {
      "provider": "google",
      "model": "gemini-2.5-pro",
      "today_requests": 3,
      "minute_requests": 1,
      "rpd_limit": 25,
      "rpm_limit": 5,
      "available": true
    }
  ]
}
```

## 配置管理接口

### GET /api/config

获取当前配置（API Key 被隐藏）。返回按厂商优先级排序的配置信息。

### POST /api/config/apikey

更新厂商 API Key。保存后**自动注册/注销厂商 Provider**，无需重启服务。

| 参数 | 类型 | 说明 |
|------|------|------|
| provider | string | 厂商 ID |
| api_key | string | API Key 值（清空则注销厂商） |

**动态行为**：填入有效 Key 后立即创建 Provider 实例并注册到 Dispatcher；清空 Key 则自动注销厂商。

### POST /api/config/model/toggle

启用/禁用模型。

| 参数 | 类型 | 说明 |
|------|------|------|
| provider | string | 厂商 ID |
| model_name | string | 模型 ID |
| enabled | bool | 启用/禁用 |

### POST /api/config/model/priority

更新模型优先级。

| 参数 | 类型 | 说明 |
|------|------|------|
| provider | string | 厂商 ID |
| model_name | string | 模型 ID |
| priority | int | 优先级值（越小越优先） |

### POST /api/config/model/add

添加新模型到配置。如果目录中不存在则先添加到目录。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| provider | string | - | 厂商 ID |
| name | string | - | 模型 ID |
| priority | int | 99 | 优先级 |
| rpd | int | 0 | 每日请求限制 |
| rpm | int | 0 | 每分钟请求限制 |

### POST /api/config/model/delete

停用模型（从目录中移除 enabled 标记）。

| 参数 | 类型 | 说明 |
|------|------|------|
| provider | string | 厂商 ID |
| model_name | string | 模型 ID |

### POST /api/provider/toggle

启用/禁用厂商。**动态注册/注销 Provider**：

- 启用时自动创建 Provider 实例（需 API Key 已配置，Cursor 无需 Key）
- 禁用时自动注销 Provider

### POST /api/provider/priority

更新厂商优先级。

### POST /api/provider/reorder

批量重新排序厂商优先级（用于拖拽排序）。

**请求体**：厂商 ID 有序数组

```json
["google", "groq", "cerebras", "sambanova"]
```

### POST /api/settings

动态更新运行时设置，即时生效无需重启。

| 参数 | 类型 | 说明 |
|------|------|------|
| log_level | string | 日志等级（debug/info/warning/error） |
| default_provider | string | 默认厂商 ID |
| auto_switch | bool | 是否启用自动切换 |

所有参数均为可选，仅传入需要修改的项。`host` 和 `port` 不支持动态修改（需重启服务）。

## 目录管理接口

### GET /api/catalog/providers

获取目录中所有厂商及其模型列表。

### GET /api/catalog/provider/{provider_id}/models

获取指定厂商的所有模型。

### GET /api/catalog/search?q={keyword}

搜索模型目录，模糊匹配 ID、名称、描述、分类。

### POST /api/catalog/model/add

向目录添加新模型（更新 `providers_catalog.yaml`）。

### DELETE /api/catalog/model/delete

从目录删除模型。

### POST /api/catalog/model/activate

在目录中激活模型（设置 `enabled: true` 和 `priority`）。

## 其他接口

### GET /api/discovery

返回可免费使用的大模型厂商列表及接入说明。

### GET /api/provider/{name}/models

拉取厂商远程 API 返回的最新模型列表。需要厂商已注册（API Key 已配置并保存后会自动注册）。如厂商未注册，返回具体引导信息。

### GET /api/history?limit=50

获取最近的请求历史记录。

### GET /api/history/stats

获取请求统计汇总（总量、成功率、平均延迟、按厂商分组）。

### GET /ui

Web 管理面板入口。

### GET /

自动重定向到 `/ui`。
