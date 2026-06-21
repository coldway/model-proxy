# Cursor Agent CLI 集成修复说明

## 问题描述

Model Proxy 的 Cursor Provider 使用了过时的命令行参数格式,导致无法正常调用 Cursor Agent CLI。

错误日志:
```
error: unknown option '--prompt'
```

## 根因分析

1. **命令格式变更**: Cursor CLI 3.5.17 版本的命令格式已从 `cursor agent --prompt <text>` 改为 `cursor agent [prompt...]`
2. **缺少必要参数**: 非交互式调用需要 `--print` 和 `--trust` 参数
3. **流式输出未实现**: 虽然 Cursor CLI 支持流式输出,但原实现未使用
4. **API Key 传递问题**: 未支持通过参数传递 API Key

## 修复内容

### 1. 修正命令格式 (`src/providers/cursor.py`)

**Before:**
```python
proc = await asyncio.create_subprocess_exec(
    "cursor", "agent", "--prompt", prompt,  # ❌ --prompt 选项已移除
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
```

**After:**
```python
cmd_args = ["cursor", "agent", "--print", "--trust"]
if self._api_key:
    cmd_args.extend(["--api-key", self._api_key])
cmd_args.append(prompt)  # ✅ 直接作为位置参数

proc = await asyncio.create_subprocess_exec(
    *cmd_args,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
```

### 2. 新增参数说明

| 参数 | 作用 | 必要性 |
|-----|------|--------|
| `--print` | 启用非交互模式,输出到 stdout | ✅ 必须 |
| `--trust` | 自动信任工作空间,避免交互提示 | ✅ 必须 |
| `--api-key` | 传递 API Key (如果配置了) | 可选 |

### 3. 实现流式输出

新增 `stream_chat_completion` 方法,使用以下参数:
```bash
cursor agent --print --trust --output-format stream-json --stream-partial-output [prompt]
```

流式输出支持多种格式:
- `{"type":"assistant","delta":{"content":"..."}}`
- `{"content":"..."}`
- `{"delta":{"content":"..."}}`

### 4. 支持 API Key 配置

- 支持从构造函数参数传入
- 支持从 `CURSOR_API_KEY` 环境变量读取
- 优先级: 构造参数 > 环境变量

```python
def __init__(self, api_key: str = ""):
    super().__init__(api_key)
    import os
    self._api_key = api_key or os.environ.get("CURSOR_API_KEY", "")
```

## 验证结果

✅ **健康检查**: 成功  
✅ **非流式调用**: 成功 (已在实际环境测试)  
✅ **流式调用**: 已实现完整的格式解析  
✅ **身份认证**: 支持多种方式 (登录/API Key/环境变量)

## 使用说明

### 方式一: 通过 config.yaml 配置

```yaml
providers:
  cursor:
    api_key: "your-cursor-api-key"  # 可选,也可不填
```

### 方式二: 通过环境变量

```bash
export CURSOR_API_KEY="your-cursor-api-key"
python main.py
```

### 方式三: 使用 Cursor IDE 登录

如果不配置 API Key,需要先运行:
```bash
cursor agent login
```

登录后 Cursor CLI 会自动使用 IDE 的认证信息。

## 测试验证

### 1. 健康检查
```bash
cursor --version
# 输出: 3.5.17 (或更高版本)
```

### 2. 非流式调用测试

通过 Model Proxy API:
```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cursor-agent",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### 3. 流式调用测试

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cursor-agent",
    "messages": [{"role": "user", "content": "Count to 5"}],
    "stream": true
  }'
```

## 兼容性

- **Cursor CLI 版本**: 3.5.x 及以上
- **Python 版本**: 3.11+
- **操作系统**: macOS / Linux / Windows

## 注意事项

1. **认证要求**: Cursor Agent 需要认证,请确保:
   - 已运行 `cursor agent login`,或
   - 已配置 `CURSOR_API_KEY`,或
   - 已在 `config.yaml` 中填写 `cursor.api_key`

2. **工作空间**: Cursor Agent 会在当前工作目录执行,确保启动 Model Proxy 时在合适的目录

3. **超时设置**: 默认超时 120 秒,复杂任务可能需要调整

4. **并发限制**: Cursor Agent 按账号限速,建议合理控制并发

## 相关文档

- [Cursor CLI 文档](https://docs.cursor.com/cli)
- [Model Proxy 配置说明](docs/config.md)
- [厂商适配器开发](docs/providers.md)

## 更新日期

2026-05-26
