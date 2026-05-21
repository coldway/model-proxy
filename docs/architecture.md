# 项目架构

## 整体设计

Model Proxy 采用分层架构，将推理请求的接收、调度、执行三个职责分离。

```
┌─────────────────────────────────────────────┐
│              FastAPI 应用层                   │
│  routes.py  │  ui.py  │ streaming │ thinking│
├──────────┬──────────────────┬───────────────┤
│ 配置层   │      调度层       │   记忆层      │
│ manager  │  dispatcher      │  memory.py    │
│ catalog  │  rate_limiter    │  L1: Session  │
│          │  circuit_breaker │  L2: 长期持久化│
│          │  payload_tracker │  巩固 + 检索   │
│          │  session         │               │
│          │  history         │               │
├──────────┴──────────────────┴───────────────┤
│               厂商适配层                      │
│  google │ groq │ github │ huggingface │ ... │
│  openai_compat │ cloudflare │ cursor │ollama│
└─────────────────────────────────────────────┘
```

## 核心模块

| 模块 | 路径 | 职责 |
|------|------|------|
| **API 层** | `src/api/` | 接收 HTTP 请求，返回推理结果和管理操作。含内部工具系统（`internal_tools.py`）：6 个 LLM 可调用工具（`add_model`、`list_provider_models`、`search_ollama_library` 等） |
| **配置层** | `src/config/` | 管理 API Key（config.yaml）和厂商目录（providers_catalog.yaml） |
| **调度层** | `src/scheduler/` | 模型选择、速率限制、熔断器、payload 追踪、会话管理、请求历史 |
| **记忆层** | `src/scheduler/memory.py` | L1 会话记忆 + L2 跨会话长期记忆，规则/LLM 提取 + bigram 检索，详见 [memory.md](memory.md) |
| **厂商层** | `src/providers/` | 各厂商 API 的具体适配实现。含 Ollama 本地模型 Provider（`ollama.py`）：自动模型发现、一键安装、abliterated 推断 |
| **数据模型** | `src/models/` | Pydantic 数据结构定义 |

## 内部工具系统 (Internal Tools)

`src/api/internal_tools.py` 注册了 6 个 LLM 可通过 tool calling 调用的内部工具：

| 工具 | 功能 | 典型触发语句 |
|------|------|-------------|
| `test_structured_output` | 测试模型 JSON 结构化输出能力 | "测试 gpt-4o-mini 的 JSON 输出" |
| `list_available_models` | 列出当前所有已启用模型 | "有哪些模型可用" |
| `get_model_capabilities` | 查询模型已探测能力 | "gemma-4 支持哪些能力" |
| `add_model` | 为厂商添加模型（Ollama 自动安装） | "为我的 ollama 增加 qwen3:8b" |
| `list_provider_models` | 查询厂商远程支持的全部模型 | "groq 支持哪些模型" |
| `search_ollama_library` | 搜索 Ollama 模型库 + 硬件适配评估 | "适合本机的 abliterated 模型" |

`search_ollama_library` 工作流程：
```
用户: "ollama 支持哪些 abliterated 适合本机的模型"
    │
    ▼
LLM 识别意图 → tool_call: search_ollama_library(query="abliterated", vram_gb=12)
    │
    ▼
抓取 ollama.com/search?q=abliterated
    ├─ 解析模型名称、能力标签（tools/thinking/vision）、参数大小、下载量
    ├─ 查询本地 Ollama 已安装模型 → 标记 installed
    └─ 按 GPU VRAM 估算 Q4 量化占用 → 分级: 推荐 / 可用 / 不适合
    │
    ▼
LLM 综合工具返回 → 生成推荐列表回复用户
```

## 请求流程

```
客户端请求 POST /v1/chat/completions
    │
    ▼
routes.py: 接收请求，校验参数
    │
    ▼
dispatcher.py: 选择模型
    ├─ model="auto" → 按厂商优先级+模型优先级遍历
    └─ model="具体名称" → 直接路由
    │
    ▼
rate_limiter.py: 检查 RPD/RPM 限制
    ├─ 通过 → 继续
    └─ 受限 → 跳过，尝试下一模型
    │
    ▼
providers/*.py: 调用厂商 API
    ├─ 成功 → 返回结果，记录历史
    └─ 失败 → 判断是否额度用尽，自动切换
    │
    ▼
history.py: 记录请求元数据（不含消息内容）
```

## 双配置文件架构

项目将敏感信息与公开配置分离为两个文件：

| 文件 | 内容 | Git 策略 |
|------|------|---------|
| `conf/config.yaml` | API Key + 服务设置 | `.gitignore` 排除 |
| `conf/providers_catalog.yaml` | 厂商目录、模型列表、启用状态、优先级 | 安全提交 |

`ConfigManager` 负责读取 API Key，`CatalogManager` 管理厂商/模型的运行时状态。两者通过引用关联，对外提供统一的配置视图。

## 动态配置热加载

所有配置变更通过 API / UI 操作后即时生效，无需重启服务：

```
用户在 UI 保存 API Key
    │
    ▼
routes.py: POST /api/config/apikey
    ├─ ConfigManager: 保存 Key 到 config.yaml
    └─ Dispatcher: 用 provider_factories 创建 Provider 实例并注册
    │
    ▼
下一次推理请求即可使用新注册的厂商
```

支持动态加载的配置项：

| 操作 | 动态生效机制 |
|------|-------------|
| API Key | routes 层持有 provider_factories，保存后即时创建并注册 Provider |
| 厂商启用/禁用 | toggle 时调用 Dispatcher.register/unregister_provider |
| 模型启用/禁用/优先级 | CatalogManager 即时更新，调度时实时读取 |
| log_level | 直接调用 logging.getLogger().setLevel() |
| default_provider / auto_switch | 直接更新 AppSettings 内存对象 |
| host / port | 需重启服务（网络监听地址无法热切换） |

## 技术栈

| 组件 | 技术 | 用途 |
|------|------|------|
| Web 框架 | FastAPI | 异步 API 服务 |
| 服务器 | Uvicorn | ASGI 服务器，支持热重载 |
| HTTP 客户端 | HTTPX | 异步调用厂商 API |
| 数据验证 | Pydantic v2 | 请求/响应/配置数据模型 |
| 配置序列化 | PyYAML | YAML 文件读写 |
| UI | 内嵌 HTML/JS | 无需前端构建，单文件嵌入 |
