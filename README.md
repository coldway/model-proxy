# Model Proxy - 免费大模型推理代理

灵活选择免费大模型进行推理的代理服务。支持 10 家模型厂商、自动调度与失败切换、内嵌 Web 管理面板，兼容 OpenAI SDK 格式。

## 功能概览

- **统一推理接口**：兼容 OpenAI `/v1/chat/completions` 格式，支持自主选择模型或由系统自动调度
- **10 家免费厂商**：Google AI Studio、Groq、GitHub Models、Cursor CLI、Cerebras、SambaNova、OpenRouter、Cloudflare、HuggingFace、Mistral AI
- **智能调度**：基于厂商优先级 + 模型优先级 + 滑动窗口限速的多级调度，超限/失败自动切换
- **流式输出**：所有厂商均支持 SSE 流式响应，兼容 OpenAI SDK `stream=True`
- **Web 管理面板**：内嵌单页 UI，无需额外前端构建，提供使用量监控、模型管理、API Key 配置、问答聊天等功能
- **模型目录**：`conf/providers_catalog.yaml` 记录各厂商可用模型和默认限速，可安全提交 Git
- **请求历史**：记录每次推理的元数据（延迟、Token 数、成功/失败），持久化到 JSONL 文件
- **动态配置**：API Key、厂商启用/禁用、优先级、运行时设置等所有配置变更即时生效，无需重启服务

## 已接入厂商

| 厂商 | 特点 | 免费额度参考 |
|------|------|-------------|
| **Google AI Studio** | Gemini 系列，默认首选 | Gemini 2.5 Pro 25次/天，Flash 500+次/天 |
| **Groq** | 极速推理开源模型 | ~14,400 次/天 |
| **Cerebras** | ~2000 tok/s 超快推理 | ~1,000 次/天 |
| **SambaNova** | 支持 405B/DeepSeek 超大模型 | ~1,000 次/天 |
| **GitHub Models** | 使用 GitHub Token 调用 | ~50 次/天 |
| **OpenRouter** | 聚合平台，`:free` 后缀免费 | ~200 次/天 |
| **Mistral AI** | 官方平台免费层 | Codestral 代码模型免费 |
| **Cloudflare** | Workers AI 每日万次 neurons | ~10,000 neurons/天 |
| **HuggingFace** | 数千个开源模型免费推理 | 有速率限制但免费 |
| **Cursor CLI** | 通过 Cursor IDE 登录调用 | 取决于 Cursor 订阅 |

> 所有厂商均**无新用户限制**，注册即可使用。详细模型列表见 `conf/providers_catalog.yaml`。

### Google AI Studio 默认模型

| 模型 | 每日请求数 (RPD) | 每分钟请求数 (RPM) | 定位 |
|------|:---:|:---:|------|
| Gemini 2.5 Pro | 25 | 5 | 最强推理 |
| Gemini 2.5 Flash | 500 | 10 | 平衡之选 |
| Gemini 2.0 Flash | 1,500 | 15 | 速度快 |
| Gemma 4 26B | 1,500 | 15 | 开源大模型 |

## 跨平台支持

| 平台 | 支持状态 | 备注 |
|------|:---:|------|
| Windows 10/11 | ✅ | PowerShell / CMD 均可 |
| macOS (Intel / Apple Silicon) | ✅ | - |
| Linux (Ubuntu / Debian / CentOS 等) | ✅ | - |

> 项目使用纯 Python 跨平台依赖，无平台特定的原生扩展。

## 快速开始

### 环境要求

- Python 3.11+
- 操作系统：Windows / macOS / Linux

### 安装与启动

```bash
# 克隆项目
git clone https://github.com/coldway/model-proxy.git
cd model-proxy

# 创建虚拟环境
python -m venv .venv
```

激活虚拟环境（根据平台选择）：

**Windows (PowerShell)**
```powershell
.venv\Scripts\Activate.ps1
```

**Windows (CMD)**
```cmd
.venv\Scripts\activate.bat
```

**macOS / Linux**
```bash
source .venv/bin/activate
```

安装依赖并启动：

```bash
pip install -r requirements.txt

# 复制示例配置并填入 API Key
cp conf/config.yaml.example conf/config.yaml   # Windows: copy conf\config.yaml.example conf\config.yaml

# 启动服务（支持热重载）
python main.py
```

服务默认监听 `http://127.0.0.1:8000`，访问 `http://127.0.0.1:8000/ui` 进入管理面板。

## 配置说明

项目采用**双配置文件**架构，将敏感信息与公开配置分离：

| 文件 | 内容 | 是否提交 Git |
|------|------|:---:|
| `conf/config.yaml` | **仅 API Key** + 服务设置 | ❌（`.gitignore` 排除） |
| `conf/providers_catalog.yaml` | 厂商目录、模型列表、启用状态、优先级 | ✅ 安全提交 |

### conf/config.yaml（仅保存 API Key）

```yaml
providers:
  google:
    api_key: "YOUR_GOOGLE_API_KEY"
  groq:
    api_key: "YOUR_GROQ_API_KEY"
  # ... 其他厂商同理

settings:
  host: "127.0.0.1"
  port: 8000
  default_provider: "google"
  auto_switch: true
  log_level: "info"
```

### conf/providers_catalog.yaml（厂商目录 + 运行时状态）

```yaml
providers:
  google:
    name: "Google AI Studio"
    enabled: true          # 厂商启用状态
    priority: 1            # 厂商优先级，数字越小越优先
    url: "https://aistudio.google.com/"
    models:
      - id: "gemini-2.5-pro"
        enabled: true      # 模型是否激活
        priority: 1        # 模型优先级
        default_rpd: 25
        default_rpm: 5
```

### 厂商优先级

每个厂商有独立的 `priority` 值（1 = 最高），支持**拖拽卡片排序**：
1. 先按**厂商优先级**排序
2. 同厂商内按**模型优先级**排序
3. 跳过限速已满或不可用的模型，自动降级到下一个

### 动态配置（无需重启）

所有配置变更通过 UI 或 API 操作后**即时生效**：

| 操作 | 效果 |
|------|------|
| 保存/清空 API Key | 自动注册/注销厂商 Provider |
| 启用/禁用厂商 | 自动加载/卸载 Provider 实例 |
| 启用/禁用模型 | 调度时即时反映 |
| 拖拽优先级排序 | 立即保存并生效 |
| 修改 log_level / default_provider / auto_switch | 即时生效 |

> 仅 `host` 和 `port` 需要重启服务（网络监听地址无法热切换）。

## 调度策略

- 内置滑动窗口限速器，按 RPD（每日请求数）和 RPM（每分钟请求数）控制调用频率
- `model="auto"` 时按厂商优先级 → 模型优先级顺序自动选择
- 速率受限时跳过，调用失败时判断是否额度用尽并自动切换
- 指定具体模型名称时直接路由，限速时返回 429 错误

## API 使用

所有接口兼容 OpenAI SDK 格式。

### 推理请求

```bash
POST /v1/chat/completions
Content-Type: application/json

{
  "model": "auto",
  "messages": [{"role": "user", "content": "Hello"}]
}
```

- `model` 设为 `"auto"` 时系统按优先级自动选择
- 也可指定如 `"gemini-2.5-flash"`、`"llama-3.3-70b-versatile"` 等具体模型

### 流式输出

```bash
POST /v1/chat/completions

{
  "model": "gemini-2.5-flash",
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": true
}
```

响应为 SSE（Server-Sent Events）格式，兼容 `openai.ChatCompletion.create(stream=True)`。

```python
# Python OpenAI SDK 示例
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused")

# 非流式
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "你好"}],
)
print(response.choices[0].message.content)

# 流式
stream = client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### 其他接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/v1/models` | GET | 列出所有已配置模型 |
| `/v1/usage` | GET | 各模型实时使用量和剩余额度 |
| `/api/config` | GET | 获取当前配置（隐藏 API Key） |
| `/api/config/apikey` | POST | 更新厂商 API Key（保存后自动注册 Provider） |
| `/api/config/model/toggle` | POST | 启用/禁用模型 |
| `/api/config/model/priority` | POST | 更新模型优先级 |
| `/api/config/model/add` | POST | 添加新模型到运行配置 |
| `/api/config/model/delete` | POST | 从运行配置删除模型 |
| `/api/provider/toggle` | POST | 启用/禁用厂商（动态注册/注销） |
| `/api/provider/priority` | POST | 更新厂商优先级 |
| `/api/provider/reorder` | POST | 批量重排厂商优先级（拖拽排序） |
| `/api/settings` | POST | 动态更新运行时设置（log_level/default_provider/auto_switch） |
| `/api/history` | GET | 最近请求历史（支持 `?limit=50`） |
| `/api/history/stats` | GET | 请求统计汇总 |
| `/api/catalog/providers` | GET | 目录中所有厂商和模型 |
| `/api/catalog/search` | GET | 搜索模型目录（`?q=llama`） |
| `/api/catalog/model/activate` | POST | 从目录激活模型到运行配置 |
| `/api/discovery` | GET | 查找可免费使用的大模型厂商 |
| `/api/provider/{name}/models` | GET | 拉取厂商最新远程模型列表 |

## UI 管理面板

内嵌单页 Web 面板，无需 Node.js 或前端构建，启动服务后直接访问 `/ui`。

| 功能模块 | 说明 |
|----------|------|
| **使用量监控** | 实时展示各厂商/模型调用次数、剩余额度、可用状态 |
| **模型管理** | 厂商卡片式布局，点击厂商进入模型管理弹窗 |
| **拖拽排序** | 拖拽厂商卡片直接调整优先级顺序，自动保存 |
| **厂商模型弹窗** | 查看/启用/停用模型，调整优先级，搜索目录模型，拉取厂商远程模型，手动添加自定义模型 |
| **接入指南** | 每个厂商卡片 `?` 图标，点击查看接入说明 |
| **问答聊天** | 内置聊天界面，支持选择模型或使用 auto 模式直接对话 |

## 运行测试

```bash
pip install pytest pytest-asyncio

# 运行全部测试
python -m pytest tests/ -v

# 运行特定模块
python -m pytest tests/test_rate_limiter.py -v
python -m pytest tests/test_config.py -v
python -m pytest tests/test_dispatcher.py -v
python -m pytest tests/test_api.py -v
```

| 模块 | 测试数 | 覆盖内容 |
|------|:---:|------|
| rate_limiter | 7 | RPD/RPM 限制、滑动窗口、模型独立性 |
| config | 6 | 加载/保存/更新配置、默认值、模型管理 |
| dispatcher | 6 | 自动选择、指定路由、失败切换、全部不可用 |
| api | 7 | 所有 HTTP 端点响应正确性 |

## 项目结构

```
model-proxy/
├── main.py                       # 服务入口
├── conf/
│   ├── config.yaml               # 运行配置（不提交，仅 API Key）
│   ├── config.yaml.example       # 配置模板
│   └── providers_catalog.yaml    # 厂商模型目录（可提交）
├── requirements.txt              # Python 依赖
├── prompt.md                     # 项目需求文档
├── README.md
├── .gitignore
├── src/
│   ├── api/
│   │   ├── routes.py             # API 路由（推理、配置、目录、历史）
│   │   ├── streaming.py          # SSE 流式输出
│   │   └── ui.py                 # 内嵌 Web 管理面板
│   ├── providers/
│   │   ├── base.py               # 厂商抽象基类（含流式接口）
│   │   ├── google.py             # Google AI Studio（支持流式）
│   │   ├── groq.py               # Groq（支持流式）
│   │   ├── github.py             # GitHub Models（支持流式）
│   │   ├── cursor.py             # Cursor Agent CLI
│   │   ├── openai_compat.py      # OpenAI 兼容通用适配器（支持流式）
│   │   ├── cloudflare.py         # Cloudflare Workers AI
│   │   └── huggingface.py        # HuggingFace Inference API
│   ├── scheduler/
│   │   ├── dispatcher.py         # 模型调度器（优先级 + 自动切换 + 流式调度）
│   │   ├── rate_limiter.py       # 滑动窗口限速器
│   │   └── history.py            # 请求历史记录与持久化
│   ├── config/
│   │   ├── manager.py            # conf/config.yaml 配置管理
│   │   └── catalog.py            # conf/providers_catalog.yaml 目录管理
│   └── models/
│       └── schemas.py            # Pydantic 数据模型
├── tests/
│   ├── test_api.py
│   ├── test_config.py
│   ├── test_dispatcher.py
│   └── test_rate_limiter.py
├── docs/                         # 项目文档
│   ├── architecture.md           # 架构概览
│   ├── api-reference.md          # API 接口参考
│   ├── providers.md              # 厂商适配器
│   ├── config.md                 # 配置系统
│   ├── scheduler.md              # 调度器与限速器
│   ├── ui.md                     # UI 管理面板
│   ├── deployment.md             # 部署指南
│   ├── faq.md                    # 常见问题
│   ├── changelog.md              # 更新日志
│   ├── contributing.md           # 贡献指南
│   └── testing.md                # 测试文档
└── data/                         # 运行时数据（自动创建，不提交）
    └── request_history.jsonl
```

## 文档

完整文档位于 `docs/` 目录下：

| 文档 | 说明 |
|------|------|
| [架构概览](docs/architecture.md) | 分层架构、请求流程、技术栈 |
| [API 接口参考](docs/api-reference.md) | 全部 19 个接口的请求/响应详细说明 |
| [厂商适配器](docs/providers.md) | BaseProvider 接口、各厂商实现、扩展方法 |
| [配置系统](docs/config.md) | 双配置文件架构、ConfigManager / CatalogManager |
| [调度器与限速器](docs/scheduler.md) | 调度逻辑、滑动窗口限速、请求历史 |
| [UI 管理面板](docs/ui.md) | 单文件 UI 实现、功能模块、交互特性 |
| [部署指南](docs/deployment.md) | Docker / Gunicorn / Nginx / Systemd 部署方式 |
| [常见问题](docs/faq.md) | 安装、配置、调度、UI、流式输出相关 FAQ |
| [更新日志](docs/changelog.md) | 版本历史与功能变更记录 |
| [贡献指南](docs/contributing.md) | 开发环境、代码规范、新增厂商步骤、提交规范 |
| [测试文档](docs/testing.md) | 测试策略、26 项用例详解、Mock 说明 |

## 安全注意事项

- API Key 仅存于本地 `conf/config.yaml`，该文件已被 `.gitignore` 排除
- `conf/providers_catalog.yaml` 不含任何敏感信息，可安全提交
- UI 面板仅监听 localhost，不对外暴露
- 请求历史只记录元数据（延迟、Token 数、成功/失败），不记录消息内容
- 禁止在代码或日志中明文输出 API Key

## License

MIT
