# 贡献指南

感谢你对 Model Proxy 的关注！以下指南将帮助你快速参与项目开发。

---

## 开发环境搭建

```bash
# 1. Fork 并克隆仓库
git clone <your-fork-url>
cd model-proxy

# 2. 创建虚拟环境
python -m venv .venv

# 3. 激活虚拟环境
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

# 4. 安装运行依赖 + 测试依赖
pip install -r requirements.txt
pip install pytest pytest-asyncio

# 5. 复制配置
cp conf/config.yaml.example conf/config.yaml
```

---

## 项目结构概览

```
model-proxy/
├── main.py                  # 服务入口，Provider 注册
├── conf/                    # 配置文件
├── src/
│   ├── api/                 # FastAPI 路由 + UI
│   ├── config/              # 配置管理（ConfigManager + CatalogManager）
│   ├── models/              # Pydantic 数据模型
│   ├── providers/           # 厂商适配器
│   └── scheduler/           # 调度器 + 限速器 + 请求历史
├── tests/                   # 单元测试
└── docs/                    # 项目文档
```

---

## 代码规范

### 通用规范

- 代码中的注释和日志输出使用**中文**
- 文件头部包含创建日期信息
- 使用 `from __future__ import annotations` 统一类型注解风格
- 遵循 PEP 8 编码规范

### 命名规范

| 类型 | 风格 | 示例 |
|------|------|------|
| 模块/文件 | `snake_case` | `rate_limiter.py` |
| 类名 | `PascalCase` | `RateLimiter` |
| 函数/方法 | `snake_case` | `can_request()` |
| 常量 | `UPPER_SNAKE_CASE` | `UI_HTML` |
| 私有成员 | `_前缀` | `_providers` |

### 类型注解

所有公开 API 的参数和返回值必须提供类型注解：

```python
async def dispatch(
    self,
    request: ChatCompletionRequest,
    enabled_models: list[tuple[str, ModelConfig]],
) -> ChatCompletionResponse:
```

### 异常处理

- 精准捕获特定异常，禁止裸 `except Exception`
- 厂商调用异常统一包装为 `ProviderCallError`
- 所有异常日志需包含上下文信息（厂商名 + 模型名 + 错误内容）

---

## 添加新厂商

这是最常见的贡献类型。完整步骤：

### 1. 创建适配器

如果厂商 API 兼容 OpenAI 格式，直接在 `main.py` 中使用 `create_openai_provider`：

```python
# main.py - provider_factories 中
"new_provider": lambda key: create_openai_provider("new_provider", key),
```

并在 `src/providers/openai_compat.py` 的 `PROVIDER_CONFIGS` 中添加配置。

如果 API 有自定义格式，创建独立适配器文件 `src/providers/new_provider.py`：

```python
from src.providers.base import BaseProvider

class NewProvider(BaseProvider):
    async def chat_completion(self, model: str, request) -> ChatCompletionResponse:
        # 实现厂商 API 调用
        ...

    async def list_models(self) -> list[str]:
        # 返回厂商支持的模型列表
        ...

    async def health_check(self) -> bool:
        # 健康检查
        ...
```

### 2. 注册厂商

在 `main.py` 的 `provider_factories` 字典中添加：

```python
"new_provider": lambda key: NewProvider(key),
```

### 3. 更新配置

在 `conf/providers_catalog.yaml` 中添加厂商和模型信息：

```yaml
new_provider:
  name: "新厂商名称"
  enabled: true
  priority: 11
  url: "https://example.com"
  models:
    - id: "model-name"
      enabled: true
      priority: 1
      default_rpd: 1000
      default_rpm: 10
```

在 `conf/config.yaml.example` 中添加 API Key 占位：

```yaml
new_provider:
  api_key: ""
```

### 4. 更新 UI（可选）

在 `src/api/ui.py` 的 `providerMeta` 对象中添加厂商描述和接入指南。

---

## 提交测试

所有提交前必须通过现有测试：

```bash
python -m pytest tests/ -v
```

新增功能建议添加对应的测试用例。测试文件统一放在 `tests/` 目录下，命名格式 `test_<模块名>.py`。

---

## 提交规范

### Commit Message 格式

使用中文编写 commit message，格式参考：

```
<类型>: <简要描述>

<可选的详细说明>
```

常用类型：

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | 修复 Bug |
| `docs` | 文档变更 |
| `refactor` | 重构（不影响功能） |
| `test` | 测试相关 |
| `chore` | 构建/工具/配置变更 |

示例：

```
feat: 新增 Anthropic 免费厂商适配

- 实现 AnthropicProvider 适配器
- 在 providers_catalog.yaml 中添加 Claude 系列模型
- 更新 UI 中的厂商元信息
```

### Pull Request

- 分支命名：`feat/xxx`、`fix/xxx`、`docs/xxx`
- PR 标题简明概括改动内容
- PR 描述中说明改动原因和测试方式
- 确保 CI 测试通过后再请求 review

---

## 不允许的操作

- 将 API Key 或其他凭据硬编码到代码中
- 向 `conf/config.yaml` 提交敏感信息
- 在日志中明文输出 API Key
- 未经讨论的大规模架构变更
