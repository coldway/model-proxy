# 测试文档

## 测试策略

项目采用**单元测试**为主、**集成测试**为辅的策略，覆盖核心调度逻辑、配置管理和 API 端点。

### 测试原则

- 所有测试可离线运行，不依赖外部厂商 API
- 使用 Mock Provider 模拟厂商调用行为
- 异步测试使用 `pytest-asyncio` 支持
- 测试隔离：每个测试用例使用临时目录，互不干扰

---

## 环境准备

```bash
# 确认虚拟环境已激活
pip install pytest pytest-asyncio
```

---

## 运行测试

```bash
# 运行全部测试
python -m pytest tests/ -v

# 运行单个模块
python -m pytest tests/test_rate_limiter.py -v
python -m pytest tests/test_config.py -v
python -m pytest tests/test_dispatcher.py -v
python -m pytest tests/test_api.py -v

# 运行特定测试类
python -m pytest tests/test_dispatcher.py::TestDispatcher -v

# 运行特定测试方法
python -m pytest tests/test_dispatcher.py::TestDispatcher::test_auto_switch_on_failure -v

# 显示详细输出（含 print）
python -m pytest tests/ -v -s
```

---

## 测试模块详解

### 1. 限速器测试（`test_rate_limiter.py`）

覆盖 `RateLimiter` 的滑动窗口限速逻辑。

| 测试用例 | 验证内容 |
|----------|---------|
| `test_can_request_within_limits` | 未达限速时允许请求 |
| `test_rpm_limit_enforced` | RPM（每分钟请求数）达到上限后拒绝 |
| `test_rpd_limit_enforced` | RPD（每日请求数）达到上限后拒绝 |
| `test_different_models_independent` | 不同模型的限速计数相互独立 |
| `test_get_usage` | 正确返回当前使用量 |
| `test_is_exhausted` | 额度耗尽判断准确 |
| `test_zero_limit_means_unlimited` | 限速为 0 表示无限制 |

**共 7 项测试**

### 2. 配置管理测试（`test_config.py`）

覆盖 `ConfigManager` + `CatalogManager` 的双配置文件架构。

| 测试用例 | 验证内容 |
|----------|---------|
| `test_default_config_has_google_models` | 默认配置包含 Google 的 4 个基础模型 |
| `test_load_from_file` | 从 YAML 文件正确加载 API Key 和设置 |
| `test_save_and_reload` | 保存 API Key 后重新加载保持一致 |
| `test_get_enabled_models_sorted_by_priority` | 已启用模型按优先级正确排序 |
| `test_toggle_model` | 启用/禁用模型状态切换生效 |
| `test_add_model` | 添加新模型到 Catalog 并激活 |

**共 6 项测试**

关键实现细节：
- 使用 `_make_catalog` 辅助函数将项目的 `providers_catalog.yaml` 复制到 `tmp_path` 临时目录
- `ConfigManager` 构造时注入 `CatalogManager` 实例，验证双配置架构的协作

### 3. 调度器测试（`test_dispatcher.py`）

覆盖 `Dispatcher` 的模型选择、故障切换和异常处理。

| 测试用例 | 验证内容 |
|----------|---------|
| `test_auto_dispatch_selects_first_available` | auto 模式按优先级选择第一个可用模型 |
| `test_specific_model_dispatch` | 指定模型名时精确路由 |
| `test_model_not_found` | 请求不存在的模型时抛出 `ModelNotFound` |
| `test_auto_switch_on_failure` | 首选模型调用失败后自动切换到备选 |
| `test_all_models_unavailable` | 所有模型不可用时抛出 `AllModelsUnavailable` |
| `test_rate_limited_model_skipped` | 限速已满的模型被跳过 |

**共 6 项测试**

关键实现细节：
- `MockProvider` 可通过 `should_fail=True` 模拟调用失败
- `call_count` 属性验证厂商被实际调用的次数
- 故障切换测试同时使用 `bad`（必定失败）和 `good`（正常）两个 Provider

### 4. API 端点测试（`test_api.py`）

覆盖 FastAPI HTTP 端点的完整请求/响应。

| 测试用例 | 验证内容 |
|----------|---------|
| `test_models_endpoint` | `GET /v1/models` 返回模型列表 |
| `test_usage_endpoint` | `GET /v1/usage` 返回使用量统计 |
| `test_config_endpoint` | `GET /api/config` 返回配置（不泄露 API Key） |
| `test_discovery_endpoint` | `GET /api/discovery` 返回厂商发现信息 |
| `test_ui_panel_accessible` | `GET /ui` 返回管理面板 HTML |
| `test_chat_completions_no_provider_available` | 无可用 Provider 时返回 503 |
| `test_root_redirects_to_ui` | `GET /` 重定向到 `/ui` |

**共 7 项测试**

关键实现细节：
- 使用 HTTPX 的 `ASGITransport` 直接测试 FastAPI 应用，无需启动真实服务器
- API Key 安全性验证：确认 `/api/config` 响应中不包含 `api_key` 字段
- 使用 `@pytest_asyncio.fixture` 处理异步 client fixture

---

## 测试覆盖统计

| 模块 | 测试文件 | 用例数 | 覆盖范围 |
|------|----------|:---:|---------|
| 限速器 | `test_rate_limiter.py` | 7 | RPD/RPM 限制、滑动窗口、模型独立性、额度耗尽 |
| 配置管理 | `test_config.py` | 6 | 加载/保存/更新、双配置架构、模型增删改 |
| 调度器 | `test_dispatcher.py` | 6 | 自动选择、指定路由、失败切换、限速跳过 |
| API 端点 | `test_api.py` | 7 | 全部 HTTP 端点、安全性、错误码 |
| **合计** | | **26** | |

---

## Mock 对象说明

### MockProvider

`tests/test_dispatcher.py` 中定义的测试替身：

```python
class MockProvider(BaseProvider):
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.call_count = 0

    async def chat_completion(self, model, request):
        self.call_count += 1
        if self.should_fail:
            raise RuntimeError("模拟调用失败")
        return ChatCompletionResponse(...)
```

- `should_fail`：控制调用是否抛出异常
- `call_count`：追踪实际被调用的次数

### _make_catalog

`tests/test_config.py` 中的辅助函数，将项目真实的 `providers_catalog.yaml` 复制到临时目录以实现测试隔离：

```python
def _make_catalog(tmp_path: Path) -> CatalogManager:
    dst = tmp_path / "providers_catalog.yaml"
    shutil.copy(CATALOG_FILE, dst)
    return CatalogManager(catalog_path=dst)
```

---

## 扩展测试建议

当前测试覆盖了核心逻辑，以下方向可按需扩展：

| 方向 | 说明 |
|------|------|
| **流式输出** | 验证 SSE 事件格式、chunk 内容完整性 |
| **请求历史** | 验证 JSONL 持久化和统计接口准确性 |
| **厂商适配器** | Mock HTTP 调用验证各厂商的请求/响应转换 |
| **并发安全** | 模拟并发请求验证限速器的线程安全性 |
| **配置热更新** | 验证运行时修改配置后服务行为的一致性 |
| **UI 端到端** | 使用 Playwright 等工具验证 UI 交互流程 |
