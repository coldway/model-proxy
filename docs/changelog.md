# 更新日志 (CHANGELOG)

版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

---

## [Unreleased]

### 修复（Bug Fix）— 第六轮审查（14项）

**P0 安全/数据正确性：**
- **Memory LLM 提取完全失效**：`llm_extract_memories()` 引用了不存在的 `get_dispatcher()`/`get_config_manager()` 函数，导致对话轮次达 6/12/20 时 LLM 记忆提取必定失败；改为通过 `_deps` 获取已初始化的依赖
- **MemoryManager 单例竞态**：`get_memory_manager()` 无锁惰性初始化，多线程可创建多个实例导致记忆数据不一致；新增双重检查锁（`threading.Lock`）
- **delete_memory_entry 绕过锁竞态**：直接操作 `store._entries` 私有属性绕过 `_lock`；新增 `LongTermMemoryStore.remove_by_id()` 线程安全方法

**P1 逻辑缺陷：**
- **get_context_messages 误删有效 assistant 回复**：截断后首条 assistant 消息若不含 tool_calls 会被删除，但该消息可能是正常回复；改为只删除带 tool_calls 但后续 tool 回复缺失的 assistant 消息
- **流式路由策略 contextvars 部分分支未设置**：`dispatch_stream` 的快速路径、LLM 路由、规则遍历分支均缺少 `_route_strategy_var.set()`；补全所有分支
- **流式会话保存阈值 >=10 导致短回复丢失**：回复如 "是的"(2字符) 不会保存到会话；阈值从 `>= 10` 降至 `>= 1`
- **LongTermMemoryStore.retrieve() 遍历未加锁**：评分遍历在 `_lock` 外进行，其他线程修改 `_entries` 可致 `RuntimeError`；改为先取快照再遍历
- **_schedule_bindings_save Timer 竞态**：Timer 创建不在 `_session_lock` 内，多个调用者可能创建多个 Timer；移入锁内
- **_map_dispatch_error 死代码**：定义了统一异常映射函数但未使用；重构 `_handle_stream` 使用该函数消除重复 except 块

**P2 设计缺陷：**
- **Memory 自调度 Timer 无法停止**：`_check_idle_sessions` 在 finally 中无条件创建下一个 Timer；新增 `MemoryManager.close()` 方法
- **internal_tools CapabilityCache 每次新建空实例**：`_handle_model_capabilities` 每次创建新的空缓存而非使用运行时缓存；改为引用 `_deps.capability_tester.cache`
- **send_chat_message 异常时 provider_name 未定义**：dispatch() 首轮抛异常时变量未赋值致 `NameError`；添加默认值 `"unknown"`

### 修复（Bug Fix）— 第五轮审查

- **_cleanup_expired_trash 死锁**：遍历 `_trash` 未持锁，并发修改可致 `RuntimeError`；整个操作移入 `_lock` 内
- **CircuitBreaker 半开冷却计算错误**：`min(cooldown*2, cooldown*3)` 永远等于 `*2`；改为 `int(cooldown * 1.5)`
- **路由缓存并发读写不一致**：`_get_cached_route` 含无锁 `pop` 写操作；新增 `threading.Lock` 统一保护
- **流式会话绑定降级遗漏**：流式路径只捕获 `ProviderCallError`，`RateLimitExceeded` 等异常会冒泡且不清绑定；改为 `except Exception`
- **_extract_json 花括号匹配错误**：简单深度计数不处理字符串内花括号；改用 `json.loads` 解析
- **流式历史记录虚假成功**：`_handle_stream` 在流未结束时即记录 `success=True`；移除连接建立时的记录
- **get_context_messages tool_call 链断裂**：截断可能保留 `tool` 回复但丢失前序 `assistant` tool_calls；截断后跳过孤立的 `tool` 消息
- **Memory 系统并发安全**：`LongTermMemoryStore` 和 `MemoryManager` 均无线程安全保护；新增 `threading.Lock`
- **MemoryConsolidator 批量 I/O**：巩固 N 条记忆触发 N 次磁盘写入；改为批量完成后统一 `_save()`
- **_is_blacklisted_unlocked 锁内创建 Timer**：持锁时调 `_save_blacklist()` 创建 Timer 线程，改为仅标记 dirty
- **dispatcher 函数内 import yaml**：提升到模块级，符合 MP-43 规范
- **PayloadTracker 定时器泄漏**：未检查旧 Timer 存活即覆盖；新增 `is_alive()` 前置检查

### 修复（Bug Fix）

- **SessionManager 竞态条件修复（MP-52）**：`save()` 方法将 timer cancellation 移入 `_lock` 内，消除 cancel 与 lock acquisition 之间的 race window；timer 回调改为独立的 `_timer_flush()` 方法，避免 `save()` → `_lock` 的间接递归风险
- **_filter_available 熔断检查缺失（MP-53）**：`_filter_available()` 新增 `_breaker.is_open()` 检查，被熔断的模型不再进入候选列表，避免浪费请求延迟后再失败
- **PayloadTracker 持久化丢失（MP-54）**：新增 60s 周期自动 flush 定时器，`_save()` 标记的脏数据不再依赖外部调用 `flush()`；增加 `close()` 方法供优雅关闭时执行最终持久化
- **get_context_messages 误删 tool_calls（MP-55）**：截断后首条消息若为 assistant 且包含 `tool_calls`，不再删除，避免丢失工具调用上下文导致模型回复不连贯

### 增强（Enhancement）

- **CircuitBreaker 半开状态（MP-56）**：实现 CLOSED → OPEN → HALF_OPEN → CLOSED 三态转换；冷却期结束后进入 HALF_OPEN 允许单个探测请求，探测成功回到 CLOSED、失败则以 2x cooldown 重新 OPEN，避免全量放开导致震荡
- **路由缓存并发保护（MP-57）**：新增 `asyncio.Lock` 保护 `_route_cache` 写操作（`_set_cached_route_async` / `purge_expired_cache`），防止多协程并发写入导致条目超限
- **_can_skip_routing 精细化（MP-58）**：当候选模型 ≤3 但请求需要 tool_calling 或包含中文时，不再跳过 LLM 路由，确保在模型能力差异大时仍能做出最优选择
- **_compute_feature_hash 换用 blake2b（MP-59）**：路由缓存 hash 从 MD5 切换为 `hashlib.blake2b(digest_size=8)`，碰撞概率更低且 CPython 实现更快
- **Session 绑定持久化（MP-60）**：会话绑定新增磁盘持久化（`data/session_bindings.yaml`），30s debounce 写盘 + 启动时自动加载，服务重启后多轮对话的模型绑定不再丢失
- **RateLimiter 锁设计文档（MP-61）**：为 `threading.Lock` 的选择添加设计说明（临界区 < 50μs，RPM 有界），明确在 async 环境中的安全性边界

### 优化

- **CircuitBreaker 并发安全（H1）**：所有方法（`record_failure`/`record_success`/`is_open`/`get_status`/`clear`）加 `threading.Lock` 保护，防止高并发下计数竞态和 dict resize 异常
- **CatalogManager 写保护（H2）**：`save()`/`_flush()`/`flush()` 加 `threading.Lock`，解决并发管理 API 触发 debounce timer 重叠导致 YAML 文件写坏的问题
- **CancelledError 不计入熔断（H3）**：非流式 `_call_provider` 和流式 `_guarded_stream` 的 `except Exception` 前新增 `except asyncio.CancelledError` 分支，客户端断开/取消不再错误触发厂商熔断
- **_detect_chinese 性能优化（M3）**：从逐字符 Python 循环改为 `re.compile(r"[\u4e00-\u9fff]").search()`，大文本场景性能提升 5-10x
- **周期刷盘非阻塞（M5）**：lifespan 中 `_periodic_flush` 改用 `asyncio.to_thread()` 包裹同步 I/O，不再阻塞事件循环
- **会话绑定失败自动清除（M6）**：`dispatch()` 中会话绑定模型调用失败时自动 `clear_session_binding`，避免反复命中坏模型直到 TTL
- **Google Provider 流式错误类型修正（M7）**：`stream_chat_completion` 非 200 时抛出 `httpx.HTTPStatusError`（取代通用 `Exception`），上游 Dispatcher 可正确区分 429/413/5xx 并做差异化处理
- **X-Trace-Id 响应头（M9）**：新增全局中间件，请求时可传入 `X-Trace-Id`（复用），否则自动生成；所有响应统一携带此头部，便于日志关联和问题排查
- **异常映射提炼（M12）**：新增 `_map_dispatch_error()` 辅助函数，将路由层 6 处重复的 `except → HTTPException` 映射集中管理（路由端点仍保持兼容，未直接替换以保留 trace_id 日志粒度）
- **异常类独立模块（MP-34）**：`DispatchError` 及 5 个子类从 `dispatcher.py` 抽取到 `src/scheduler/exceptions.py`，消除 `routes.py` 中 3 处函数内 import，降低模块耦合
- **日志格式化规范（MP-35）**：全项目 18 处 `logger.xxx(f"...")` f-string 日志改为 `%s` 惰性格式化，避免日志级别未开启时的无效字符串拼接开销
- **LLM 路由跳过阈值（MP-36）**：`_can_skip_routing` 阈值从 ≤2 提升至 ≤3，候选模型少时直接规则排序，节省一次 LLM 路由调用
- **路由缓存周期清理（MP-37）**：新增 `purge_expired_cache()` 方法 + lifespan 周期任务，按 TTL 间隔主动清理过期缓存条目，防止长期运行后内存缓慢增长
- **unregister_provider 连接泄漏修复（MP-38）**：`unregister_provider` 改为 `async`，pop 后自动调用 `provider.close()` 关闭 httpx 连接，防止动态切换厂商时连接池泄漏
- **路由日志 deque 优化（MP-39）**：`_route_log` 从 `list` + 手动截断改为 `deque(maxlen=N)`，消除全量拷贝的内存抖动
- **SessionManager 延迟写盘（MP-40）**：会话持久化从同步立即写盘改为 debounce timer（5 秒合并），减少高频对话时的磁盘 I/O
- **429 黑名单延迟写盘（MP-41）**：`_save_blacklist` 从锁内直接写盘改为 dirty 标记 + debounce flush，消除 429 高频触发时的锁竞争
- **历史记录启动单次扫描（MP-42）**：`_load_from_file` + `_cleanup_old_records` 合并为 `_load_and_cleanup`，启动时只读一次 JSONL 文件
- **全项目函数内 import 清零（MP-43）**：`streaming.py`、`routes.py`、`google.py`、`huggingface.py`、`capability_tester.py` 中共 8 处函数内 import 提升到模块级，消除运行时重复 import 查找和代码可读性问题
- **Import 排序规范（MP-44）**：`dispatcher.py` 中 `import re` 从第三方包区移入标准库区，符合 PEP 8 import 分组约定
- **`_summarize_request` 中文检测退化修复（MP-45）**：`_summarize_request` 中 `any("\u4e00" <= ch <= "\u9fff" ...)` 逐字符循环替换为已有的 `_RE_CHINESE.search()` 正则，与 MP-23 修复一致
- **`_dispatch_specific` 冗余解包消除（MP-46）**：`_dispatch_specific` 中两次调用 `_unpack_rate_limit` 合并为一次，消除重复元组解构
- **`routes.py` 函数内 import 二次清零（MP-47～MP-50）**：`create_stream_response`、`ModelCapabilities`、`StreamingResponse`、`get_instance`、`Path` 共 7 处函数内 import 提升到模块级（仅保留 `CursorProvider` 延迟导入）
- **流式会话残缺输出写入阈值（MP-51）**：`_collect_and_stream` 中 `save_assistant` 阈值从 `>= 1` 提升到 `>= 10`，避免模型只返回 1-9 字符的异常短输出（如单个标点）时污染会话历史

### 安全修复（P0）

- **`/api/config` Token 泄漏（MP-29）**：`get_config` 使用 `model_dump(exclude=_SECRET_KEYS)` 并回填掩码值，防止新增敏感字段被意外暴露
- **`/api/logs/history` 路径穿越（MP-30）**：新增 `target.resolve().parent.samefile(log_dir)` 校验，阻止通过 `date=../../etc` 目录穿越读取任意文件

### 修复

- **部署文档与代码矛盾修正（H7）**：`docs/deployment.md` 中 `gunicorn --workers 4` 建议改为 `--workers 1`，并加大号警告说明单 Worker 设计约束，避免部署时多进程导致状态不一致
- **会话绑定模型故障不 fallback（MP-31）**：`dispatch_stream` 中会话绑定的模型失败时自动 `clear_session_binding`，确保后续请求正确降级而非反复命中坏模型
- **路由策略并发竞态（MP-32）**：`_last_route_strategy` 从实例属性改为 `contextvars.ContextVar`，消除异步并发请求间的状态串扰
- **日志预加载 OOM 风险（MP-33）**：`preload_from_file` 使用新增的 `_tail_lines()` 从文件末尾高效读取最后 N 行，避免大日志文件全量加载导致内存溢出

### 新增

- **流式 SSE 完整 delta 传递（MP-01）**：所有 provider 的 `stream_chat_completion` 从 yield 纯文本改为 yield 完整 delta dict，保留 `tool_calls`、`reasoning` 等非 content 字段；`streaming.py` 和 dispatcher `_guarded_stream` 同步适配 `dict | str` 类型
- **/v1/* API Token 认证（MP-02）**：新增 `api_token` 配置字段，非空时 `/v1/*` 路由要求 `Authorization: Bearer <api_token>`（OpenAI SDK 的 `api_key` 参数即为此 token）；为空时向后兼容不启用

### 优化

- **模型请求/响应完整日志增强**：请求日志新增 `payload` 大小（KB）、`tools` 名称列表（前 5 个 + 计数）、`temperature`/`max_tokens` 参数。非流式响应 INFO 日志新增 `finish_reason`、`tool_calls` 名称/数量、内容预览（前 200 字符）；DEBUG 日志输出完整 `message` 对象。流式响应 INFO 日志新增 tool_calls 详情（函数名 + 参数预览）、reasoning 摘要、内容预览、首包 TTFB（Time To First Byte）；DEBUG 日志输出所有 tool_call chunks 和响应结构
- **P2 批量（MP-10～MP-18）**：路由缓存哈希纳入 tools 体积/摘要与多模态图像段尺度；可配置 CORS（`cors_origins`）；能力缓存与 `providers_catalog` 合并且目录优先；会话 YAML 读写加进程内锁；DEBUG 请求体改为脱敏摘要；熔断键细化为 `provider:model`（兼容旧厂商级键）；Payload 估算 +10% 余量且 413 记录原始/调整后限制；`routes.py` / `dispatcher.py` / 管理 UI 增加 region 注释分段

### 修复

- **SSE 流式错误帧（MP-09）**：异常时 `finish_reason` 改为 `stop`，顶层增加 `error: {message, type: server_error, code: null}`，与 OpenAI API 错误结构对齐
- **流式会话残缺写入（MP-08）**：`/api/chat/sessions/{id}/stream` 在传输异常或 strip 后正文少于 10 字时不写入助手消息，避免污染会话历史
- **Groq tool_use_failed 智能恢复**：当 Groq 模型在多轮 tool calling 后想输出文本但被严格模式拒绝时（HTTP 400 `tool_use_failed`），`GroqProvider` 自动从 `failed_generation` 字段提取文本作为有效响应返回（非流式 + 流式均支持）。新增 `_is_tool_call_json` 检测：当 `failed_generation` 内容为工具调用 JSON（模型试图调用工具但格式不被接受）时不恢复，正确降级到其他模型
- **Groq schema 通用放宽**：`GroqProvider._relax_schema()` 递归放宽 tool schema：(1) `type: boolean` → `anyOf[boolean, string("true"/"false")]`；(2) `items: {type: "string"}` → `anyOf[string, object]`，修复模型将 `candidates` 输出为对象数组（`[{name: ...}]`）而非字符串数组时被 Groq 严格校验拒绝的问题
- **熔断状态日志降级**：`ProviderCallError`（含熔断状态）从通用 `except Exception`（ERROR+堆栈）中分离为独立 catch（WARNING 无堆栈），涉及 4 处路由端点（非流式/流式/会话/流式会话），减少熔断期间日志刷屏
- **UI 日志重启后恢复（增强版）**：`preload_from_file()` 现在自动扫描所有轮转备份文件（`app.log.YYYY-MM-DD`），按日期从旧到新加载，预加载量从 500 行提升到 2000 行。新增 `GET /api/logs/history` 端点：无参数返回可用日志文件列表（含大小）；传 `date=YYYY-MM-DD` 或 `date=current` 可查询任意一天的日志文件（最多 5000 行）。文件日志通过 `TimedRotatingFileHandler(backupCount=30)` 保留 30 天轮转。UI 实时日志支持无限滚动加载历史日志：向下滚动接近底部时自动从轮转日志文件加载更多条目，底部显示「加载更多历史日志（还有 N 天）」提示；最大渲染条数从 500 提升至 2000

### 新增

- **健康探针（MP-06）**：`GET /health` 返回 `{"status":"ok"}`；`GET /ready` 校验至少一个厂商在配置中填写了 API Key；两路径加入 `OPEN_PATHS` 免 Bearer 校验
- **请求历史容量可配置（MP-07）**：`AppSettings.request_history_max_records`（默认 2000），`RequestHistory` 内存窗口与 `/api/history` 的 limit 上限与之对齐
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

- **流式配额记账时点（MP-03）**：移除连接建立时的 `record_request`，改为流正常结束后再 `try_record_request`
- **流式首包超时 per-model（MP-04）**：等待首块使用 `ModelConfig.timeout`（Schema 默认 60s）
- **RateLimiter 并发一致性（MP-05）**：使用 `threading.Lock` 保护读改写；新增原子方法 `try_record_request`；`_call_provider` 成功路径改为原子登记（竞争失败时打日志仍返回结果）
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

---

## 优化 Backlog 总览

> 扫描日期：2026-05-17（第一轮）/ 2026-05-18（第二、三轮）| 全部已完成 ✅

### P0（阻塞级）

| 编号 | 名称 | 涉及文件 |
|------|------|---------|
| MP-01 | 流式 SSE 保留完整 delta dict | `openai_compat.py`, `github.py`, `groq.py`, `google.py`, `huggingface.py`, `base.py`, `streaming.py`, `dispatcher.py` |
| MP-02 | /v1/* API Token 认证 | `main.py`, `schemas.py` |

### P1（重要）

| 编号 | 名称 | 涉及文件 |
|------|------|---------|
| MP-03 | 流式配额记账时点修正 | `dispatcher.py` |
| MP-04 | per-model 首包超时 | `dispatcher.py` |
| MP-05 | RateLimiter 原子操作 | `rate_limiter.py`, `dispatcher.py` |
| MP-06 | /health + /ready 探针 | `routes.py`, `main.py` |
| MP-07 | RequestHistory 容量可配置 | `schemas.py`, `history.py`, `routes.py` |
| MP-08 | 流式异常不存残缺回复 | `routes.py` |
| MP-09 | SSE 错误帧 OpenAI 规范化 | `streaming.py` |

### P2（改善）— 第一轮

| 编号 | 名称 | 涉及文件 |
|------|------|---------|
| MP-10 | 路由缓存哈希含多模态/tools | `dispatcher.py` |
| MP-11 | CORS 中间件可配置 | `main.py`, `schemas.py` |
| MP-12 | catalog/capabilities 合并一致 | `capability_tester.py`, `catalog.py`, `dispatcher.py` |
| MP-13 | routes/dispatcher region 分段 | `routes.py`, `dispatcher.py` |
| MP-14 | 管理 UI 注释分区 | `ui.html` |
| MP-15 | SessionManager 并发锁 | `session.py` |
| MP-16 | DEBUG 请求体脱敏 | `dispatcher.py` |
| MP-17 | 熔断粒度到模型级 | `circuit_breaker.py`, `dispatcher.py` |
| MP-18 | Payload 估算安全余量 | `payload_tracker.py` |

### 第二轮优化（2026-05-18）

| 编号 | 名称 | 涉及文件 |
|------|------|---------|
| MP-19 | CircuitBreaker 并发锁 | `circuit_breaker.py` |
| MP-20 | CatalogManager 写保护锁 | `catalog.py` |
| MP-21 | CancelledError 不计入熔断 | `dispatcher.py` |
| MP-22 | 部署文档单 Worker 修正 | `docs/deployment.md` |
| MP-23 | _detect_chinese 正则优化 | `dispatcher.py` |
| MP-24 | 周期刷盘 asyncio.to_thread | `main.py` |
| MP-25 | 会话绑定失败自动清除 | `dispatcher.py` |
| MP-26 | Google 流式错误 HTTPStatusError | `google.py` |
| MP-27 | X-Trace-Id 响应头中间件 | `main.py` |
| MP-28 | 异常映射 _map_dispatch_error | `routes.py` |

### 第三轮优化（2026-05-18 PR #4 Review）

| 编号 | 名称 | 涉及文件 |
|------|------|---------|
| MP-29 | /api/config Token 泄漏修复 | `routes.py` |
| MP-30 | /api/logs/history 路径穿越修复 | `routes.py` |
| MP-31 | 会话绑定模型故障不 fallback | `dispatcher.py` |
| MP-32 | 路由策略并发竞态 contextvars | `dispatcher.py` |
| MP-33 | 日志预加载 OOM 风险 _tail_lines | `log_buffer.py` |
| MP-34 | 异常类独立模块 | `exceptions.py`, `dispatcher.py`, `routes.py` |
| MP-35 | 日志格式化规范 f-string → %s | 全项目 10 文件 18 处 |
| MP-36 | LLM 路由跳过阈值 ≤3 | `dispatcher.py` |
| MP-37 | 路由缓存周期清理 purge_expired | `dispatcher.py`, `main.py` |

### 第四轮优化（2026-05-18 资源管理/I/O 优化）

| 编号 | 名称 | 涉及文件 |
|------|------|---------|
| MP-38 | unregister_provider 连接泄漏修复 | `dispatcher.py`, `routes.py` |
| MP-39 | 路由日志 deque 优化 | `dispatcher.py` |
| MP-40 | SessionManager 延迟写盘 | `session.py` |
| MP-41 | 429 黑名单延迟写盘 | `rate_limiter.py` |
| MP-42 | 历史记录启动单次扫描 | `history.py` |

### 第五轮优化（2026-05-18 Import 规范化）

| 编号 | 名称 | 涉及文件 |
|------|------|---------|
| MP-43 | 全项目函数内 import 清零 | `streaming.py`, `routes.py`, `google.py`, `huggingface.py`, `capability_tester.py` |
| MP-44 | Import 排序 PEP 8 规范 | `dispatcher.py` |

### 第六轮优化（2026-05-18 深度扫描）

| 编号 | 名称 | 涉及文件 |
|------|------|---------|
| MP-45 | `_summarize_request` 中文检测退化修复 | `dispatcher.py` |
| MP-46 | `_dispatch_specific` 冗余 rate_limit 解包 | `dispatcher.py` |
| MP-47 | `_handle_stream` 函数内 import 提升 | `routes.py` |
| MP-48 | `_tail_file`/`get_log_history` 函数内 import 提升 | `routes.py` |
| MP-49 | `stream_chat_message` 函数内 import 提升 | `routes.py` |
| MP-50 | `list_models` 函数内 import 提升 | `routes.py` |
| MP-51 | 流式会话残缺输出写入阈值修正 | `routes.py` |
