# Cursor Agent CLI Plan 模式使用指南

## 概览

model-proxy 现已支持 Cursor Agent CLI 的三种执行模式：

| 模式 | 说明 | 权限 | 使用场景 |
|------|------|------|----------|
| **agent** | 默认模式 | 读写文件、执行命令 | 复杂编码任务、代码重构 |
| **plan** | 规划模式 | 只读 | 设计方案、分析架构、制定计划 |
| **ask** | 问答模式 | 只读 | 代码解释、问题回答 |

## API 使用

### 请求格式

在 `/v1/chat/completions` 请求中添加 `mode` 字段：

```json
{
  "model": "cursor-agent",
  "messages": [
    {"role": "user", "content": "分析这个项目的架构并提出重构方案"}
  ],
  "mode": "plan"
}
```

### Python SDK 示例

#### Plan 模式（规划优先）

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused")

# Plan 模式：只分析不修改
response = client.chat.completions.create(
    model="cursor-agent",
    messages=[
        {"role": "user", "content": "分析 auth 模块并提出重构方案"}
    ],
    extra_body={"mode": "plan"}  # 添加 mode 参数
)
print(response.choices[0].message.content)
```

#### Ask 模式（问答）

```python
response = client.chat.completions.create(
    model="cursor-agent",
    messages=[
        {"role": "user", "content": "这段代码的性能瓶颈在哪里？"}
    ],
    extra_body={"mode": "ask"}
)
print(response.choices[0].message.content)
```

#### Agent 模式（默认，全权限）

```python
# 不指定 mode 或 mode="agent"，具备文件修改权限
response = client.chat.completions.create(
    model="cursor-agent",
    messages=[
        {"role": "user", "content": "重构 auth 模块为 JWT 认证"}
    ]
    # mode 默认为 "agent"
)
```

### 流式输出

```python
stream = client.chat.completions.create(
    model="cursor-agent",
    messages=[
        {"role": "user", "content": "分析这个项目的技术债务"}
    ],
    stream=True,
    extra_body={"mode": "plan"}
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### cURL 示例

```bash
# Plan 模式
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cursor-agent",
    "messages": [
      {"role": "user", "content": "分析项目架构"}
    ],
    "mode": "plan"
  }'

# Ask 模式
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cursor-agent",
    "messages": [
      {"role": "user", "content": "这个函数做什么？"}
    ],
    "mode": "ask"
  }'
```

## 前端集成示例

### JavaScript/TypeScript

```typescript
interface ChatRequest {
  model: string;
  messages: Array<{role: string; content: string}>;
  mode?: 'agent' | 'plan' | 'ask';
  stream?: boolean;
}

async function sendChatRequest(content: string, mode: 'plan' | 'ask' | 'agent' = 'agent') {
  const response = await fetch('http://127.0.0.1:8000/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: 'cursor-agent',
      messages: [{role: 'user', content}],
      mode,
    } as ChatRequest),
  });
  
  const data = await response.json();
  return data.choices[0].message.content;
}

// 使用 plan 模式
const analysis = await sendChatRequest(
  '分析这个项目的架构并提出改进建议',
  'plan'
);
console.log(analysis);
```

### React 组件示例

```tsx
import { useState } from 'react';

type Mode = 'agent' | 'plan' | 'ask';

export function CursorChat() {
  const [mode, setMode] = useState<Mode>('agent');
  const [input, setInput] = useState('');
  const [response, setResponse] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    
    try {
      const res = await fetch('http://127.0.0.1:8000/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: 'cursor-agent',
          messages: [{role: 'user', content: input}],
          mode,
        }),
      });
      
      const data = await res.json();
      setResponse(data.choices[0].message.content);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div className="mode-selector">
        <button onClick={() => setMode('agent')} disabled={mode === 'agent'}>
          Agent（编码）
        </button>
        <button onClick={() => setMode('plan')} disabled={mode === 'plan'}>
          Plan（规划）
        </button>
        <button onClick={() => setMode('ask')} disabled={mode === 'ask'}>
          Ask（问答）
        </button>
      </div>
      
      <form onSubmit={handleSubmit}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={`${mode} 模式：${getModeHint(mode)}`}
        />
        <button type="submit" disabled={loading}>
          {loading ? '处理中...' : '发送'}
        </button>
      </form>
      
      {response && (
        <div className="response">
          <pre>{response}</pre>
        </div>
      )}
    </div>
  );
}

function getModeHint(mode: Mode): string {
  switch (mode) {
    case 'plan':
      return '输入需求，AI 将只分析不修改代码';
    case 'ask':
      return '提问关于代码的问题';
    case 'agent':
      return '输入任务，AI 将执行完整的代码修改';
  }
}
```

## 使用建议

### 何时使用 Plan 模式

- **大型重构前**：先用 plan 模式分析架构、识别风险、设计方案
- **技术选型**：对比多种实现方案的优劣
- **代码审查**：分析现有代码的问题和改进空间
- **文档生成**：基于代码生成设计文档

### 何时使用 Ask 模式

- **代码理解**：快速了解某段代码的功能
- **问题诊断**：询问可能的 bug 原因
- **知识查询**：询问最佳实践、设计模式

### 何时使用 Agent 模式

- **实际开发**：需要修改文件、执行命令
- **自动化任务**：批量重构、代码生成
- **端到端实现**：从需求直接到可运行代码

## 典型工作流

### 1. 计划 → 执行流程

```python
# 步骤1: 用 plan 模式先分析
plan_response = client.chat.completions.create(
    model="cursor-agent",
    messages=[{"role": "user", "content": "分析 auth 模块重构为 JWT 的方案"}],
    extra_body={"mode": "plan"}
)
print("方案：", plan_response.choices[0].message.content)

# 步骤2: 确认方案后，用 agent 模式执行
confirm = input("确认执行此方案？(y/n): ")
if confirm.lower() == 'y':
    exec_response = client.chat.completions.create(
        model="cursor-agent",
        messages=[
            {"role": "user", "content": "分析 auth 模块重构为 JWT 的方案"},
            {"role": "assistant", "content": plan_response.choices[0].message.content},
            {"role": "user", "content": "按照上述方案执行重构"}
        ]
        # mode 默认为 "agent"
    )
```

### 2. 问答 → 修复流程

```python
# 步骤1: 用 ask 模式询问问题
ask_response = client.chat.completions.create(
    model="cursor-agent",
    messages=[{"role": "user", "content": "这段代码有什么性能问题？"}],
    extra_body={"mode": "ask"}
)
print("分析：", ask_response.choices[0].message.content)

# 步骤2: 用 agent 模式修复
fix_response = client.chat.completions.create(
    model="cursor-agent",
    messages=[
        {"role": "user", "content": "这段代码有什么性能问题？"},
        {"role": "assistant", "content": ask_response.choices[0].message.content},
        {"role": "user", "content": "修复上述性能问题"}
    ]
)
```

## 注意事项

1. **mode 参数仅对 Cursor provider 生效**，其他 provider（Google、Groq 等）会忽略此参数
2. **plan 和 ask 模式不会修改文件**，适合安全的分析场景
3. **agent 模式具有完整权限**，建议在受控环境中使用
4. **流式输出在所有模式下都支持**

## 错误处理

```python
try:
    response = client.chat.completions.create(
        model="cursor-agent",
        messages=[{"role": "user", "content": "分析项目"}],
        extra_body={"mode": "invalid-mode"}  # 无效的 mode
    )
except Exception as e:
    # 无效的 mode 会被忽略，回退到默认的 agent 模式
    print(f"警告: {e}")
```

## 配置

确保 `conf/config.yaml` 中 Cursor provider 已配置：

```yaml
providers:
  cursor:
    api_key: ""  # 可选，留空则使用 CURSOR_API_KEY 环境变量
```

确保 `conf/providers_catalog.yaml` 中 Cursor 已启用：

```yaml
providers:
  cursor:
    enabled: true
    priority: 10
    models:
      - id: "cursor-agent"
        enabled: true
        priority: 1
```

## 更多资源

- [Cursor CLI 官方文档](https://cursor.com/docs/cli/overview)
- [model-proxy README](../README.md)
- [API 接口参考](./api-reference.md)
