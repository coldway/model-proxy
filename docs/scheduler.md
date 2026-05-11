# 调度器与限速器

## 模型调度器（Dispatcher）

`src/scheduler/dispatcher.py`

负责根据优先级和可用性选择模型，并将请求转发到对应的厂商 Provider。

### 调度模式

**自动调度**（`model="auto"`）

1. 获取所有已启用模型，按厂商优先级 → 模型优先级排序
2. 遍历候选列表，对每个模型：
   - 检查 RateLimiter 是否允许请求
   - 受限 → 跳过，继续下一个
   - 允许 → 调用 Provider
     - 成功 → 返回结果
     - 失败 → 检查是否额度用尽，记录错误，尝试下一个
3. 全部失败 → 抛出 `AllModelsUnavailable`

**指定模型**（`model="gemini-2.5-flash"`）

1. 在已启用模型中查找匹配的模型名称
2. 检查速率限制
3. 受限 → 抛出 `RateLimitExceeded`
4. 调用 Provider → 返回结果或抛出异常

### 异常类

| 异常 | 含义 | HTTP 状态码 |
|------|------|------------|
| `RateLimitExceeded` | 指定模型速率受限 | 429 |
| `ModelNotFound` | 模型未找到或未启用 | 404 |
| `AllModelsUnavailable` | 自动模式下所有模型不可用 | 503 |
| `ProviderCallError` | 厂商 API 调用失败 | 500 |

### Provider 注册

启动时通过 `register_provider(name, provider)` 注册各厂商实例：

```python
dispatcher.register_provider("google", GoogleProvider(api_key))
```

只有注册过的厂商才能处理请求。未注册的厂商（无 API Key）的模型在调度时会被跳过。

## 滑动窗口限速器（RateLimiter）

`src/scheduler/rate_limiter.py`

基于滑动窗口算法实现每日请求数（RPD）和每分钟请求数（RPM）限制。

### 数据结构

```
ModelUsage:
  daily_count: int          # 今日已请求次数
  minute_counts: list[float] # 最近 60 秒内的请求时间戳
  last_reset_day: str       # 上次重置的日期
```

Key 格式：`"provider:model_name"`，每个 provider+model 组合独立计数。

### 核心方法

| 方法 | 说明 |
|------|------|
| `can_request(provider, model, rpd, rpm)` | 检查是否可以请求（不消耗额度） |
| `record_request(provider, model)` | 记录一次请求（消耗额度） |
| `get_usage(provider, model)` | 返回 (今日请求数, 当前分钟请求数) |
| `is_exhausted(provider, model, rpd)` | 判断今日额度是否用尽 |

### 滑动窗口机制

- **RPD**：每天零点自动重置（通过比较日期字符串）
- **RPM**：维护一个时间戳列表，每次检查时清除 60 秒前的记录，列表长度即当前分钟请求数
- **无限制**：当 rpd=0 或 rpm=0 时，对应维度不做限制

### 调度器与限速器的交互

```
Dispatcher.dispatch()
    │
    ├─ rate_limiter.can_request() → 检查限额
    │   ├─ True → 继续
    │   └─ False → 跳过此模型
    │
    ├─ rate_limiter.record_request() → 记录请求（调用前计数）
    │
    └─ Provider 调用失败时:
        └─ rate_limiter.is_exhausted() → 判断是否额度用尽
            ├─ True → 日志警告，切换下一模型
            └─ False → 可能是临时错误，仍切换
```

## 请求历史（RequestHistory）

`src/scheduler/history.py`

记录每次推理请求的元数据，支持内存队列和文件持久化。

### 记录字段

| 字段 | 类型 | 说明 |
|------|------|------|
| timestamp | float | 请求时间 |
| provider | string | 厂商 ID |
| model | string | 模型 ID |
| success | bool | 是否成功 |
| latency_ms | float | 延迟（毫秒） |
| error | string | 错误信息（仅失败时） |
| prompt_tokens | int | 输入 token 数 |
| completion_tokens | int | 输出 token 数 |

**不记录消息内容**，仅记录元数据。

### 存储机制

- **内存**：`deque(maxlen=500)` 环形队列，保留最近 500 条
- **文件**：追加写入 `data/request_history.jsonl`，每行一条 JSON 记录

### 统计汇总

`get_stats()` 返回：
- 总请求数
- 成功率（%）
- 平均延迟（ms）
- 按厂商分组统计（每个厂商的总数、成功率、平均延迟）
