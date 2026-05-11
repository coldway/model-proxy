# API 接口参考

所有接口兼容 OpenAI SDK 格式。服务默认监听 `http://127.0.0.1:8000`。

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
