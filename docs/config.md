# 配置系统

## 双文件架构

配置分为两个文件，存放在 `conf/` 目录下：

| 文件 | 内容 | 管理类 | Git 提交 |
|------|------|--------|---------|
| `conf/config.yaml` | API Key + 服务设置 | `ConfigManager` | ❌ 排除 |
| `conf/providers_catalog.yaml` | 厂商目录 + 运行时状态 | `CatalogManager` | ✅ 安全 |

### conf/config.yaml

仅保存敏感的 API Key 和服务运行参数。

```yaml
providers:
  google:
    api_key: "YOUR_GOOGLE_API_KEY"
  groq:
    api_key: ""

settings:
  host: "127.0.0.1"
  port: 8000
  default_provider: "google"
  auto_switch: true
  log_level: "info"
```

### conf/providers_catalog.yaml

包含厂商目录信息和运行时状态。厂商级别的 `enabled` 和 `priority`、模型级别的 `enabled` 和 `priority` 都存储在这里。

```yaml
providers:
  google:
    name: "Google AI Studio"
    enabled: true          # 厂商是否启用
    priority: 1            # 厂商优先级（越小越优先）
    url: "https://aistudio.google.com/"
    description: "..."
    api_key_guide: "..."
    models:
      - id: "gemini-2.5-pro"
        name: "Gemini 2.5 Pro"
        description: "最强推理能力"
        default_rpd: 25      # 每日请求限制
        default_rpm: 5        # 每分钟请求限制
        category: "推理"
        enabled: true         # 是否已激活（仅激活的模型参与调度）
        priority: 1           # 模型优先级
```

**未启用的模型**（没有 `enabled: true` 字段）仅作为目录条目存在，不参与调度，但可以通过 UI 搜索和激活。

## ConfigManager

`src/config/manager.py`

职责：读写 API Key 和 settings。

| 方法 | 说明 |
|------|------|
| `get_api_key(provider)` | 获取厂商 API Key |
| `update_api_key(provider, key)` | 更新 API Key（保存到 config.yaml） |
| `has_api_key(provider)` | 检查是否已配置 API Key |
| `settings` | 服务设置属性 |
| `config` | 兼容属性，构造完整 AppConfig 对象 |
| `get_enabled_models()` | 获取已启用模型列表（委托 CatalogManager） |
| `get_providers_sorted()` | 获取按优先级排序的厂商列表 |

ConfigManager 持有 CatalogManager 的引用。修改模型/厂商的启用状态和优先级等操作会委托给 CatalogManager 处理。

## 动态配置热加载

通过 API / UI 修改配置后即时生效，无需重启服务。核心实现在 `routes.py`：

- **API Key 保存**：`POST /api/config/apikey` 保存 Key 后，使用 `provider_factories`（启动时注入的厂商工厂字典）即时创建 Provider 实例并注册到 Dispatcher
- **厂商启用/禁用**：`POST /api/provider/toggle` 启用时自动注册 Provider（Cursor 无需 API Key），禁用时调用 `Dispatcher.unregister_provider()`
- **Settings 更新**：`POST /api/settings` 直接修改内存中的 `AppSettings` 对象，`log_level` 变更通过 `logging.getLogger().setLevel()` 即时生效

Dispatcher 提供的动态管理方法：

| 方法 | 说明 |
|------|------|
| `register_provider(name, provider)` | 注册厂商 |
| `unregister_provider(name)` | 注销厂商 |
| `has_provider(name)` | 检查厂商是否已注册 |

> `host` 和 `port` 的变更需要重启服务，因为网络监听地址由 Uvicorn 在启动时绑定。

## CatalogManager

`src/config/catalog.py`

职责：管理 `providers_catalog.yaml`，提供厂商/模型的 CRUD 和运行时状态管理。

**查询方法**

| 方法 | 说明 |
|------|------|
| `get_all_providers()` | 获取所有厂商 |
| `get_provider(id)` | 获取单个厂商 |
| `get_models(provider_id)` | 获取厂商下所有模型 |
| `search_models(query)` | 模糊搜索模型 |
| `get_providers_sorted()` | 按优先级排序获取厂商 |
| `get_active_models(provider_id)` | 获取已启用模型 |
| `get_all_active_models_sorted()` | 获取全局已启用模型（按优先级排序） |

**运行时状态**

| 方法 | 说明 |
|------|------|
| `set_provider_enabled(id, enabled)` | 启用/禁用厂商 |
| `set_provider_priority(id, priority)` | 设置厂商优先级 |
| `set_model_enabled(prov, model, enabled)` | 启用/禁用模型 |
| `set_model_priority(prov, model, priority)` | 设置模型优先级 |
| `activate_model(prov, model, priority)` | 激活模型 |
| `deactivate_model(prov, model)` | 停用模型 |
| `reorder_providers(ordered_ids)` | 拖拽排序后批量更新优先级 |

**目录编辑**

| 方法 | 说明 |
|------|------|
| `add_model(prov, model_data)` | 添加模型到目录 |
| `remove_model(prov, model_id)` | 从目录删除模型 |
| `add_provider(id, info)` | 添加新厂商 |
| `remove_provider(id)` | 删除厂商 |

## 数据模型

`src/models/schemas.py` 定义了所有 Pydantic 模型：

| 模型 | 说明 |
|------|------|
| `AppConfig` | 顶层应用配置 |
| `AppSettings` | 服务设置（host, port, auto_switch 等） |
| `ProviderConfig` | 单个厂商配置（api_key, enabled, priority, models） |
| `ModelConfig` | 单个模型配置（name, enabled, priority, rate_limit） |
| `RateLimit` | 速率限制（rpd, rpm） |
| `ChatCompletionRequest` | 聊天补全请求 |
| `ChatCompletionResponse` | 聊天补全响应 |
| `ModelInfo` | 模型信息 |
| `UsageStats` | 使用量统计 |
| `ProviderDiscovery` | 厂商发现信息 |
