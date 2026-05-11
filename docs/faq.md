# 常见问题 FAQ

## 安装与启动

### Q: 启动时报错 `ModuleNotFoundError`

确认已激活虚拟环境且依赖安装完成：

```bash
# 确认虚拟环境已激活（命令行前缀应显示 .venv）
pip list | grep fastapi

# 重新安装依赖
pip install -r requirements.txt
```

### Q: 启动时端口 8000 被占用

编辑 `conf/config.yaml` 修改端口：

```yaml
settings:
  port: 8080  # 改为其他可用端口
```

或者查找并释放占用的进程：

```bash
# Linux/macOS
lsof -i :8000
# Windows
netstat -ano | findstr :8000
```

### Q: Windows 上 `cp` 命令不可用

Windows CMD 中使用 `copy` 替代 `cp`：

```cmd
copy conf\config.yaml.example conf\config.yaml
```

PowerShell 中 `cp` 是 `Copy-Item` 的别名，可正常使用。

---

## 配置

### Q: `config.yaml` 和 `providers_catalog.yaml` 各存什么？

| 文件 | 内容 | Git 提交 |
|------|------|:---:|
| `conf/config.yaml` | API Key + 服务设置（host/port/log_level） | ❌ 不提交 |
| `conf/providers_catalog.yaml` | 厂商目录、模型列表、启用/禁用状态、优先级、默认限速 | ✅ 安全提交 |

`config.yaml` 只包含敏感信息，绝不提交 Git。所有与模型状态相关的配置（启用、优先级）都由 `providers_catalog.yaml` 管理。

### Q: 如何新增一个模型厂商？

1. 在 `src/providers/` 中创建适配器（继承 `BaseProvider` 或使用 `OpenAICompatibleProvider`）
2. 在 `conf/providers_catalog.yaml` 中添加厂商和模型信息
3. 在 `conf/config.yaml` 中添加 API Key 配置
4. 在 `main.py` 的 `provider_factories` 字典中注册厂商

详见 [厂商适配器文档](providers.md)。

### Q: 修改配置后需要重启服务吗？

绝大多数配置修改**无需重启**，通过 UI 或 API 操作后即时生效：

- API Key 保存/清空 → 自动注册/注销厂商
- 厂商启用/禁用 → 自动加载/卸载 Provider
- 模型启用/禁用/优先级 → 即时反映到调度
- log_level / default_provider / auto_switch → 即时生效

**唯一例外**：`host` 和 `port` 需要重启服务（网络监听地址由 Uvicorn 在启动时绑定）。

### Q: 如何获取各厂商的 API Key？

| 厂商 | 获取方式 |
|------|---------|
| Google AI Studio | [aistudio.google.com](https://aistudio.google.com/) → 获取 API Key |
| Groq | [console.groq.com](https://console.groq.com/) → API Keys |
| Cerebras | [cloud.cerebras.ai](https://cloud.cerebras.ai/) → API Keys |
| SambaNova | [cloud.sambanova.ai](https://cloud.sambanova.ai/) → API Keys |
| GitHub Models | 使用 GitHub Personal Access Token |
| OpenRouter | [openrouter.ai](https://openrouter.ai/) → Keys |
| Mistral AI | [console.mistral.ai](https://console.mistral.ai/) → API Keys |
| Cloudflare | Workers AI → Account ID + API Token，格式为 `"account_id:api_token"` |
| HuggingFace | [huggingface.co](https://huggingface.co/settings/tokens) → Access Tokens |
| Cursor | 无需 API Key，使用 Cursor IDE 登录凭据 |

### Q: Cursor 厂商不需要 API Key，如何启用？

Cursor 使用本地 Cursor IDE 的登录凭据，无需填写 API Key。只需在 `conf/providers_catalog.yaml` 中将 `cursor` 的 `enabled` 设为 `true`：

```yaml
cursor:
  name: "Cursor Agent CLI"
  enabled: true
  priority: 10
```

---

## 厂商与模型

### Q: 指定某个 Google 模型时返回 429 错误

Google 免费额度有严格的每日/每分钟限制：

| 模型 | RPD（次/天） | RPM（次/分钟） |
|------|:---:|:---:|
| Gemini 2.5 Pro | 25 | 5 |
| Gemini 2.5 Flash | 500 | 10 |
| Gemini 2.0 Flash | 1,500 | 15 |
| Gemma 4 26B | 1,500 | 15 |

当额度用尽时 Google API 返回 429 Too Many Requests。系统会返回明确提示并建议使用 auto 模式。auto 模式下会自动跳过限流模型，切换到 Groq（14400 次/天）等更宽松的厂商。额度每日 UTC 00:00 重置。

### Q: 模型返回 404 Not Found

模型 ID 必须与厂商 API 中的实际名称完全匹配。例如 Google 的 Gemma 4 模型 ID 是 `gemma-4-26b-a4b-it` 而非 `gemma-4-27b`。可通过 UI 中的"拉取厂商全部"功能获取最新的准确模型列表。

### Q: 建议如何选择模型？

- **日常使用**：推荐 auto 模式，系统按优先级自动调度，限流时自动切换
- **高频调用**：选择 Groq 的 `llama-3.3-70b-versatile`（14400 RPD，最稳定）
- **推理质量优先**：Gemini 2.5 Pro（25 RPD，最强但额度最少）

---

## 调度与限速

### Q: `model="auto"` 时模型选择的逻辑是什么？

1. 获取所有已启用的模型，按 **厂商优先级 → 模型优先级** 排序
2. 逐一检查 RPD/RPM 限速是否允许
3. 调用第一个可用模型，失败则自动切换到下一个
4. 所有模型均不可用时返回 503 错误

### Q: 如何调整模型优先级？

两种方式：

- **UI 面板**：拖拽厂商卡片调整厂商优先级，点击厂商卡片进入模型管理弹窗拖拽调整模型优先级
- **配置文件**：编辑 `conf/providers_catalog.yaml` 中各厂商/模型的 `priority` 值（数字越小越优先）

### Q: 某个模型一直返回失败，怎么排查？

1. 检查 `conf/config.yaml` 中对应厂商的 API Key 是否正确
2. 访问 `/v1/usage` 查看该模型的 RPD/RPM 使用量是否已满
3. 查看 `/api/history` 的最近请求记录，确认错误类型
4. 查看终端日志中的 `ERROR` 级别输出

### Q: 限速数据存在哪里？服务重启后会清零吗？

限速数据存储在内存中（滑动窗口），服务重启后自动清零。这是预期行为，因为限速窗口通常在分钟/天级别，重启后从零开始不影响正常使用。

---

## UI 面板

### Q: 访问 `/ui` 页面空白

可能原因：

1. 服务未完全启动 —— 检查终端是否出现 `Model Proxy 启动于 http://...` 日志
2. 浏览器缓存 —— 按 `Ctrl+Shift+R` 强制刷新
3. JavaScript 报错 —— 打开浏览器开发者工具（F12）查看控制台错误

### Q: UI 上的拖拽排序不生效

- 确认拖拽的是厂商卡片本身（需要在卡片主体区域进行拖拽）
- 拖拽完成后检查是否有 toast 提示"厂商优先级已更新"
- 刷新页面后优先级是否保持 —— 如未保持则检查 `providers_catalog.yaml` 的文件写权限

### Q: 切换 Tab 后刷新页面回到了第一个 Tab

UI 使用 `location.hash` 记忆当前 Tab。确认浏览器 URL 中包含 `#models` / `#chat` 等锚点。如果 URL 中没有 hash，可能是浏览器扩展或安全策略阻止了 hash 修改。

---

## 流式输出

### Q: 流式输出（SSE）收不到数据

- 确认请求中设置了 `"stream": true`
- 如果使用了反向代理（Nginx），需要关闭缓冲：`proxy_buffering off;`
- 检查客户端是否正确处理 SSE 格式（`data: {...}\n\n`）
- Google/Groq/GitHub/OpenAI 兼容厂商支持原生流式输出；Cloudflare/HuggingFace/Cursor 使用默认回退（一次性返回完整内容）

### Q: 流式输出中途断开

- 使用 Uvicorn reload 模式时，文件变更会触发进程重启导致流式连接中断
- 生产环境建议关闭 reload：`uvicorn.run(app, reload=False)`
- 如果厂商 API 返回非 200 状态码（如 503），流式生成器会捕获异常并在 SSE 事件中返回错误信息

### Q: 使用 OpenAI Python SDK 如何接入？

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="not-needed",  # 本地代理不验证客户端 Key
)

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

---

## 数据与日志

### Q: 请求历史保存在哪里？

请求历史以 JSONL 格式持久化到 `data/request_history.jsonl`，每行一条记录。记录仅包含元数据（时间戳、模型名、延迟、token 数、成功/失败），**不记录消息内容**。

### Q: 如何清空请求历史？

删除 `data/request_history.jsonl` 文件并重启服务：

```bash
rm data/request_history.jsonl   # Windows: del data\request_history.jsonl
```

### Q: 日志在哪里查看？

日志输出到标准输出（stdout）。开发模式下直接在终端查看；Docker 部署时使用 `docker logs model-proxy`。日志等级通过 `conf/config.yaml` 中的 `settings.log_level` 控制。
