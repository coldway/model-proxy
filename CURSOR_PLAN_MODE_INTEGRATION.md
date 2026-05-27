# Cursor Agent CLI Plan 模式集成 - 快速开始

## 已完成的修改

✅ **1. Schema 扩展** (`src/models/schemas.py`)
- 在 `ChatCompletionRequest` 中添加 `mode` 字段
- 支持 `plan`、`ask`、`agent` 三种模式

✅ **2. Provider 更新** (`src/providers/cursor.py`)
- `_build_cmd_args()` 方法支持 `--plan` 和 `--mode ask` 参数
- `_run_cli()` 方法传递 mode 参数
- `chat_completion()` 和 `stream_chat_completion()` 都支持 mode

✅ **3. 测试用例** (`tests/test_cursor_plan_mode.py`)
- 6 个单元测试全部通过
- 覆盖 plan、ask、agent 三种模式
- 测试流式 + plan 组合

✅ **4. 文档** (`docs/cursor-plan-mode.md`)
- 完整使用指南
- Python SDK 示例
- React 前端集成示例
- cURL 示例
- 最佳实践

✅ **5. 验证脚本** (`scripts/test_cursor_plan_mode.py`)
- 可执行的端到端测试脚本
- 测试所有模式和场景

## 如何验证

### 1. 启动 model-proxy

```bash
cd /Users/yuanrui/go/src/gitlab.p1staff.com/new/AI-Coding/workspace/AI-agent/model-proxy
python main.py
```

### 2. 运行单元测试

```bash
python -m pytest tests/test_cursor_plan_mode.py -v
```

### 3. 运行端到端验证

```bash
python scripts/test_cursor_plan_mode.py
```

### 4. 手动测试（cURL）

```bash
# Plan 模式
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cursor-agent",
    "messages": [{"role": "user", "content": "分析这个项目的架构"}],
    "mode": "plan"
  }'

# Ask 模式
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cursor-agent",
    "messages": [{"role": "user", "content": "这个项目是做什么的？"}],
    "mode": "ask"
  }'
```

### 5. Python SDK 测试

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused")

# Plan 模式
response = client.chat.completions.create(
    model="cursor-agent",
    messages=[{"role": "user", "content": "分析项目架构"}],
    extra_body={"mode": "plan"}
)
print(response.choices[0].message.content)
```

## 前端集成

### JavaScript/TypeScript

```typescript
async function callCursorPlan(content: string, mode: 'plan' | 'ask' | 'agent' = 'plan') {
  const response = await fetch('http://127.0.0.1:8000/v1/chat/completions', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      model: 'cursor-agent',
      messages: [{role: 'user', content}],
      mode,
    }),
  });
  
  const data = await response.json();
  return data.choices[0].message.content;
}

// 使用
const analysis = await callCursorPlan('分析这个项目', 'plan');
console.log(analysis);
```

### React Hook 示例

```tsx
import { useState } from 'react';

export function useCursorAgent() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sendMessage = async (
    content: string,
    mode: 'plan' | 'ask' | 'agent' = 'agent'
  ) => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch('http://127.0.0.1:8000/v1/chat/completions', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          model: 'cursor-agent',
          messages: [{role: 'user', content}],
          mode,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();
      return data.choices[0].message.content;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error');
      throw e;
    } finally {
      setLoading(false);
    }
  };

  return { sendMessage, loading, error };
}

// 在组件中使用
function MyComponent() {
  const { sendMessage, loading, error } = useCursorAgent();
  const [result, setResult] = useState('');

  const analyzePlan = async () => {
    const response = await sendMessage('分析项目架构', 'plan');
    setResult(response);
  };

  return (
    <div>
      <button onClick={analyzePlan} disabled={loading}>
        {loading ? '分析中...' : '分析架构（Plan 模式）'}
      </button>
      {error && <div className="error">{error}</div>}
      {result && <pre>{result}</pre>}
    </div>
  );
}
```

## 技术细节

### Mode 参数如何传递

```
前端请求 → FastAPI (/v1/chat/completions)
  ↓
ChatCompletionRequest.mode 字段
  ↓
Dispatcher.dispatch() 传递 request
  ↓
CursorProvider.chat_completion(model, request)
  ↓
_run_cli(..., mode=request.mode)
  ↓
_build_cmd_args(..., mode=mode)
  ↓
生成 cursor agent --plan 或 --mode ask 命令
```

### 实际命令示例

```bash
# Plan 模式
cursor agent --print --trust --model auto --plan "分析项目架构"

# Ask 模式
cursor agent --print --trust --model auto --mode ask "这个项目做什么?"

# Agent 模式（默认）
cursor agent --print --trust --model auto "重构代码"
```

## 常见问题

### Q: 前端如何知道使用了哪个模式？

A: 响应中会包含 `proxy_info` 字段，其中 `provider` 显示为 `cursor`，日志会记录 mode 参数。

### Q: mode 参数对其他 provider 有影响吗？

A: 不会。只有 `CursorProvider` 会处理 mode 参数，其他 provider（Google、Groq 等）会忽略此字段。

### Q: 可以在流式输出中使用 plan 模式吗？

A: 可以。设置 `stream=True` 和 `mode="plan"` 即可。

### Q: 如何在前端区分三种模式？

A: 建议使用按钮或下拉菜单让用户选择：
- Agent（编码）：全权限，可修改文件
- Plan（规划）：只读，分析和设计
- Ask（问答）：只读，快速问答

## 下一步

1. 更新 `README.md` 添加 plan 模式说明
2. 在 UI 面板中添加 mode 选择器
3. 更新 API 文档 (`docs/api-reference.md`)
4. 考虑添加更多 Cursor CLI 参数支持（如 `--sandbox`、`--force` 等）

## 参考资料

- [Cursor CLI 官方文档](https://cursor.com/docs/cli/overview)
- [model-proxy 完整使用指南](docs/cursor-plan-mode.md)
- [测试用例](tests/test_cursor_plan_mode.py)
- [验证脚本](scripts/test_cursor_plan_mode.py)
