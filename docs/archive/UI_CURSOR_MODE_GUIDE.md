# UI 添加 Cursor Mode 选择器 - 实现指南

## 需要修改的部分

### 1. 添加 Mode 选择器 UI(在聊天输入框上方)

在 `src/api/static/ui.html` 的第 537 行左右,在输入框之前添加:

```html
<!-- 在第 537 行左右,<div style="flex-shrink:0;border-top..."> 之前添加 -->
<div style="padding:12px;border-top:1px solid var(--border);display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:var(--bg-tertiary)">
    <label style="font-size:0.8rem;font-weight:500;color:var(--text-secondary)">Cursor 模式:</label>
    <div style="display:flex;gap:6px" id="cursor-mode-group">
        <button class="btn btn-sm cursor-mode-btn" data-mode="agent" onclick="selectCursorMode('agent')" style="background:var(--accent-blue);color:#fff">
            Agent (编码)
        </button>
        <button class="btn btn-sm btn-ghost cursor-mode-btn" data-mode="plan" onclick="selectCursorMode('plan')">
            Plan (规划)
        </button>
        <button class="btn btn-sm btn-ghost cursor-mode-btn" data-mode="ask" onclick="selectCursorMode('ask')">
            Ask (问答)
        </button>
    </div>
    <div style="display:flex;gap:8px;align-items:center;margin-left:auto">
        <label style="font-size:0.75rem;display:flex;align-items:center;gap:4px;cursor:pointer" title="强制执行(--force),plan/ask模式下无效">
            <input type="checkbox" id="cursor-force"> Force
        </label>
        <label style="font-size:0.75rem;color:var(--text-muted)">Sandbox:</label>
        <select id="cursor-sandbox" style="font-size:0.75rem;padding:2px 6px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-xs)">
            <option value="">默认</option>
            <option value="enabled">启用</option>
            <option value="disabled">禁用</option>
        </select>
    </div>
</div>
```

### 2. 添加 CSS 样式

在 `<style>` 标签内(第 180 行左右)添加:

```css
.cursor-mode-btn {
    transition: all 0.15s;
}
.cursor-mode-btn[data-mode="agent"]:not(.active) {
    background: rgba(59,130,246,0.1);
    color: var(--accent-blue);
}
.cursor-mode-btn[data-mode="plan"]:not(.active) {
    background: rgba(139,92,246,0.1);
    color: var(--accent-purple);
}
.cursor-mode-btn[data-mode="ask"]:not(.active) {
    background: rgba(16,185,129,0.1);
    color: var(--accent-green);
}
.cursor-mode-btn.active {
    background: var(--accent-blue);
    color: #fff;
    box-shadow: 0 0 0 2px rgba(59,130,246,0.2);
}
.cursor-mode-btn[data-mode="plan"].active {
    background: var(--accent-purple);
}
.cursor-mode-btn[data-mode="ask"].active {
    background: var(--accent-green);
}
```

### 3. 添加 JavaScript 函数

在 `<script>` 标签内(第 1500 行之后)添加:

```javascript
// Cursor Mode 管理
let currentCursorMode = 'agent';

function selectCursorMode(mode) {
    currentCursorMode = mode;
    document.querySelectorAll('.cursor-mode-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    document.querySelector(`.cursor-mode-btn[data-mode="${mode}"]`).classList.add('active');
    
    // plan/ask 模式下禁用 force
    const forceCheckbox = document.getElementById('cursor-force');
    if (mode === 'plan' || mode === 'ask') {
        forceCheckbox.checked = false;
        forceCheckbox.disabled = true;
    } else {
        forceCheckbox.disabled = false;
    }
    
    // 更新输入框提示
    const input = document.getElementById('chat-input');
    const hints = {
        'agent': '输入任务,AI 将执行完整的代码修改...',
        'plan': '输入需求,AI 将只分析不修改代码...',
        'ask': '提问关于代码的问题...'
    };
    input.placeholder = hints[mode] || hints['agent'];
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    selectCursorMode('agent');
});
```

### 4. 修改 sendChat 函数

在 `sendChat` 函数中(第 2230-2234 行左右),修改请求体:

```javascript
// 原代码:
body: JSON.stringify({
    model: model,
    messages: [{role: 'user', content: msg}],
    temperature: 0.7,
}),

// 修改为:
body: JSON.stringify({
    model: model,
    messages: [{role: 'user', content: msg}],
    temperature: 0.7,
    mode: currentCursorMode,
    force: document.getElementById('cursor-force').checked,
    sandbox: document.getElementById('cursor-sandbox').value || undefined,
}),
```

## 快速应用脚本

可以使用以下 Python 脚本自动应用这些修改:

```bash
python scripts/patch_ui_cursor_mode.py
```

## 手动修改步骤

1. 备份原文件: `cp src/api/static/ui.html src/api/static/ui.html.bak`
2. 按照上述说明在对应位置添加代码
3. 重启 model-proxy 服务
4. 访问 http://127.0.0.1:8000/ui 验证

## 预览效果

Mode 选择器将显示在聊天输入框上方,包含:
- 3个模式按钮: Agent (蓝色) / Plan (紫色) / Ask (绿色)
- Force 复选框(plan/ask 模式下禁用)
- Sandbox 下拉菜单

选择不同模式时:
- 按钮高亮当前模式
- 输入框提示文字自动更新
- plan/ask 模式下自动禁用 force

## 测试验证

1. 启动服务: `python main.py`
2. 访问 http://127.0.0.1:8000/ui
3. 切换到 Chat 标签
4. 测试3种模式:
   - Agent: 输入"列出项目文件",应能执行命令
   - Plan: 输入"分析项目架构",应返回只读分析
   - Ask: 输入"这个项目做什么",应返回问答

5. 检查网络请求:
   - 打开浏览器开发者工具 → Network
   - 发送消息
   - 查看请求 body 是否包含 mode、force、sandbox 字段
