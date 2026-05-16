# 更新日志 (CHANGELOG)

版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

---

## [Unreleased]

### 新增

- **单模型能力测试 API**：`POST /api/capabilities/test?provider=xxx&model=yyy&force=true`，支持精准测试单个模型（约 15-30 秒），避免全量测试阻塞
- **UI 测试能力按钮**：模型管理页面每个模型卡片新增「🔍 测试能力」按钮，一键检测 7 项能力（工具调用、多轮对话、中文、视觉、JSON、流式、推理）
- **空内容检测**：能力测试器新增对 Google API "200 OK 但无内容"（软限流）的检测，标记 `error: empty_response` 并跳过后续能力检测，避免误判为"可用但无能力"
- **持久 Toast**：长耗时操作（如能力测试）的 Toast 提示持续显示直到操作完成，不再 3 秒自动消失
- **5 层保护机制文档**：`SERVICE.md` 新增完整的模型可用性保护机制说明（能力缓存、429 黑名单、RPD/RPM 配额、厂商熔断、Payload 上限）
- **路由排序日志**：每次自动路由输出前 5 名模型及其能力组合分值（如 `TC+MT+R`），便于调试路由决策
- **`/v1/models` 能力字段增强**：返回每个模型的已探测能力（streaming/reasoning/multi_turn_tc/chinese/vision/json_mode/latency_ms），供上游服务（如 ai-assitance）动态选择模型
- **CircuitBreaker 独立模块**：熔断器从 Dispatcher 中抽取为 `src/scheduler/circuit_breaker.py`，含 13 项单元测试
- **SessionManager 测试**：新增 13 项会话管理测试，覆盖创建/删除/上下文截断/会话淘汰/序列化往返
- **HuggingFace 真流式**：`HuggingFaceProvider.stream_chat_completion` 使用 SSE 逐块输出（失败回退到非流式）

### 优化

- **路由排序策略重构**：`_sort_by_capability` 新增 5 级排序维度
  - 健康度 > 流式匹配 > 能力组合 > 中文匹配 > 延迟
  - 能力组合评分：TC+MT+R(6) > TC+MT(5) > TC+R(4) > MT+R(3) > TC(2) > MT|R(1) > 无(0)
  - 流式请求（`stream=True`）自动优先选择支持流式的模型
- **`providers_catalog.yaml` 更新**：`gemini-2.5-flash-lite` 和 `gemini-flash-lite-latest` 的 `tool_calling` 从 `false` 改为 `true`（实测验证）
- **thinking 逻辑抽取**：`routes.py` 中 200+ 行的 thinking 剥离/去重逻辑移至独立模块 `src/api/thinking.py`，`routes.py` 减重 ~200 行
- **Dispatcher 职责拆分**：熔断器独立为 `CircuitBreaker` 类，Dispatcher 通过委托调用
- **CapabilityTester 并行探测**：阶段 3-7（中文/视觉/JSON/流式/推理）从串行改为 `asyncio.gather` 并行执行，单模型测试速度提升约 4 倍
- **路由缓存优化**：`_compute_feature_hash` 新增消息长度桶（S/M/L/XL）和 stream 标记，减少无效 cache miss
- **RateLimiter 持久化**：补充 `daily_tokens` 的磁盘持久化，重启后 TPD 计数不丢失
- **SessionManager 配置接线**：`max_context_tokens` 和 `max_sessions` 从 `AppSettings` 传入并实际生效
  - `max_sessions` 达到上限时自动淘汰最旧会话
  - `max_context_tokens` 控制每个会话的上下文窗口大小
- **`/api/discovery` 动态化**：从 catalog 动态生成厂商发现列表，不再硬编码静态数据
- **CursorProvider 模型参数**：响应中的 model 字段现使用请求中传入的模型名，不再固定为 `cursor-agent`
- **依赖精简**：移除未使用的 `apscheduler`、`pydantic-settings`、`aiofiles` 三个依赖
- **test_admin_auth 修复**：测试路由从不存在的 `/api/models` 改为真实的 `/api/config`，并增加编码兼容（UTF-8）

### 修复

- **UI 限流误报**：清除 `model_capabilities.yaml` 中残留的 5/13 旧 429/503 错误缓存，解决 UI 模型卡片显示错误的"限流"徽标
- **watchfiles 日志刷屏**：将 `watchfiles` logger 级别从 INFO 调至 WARNING，抑制高频 `change detected` 通知，保持 reload 热重载正常工作
- **版本号对齐**：`FastAPI(version=...)` 从 `0.1.0` 更正为 `0.3.0`
- **config.yaml.example 补全**：补充 `admin_token`、`route_cache_ttl`、`breaker_threshold`、`breaker_cooldown`、`max_context_tokens`、`max_sessions`、`probe_interval` 等缺失字段
- **429 描述统一**：Dispatcher 中 429 黑名单描述从"次日恢复"改为准确的"指数退避后自动恢复"
- **能力缓存 .gitignore**：`conf/model_capabilities.yaml` 加入 gitignore 防止运行时缓存误提交
- **私有成员越界**：`list_models` 端点从 `capability_tester._cache` 改为公开 API `capability_tester.cache`
- **README/测试文档**：测试数据从 26 项更新为 78 项，项目结构补全 14 个缺失文件

---

## [0.3.0] - 2026-05-11

### 新增

- **SSE 流式输出**：所有主要厂商（Google/Groq/GitHub/OpenAI 兼容）实现原生流式输出
  - `BaseProvider` 新增 `stream_chat_completion` 异步生成器接口
  - `Dispatcher` 新增 `dispatch_stream` 流式调度方法
  - `POST /v1/chat/completions` 端点集成 `stream=true` 支持
  - 流式解析器增加空 choices 列表容错处理
- **429 限流识别**：厂商返回 HTTP 429 时返回明确的 429 错误（而非通用 500），提示建议使用 auto 模式
  - auto 模式下对 RateLimitExceeded 单独处理，静默跳过并切换下一模型

### 修复

- 修复 `providers-grid` HTML 开始标签缺少闭合 `>` 导致 UI 面板渲染异常
- 修正 Gemma 4 模型 ID：`gemma-4-27b` → `gemma-4-26b-a4b-it`（匹配 Google API 实际命名）
- 新增 `gemma-4-31b-it` 到 Google 模型目录

---

## [0.2.0] - 2026-05-11

### 新增

- **动态配置热加载**：所有配置变更通过 UI/API 操作后即时生效，无需重启服务
  - API Key 保存后自动注册 Provider，清空后自动注销
  - 厂商启用/禁用时动态加载/卸载 Provider（含 Cursor 特殊处理）
  - 新增 `POST /api/settings` 端点，支持动态修改 log_level、default_provider、auto_switch
  - Dispatcher 新增 `unregister_provider()` 和 `has_provider()` 方法
- **厂商卡片底部对齐**：所有卡片的统计行（已配置/已启用/API Key/?）吸底到同一水平线
- **错误提示优化**：拉取厂商模型失败时展示服务端实际原因和操作指引
- **新增文档**：部署指南、FAQ、更新日志、贡献指南、测试文档

---

## [0.1.0] - 2026-05-11

首个功能完整版本，涵盖核心推理代理、调度系统、管理面板。

### 新增

- **核心推理代理**
  - 兼容 OpenAI `/v1/chat/completions` 格式的统一推理接口
  - 支持 `model="auto"` 自动调度和指定模型名直接路由
  - SSE 流式输出，兼容 OpenAI SDK `stream=True`

- **10 家模型厂商适配**
  - Google AI Studio（Gemini 2.5 Pro / Flash / 2.0 Flash / Gemma 4）
  - Groq（LLaMA / Gemma / Mixtral 等开源模型）
  - Cerebras（超快推理 ~2000 tok/s）
  - SambaNova（支持 405B / DeepSeek 超大模型）
  - GitHub Models（使用 GitHub Token 调用）
  - OpenRouter（聚合平台 `:free` 后缀免费模型）
  - Mistral AI（Codestral 代码模型等）
  - Cloudflare Workers AI（每日万次 neurons）
  - HuggingFace Inference API（数千开源模型）
  - Cursor Agent CLI（使用 Cursor IDE 登录凭据）

- **智能调度系统**
  - 厂商优先级 + 模型优先级二级排序
  - 滑动窗口限速器（RPD / RPM）
  - 限速跳过 + 调用失败自动切换
  - 额度耗尽检测与降级

- **Web 管理面板**（单文件嵌入，无需前端构建）
  - 使用量监控（实时展示各模型调用次数与剩余额度）
  - 模型管理（厂商卡片布局，点击进入模型管理弹窗）
  - 厂商卡片拖拽排序（自动保存优先级）
  - 模型弹窗内拖拽排序
  - 模型搜索（目录内搜索 + 拉取厂商远程模型列表）
  - 每个厂商卡片附带接入指南（`?` 图标点击查看）
  - API Key 配置面板
  - 内置问答聊天（支持选择模型或 auto 模式）
  - Tab 切换后刷新页面保持当前 Tab

- **双配置文件架构**
  - `conf/config.yaml`：仅存 API Key + 服务设置（.gitignore 排除）
  - `conf/providers_catalog.yaml`：厂商目录 + 模型列表 + 启用状态 + 优先级（安全提交 Git）

- **请求历史**
  - 内存 + JSONL 文件双持久化
  - 记录时间戳、模型名、延迟、token 数、成功/失败
  - `/api/history` 和 `/api/history/stats` 查询接口

- **跨平台支持**
  - Windows 10/11（PowerShell / CMD）
  - macOS（Intel / Apple Silicon）
  - Linux（Ubuntu / Debian / CentOS 等）

- **开发工具**
  - Uvicorn 热重载（`reload=True`）
  - 52 项单元测试覆盖核心模块（rate_limiter / config / dispatcher / api / blacklist / streaming / utils / admin_auth）
  - 完整的项目文档（`docs/` 目录下 8 个模块文档）
