# Model Proxy 前端对接示例

本文档提供多种技术栈的前端对接示例，帮助开发者快速集成 Model Proxy API，特别是 Cursor Agent CLI 的 plan/ask/agent 模式。

## 目录

1. [纯 JavaScript 示例](#1-纯-javascript-示例)
2. [React + TypeScript 示例](#2-react--typescript-示例)
3. [Vue 3 示例](#3-vue-3-示例)
4. [完整单页面应用（SPA）](#4-完整单页面应用spa)

---

## 1. 纯 JavaScript 示例

### 基础聊天请求（非流式）

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>Model Proxy - 基础示例</title>
    <style>
        body { font-family: sans-serif; max-width: 800px; margin: 20px auto; padding: 20px; }
        .message { padding: 12px; margin: 8px 0; border-radius: 8px; }
        .user { background: #e3f2fd; text-align: right; }
        .assistant { background: #f5f5f5; }
        .tags { display: flex; gap: 8px; margin-top: 4px; font-size: 12px; }
        .tag { padding: 2px 8px; border-radius: 4px; }
        .tag-model { background: rgba(59,130,246,0.1); color: #3b82f6; }
        .tag-mode { background: rgba(139,92,246,0.1); color: #8b5cf6; font-weight: bold; }
        button { padding: 8px 16px; margin: 4px; cursor: pointer; }
        button.active { background: #3b82f6; color: white; border: none; }
    </style>
</head>
<body>
    <h1>Model Proxy Chat</h1>
    
    <!-- 模式选择器 -->
    <div id="mode-selector">
        <label>Cursor 模式:</label>
        <button onclick="selectMode('agent')" id="btn-agent">Agent</button>
        <button onclick="selectMode('plan')" id="btn-plan">Plan</button>
        <button onclick="selectMode('ask')" id="btn-ask">Ask</button>
        <label><input type="checkbox" id="force"> Force</label>
        <select id="sandbox">
            <option value="">Sandbox: 默认</option>
            <option value="enabled">启用</option>
            <option value="disabled">禁用</option>
        </select>
    </div>
    
    <!-- 消息容器 -->
    <div id="messages"></div>
    
    <!-- 输入区 -->
    <textarea id="input" rows="3" style="width:100%"></textarea>
    <button onclick="sendMessage()">发送</button>

    <script>
        const API_BASE = 'http://localhost:8009';
        let currentMode = 'agent';
        let sessionId = null;

        function selectMode(mode) {
            currentMode = mode;
            ['agent', 'plan', 'ask'].forEach(m => {
                document.getElementById(`btn-${m}`).className = m === mode ? 'active' : '';
            });
            // plan/ask 模式禁用 force
            document.getElementById('force').disabled = mode !== 'agent';
        }

        async function createSession() {
            const res = await fetch(`${API_BASE}/api/chat/sessions`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    model: 'auto',
                    title: '前端示例会话'
                })
            });
            const data = await res.json();
            sessionId = data.data.session_id;
            console.log('会话创建:', sessionId);
        }

        async function sendMessage() {
            if (!sessionId) await createSession();
            
            const input = document.getElementById('input');
            const userMsg = input.value.trim();
            if (!userMsg) return;

            // 显示用户消息
            addMessage('user', userMsg);
            input.value = '';

            // 构建请求
            const force = document.getElementById('force').checked;
            const sandbox = document.getElementById('sandbox').value;
            const requestBody = {
                model: 'auto',
                messages: [{ role: 'user', content: userMsg }],
                temperature: 0.7,
                mode: currentMode,
                force: currentMode === 'agent' ? force : false,
            };
            if (sandbox) requestBody.sandbox = sandbox;

            try {
                const res = await fetch(`${API_BASE}/api/chat/sessions/${sessionId}/message`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody)
                });
                const data = await res.json();
                
                // 显示助手回复
                const reply = data.data.choices[0].message.content;
                const model = data.data.model;
                addMessage('assistant', reply, { model, mode: currentMode });
            } catch (err) {
                addMessage('assistant', `❌ 错误: ${err.message}`);
            }
        }

        function addMessage(role, content, meta = {}) {
            const div = document.createElement('div');
            div.className = `message ${role}`;
            div.textContent = content;
            
            if (meta.model || meta.mode) {
                const tags = document.createElement('div');
                tags.className = 'tags';
                if (meta.model) {
                    const modelTag = document.createElement('span');
                    modelTag.className = 'tag tag-model';
                    modelTag.textContent = `🤖 ${meta.model}`;
                    tags.appendChild(modelTag);
                }
                if (meta.mode && meta.mode !== 'agent') {
                    const modeTag = document.createElement('span');
                    modeTag.className = 'tag tag-mode';
                    const icons = { plan: '📐', ask: '💬' };
                    modeTag.textContent = `${icons[meta.mode]} ${meta.mode.toUpperCase()}`;
                    tags.appendChild(modeTag);
                }
                div.appendChild(tags);
            }
            
            document.getElementById('messages').appendChild(div);
        }

        // 初始化
        selectMode('agent');
    </script>
</body>
</html>
```

### 流式聊天请求

```javascript
async function sendStreamingMessage(userMsg) {
    if (!sessionId) await createSession();
    
    const requestBody = {
        model: 'auto',
        messages: [{ role: 'user', content: userMsg }],
        temperature: 0.7,
        mode: currentMode,
        force: currentMode === 'agent' && document.getElementById('force').checked,
    };
    
    const sandbox = document.getElementById('sandbox').value;
    if (sandbox) requestBody.sandbox = sandbox;

    const res = await fetch(`${API_BASE}/api/chat/sessions/${sessionId}/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let fullText = '';
    let usedModel = '';
    
    // 创建占位气泡
    const bubble = addMessage('assistant', '');

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const payload = line.slice(6).trim();
            if (payload === '[DONE]') continue;

            try {
                const chunk = JSON.parse(payload);
                
                // 提取 meta 信息
                if (chunk.meta && chunk.meta.model) {
                    usedModel = chunk.meta.model;
                }
                
                // 提取内容增量
                const delta = chunk.choices?.[0]?.delta;
                if (delta?.content) {
                    fullText += delta.content;
                    bubble.textContent = fullText;
                }
            } catch (e) {
                console.warn('解析 SSE 失败:', e);
            }
        }
    }

    // 添加标签
    if (usedModel || currentMode !== 'agent') {
        addMetaTags(bubble, { model: usedModel, mode: currentMode });
    }
}

function addMetaTags(element, meta) {
    const tags = document.createElement('div');
    tags.className = 'tags';
    
    if (meta.model) {
        const modelTag = document.createElement('span');
        modelTag.className = 'tag tag-model';
        modelTag.textContent = `🤖 ${meta.model}`;
        tags.appendChild(modelTag);
    }
    
    if (meta.mode && meta.mode !== 'agent') {
        const modeTag = document.createElement('span');
        modeTag.className = 'tag tag-mode';
        const icons = { plan: '📐', ask: '💬' };
        modeTag.textContent = `${icons[meta.mode]} ${meta.mode.toUpperCase()}`;
        tags.appendChild(modeTag);
    }
    
    element.appendChild(tags);
}
```

---

## 2. React + TypeScript 示例

### 类型定义

```typescript
// types.ts
export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface CursorMode {
  mode?: 'agent' | 'plan' | 'ask';
  force?: boolean;
  sandbox?: '' | 'enabled' | 'disabled';
}

export interface ChatRequest extends CursorMode {
  model: string;
  messages: ChatMessage[];
  temperature?: number;
  max_tokens?: number;
  stream?: boolean;
}

export interface ChatResponse {
  data: {
    id: string;
    model: string;
    choices: Array<{
      message: ChatMessage;
      index: number;
    }>;
    usage?: {
      prompt_tokens: number;
      completion_tokens: number;
      total_tokens: number;
    };
  };
}

export interface StreamChunk {
  meta?: {
    model?: string;
    title?: string;
  };
  choices?: Array<{
    delta?: {
      role?: string;
      content?: string;
    };
    index: number;
  }>;
  session_info?: {
    tokens_est: number;
    total_messages: number;
  };
}
```

### API 客户端

```typescript
// api.ts
const API_BASE = process.env.REACT_APP_API_BASE || 'http://localhost:8009';

export class ModelProxyClient {
  private sessionId: string | null = null;

  async createSession(model: string = 'auto', title?: string) {
    const res = await fetch(`${API_BASE}/api/chat/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, title }),
    });
    const data = await res.json();
    this.sessionId = data.data.session_id;
    return this.sessionId;
  }

  async sendMessage(
    message: string,
    options: Partial<ChatRequest> = {}
  ): Promise<ChatResponse> {
    if (!this.sessionId) {
      await this.createSession(options.model);
    }

    const res = await fetch(
      `${API_BASE}/api/chat/sessions/${this.sessionId}/message`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: 'auto',
          messages: [{ role: 'user', content: message }],
          temperature: 0.7,
          ...options,
        }),
      }
    );

    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail);
    }

    return res.json();
  }

  async *streamMessage(
    message: string,
    options: Partial<ChatRequest> = {}
  ): AsyncGenerator<StreamChunk, void, unknown> {
    if (!this.sessionId) {
      await this.createSession(options.model);
    }

    const res = await fetch(
      `${API_BASE}/api/chat/sessions/${this.sessionId}/stream`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: 'auto',
          messages: [{ role: 'user', content: message }],
          temperature: 0.7,
          ...options,
        }),
      }
    );

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6).trim();
        if (payload === '[DONE]') continue;

        try {
          yield JSON.parse(payload) as StreamChunk;
        } catch (e) {
          console.warn('Failed to parse SSE:', e);
        }
      }
    }
  }
}
```

### React 组件

```tsx
// ChatComponent.tsx
import React, { useState, useRef, useEffect } from 'react';
import { ModelProxyClient, CursorMode } from './api';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  model?: string;
  mode?: string;
}

export const ChatComponent: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [mode, setMode] = useState<'agent' | 'plan' | 'ask'>('agent');
  const [force, setForce] = useState(false);
  const [sandbox, setSandbox] = useState<'' | 'enabled' | 'disabled'>('');
  const [loading, setLoading] = useState(false);
  const clientRef = useRef(new ModelProxyClient());
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(scrollToBottom, [messages]);

  const sendMessage = async () => {
    if (!input.trim() || loading) return;

    const userMsg = input.trim();
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: userMsg }]);
    setLoading(true);

    try {
      const options: CursorMode = {
        mode,
        force: mode === 'agent' ? force : false,
      };
      if (sandbox) options.sandbox = sandbox;

      let assistantMsg: Message = { role: 'assistant', content: '', mode };
      setMessages((prev) => [...prev, assistantMsg]);

      for await (const chunk of clientRef.current.streamMessage(userMsg, options)) {
        if (chunk.meta?.model) {
          assistantMsg.model = chunk.meta.model;
        }
        
        const delta = chunk.choices?.[0]?.delta;
        if (delta?.content) {
          assistantMsg = {
            ...assistantMsg,
            content: assistantMsg.content + delta.content,
          };
          setMessages((prev) => [...prev.slice(0, -1), assistantMsg]);
        }
      }
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `❌ 错误: ${err.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div style={{ maxWidth: '800px', margin: '0 auto', padding: '20px' }}>
      <h2>Model Proxy Chat</h2>

      {/* 模式选择器 */}
      <div style={{ marginBottom: '16px', display: 'flex', gap: '8px', alignItems: 'center' }}>
        <label>模式:</label>
        {(['agent', 'plan', 'ask'] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            style={{
              padding: '6px 12px',
              background: mode === m ? '#3b82f6' : '#f3f4f6',
              color: mode === m ? 'white' : 'black',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
            }}
          >
            {m.toUpperCase()}
          </button>
        ))}
        <label>
          <input
            type="checkbox"
            checked={force}
            onChange={(e) => setForce(e.target.checked)}
            disabled={mode !== 'agent'}
          />
          Force
        </label>
        <select value={sandbox} onChange={(e) => setSandbox(e.target.value as any)}>
          <option value="">Sandbox: 默认</option>
          <option value="enabled">启用</option>
          <option value="disabled">禁用</option>
        </select>
      </div>

      {/* 消息列表 */}
      <div
        style={{
          height: '500px',
          overflowY: 'auto',
          border: '1px solid #e5e7eb',
          borderRadius: '8px',
          padding: '16px',
          marginBottom: '16px',
          background: '#f9fafb',
        }}
      >
        {messages.map((msg, idx) => (
          <div
            key={idx}
            style={{
              padding: '12px',
              margin: '8px 0',
              borderRadius: '8px',
              background: msg.role === 'user' ? '#e3f2fd' : '#ffffff',
              textAlign: msg.role === 'user' ? 'right' : 'left',
            }}
          >
            <div style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div>
            {(msg.model || (msg.mode && msg.mode !== 'agent')) && (
              <div style={{ display: 'flex', gap: '8px', marginTop: '8px', fontSize: '12px' }}>
                {msg.model && (
                  <span
                    style={{
                      padding: '2px 8px',
                      background: 'rgba(59,130,246,0.1)',
                      color: '#3b82f6',
                      borderRadius: '4px',
                    }}
                  >
                    🤖 {msg.model}
                  </span>
                )}
                {msg.mode && msg.mode !== 'agent' && (
                  <span
                    style={{
                      padding: '2px 8px',
                      background: 'rgba(139,92,246,0.1)',
                      color: '#8b5cf6',
                      borderRadius: '4px',
                      fontWeight: 'bold',
                    }}
                  >
                    {msg.mode === 'plan' ? '📐' : '💬'} {msg.mode.toUpperCase()}
                  </span>
                )}
              </div>
            )}
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* 输入区 */}
      <div style={{ display: 'flex', gap: '8px' }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
          rows={3}
          style={{
            flex: 1,
            padding: '12px',
            borderRadius: '8px',
            border: '1px solid #e5e7eb',
            resize: 'none',
          }}
        />
        <button
          onClick={sendMessage}
          disabled={loading || !input.trim()}
          style={{
            padding: '12px 24px',
            background: loading ? '#94a3b8' : '#3b82f6',
            color: 'white',
            border: 'none',
            borderRadius: '8px',
            cursor: loading ? 'not-allowed' : 'pointer',
          }}
        >
          {loading ? '思考中...' : '发送'}
        </button>
      </div>
    </div>
  );
};
```

---

## 3. Vue 3 示例

### Composition API 实现

```vue
<template>
  <div class="chat-container">
    <h2>Model Proxy Chat</h2>

    <!-- 模式选择器 -->
    <div class="mode-selector">
      <label>模式:</label>
      <button
        v-for="m in ['agent', 'plan', 'ask']"
        :key="m"
        :class="{ active: mode === m }"
        @click="mode = m"
      >
        {{ m.toUpperCase() }}
      </button>
      <label>
        <input type="checkbox" v-model="force" :disabled="mode !== 'agent'" />
        Force
      </label>
      <select v-model="sandbox">
        <option value="">Sandbox: 默认</option>
        <option value="enabled">启用</option>
        <option value="disabled">禁用</option>
      </select>
    </div>

    <!-- 消息列表 -->
    <div class="messages" ref="messagesContainer">
      <div
        v-for="(msg, idx) in messages"
        :key="idx"
        :class="['message', msg.role]"
      >
        <div class="content">{{ msg.content }}</div>
        <div v-if="msg.model || (msg.mode && msg.mode !== 'agent')" class="tags">
          <span v-if="msg.model" class="tag tag-model">🤖 {{ msg.model }}</span>
          <span v-if="msg.mode && msg.mode !== 'agent'" class="tag tag-mode">
            {{ msg.mode === 'plan' ? '📐' : '💬' }} {{ msg.mode.toUpperCase() }}
          </span>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="input-area">
      <textarea
        v-model="input"
        @keydown.enter.exact="sendMessage"
        placeholder="输入消息... (Enter 发送)"
        rows="3"
      ></textarea>
      <button @click="sendMessage" :disabled="loading || !input.trim()">
        {{ loading ? '思考中...' : '发送' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick } from 'vue';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  model?: string;
  mode?: string;
}

const API_BASE = 'http://localhost:8009';
const sessionId = ref<string | null>(null);
const messages = ref<Message[]>([]);
const input = ref('');
const mode = ref<'agent' | 'plan' | 'ask'>('agent');
const force = ref(false);
const sandbox = ref<'' | 'enabled' | 'disabled'>('');
const loading = ref(false);
const messagesContainer = ref<HTMLElement>();

const scrollToBottom = () => {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
    }
  });
};

const createSession = async () => {
  const res = await fetch(`${API_BASE}/api/chat/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: 'auto', title: 'Vue 示例' }),
  });
  const data = await res.json();
  sessionId.value = data.data.session_id;
};

const sendMessage = async () => {
  if (!input.value.trim() || loading.value) return;

  const userMsg = input.value.trim();
  input.value = '';
  messages.value.push({ role: 'user', content: userMsg });
  scrollToBottom();
  loading.value = true;

  try {
    if (!sessionId.value) await createSession();

    const requestBody: any = {
      model: 'auto',
      messages: [{ role: 'user', content: userMsg }],
      temperature: 0.7,
      mode: mode.value,
      force: mode.value === 'agent' ? force.value : false,
    };
    if (sandbox.value) requestBody.sandbox = sandbox.value;

    const assistantMsg: Message = { role: 'assistant', content: '', mode: mode.value };
    messages.value.push(assistantMsg);
    scrollToBottom();

    const res = await fetch(`${API_BASE}/api/chat/sessions/${sessionId.value}/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody),
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6).trim();
        if (payload === '[DONE]') continue;

        try {
          const chunk = JSON.parse(payload);
          if (chunk.meta?.model) {
            assistantMsg.model = chunk.meta.model;
          }
          const delta = chunk.choices?.[0]?.delta;
          if (delta?.content) {
            assistantMsg.content += delta.content;
            scrollToBottom();
          }
        } catch (e) {
          console.warn('解析 SSE 失败:', e);
        }
      }
    }
  } catch (err: any) {
    messages.value.push({
      role: 'assistant',
      content: `❌ 错误: ${err.message}`,
    });
  } finally {
    loading.value = false;
  }
};
</script>

<style scoped>
.chat-container {
  max-width: 800px;
  margin: 0 auto;
  padding: 20px;
}

.mode-selector {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  align-items: center;
}

button {
  padding: 6px 12px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  background: #f3f4f6;
}

button.active {
  background: #3b82f6;
  color: white;
}

.messages {
  height: 500px;
  overflow-y: auto;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
  background: #f9fafb;
}

.message {
  padding: 12px;
  margin: 8px 0;
  border-radius: 8px;
}

.message.user {
  background: #e3f2fd;
  text-align: right;
}

.message.assistant {
  background: white;
}

.content {
  white-space: pre-wrap;
}

.tags {
  display: flex;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
}

.tag {
  padding: 2px 8px;
  border-radius: 4px;
}

.tag-model {
  background: rgba(59, 130, 246, 0.1);
  color: #3b82f6;
}

.tag-mode {
  background: rgba(139, 92, 246, 0.1);
  color: #8b5cf6;
  font-weight: bold;
}

.input-area {
  display: flex;
  gap: 8px;
}

textarea {
  flex: 1;
  padding: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  resize: none;
}
</style>
```

---

## 4. 完整单页面应用（SPA）

完整的独立 HTML 文件，可直接在浏览器打开使用（需要 Model Proxy 后端运行）。

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Model Proxy - 完整示例</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
            background: #0f172a;
            color: #f1f5f9;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1000px; margin: 0 auto; }
        
        header {
            background: #1e293b;
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        h1 {
            font-size: 1.5rem;
            background: linear-gradient(135deg, #3b82f6, #06b6d4);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .mode-selector {
            background: #1e293b;
            padding: 16px;
            border-radius: 12px;
            margin-bottom: 20px;
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            align-items: center;
        }
        .mode-selector label { font-size: 0.9rem; color: #94a3b8; }
        .mode-btn {
            padding: 8px 16px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            background: #334155;
            color: #94a3b8;
            font-weight: 500;
            transition: all 0.2s;
        }
        .mode-btn:hover { background: #475569; color: #f1f5f9; }
        .mode-btn.active { background: #3b82f6; color: white; }
        
        .chat-container {
            background: #1e293b;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .messages {
            height: 500px;
            overflow-y: auto;
            margin-bottom: 16px;
            padding: 16px;
            background: #0f172a;
            border-radius: 8px;
        }
        .message {
            padding: 12px 16px;
            margin: 8px 0;
            border-radius: 12px;
            max-width: 80%;
            word-wrap: break-word;
        }
        .message.user {
            background: linear-gradient(135deg, #3b82f6, #06b6d4);
            margin-left: auto;
            color: white;
        }
        .message.assistant {
            background: #334155;
            margin-right: auto;
        }
        .message pre {
            background: #0f172a;
            padding: 12px;
            border-radius: 8px;
            overflow-x: auto;
            margin: 8px 0;
        }
        .tags {
            display: flex;
            gap: 8px;
            margin-top: 8px;
        }
        .tag {
            font-size: 0.7rem;
            padding: 2px 8px;
            border-radius: 4px;
            font-weight: 500;
        }
        .tag-model {
            background: rgba(59, 130, 246, 0.2);
            color: #60a5fa;
        }
        .tag-mode {
            background: rgba(139, 92, 246, 0.2);
            color: #a78bfa;
            font-weight: bold;
        }
        
        .input-area {
            display: flex;
            gap: 12px;
        }
        #input {
            flex: 1;
            padding: 12px;
            border: 1px solid #334155;
            background: #0f172a;
            color: #f1f5f9;
            border-radius: 8px;
            resize: none;
            font-family: inherit;
        }
        #send-btn {
            padding: 12px 24px;
            background: #3b82f6;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.2s;
        }
        #send-btn:hover { background: #2563eb; }
        #send-btn:disabled {
            background: #475569;
            cursor: not-allowed;
        }
        
        select, input[type="checkbox"] {
            background: #334155;
            color: #f1f5f9;
            border: 1px solid #475569;
            padding: 6px 10px;
            border-radius: 6px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🤖 Model Proxy Chat</h1>
            <span id="status" style="font-size:0.85rem;color:#94a3b8">未连接</span>
        </header>

        <div class="mode-selector">
            <label>Cursor 模式:</label>
            <button class="mode-btn active" data-mode="agent" onclick="selectMode('agent')">Agent</button>
            <button class="mode-btn" data-mode="plan" onclick="selectMode('plan')">📐 Plan</button>
            <button class="mode-btn" data-mode="ask" onclick="selectMode('ask')">💬 Ask</button>
            
            <label style="display:flex;align-items:center;gap:6px">
                <input type="checkbox" id="force">
                Force
            </label>
            
            <select id="sandbox">
                <option value="">Sandbox: 默认</option>
                <option value="enabled">启用</option>
                <option value="disabled">禁用</option>
            </select>
        </div>

        <div class="chat-container">
            <div class="messages" id="messages"></div>
            <div class="input-area">
                <textarea id="input" rows="3" placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"></textarea>
                <button id="send-btn" onclick="sendMessage()">发送</button>
            </div>
        </div>
    </div>

    <script>
        const API_BASE = 'http://localhost:8009';
        let currentMode = 'agent';
        let sessionId = null;
        let loading = false;

        // 初始化
        document.getElementById('input').addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        checkConnection();

        function selectMode(mode) {
            currentMode = mode;
            document.querySelectorAll('.mode-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.mode === mode);
            });
            document.getElementById('force').disabled = mode !== 'agent';
        }

        async function checkConnection() {
            try {
                const res = await fetch(`${API_BASE}/health`);
                if (res.ok) {
                    document.getElementById('status').textContent = '✅ 已连接';
                    document.getElementById('status').style.color = '#10b981';
                }
            } catch {
                document.getElementById('status').textContent = '❌ 连接失败';
                document.getElementById('status').style.color = '#ef4444';
            }
        }

        async function createSession() {
            const res = await fetch(`${API_BASE}/api/chat/sessions`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: 'auto', title: '完整示例会话' })
            });
            const data = await res.json();
            sessionId = data.data.session_id;
        }

        async function sendMessage() {
            if (loading) return;
            
            const input = document.getElementById('input');
            const userMsg = input.value.trim();
            if (!userMsg) return;

            input.value = '';
            addMessage('user', userMsg);
            
            const btn = document.getElementById('send-btn');
            btn.disabled = true;
            btn.textContent = '思考中...';
            loading = true;

            try {
                if (!sessionId) await createSession();

                const force = document.getElementById('force').checked;
                const sandbox = document.getElementById('sandbox').value;
                const requestBody = {
                    model: 'auto',
                    messages: [{ role: 'user', content: userMsg }],
                    temperature: 0.7,
                    mode: currentMode,
                    force: currentMode === 'agent' ? force : false,
                };
                if (sandbox) requestBody.sandbox = sandbox;

                const assistantDiv = addMessage('assistant', '');
                let fullText = '';
                let usedModel = '';

                const res = await fetch(`${API_BASE}/api/chat/sessions/${sessionId}/stream`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody)
                });

                if (!res.ok) throw new Error(`HTTP ${res.status}`);

                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';

                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;
                        const payload = line.slice(6).trim();
                        if (payload === '[DONE]') continue;

                        try {
                            const chunk = JSON.parse(payload);
                            if (chunk.meta?.model) usedModel = chunk.meta.model;
                            const delta = chunk.choices?.[0]?.delta;
                            if (delta?.content) {
                                fullText += delta.content;
                                assistantDiv.querySelector('.content').textContent = fullText;
                                document.getElementById('messages').scrollTop = 999999;
                            }
                        } catch (e) {}
                    }
                }

                // 添加标签
                if (usedModel || currentMode !== 'agent') {
                    const tags = document.createElement('div');
                    tags.className = 'tags';
                    
                    if (usedModel) {
                        const modelTag = document.createElement('span');
                        modelTag.className = 'tag tag-model';
                        modelTag.textContent = `🤖 ${usedModel}`;
                        tags.appendChild(modelTag);
                    }
                    
                    if (currentMode !== 'agent') {
                        const modeTag = document.createElement('span');
                        modeTag.className = 'tag tag-mode';
                        const icons = { plan: '📐', ask: '💬' };
                        modeTag.textContent = `${icons[currentMode]} ${currentMode.toUpperCase()}`;
                        tags.appendChild(modeTag);
                    }
                    
                    assistantDiv.appendChild(tags);
                }

            } catch (err) {
                addMessage('assistant', `❌ 错误: ${err.message}`);
            } finally {
                btn.disabled = false;
                btn.textContent = '发送';
                loading = false;
            }
        }

        function addMessage(role, content) {
            const div = document.createElement('div');
            div.className = `message ${role}`;
            
            const contentDiv = document.createElement('div');
            contentDiv.className = 'content';
            contentDiv.textContent = content;
            div.appendChild(contentDiv);
            
            document.getElementById('messages').appendChild(div);
            document.getElementById('messages').scrollTop = 999999;
            
            return div;
        }
    </script>
</body>
</html>
```

---

## 使用说明

### 环境要求

1. Model Proxy 后端运行在 `http://localhost:8009`（可修改 `API_BASE` 变量）
2. 已启用至少一个 Cursor 相关模型（如 `composer-2.5`）
3. 浏览器需支持 ES6+ 和 Fetch API（现代浏览器均支持）

### 快速开始

**纯 HTML 示例**

1. 保存上述"完整单页面应用"为 `chat.html`
2. 双击打开即可使用

**React 示例**

```bash
# 1. 创建 React 项目
npx create-react-app model-proxy-chat --template typescript
cd model-proxy-chat

# 2. 复制示例代码
# - api.ts -> src/api.ts
# - ChatComponent.tsx -> src/ChatComponent.tsx

# 3. 修改 App.tsx
# import { ChatComponent } from './ChatComponent';
# function App() { return <ChatComponent />; }

# 4. 启动
npm start
```

**Vue 3 示例**

```bash
# 1. 创建 Vue 项目
npm init vue@latest model-proxy-chat
cd model-proxy-chat
npm install

# 2. 复制示例代码到 src/components/Chat.vue

# 3. 修改 App.vue
# <template><Chat /></template>
# <script setup><import Chat from './components/Chat.vue'></script>

# 4. 启动
npm run dev
```

### 跨域问题

如果遇到 CORS 错误，有两种解决方案：

**方案 1：修改 Model Proxy 配置**

在 `conf/base.yaml` 中启用 CORS：

```yaml
cors:
  enabled: true
  allow_origins:
    - "http://localhost:3000"  # React 默认端口
    - "http://localhost:5173"  # Vite 默认端口
  allow_methods:
    - GET
    - POST
  allow_headers:
    - "*"
```

**方案 2：使用代理（开发环境）**

React (package.json):
```json
{
  "proxy": "http://localhost:8009"
}
```

Vite (vite.config.ts):
```typescript
export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://localhost:8009'
    }
  }
});
```

---

## 常见问题

### Q: 如何判断是否应该显示 Mode 选择器？

A: 显示逻辑：
- **Cursor 模型**（provider === 'cursor'）：始终显示
- **`auto` 自动模式**：也显示（auto 可能路由到 Cursor）
- **其他厂商模型**：隐藏

```javascript
function shouldShowCursorModeSelector(model) {
    if (model === 'auto') return true;  // auto 也支持
    
    // 查询模型的 provider
    const models = await fetch(`${API_BASE}/api/models`).then(r => r.json());
    const modelInfo = models.data.find(m => m.id === model);
    return modelInfo?.provider === 'cursor';
}
```

**为什么 auto 也显示？**
- auto 可能会路由到 Cursor 模型，用户可以提前设置 mode
- 如果最终路由到非 Cursor 模型，这些参数会被自动忽略，不会出错

### Q: Force 和 Sandbox 参数什么时候生效?

A:
- `force`: 仅 `agent` 模式下有效，用于跳过文件修改确认
- `sandbox`: 所有模式均可用，控制执行环境隔离

### Q: 如何处理 Streaming 中断?

A: 在流式请求中添加 AbortController：

```javascript
const controller = new AbortController();
const res = await fetch(url, {
  signal: controller.signal,
  // ...
});

// 用户点击停止时
stopButton.onclick = () => controller.abort();
```

### Q: 如何持久化会话历史?

A: 使用 localStorage 保存 sessionId：

```javascript
// 保存
localStorage.setItem('model-proxy-session', sessionId);

// 加载
const savedSessionId = localStorage.getItem('model-proxy-session');
if (savedSessionId) {
  // 验证会话是否有效
  const res = await fetch(`${API_BASE}/api/chat/sessions/${savedSessionId}`);
  if (res.ok) sessionId = savedSessionId;
}
```

---

## 参考文档

- [Model Proxy API 参考](./api-reference.md)
- [Cursor Plan Mode 指南](./cursor-plan-mode.md)
- [UI 管理面板](./ui.md)

## 贡献

欢迎提交 Issue 或 PR 补充更多示例：
- Angular、Svelte 等框架
- WebSocket 集成
- Markdown 实时渲染
- 代码高亮与复制
