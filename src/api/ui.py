# Created by model-proxy on 2026/05/11
# Copyright © 2026

"""内嵌 UI 面板 - 单文件 HTML，无需额外前端构建"""

UI_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Model Proxy - 管理面板</title>
    <style>
        :root {
            --bg-primary: #0f172a;
            --bg-secondary: #1e293b;
            --bg-tertiary: #334155;
            --text-primary: #f1f5f9;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --accent-blue: #3b82f6;
            --accent-cyan: #06b6d4;
            --accent-green: #10b981;
            --accent-yellow: #f59e0b;
            --accent-red: #ef4444;
            --accent-purple: #8b5cf6;
            --border: #1e293b;
            --border-hover: #475569;
            --shadow: 0 1px 3px rgba(0,0,0,0.2), 0 4px 12px rgba(0,0,0,0.15);
            --shadow-lg: 0 8px 30px rgba(0,0,0,0.3);
            --radius: 16px;
            --radius-sm: 10px;
            --radius-xs: 6px;
            --transition: all 0.2s ease;
            --gap: 16px;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            line-height: 1.6;
            font-size: 14px;
        }
        .container { max-width: 1320px; margin: 0 auto; padding: 20px 28px; }

        /* 头部 */
        header {
            display: flex; align-items: center; justify-content: space-between;
            margin-bottom: 20px; padding: 16px 24px;
            background: var(--bg-secondary); border-radius: var(--radius);
            border: 1px solid var(--border);
        }
        .header-left { display: flex; align-items: center; gap: 12px; }
        .header-left h1 {
            font-size: 1.35rem; font-weight: 700; letter-spacing: -0.02em;
            background: linear-gradient(135deg, var(--accent-blue), var(--accent-cyan));
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .header-left .version {
            font-size: 0.7rem; color: var(--text-muted);
            background: var(--bg-tertiary); padding: 2px 8px; border-radius: 4px;
        }
        .status-badge {
            display: flex; align-items: center; gap: 6px;
            padding: 5px 12px; border-radius: 20px;
            background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.2);
        }
        .status-dot {
            width: 7px; height: 7px; border-radius: 50%;
            background: var(--accent-green); animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }
        .status-text { font-size: 0.75rem; color: var(--accent-green); font-weight: 600; }

        /* 选项卡 */
        .tabs {
            display: flex; gap: 2px; margin-bottom: 20px;
            background: var(--bg-secondary); padding: 4px;
            border-radius: var(--radius-sm); border: 1px solid var(--border);
        }
        .tab {
            padding: 9px 18px; border-radius: 8px; cursor: pointer;
            background: transparent; color: var(--text-muted); border: none;
            font-size: 0.85rem; font-weight: 500; transition: var(--transition);
            position: relative; white-space: nowrap;
        }
        .tab.active {
            background: var(--accent-blue); color: #fff;
            box-shadow: 0 1px 6px rgba(59, 130, 246, 0.3);
        }
        .tab:not(.active):hover { background: var(--bg-tertiary); color: var(--text-primary); }
        .tab .tab-count {
            position: absolute; top: 2px; right: 2px;
            background: var(--accent-red); color: #fff;
            font-size: 0.6rem; padding: 1px 4px; border-radius: 6px;
            min-width: 14px; text-align: center; display: none;
        }

        /* 面板 */
        .panel { display: none; animation: fadeIn 0.25s ease; }
        .panel.active { display: block; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }

        /* 卡片 */
        .card {
            background: var(--bg-secondary); border-radius: var(--radius);
            padding: 20px 24px; margin-bottom: 16px;
            border: 1px solid var(--border);
        }
        .card-header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid rgba(51,65,85,0.5);
        }
        .card-header h3 { font-size: 1.05rem; color: var(--text-primary); font-weight: 600; }

        /* 统计网格 */
        .stats-grid {
            display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: var(--gap);
        }
        .stat-card {
            background: var(--bg-primary); border-radius: var(--radius-sm);
            padding: 16px 18px; border: 1px solid var(--border);
            transition: var(--transition); position: relative; overflow: hidden;
            display: flex; flex-direction: column;
        }
        .stat-card:hover { border-color: var(--border-hover); transform: translateY(-1px); box-shadow: var(--shadow); }
        .stat-card::before {
            content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
            background: linear-gradient(90deg, var(--accent-blue), var(--accent-cyan));
            opacity: 0; transition: opacity 0.3s;
        }
        .stat-card:hover::before { opacity: 1; }

        /* 拖拽相关样式 */
        .stat-card.draggable { cursor: grab; }
        .stat-card.draggable:active { cursor: grabbing; }
        .stat-card.dragging { opacity: 0.35; transform: scale(0.97); }
        .stat-card.drag-over {
            border-color: var(--accent-cyan);
            box-shadow: 0 0 0 2px rgba(6, 182, 212, 0.25);
        }
        .guide-btn {
            width: 22px; height: 22px; border-radius: 50%;
            border: 1px solid rgba(51,65,85,0.6); background: transparent;
            color: var(--text-muted); font-size: 0.7rem; cursor: pointer;
            display: inline-flex; align-items: center; justify-content: center;
            transition: var(--transition); flex-shrink: 0;
        }
        .guide-btn:hover { border-color: var(--accent-blue); color: var(--accent-blue); }

        /* 模型拖拽排序 */
        .model-item { transition: var(--transition); }
        .model-item.dragging { opacity: 0.35; }
        .model-item.drag-over { border-color: var(--accent-cyan) !important; box-shadow: 0 0 0 2px rgba(6, 182, 212, 0.25); }
        .stat-card .model-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .stat-card .model-name { font-weight: 600; font-size: 0.9rem; color: var(--accent-cyan); }
        .stat-card .provider-tag {
            font-size: 0.65rem; background: rgba(51,65,85,0.5); padding: 2px 8px;
            border-radius: 10px; color: var(--text-secondary); font-weight: 500;
        }
        .stat-card .availability {
            display: inline-flex; align-items: center; gap: 4px;
            font-size: 0.7rem; padding: 2px 8px; border-radius: 10px; margin-bottom: 10px;
        }
        .stat-card .availability.available { background: rgba(16,185,129,0.1); color: var(--accent-green); }
        .stat-card .availability.unavailable { background: rgba(239,68,68,0.1); color: var(--accent-red); }
        .progress-container { margin: 10px 0; }
        .progress-label { display: flex; justify-content: space-between; font-size: 0.7rem; color: var(--text-muted); margin-bottom: 4px; }
        .progress-bar {
            height: 6px; background: var(--bg-tertiary); border-radius: 3px;
            overflow: hidden; position: relative;
        }
        .progress-bar .fill {
            height: 100%; border-radius: 3px; transition: width 0.5s ease;
            position: relative;
        }
        .fill.green { background: linear-gradient(90deg, #059669, var(--accent-green)); }
        .fill.yellow { background: linear-gradient(90deg, #d97706, var(--accent-yellow)); }
        .fill.red { background: linear-gradient(90deg, #dc2626, var(--accent-red)); }
        .stat-detail {
            display: grid; grid-template-columns: 1fr 1fr; gap: 6px;
            margin-top: 10px; font-size: 0.75rem;
        }
        .stat-item { display: flex; flex-direction: column; gap: 1px; }
        .stat-item .label { color: var(--text-muted); font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.03em; }
        .stat-item .value { color: var(--text-primary); font-weight: 600; font-size: 0.85rem; }

        /* 表格 */
        .table-wrapper { overflow-x: auto; }
        table { width: 100%; border-collapse: separate; border-spacing: 0; }
        th {
            padding: 12px 16px; text-align: left; font-size: 0.75rem;
            text-transform: uppercase; letter-spacing: 0.05em;
            color: var(--text-muted); background: var(--bg-primary);
            border-bottom: 1px solid var(--border);
        }
        td {
            padding: 14px 16px; border-bottom: 1px solid var(--border);
            font-size: 0.9rem; vertical-align: middle;
        }
        tr:hover td { background: rgba(59, 130, 246, 0.05); }
        .model-cell { display: flex; align-items: center; gap: 8px; }
        .model-icon {
            width: 30px; height: 30px; border-radius: var(--radius-xs);
            display: flex; align-items: center; justify-content: center;
            font-size: 0.7rem; font-weight: 700; color: #fff; flex-shrink: 0;
        }
        .icon-google { background: linear-gradient(135deg, #4285f4, #34a853); }
        .icon-groq { background: linear-gradient(135deg, #f97316, #ef4444); }
        .icon-github { background: linear-gradient(135deg, #6e40c9, #8b5cf6); }
        .icon-cursor { background: linear-gradient(135deg, #06b6d4, #3b82f6); }
        .priority-badge {
            width: 26px; height: 26px; border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            font-size: 0.75rem; font-weight: 700; flex-shrink: 0;
            background: var(--bg-tertiary); color: var(--text-secondary);
        }

        /* 按钮 */
        .btn {
            padding: 7px 14px; border-radius: var(--radius-xs); border: none;
            cursor: pointer; font-size: 0.8rem; font-weight: 500;
            transition: var(--transition); display: inline-flex; align-items: center; gap: 5px;
            white-space: nowrap;
        }
        .btn-primary { background: var(--accent-blue); color: #fff; }
        .btn-primary:hover { background: #2563eb; box-shadow: 0 2px 6px rgba(59,130,246,0.35); }
        .btn-secondary { background: var(--bg-tertiary); color: var(--text-primary); }
        .btn-secondary:hover { background: #475569; }
        .btn-danger { background: var(--accent-red); color: #fff; }
        .btn-success { background: var(--accent-green); color: #fff; }
        .btn-ghost { background: transparent; border: 1px solid rgba(51,65,85,0.6); color: var(--text-secondary); }
        .btn-ghost:hover { border-color: var(--accent-blue); color: var(--accent-blue); }
        .btn-sm { padding: 4px 10px; font-size: 0.75rem; }
        .btn-icon { width: 30px; height: 30px; padding: 0; justify-content: center; }

        /* 表单 */
        .form-group { margin-bottom: 14px; }
        .form-group label {
            display: block; margin-bottom: 5px; font-size: 0.75rem;
            color: var(--text-secondary); font-weight: 500;
        }
        input, select {
            width: 100%; padding: 8px 12px; font-size: 0.85rem;
            background: var(--bg-primary); border: 1px solid var(--border);
            border-radius: var(--radius-xs); color: var(--text-primary);
            transition: var(--transition);
        }
        input:focus, select:focus {
            outline: none; border-color: var(--accent-blue);
            box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.12);
        }
        input::placeholder { color: var(--text-muted); }
        .input-group { display: flex; gap: 6px; }
        .input-group input { flex: 1; }

        /* 开关 */
        .toggle { position: relative; width: 40px; height: 22px; display: inline-block; flex-shrink: 0; }
        .toggle input { opacity: 0; width: 0; height: 0; }
        .toggle .slider {
            position: absolute; cursor: pointer; inset: 0;
            background: var(--bg-tertiary); border-radius: 11px;
            transition: 0.25s;
        }
        .toggle .slider::before {
            content: ""; position: absolute; height: 16px; width: 16px;
            left: 3px; bottom: 3px; background: #fff; border-radius: 50%;
            transition: 0.25s; box-shadow: 0 1px 2px rgba(0,0,0,0.2);
        }
        .toggle input:checked + .slider { background: var(--accent-green); }
        .toggle input:checked + .slider::before { transform: translateX(18px); }

        /* 发现卡片 */
        .discovery-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); gap: 16px; }
        .discovery-card {
            background: var(--bg-primary); border-radius: var(--radius-sm);
            padding: 20px; border: 1px solid var(--border);
            transition: var(--transition);
        }
        .discovery-card:hover { border-color: var(--accent-purple); transform: translateY(-2px); }
        .discovery-card .dc-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px; }
        .discovery-card h4 { color: var(--accent-cyan); font-size: 1rem; }
        .discovery-card .dc-desc { font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 12px; line-height: 1.5; }
        .discovery-card a { color: var(--accent-blue); text-decoration: none; font-size: 0.85rem; }
        .discovery-card a:hover { text-decoration: underline; }
        .discovery-card .guide {
            margin-top: 10px; padding: 10px 12px; font-size: 0.8rem;
            background: var(--bg-secondary); border-radius: 6px;
            color: var(--text-secondary); border-left: 3px solid var(--accent-blue);
        }
        .model-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
        .model-tag {
            background: var(--bg-tertiary); padding: 3px 10px; border-radius: 12px;
            font-size: 0.75rem; color: var(--text-primary); font-weight: 500;
        }
        .new-user-badge {
            font-size: 0.7rem; background: rgba(245,158,11,0.2); color: var(--accent-yellow);
            padding: 2px 8px; border-radius: 4px; font-weight: 600;
        }

        /* 配置面板 */
        .config-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: var(--gap); }
        .config-item {
            background: var(--bg-primary); border-radius: var(--radius-sm);
            padding: 16px 18px; border: 1px solid var(--border);
        }
        .config-item .ci-header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 10px;
        }
        .config-item .ci-name { font-weight: 600; font-size: 0.9rem; }
        .config-item .ci-status {
            font-size: 0.7rem; padding: 2px 8px; border-radius: 10px;
        }
        .ci-status.configured { background: rgba(16,185,129,0.1); color: var(--accent-green); }
        .ci-status.not-configured { background: rgba(239,68,68,0.1); color: var(--accent-red); }

        /* 模态框 */
        .modal-overlay {
            display: none; position: fixed; inset: 0; z-index: 1000;
            background: rgba(0,0,0,0.5); backdrop-filter: blur(6px);
            align-items: center; justify-content: center;
        }
        .modal-overlay.show { display: flex; }
        .modal {
            background: var(--bg-secondary); border-radius: var(--radius);
            padding: 24px; width: 92%; max-width: 520px;
            border: 1px solid var(--border); box-shadow: var(--shadow-lg);
            animation: modalIn 0.2s ease;
        }
        @keyframes modalIn { from { opacity: 0; transform: scale(0.96) translateY(8px); } to { opacity: 1; transform: scale(1) translateY(0); } }
        .modal h3 { margin-bottom: 16px; font-size: 1.05rem; }
        .modal-footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px; }

        /* Toast 通知 */
        .toast-container { position: fixed; top: 20px; right: 20px; z-index: 2000; max-width: 380px; }
        .toast {
            background: var(--bg-secondary); border: 1px solid var(--border);
            border-radius: var(--radius-xs); padding: 10px 16px;
            margin-bottom: 6px; box-shadow: var(--shadow);
            animation: slideIn 0.25s ease; font-size: 0.8rem;
            display: flex; align-items: center; gap: 8px;
        }
        @keyframes slideIn { from { opacity: 0; transform: translateX(16px); } to { opacity: 1; transform: translateX(0); } }
        .toast.success { border-left: 3px solid var(--accent-green); }
        .toast.error { border-left: 3px solid var(--accent-red); }
        .toast.info { border-left: 3px solid var(--accent-blue); }

        /* 空状态 */
        .empty-state { text-align: center; padding: 40px 20px; color: var(--text-muted); }
        .empty-state h4 { color: var(--text-secondary); margin-bottom: 6px; }

        /* 分割线 */
        .divider { border: none; border-top: 1px solid rgba(51,65,85,0.4); margin: 16px 0; }

        /* 消息气泡代码块 */
        .msg-bubble pre { margin: 8px 0; }
        .msg-bubble code { font-family: "SF Mono", "Cascadia Code", "Fira Code", monospace; }

        /* 响应式 */
        @media (max-width: 768px) {
            .container { padding: 12px; }
            .tabs { overflow-x: auto; flex-wrap: nowrap; -webkit-overflow-scrolling: touch; }
            .stats-grid, .discovery-grid, .config-grid { grid-template-columns: 1fr; }
            header { flex-direction: column; gap: 10px; align-items: flex-start; }
            .modal { width: 96%; padding: 18px; }
            .chat-sidebar { display: none !important; }
            .chat-sidebar.show { display: flex !important; position: fixed; left: 0; top: 0; bottom: 0; z-index: 999; width: 260px; border-radius: 0; }
            .chat-sidebar-toggle { display: inline-flex !important; }
            .chat-sidebar-overlay { display: none; position: fixed; inset: 0; z-index: 998; background: rgba(0,0,0,0.5); }
            .chat-sidebar-overlay.show { display: block; }
        }
        @media (min-width: 769px) {
            .chat-sidebar-toggle { display: none !important; }
            .chat-sidebar-overlay { display: none !important; }
        }
    </style>
</head>
<body>
    <div class="toast-container" id="toast-container"></div>

    <div class="container">
        <header>
            <div class="header-left">
                <h1>Model Proxy</h1>
                <span class="version">v0.1.0</span>
            </div>
            <div class="status-badge">
                <span class="status-dot"></span>
                <span class="status-text">服务运行中</span>
            </div>
        </header>

        <div class="tabs">
            <button class="tab active" onclick="switchTab('usage', this)">
                <span>📊</span> 使用量监控
            </button>
            <button class="tab" onclick="switchTab('models', this)">
                <span>🤖</span> 模型管理
            </button>
            <button class="tab" onclick="switchTab('routing', this)">
                <span>🧭</span> 路由决策
            </button>
            <button class="tab" onclick="switchTab('chat', this)">
                <span>💬</span> 问答聊天
            </button>
            <button class="tab" onclick="switchTab('config', this)">
                <span>🔑</span> API Key 配置
            </button>
        </div>

        <!-- 使用量监控 -->
        <div id="panel-usage" class="panel active">
            <div class="card">
                <div class="card-header">
                    <h3>实时使用量统计</h3>
                    <div style="display:flex;gap:8px">
                        <button class="btn btn-ghost btn-sm" onclick="loadUsage()">🔄 刷新</button>
                        <span style="font-size:0.75rem;color:var(--text-muted);align-self:center" id="last-refresh">--</span>
                    </div>
                </div>
                <div id="usage-grid" class="stats-grid">
                    <div class="empty-state"><h4>加载中...</h4></div>
                </div>
            </div>
        </div>

        <!-- 路由决策 -->
        <div id="panel-routing" class="panel">
            <div class="card">
                <div class="card-header">
                    <h3>路由决策日志</h3>
                    <div style="display:flex;gap:8px">
                        <button class="btn btn-ghost btn-sm" onclick="loadRoutingLog()">🔄 刷新</button>
                    </div>
                </div>
                <div id="routing-log" style="max-height:calc(100vh - 280px);overflow-y:auto">
                    <div class="empty-state"><h4>点击刷新查看路由决策</h4></div>
                </div>
            </div>
        </div>

        <!-- 问答聊天 -->
        <div id="panel-chat" class="panel">
            <div class="chat-sidebar-overlay" id="chat-sidebar-overlay" onclick="toggleChatSidebar()"></div>
            <div style="display:flex;gap:16px;height:calc(100vh - 220px);min-height:400px">
                <!-- 会话列表侧栏 -->
                <div class="chat-sidebar" id="chat-sidebar" style="width:220px;flex-shrink:0;display:flex;flex-direction:column;background:var(--bg-secondary);border-radius:var(--radius);border:1px solid var(--border);overflow:hidden">
                    <div style="padding:12px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between">
                        <span style="font-size:0.85rem;font-weight:600">会话列表</span>
                        <button class="btn btn-primary btn-sm" onclick="createNewSession()" style="padding:3px 8px;font-size:0.75rem">+ 新建</button>
                    </div>
                    <div id="session-list" style="flex:1;overflow-y:auto;padding:6px"></div>
                </div>
                <!-- 聊天主区域 -->
                <div class="card" style="flex:1;display:flex;flex-direction:column;min-width:0">
                    <div class="card-header" style="flex-shrink:0">
                        <div style="display:flex;align-items:center;gap:8px">
                            <button class="btn btn-ghost btn-sm chat-sidebar-toggle" onclick="toggleChatSidebar()" style="padding:4px 8px">☰</button>
                            <h3 id="chat-title" style="cursor:pointer" onclick="renameCurrentSession()" title="点击重命名">新对话</h3>
                            <span id="chat-context-info" style="font-size:0.7rem;color:var(--text-muted);padding:2px 8px;background:var(--bg-tertiary);border-radius:10px"></span>
                        </div>
                        <div style="display:flex;gap:8px;align-items:center">
                            <select id="chat-model" style="width:auto;min-width:180px">
                                <option value="auto">🤖 自动选择模型</option>
                            </select>
                            <button class="btn btn-ghost btn-sm" onclick="deleteCurrentSession()" title="删除当前会话">🗑</button>
                        </div>
                    </div>
                    <div id="chat-messages" style="flex:1;overflow-y:auto;padding:16px 0;display:flex;flex-direction:column;gap:12px">
                        <div style="text-align:center;color:var(--text-muted);padding:40px 0">
                            <p>点击左侧「+ 新建」创建会话，开始对话</p>
                            <p style="font-size:0.8rem;margin-top:8px">对话历史自动保存 · 流式输出 · Markdown 渲染</p>
                        </div>
                    </div>
                    <div style="flex-shrink:0;border-top:1px solid var(--border);padding-top:16px;display:flex;gap:8px">
                        <input id="chat-input" placeholder="输入消息...（Enter 发送）" style="flex:1" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendChat()}">
                        <button class="btn btn-primary" onclick="sendChat()" id="chat-send-btn">发送</button>
                    </div>
                </div>
            </div>
        </div>

        <!-- 模型管理 -->
        <div id="panel-models" class="panel">
            <div class="card">
                <div class="card-header">
                    <h3>模型厂商</h3>
                    <span style="font-size:0.8rem;color:var(--text-secondary)">点击卡片管理厂商模型</span>
                </div>
                <div id="providers-grid" class="stats-grid" style="grid-template-columns: repeat(auto-fill, minmax(270px, 1fr))">
                </div>
            </div>
        </div>

        <!-- API Key 配置 -->
        <div id="panel-config" class="panel">
            <div class="card">
                <div class="card-header">
                    <h3>API Key 管理</h3>
                    <span style="font-size:0.8rem;color:var(--text-muted)">🔒 所有密钥仅保存在本地</span>
                </div>
                <div id="config-grid" class="config-grid"></div>
            </div>
        </div>

    </div>

    <!-- 厂商模型管理弹窗 -->
    <div class="modal-overlay" id="modal-provider-models">
        <div class="modal" style="max-width:720px;max-height:85vh;overflow-y:auto">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
                <h3 id="provider-modal-title">模型管理</h3>
                <button class="btn btn-ghost btn-sm" onclick="hideModal('provider-models')">✕ 关闭</button>
            </div>

            <!-- 已启用模型列表 -->
            <div style="margin-bottom:20px">
                <h4 style="font-size:0.9rem;color:var(--text-secondary);margin-bottom:12px">模型列表 <span style="font-size:0.75rem;color:var(--text-muted)">（已启用的模型排在前面，可拖拽调整优先级）</span></h4>
                <div id="provider-models-list"></div>
            </div>

            <hr class="divider">

            <!-- 搜索并拉取新模型 -->
            <div>
                <h4 style="font-size:0.85rem;color:var(--text-secondary);margin-bottom:10px">搜索 & 拉取模型</h4>
                <div class="input-group" style="margin-bottom:12px">
                    <input id="provider-search-input" placeholder="搜索目录中的模型..." oninput="debouncedProviderSearch()">
                    <button class="btn btn-primary btn-sm" onclick="searchProviderModels()">搜索目录</button>
                    <button class="btn btn-ghost btn-sm" onclick="fetchRemoteModels()" title="需要 API Key，调用厂商远程 API 获取最新最全模型列表">拉取远程模型</button>
                </div>
                <div style="font-size:0.7rem;color:var(--text-muted);margin-bottom:8px">搜索目录：在本地模型目录中搜索（无需 API Key）｜拉取远程模型：调用厂商 API 获取最新模型列表（需 API Key）</div>
                <div id="provider-search-results"></div>
            </div>

            <!-- 手动添加 -->
            <hr class="divider">
            <details style="cursor:pointer">
                <summary style="font-size:0.8rem;color:var(--text-muted)">手动添加自定义模型</summary>
                <div style="margin-top:10px">
                    <div class="form-group"><label>模型 ID</label><input id="manual-model-id" placeholder="例如: gemini-2.0-flash-lite"></div>
                    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px">
                        <div class="form-group"><label>优先级</label><input id="manual-priority" type="number" value="5" min="1"></div>
                        <div class="form-group"><label>RPD</label><input id="manual-rpd" type="number" value="0" min="0"></div>
                        <div class="form-group"><label>RPM</label><input id="manual-rpm" type="number" value="0" min="0"></div>
                    </div>
                    <button class="btn btn-primary btn-sm" onclick="manualAddModel()">添加到配置</button>
                </div>
            </details>
        </div>
    </div>

    <!-- 接入说明弹窗 -->
    <div class="modal-overlay" id="modal-guide">
        <div class="modal" style="max-width:550px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
                <h3 id="guide-modal-title">接入说明</h3>
                <button class="btn btn-ghost btn-sm" onclick="hideModal('guide')">✕</button>
            </div>
            <div id="guide-modal-url" style="margin-bottom:12px"></div>
            <pre id="guide-modal-content" style="white-space:pre-wrap;font-size:0.85rem;color:var(--text-secondary);background:var(--bg-primary);padding:16px;border-radius:8px;border:1px solid var(--border);line-height:1.6;font-family:inherit"></pre>
        </div>
    </div>

    <!-- 调整优先级弹窗 -->
    <div class="modal-overlay" id="modal-priority">
        <div class="modal">
            <h3>调整模型优先级</h3>
            <p style="font-size:0.85rem;color:var(--text-secondary);margin-bottom:16px">数字越小优先级越高，优先使用高优先级模型</p>
            <div class="form-group">
                <label>新优先级</label>
                <input id="priority-value" type="number" min="1" value="1">
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="hideModal('priority')">取消</button>
                <button class="btn btn-primary" onclick="savePriority()">保存</button>
            </div>
        </div>
    </div>

    <script>
        const API = '';
        let currentPriorityTarget = null;

        // --- Tab 切换 ---
        function switchTab(name, el) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
            el.classList.add('active');
            document.getElementById('panel-' + name).classList.add('active');
            location.hash = name;
            if (name === 'routing') loadRoutingLog();
        }

        // --- Toast 通知 ---
        function toast(msg, type = 'info') {
            const container = document.getElementById('toast-container');
            const el = document.createElement('div');
            el.className = 'toast ' + type;
            el.textContent = msg;
            container.appendChild(el);
            setTimeout(() => el.remove(), 3000);
        }

        // --- 弹窗 ---
        function showModal(id) { document.getElementById('modal-' + id).classList.add('show'); }
        function hideModal(id) { document.getElementById('modal-' + id).classList.remove('show'); }

        // 点击弹窗外部关闭
        document.addEventListener('click', function(e) {
            if (e.target.classList.contains('modal-overlay') && e.target.classList.contains('show')) {
                e.target.classList.remove('show');
            }
        });

        // --- 路由决策日志 ---
        async function loadRoutingLog() {
            const container = document.getElementById('routing-log');
            try {
                const resp = await fetch(API + '/api/routing/log');
                const data = await resp.json();
                const decisions = data.decisions || [];
                if (!decisions.length) {
                    container.innerHTML = '<div class="empty-state"><h4>暂无路由记录</h4><p>发送 model=auto 的请求后，这里会显示路由决策过程</p></div>';
                    return;
                }
                container.innerHTML = decisions.map(d => {
                    const strategyColor = d.strategy.includes('快速') ? 'var(--accent-green)' :
                                          d.strategy.includes('LLM') ? 'var(--accent-blue)' :
                                          d.strategy.includes('缓存') ? 'var(--accent-purple)' : 'var(--accent-yellow)';
                    const cachedTag = d.cached ? '<span style="font-size:0.65rem;padding:1px 6px;border-radius:8px;background:rgba(139,92,246,0.12);color:var(--accent-purple)">⚡ 缓存</span>' : '';
                    const candidates = (d.candidates || []).map(c => {
                        const isSelected = d.selected && d.selected.endsWith(c);
                        return `<span style="font-size:0.7rem;padding:2px 6px;border-radius:6px;background:${isSelected ? 'rgba(16,185,129,0.15)' : 'rgba(100,116,139,0.1)'};color:${isSelected ? 'var(--accent-green)' : 'var(--text-muted)'};font-weight:${isSelected ? '600' : '400'}">${c}</span>`;
                    }).join(' ');
                    return `<div style="padding:12px;background:var(--bg-primary);border-radius:8px;margin-bottom:8px;border:1px solid var(--border)">
                        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
                            <div style="display:flex;align-items:center;gap:8px">
                                <span style="font-size:0.75rem;font-weight:600;color:${strategyColor}">${d.strategy}</span>
                                ${cachedTag}
                            </div>
                            <span style="font-size:0.7rem;color:var(--text-muted)">${d.timestamp || ''}</span>
                        </div>
                        <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
                            <span style="font-size:0.7rem;color:var(--text-muted)">选择:</span>
                            <span style="font-size:0.8rem;font-weight:600;color:var(--accent-green)">${d.selected || '-'}</span>
                        </div>
                        ${d.user_hint ? `<div style="font-size:0.7rem;color:var(--text-secondary);margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📝 ${escapeHtml(d.user_hint)}</div>` : ''}
                        <div style="display:flex;gap:4px;flex-wrap:wrap">${candidates}</div>
                    </div>`;
                }).join('');
            } catch(e) {
                container.innerHTML = `<div class="empty-state"><h4>加载失败</h4><p>${e.message}</p></div>`;
            }
        }

        // --- 使用量 ---
        async function loadUsage() {
            try {
                const resp = await fetch(API + '/v1/usage');
                const data = await resp.json();
                const grid = document.getElementById('usage-grid');

                if (data.stats.length === 0) {
                    grid.innerHTML = '<div class="empty-state"><h4>暂无使用数据</h4><p>请先配置并启用模型</p></div>';
                    return;
                }

                // 拉取模型能力信息
                let capMap = {};
                try {
                    const capResp = await fetch(API + '/api/capabilities');
                    const capData = await capResp.json();
                    const caps = capData.capabilities || capData;
                    for (const [key, val] of Object.entries(caps)) {
                        const parts = key.split('/');
                        const modelId = parts.slice(1).join('/');
                        capMap[modelId] = val;
                    }
                } catch(e) {}

                let blacklistSet = new Set();
                try {
                    const blResp = await fetch(API + '/api/blacklist');
                    const blData = await blResp.json();
                    (blData.blacklist || []).forEach(b => blacklistSet.add(b.model));
                } catch(e) {}

                grid.innerHTML = data.stats.map(s => {
                    const pct = s.rpd_limit > 0 ? (s.today_requests / s.rpd_limit * 100) : 0;
                    const color = pct > 80 ? 'red' : pct > 50 ? 'yellow' : 'green';
                    const blKey = s.provider + ':' + s.model;
                    const is429 = blacklistSet.has(blKey);
                    const statusClass = is429 ? 'unavailable' : (s.available ? 'available' : 'unavailable');
                    const statusText = is429 ? '🚫 429限流（次日恢复）' : (s.available ? '● 可用' : '● 不可用');
                    const borderStyle = is429 ? 'border-left:3px solid var(--accent-red)' : '';
                    const unblockBtn = is429 ? `<button class="btn btn-ghost btn-sm" style="font-size:0.65rem;margin-top:4px" onclick="unblock429('${s.provider}','${s.model}')">🔓 立即解除</button>` : '';
                    const cap = capMap[s.model];
                    const capHtml = cap ? buildCapBadges(cap) : '';
                    return `<div class="stat-card" style="${borderStyle}">
                        <div class="model-header">
                            <span class="model-name">${s.model}</span>
                            <span class="provider-tag">${s.provider}</span>
                        </div>
                        <span class="availability ${statusClass}">${statusText}</span>
                        ${unblockBtn}
                        ${capHtml}
                        <div class="progress-container">
                            <div class="progress-label">
                                <span>每日用量</span>
                                <span>${s.today_requests} / ${s.rpd_limit || '∞'}</span>
                            </div>
                            <div class="progress-bar"><div class="fill ${color}" style="width:${Math.min(pct,100)}%"></div></div>
                        </div>
                        <div class="stat-detail">
                            <div class="stat-item"><span class="label">今日请求</span><span class="value">${s.today_requests}</span></div>
                            <div class="stat-item"><span class="label">每日上限</span><span class="value">${s.rpd_limit || '无限制'}</span></div>
                            <div class="stat-item"><span class="label">本分钟</span><span class="value">${s.minute_requests}</span></div>
                            <div class="stat-item"><span class="label">RPM 上限</span><span class="value">${s.rpm_limit || '无限制'}</span></div>
                        </div>
                    </div>`;
                }).join('');

                document.getElementById('last-refresh').textContent = '更新于 ' + new Date().toLocaleTimeString();
            } catch (e) {
                toast('加载使用量失败: ' + e.message, 'error');
            }
        }

        async function unblock429(provider, model) {
            try {
                const resp = await fetch(API + `/api/blacklist/clear?provider=${provider}&model=${encodeURIComponent(model)}`, {method: 'DELETE'});
                const data = await resp.json();
                toast(`已解除 ${model} 的 429 限流状态`, 'success');
                loadUsage();
            } catch(e) {
                toast('解除失败: ' + e.message, 'error');
            }
        }

        // --- 厂商卡片 ---
        let currentProvider = null;
        const providerMeta = {
            google: { name: 'Google AI Studio', icon: 'G', color: 'icon-google', desc: 'Gemini 系列免费模型', url: 'https://aistudio.google.com/', guide: '获取 API Key: https://aistudio.google.com/apikey\\n\\n免费额度:\\n- Gemini 2.5 Pro: 25次/天, 5次/分\\n- Gemini 2.5 Flash: 500次/天, 10次/分\\n- Gemini 2.0 Flash: 1500次/天, 15次/分' },
            groq: { name: 'Groq', icon: 'Q', color: 'icon-groq', desc: '高速推理开源模型', url: 'https://console.groq.com/', guide: '注册后在 https://console.groq.com/keys 获取 API Key\\n\\n兼容 OpenAI SDK 格式\\n免费额度: 约 14400次/天, 30次/分' },
            github: { name: 'GitHub Models', icon: 'H', color: 'icon-github', desc: 'GitHub 模型市场', url: 'https://github.com/marketplace/models', guide: '使用 GitHub Personal Access Token\\nSettings > Developer settings > Tokens\\n\\n接口: https://models.inference.ai.azure.com\\n免费额度: 约 50次/天' },
            cursor: { name: 'Cursor', icon: 'C', color: 'icon-cursor', desc: 'Cursor Agent CLI', url: 'https://www.cursor.com/', guide: '无需 API Key，安装 Cursor IDE 并登录即可\\n确保 cursor 命令在系统 PATH 中' },
            cerebras: { name: 'Cerebras', icon: 'B', color: 'icon-groq', desc: '极速推理 ~2000 tok/s', url: 'https://cloud.cerebras.ai/', guide: '注册后在 Dashboard 获取 API Key\\n\\n兼容 OpenAI SDK:\\nbase_url = https://api.cerebras.ai/v1\\n\\n免费额度: 约 1000次/天, 推理速度极快' },
            sambanova: { name: 'SambaNova', icon: 'S', color: 'icon-github', desc: 'Llama/DeepSeek 免费推理', url: 'https://cloud.sambanova.ai/', guide: '注册后在 API 页面获取 Key\\n\\n兼容 OpenAI SDK:\\nbase_url = https://api.sambanova.ai/v1\\n\\n支持 405B 超大模型和 DeepSeek' },
            openrouter: { name: 'OpenRouter', icon: 'R', color: 'icon-cursor', desc: '聚合平台，部分模型免费', url: 'https://openrouter.ai/', guide: 'https://openrouter.ai/keys 获取 Key\\n\\n兼容 OpenAI SDK:\\nbase_url = https://openrouter.ai/api/v1\\n\\n模型名带 :free 后缀的完全免费\\n约 200次/天' },
            cloudflare: { name: 'Cloudflare', icon: 'F', color: 'icon-google', desc: 'Workers AI 每日万次免费', url: 'https://ai.cloudflare.com/', guide: 'Dashboard > AI > Workers AI\\n获取 Account ID 和 API Token\\n\\nAPI Key 格式: account_id:api_token\\n\\n每日 10,000 neurons 免费（约数千次请求）' },
            huggingface: { name: 'HuggingFace', icon: 'H', color: 'icon-github', desc: '免费推理数千开源模型', url: 'https://huggingface.co/inference-api', guide: 'https://huggingface.co/settings/tokens 创建 Token\\n\\n支持数千个开源模型免费推理\\n有速率限制但无需付费\\n支持 OpenAI 兼容 /v1/chat/completions' },
            mistral: { name: 'Mistral AI', icon: 'M', color: 'icon-groq', desc: '官方平台免费层', url: 'https://console.mistral.ai/', guide: 'https://console.mistral.ai/api-keys/ 获取 Key\\n\\n兼容 OpenAI SDK:\\nbase_url = https://api.mistral.ai/v1\\n\\nCodestral 代码模型免费（非商业用途）' },
        };

        async function loadProviderCards() {
            try {
                const [configResp, catalogResp] = await Promise.all([
                    fetch(API + '/api/config'),
                    fetch(API + '/api/catalog/providers'),
                ]);
                const configData = await configResp.json();
                const catalogData = await catalogResp.json();
                const grid = document.getElementById('providers-grid');

                // 合并 catalog 和 config 信息，按优先级排序
                const allProviders = {};
                for (const [id, cat] of Object.entries(catalogData.providers || {})) {
                    const conf = configData.providers?.[id] || {};
                    allProviders[id] = {
                        ...cat,
                        enabled: cat.enabled || conf.enabled || false,
                        priority: cat.priority ?? conf.priority ?? 99,
                        has_api_key: conf.has_api_key || false,
                        models: conf.models || [],
                    };
                }

                const sorted = Object.entries(allProviders).sort((a, b) => a[1].priority - b[1].priority);

                grid.innerHTML = sorted.map(([id, prov]) => {
                    const meta = providerMeta[id] || { name: prov.name || id, icon: id[0].toUpperCase(), color: '', desc: prov.description || '', guide: '' };
                    const modelCount = prov.models?.length || 0;
                    const enabledCount = (prov.models || []).filter(m => m.enabled).length;
                    return `<div class="stat-card draggable" draggable="true" data-provider-id="${id}" onclick="openProviderModal('${id}')">
                        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">
                            <div style="display:flex;align-items:center;gap:10px;min-width:0">
                                <span class="priority-badge">${prov.priority}</span>
                                <div class="model-icon ${meta.color}">${meta.icon}</div>
                                <div style="min-width:0">
                                    <div class="model-name" style="font-size:0.95rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${meta.name}</div>
                                    <div style="font-size:0.7rem;color:var(--text-muted);margin-top:1px">${meta.desc}</div>
                                </div>
                            </div>
                            <label class="toggle" onclick="event.stopPropagation()">
                                <input type="checkbox" ${prov.enabled ? 'checked' : ''} onchange="toggleProvider('${id}',this.checked)">
                                <span class="slider"></span>
                            </label>
                        </div>
                        <div style="margin-top:auto;padding-top:10px;display:flex;align-items:center;justify-content:space-between;border-top:1px solid rgba(51,65,85,0.3)">
                            <div style="display:flex;gap:14px;align-items:flex-end">
                                <div class="stat-item"><span class="label">模型</span><span class="value">${modelCount}</span></div>
                                <div class="stat-item"><span class="label">已启用</span><span class="value">${enabledCount}</span></div>
                                <div class="stat-item"><span class="label">Key</span><span class="value" style="color:${prov.has_api_key ? 'var(--accent-green)' : 'var(--accent-red)'}">${prov.has_api_key ? '✓' : '—'}</span></div>
                            </div>
                            <button class="guide-btn" title="接入说明" onclick="event.stopPropagation();showGuide('${id}')">?</button>
                        </div>
                    </div>`;
                }).join('');

                initDragAndDrop();
            } catch (e) {
                toast('加载厂商列表失败', 'error');
            }
        }

        // --- 模型拖拽排序 ---
        let modelDragSrc = null;

        function initModelDragAndDrop() {
            const container = document.getElementById('provider-models-list');
            const items = container.querySelectorAll('.model-item');
            items.forEach(item => {
                item.addEventListener('dragstart', onModelDragStart);
                item.addEventListener('dragover', onModelDragOver);
                item.addEventListener('dragenter', onModelDragEnter);
                item.addEventListener('dragleave', onModelDragLeave);
                item.addEventListener('drop', onModelDrop);
                item.addEventListener('dragend', onModelDragEnd);
            });
        }

        function onModelDragStart(e) {
            modelDragSrc = this;
            this.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', this.dataset.modelId);
        }
        function onModelDragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }
        function onModelDragEnter(e) { e.preventDefault(); this.classList.add('drag-over'); }
        function onModelDragLeave() { this.classList.remove('drag-over'); }
        function onModelDragEnd() {
            this.classList.remove('dragging');
            document.querySelectorAll('.model-item.drag-over').forEach(el => el.classList.remove('drag-over'));
        }

        function onModelDrop(e) {
            e.stopPropagation();
            e.preventDefault();
            this.classList.remove('drag-over');
            if (modelDragSrc !== this) {
                const container = document.getElementById('provider-models-list');
                const allItems = [...container.querySelectorAll('.model-item')];
                const srcIdx = allItems.indexOf(modelDragSrc);
                const dstIdx = allItems.indexOf(this);
                if (srcIdx < dstIdx) {
                    this.parentNode.insertBefore(modelDragSrc, this.nextSibling);
                } else {
                    this.parentNode.insertBefore(modelDragSrc, this);
                }
                saveModelOrder();
            }
        }

        async function saveModelOrder() {
            const container = document.getElementById('provider-models-list');
            const items = [...container.querySelectorAll('.model-item')];
            const updates = items.map((item, idx) => {
                const badge = item.querySelector('.priority-badge');
                if (badge) badge.textContent = idx + 1;
                return { model_id: item.dataset.modelId, priority: idx + 1 };
            });
            try {
                for (const u of updates) {
                    await fetch(API + `/api/config/model/priority?provider=${currentProvider}&model_name=${u.model_id}&priority=${u.priority}`, {method:'POST'});
                }
                toast('模型优先级已更新', 'success');
            } catch (e) {
                toast('保存模型排序失败', 'error');
            }
        }

        // --- 打开厂商模型弹窗 ---
        async function openProviderModal(providerId) {
            currentProvider = providerId;
            const meta = providerMeta[providerId] || { name: providerId };
            document.getElementById('provider-modal-title').textContent = meta.name + ' - 模型管理';
            document.getElementById('provider-search-input').value = '';
            document.getElementById('provider-search-results').innerHTML = '';
            showModal('provider-models');
            await loadProviderModels(providerId);
        }

        function buildCapBadges(cap) {
            if (!cap) return '';
            const items = [
                [cap.tool_calling, '🔧 工具调用', 'rgba(59,130,246,0.12)', 'var(--accent-blue)'],
                [cap.multi_turn_tc, '🔄 多轮对话', 'rgba(139,92,246,0.12)', 'var(--accent-purple)'],
                [cap.chinese, '🇨🇳 中文', 'rgba(245,158,11,0.12)', 'var(--accent-yellow)'],
                [cap.vision, '👁 视觉', 'rgba(6,182,212,0.12)', 'var(--accent-cyan)'],
                [cap.json_mode, '📋 JSON', 'rgba(16,185,129,0.12)', 'var(--accent-green)'],
                [cap.streaming, '⚡ 流式', 'rgba(139,92,246,0.12)', 'var(--accent-purple)'],
                [cap.reasoning, '🧠 推理', 'rgba(59,130,246,0.12)', 'var(--accent-blue)'],
            ];
            const latTag = cap.latency_ms ? `<span style="font-size:0.6rem;padding:1px 5px;border-radius:6px;background:rgba(100,116,139,0.1);color:${cap.latency_ms < 3000 ? 'var(--accent-green)' : cap.latency_ms < 8000 ? 'var(--accent-yellow)' : 'var(--accent-red)'}">${(cap.latency_ms/1000).toFixed(1)}s</span>` : '';
            const badges = items.filter(([v]) => v).map(([, text, bg, fg]) =>
                `<span style="font-size:0.6rem;padding:1px 5px;border-radius:6px;background:${bg};color:${fg}">${text}</span>`
            ).join('');
            const errTag = cap.error ? `<span style="font-size:0.6rem;color:var(--accent-red)" title="${escapeHtml(cap.error)}">⚠ ${cap.error.includes('429') ? '限流' : '异常'}</span>` : '';
            return `<div style="display:flex;gap:3px;flex-wrap:wrap;align-items:center;margin-top:4px">${latTag}${badges}${errTag}</div>`;
        }

        function buildCapSummary(cap) {
            if (!cap || cap.error) return '';
            const descs = [];
            if (cap.tool_calling) descs.push('支持工具调用');
            if (cap.multi_turn_tc) descs.push('多轮对话稳定');
            else if (cap.tool_calling && cap.mt_issue === 'loop_call') descs.push('多轮会循环调用');
            if (cap.chinese) descs.push('中文回答优秀');
            if (cap.vision) descs.push('支持图像理解');
            if (cap.json_mode) descs.push('结构化JSON输出');
            if (cap.streaming) descs.push('支持流式输出');
            if (cap.reasoning) descs.push('逻辑推理能力强');
            if (!descs.length) return '';
            return `<div style="font-size:0.7rem;color:var(--text-secondary);margin-top:3px">${descs.join(' · ')}</div>`;
        }

        async function loadProviderModels(providerId) {
            const [catalogResp, capResp] = await Promise.all([
                fetch(API + `/api/catalog/provider/${providerId}/models`),
                fetch(API + `/api/capabilities`).catch(() => null),
            ]);
            const data = await catalogResp.json();
            const allModels = data.models || [];
            const container = document.getElementById('provider-models-list');

            let capMap = {};
            if (capResp && capResp.ok) {
                const capData = await capResp.json();
                const caps = capData.capabilities || capData;
                for (const [key, val] of Object.entries(caps)) {
                    const parts = key.split('/');
                    if (parts[0] === providerId) {
                        capMap[parts.slice(1).join('/')] = val;
                    }
                }
            }

            if (allModels.length === 0) {
                container.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted)">该厂商目录中暂无模型，请从下方搜索或拉取添加</div>';
                return;
            }

            const sorted = allModels.sort((a,b) => {
                if (a.enabled && !b.enabled) return -1;
                if (!a.enabled && b.enabled) return 1;
                return (a.priority||99) - (b.priority||99);
            });

            container.innerHTML = sorted.map(m => {
                const isEnabled = m.enabled;
                const opacity = isEnabled ? '1' : '0.6';
                const borderColor = isEnabled ? 'var(--border)' : 'var(--bg-tertiary)';
                const cap = capMap[m.id];
                const capBadgesHtml = buildCapBadges(cap);
                const capSummaryHtml = buildCapSummary(cap);
                const noCap = !cap ? '<span style="font-size:0.65rem;color:var(--text-muted)">未测试</span>' : '';
                return `<div class="model-item" draggable="${isEnabled}" data-model-id="${m.id}" style="display:flex;align-items:center;justify-content:space-between;padding:12px;background:var(--bg-primary);border-radius:8px;margin-bottom:8px;border:1px solid ${borderColor};opacity:${opacity};${isEnabled ? 'cursor:grab' : ''}">
                    <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:0">
                        <span class="priority-badge">${isEnabled ? (m.priority || 99) : '-'}</span>
                        <div style="flex:1;min-width:0">
                            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                                <span style="font-weight:600;font-size:0.9rem">${m.name || m.id}</span>
                                ${noCap}
                            </div>
                            ${capBadgesHtml}
                            ${capSummaryHtml}
                            <div style="font-size:0.7rem;color:var(--text-muted);margin-top:2px">RPD: ${m.default_rpd || '∞'} | RPM: ${m.default_rpm || '∞'}</div>
                        </div>
                    </div>
                    <div style="display:flex;align-items:center;gap:8px;flex-shrink:0">
                        <label class="toggle" onclick="event.stopPropagation()">
                            <input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="toggleModelAndReload('${providerId}','${m.id}',this.checked)">
                            <span class="slider"></span>
                        </label>
                    </div>
                </div>`;
            }).join('');

            initModelDragAndDrop();
        }

        async function toggleModelAndReload(providerId, modelId, enabled) {
            if (enabled) {
                const resp = await fetch(API + `/api/catalog/provider/${providerId}/models`);
                const data = await resp.json();
                const enabledModels = (data.models || []).filter(m => m.enabled);
                const maxPri = enabledModels.reduce((max, m) => Math.max(max, m.priority || 0), 0);
                const newPri = maxPri + 1;
                await fetch(API + `/api/catalog/model/activate?provider_id=${providerId}&model_id=${modelId}&priority=${newPri}`, {method:'POST'});
                toast(`${modelId} 已启用（优先级 ${newPri}）`, 'success');
            } else {
                await fetch(API + `/api/config/model/delete?provider=${providerId}&model_name=${modelId}`, {method:'POST'});
                toast(`${modelId} 已停用`, 'success');
            }
            loadProviderModels(providerId);
            loadProviderCards();
        }

        // 兼容旧调用
        async function loadModels() { await loadProviderCards(); }

        // --- 配置 ---
        async function loadConfig() {
            try {
                const resp = await fetch(API + '/api/config');
                const data = await resp.json();
                const grid = document.getElementById('config-grid');

                grid.innerHTML = Object.entries(data.providers).map(([name, prov]) => {
                    const statusClass = prov.has_api_key ? 'configured' : 'not-configured';
                    const statusText = prov.has_api_key ? '已配置' : '未配置';
                    return `<div class="config-item">
                        <div class="ci-header">
                            <span class="ci-name">${name.charAt(0).toUpperCase() + name.slice(1)}</span>
                            <div style="display:flex;align-items:center;gap:10px">
                                <span class="ci-status ${statusClass}">${statusText}</span>
                                <label class="toggle">
                                    <input type="checkbox" ${prov.enabled ? 'checked' : ''}
                                        onchange="toggleProvider('${name}', this.checked)">
                                    <span class="slider"></span>
                                </label>
                            </div>
                        </div>
                        <div class="input-group">
                            <input type="password" id="key-${name}" placeholder="输入 ${name} API Key...">
                            <button class="btn btn-primary btn-sm" onclick="saveKey('${name}')">保存</button>
                        </div>
                    </div>`;
                }).join('');
            } catch (e) {
                toast('加载配置失败', 'error');
            }
        }


        // --- 操作 ---
        async function toggleModel(provider, model, enabled) {
            const resp = await fetch(API + `/api/config/model/toggle?provider=${provider}&model_name=${model}&enabled=${enabled}`, {method:'POST'});
            const data = await resp.json();
            toast(data.message || `${model} 已${enabled ? '启用' : '禁用'}`, 'success');
        }

        async function toggleProvider(provider, enabled) {
            const resp = await fetch(API + `/api/provider/toggle?provider=${provider}&enabled=${enabled}`, {method:'POST'});
            const data = await resp.json();
            toast(data.message || `${provider} 已${enabled ? '启用' : '禁用'}`, 'success');
            loadProviderCards();
        }

        async function saveKey(provider) {
            const input = document.getElementById('key-' + provider);
            const key = input.value.trim();
            if (!key) { toast('请输入 API Key', 'error'); return; }
            const resp = await fetch(API + `/api/config/apikey?provider=${provider}&api_key=${encodeURIComponent(key)}`, {method:'POST'});
            const data = await resp.json();
            input.value = '';
            toast(data.message || `${provider} API Key 已保存`, 'success');
            loadConfig();
            loadProviderCards();
        }

        function showPriorityModal(provider, model, current) {
            currentPriorityTarget = { provider, model };
            document.getElementById('priority-value').value = current;
            showModal('priority');
        }

        async function savePriority() {
            if (!currentPriorityTarget) return;
            const p = document.getElementById('priority-value').value;
            const { provider, model } = currentPriorityTarget;
            await fetch(API + `/api/config/model/priority?provider=${provider}&model_name=${model}&priority=${p}`, {method:'POST'});
            hideModal('priority');
            loadModels();
            toast('优先级已更新', 'success');
        }

        async function addModel() {
            // 兼容保留
            manualAddModel();
        }

        async function refreshModels() {
            toast('正在刷新...', 'info');
            loadProviderCards();
        }

        // --- 从运行配置删除模型 ---
        async function deleteConfigModel(provider, modelId) {
            if (!confirm(`确认删除 ${modelId}？`)) return;
            const resp = await fetch(API + `/api/config/model/delete?provider=${provider}&model_name=${modelId}`, {method:'POST'});
            if (resp.ok) {
                toast(`${modelId} 已删除`, 'success');
                loadProviderModels(currentProvider);
                loadProviderCards();
            } else {
                const err = await resp.json();
                toast(err.detail || '删除失败', 'error');
            }
        }

        // --- 厂商弹窗内搜索 ---
        let provSearchTimer = null;
        function debouncedProviderSearch() {
            clearTimeout(provSearchTimer);
            provSearchTimer = setTimeout(searchProviderModels, 300);
        }

        async function searchProviderModels() {
            const q = document.getElementById('provider-search-input').value.trim();
            const container = document.getElementById('provider-search-results');

            try {
                const resp = await fetch(API + `/api/catalog/search?q=${encodeURIComponent(q)}`);
                const data = await resp.json();
                const results = data.results.filter(m => m.provider_id === currentProvider);

                if (results.length === 0) {
                    container.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-muted);font-size:0.85rem">未找到匹配模型</div>';
                    return;
                }

                container.innerHTML = results.map(m => {
                    const isActive = m.enabled;
                    return `<div style="display:flex;align-items:center;justify-content:space-between;padding:10px 12px;background:var(--bg-primary);border-radius:6px;margin-bottom:6px;border:1px solid var(--border)">
                        <div>
                            <div style="font-weight:500;font-size:0.85rem">${m.name || m.id}</div>
                            <div style="font-size:0.75rem;color:var(--text-muted)">${m.description || ''} | RPD: ${m.default_rpd || '∞'} RPM: ${m.default_rpm || '∞'}</div>
                        </div>
                        ${isActive
                            ? '<span style="font-size:0.75rem;color:var(--accent-green)">✓ 已启用</span>'
                            : `<button class="btn btn-primary btn-sm" onclick="activateFromCatalog('${m.provider_id}','${m.id}')">+ 启用</button>`
                        }
                    </div>`;
                }).join('');
            } catch (e) {
                toast('搜索失败: ' + e.message, 'error');
            }
        }

        async function fetchRemoteModels() {
            const container = document.getElementById('provider-search-results');
            container.innerHTML = '<div style="padding:16px;text-align:center;color:var(--accent-cyan)"><div style="font-size:1.2rem;margin-bottom:8px">⏳</div>正在拉取模型列表并测试能力...<br><span style="font-size:0.75rem;color:var(--text-muted)">首次测试每个模型需要几秒，已测试的模型会从缓存读取</span></div>';
            try {
                const resp = await fetch(API + `/api/provider/${currentProvider}/models`);
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    container.innerHTML = '';
                    toast(err.detail || '拉取失败：厂商未注册或 API Key 未配置', 'error');
                    return;
                }
                const data = await resp.json();

                const catalogResp = await fetch(API + `/api/catalog/provider/${currentProvider}/models`);
                const catalogData = await catalogResp.json();
                const catalogMap = {};
                (catalogData.models || []).forEach(m => { catalogMap[m.id] = m; });

                const remoteModels = data.available_models || [];
                const capabilities = data.capabilities || {};
                const capSummary = capabilities.summary || {};
                const allCapResults = [...(capabilities.tested || []), ...(capabilities.cached || [])];
                const capMap = {};
                allCapResults.forEach(r => { capMap[r.model] = r; });

                if (remoteModels.length === 0) {
                    container.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-muted)">厂商未返回模型列表</div>';
                    return;
                }

                const summaryHtml = capSummary.total ? `<div style="margin-bottom:12px;padding:10px 14px;background:var(--bg-primary);border-radius:8px;border:1px solid var(--border);display:flex;gap:16px;flex-wrap:wrap;align-items:center">
                    <span style="font-size:0.85rem;font-weight:600;color:var(--accent-cyan)">能力检测</span>
                    <span style="font-size:0.8rem;color:var(--text-secondary)">共 ${capSummary.total} 个模型</span>
                    <span style="font-size:0.8rem;color:${capSummary.tested_now > 0 ? 'var(--accent-yellow)' : 'var(--accent-green)'}">🔬 本次测试: ${capSummary.tested_now}</span>
                    <span style="font-size:0.8rem;color:var(--text-muted)">📦 缓存命中: ${capSummary.from_cache}</span>
                    ${capSummary.all_cached ? '<span style="font-size:0.75rem;padding:2px 8px;background:rgba(16,185,129,0.15);color:var(--accent-green);border-radius:10px">✓ 全部已缓存</span>' : ''}
                </div>` : '';

                container.innerHTML = summaryHtml +
                    `<div style="margin-bottom:8px;font-size:0.8rem;color:var(--text-secondary)">厂商共 ${remoteModels.length} 个模型：</div>` +
                    remoteModels.map(modelId => {
                        const inCatalog = catalogMap[modelId];
                        const isActive = inCatalog && inCatalog.enabled;
                        const cap = capMap[modelId];

                        let capBadges = '';
                        if (cap) {
                            const badge = (condition, trueText, falseText, trueBg, trueFg, falseBg, falseFg) => {
                                const bg = condition ? trueBg : falseBg;
                                const fg = condition ? trueFg : falseFg;
                                const text = condition ? trueText : falseText;
                                return `<span style="font-size:0.65rem;padding:1px 5px;border-radius:6px;background:${bg};color:${fg}">${text}</span>`;
                            };
                            const availBadge = badge(cap.available, '✓ 可用', '✗ 不可用', 'rgba(16,185,129,0.1)', 'var(--accent-green)', 'rgba(239,68,68,0.1)', 'var(--accent-red)');
                            const tcBadge = badge(cap.tool_calling, '🔧 TC', '— TC', 'rgba(59,130,246,0.1)', 'var(--accent-blue)', 'rgba(100,116,139,0.1)', 'var(--text-muted)');
                            const mtBg = cap.multi_turn_tc ? 'rgba(139,92,246,0.1)' : (cap.tool_calling ? 'rgba(239,68,68,0.1)' : 'rgba(100,116,139,0.05)');
                            const mtColor = cap.multi_turn_tc ? 'var(--accent-purple)' : (cap.tool_calling ? 'var(--accent-red)' : 'var(--text-muted)');
                            const mtText = cap.multi_turn_tc ? '🔄 多轮' : (cap.tool_calling ? (cap.mt_issue === 'loop_call' ? '🔁 循环' : '— 多轮') : '');
                            const mtBadge = mtText ? `<span style="font-size:0.65rem;padding:1px 5px;border-radius:6px;background:${mtBg};color:${mtColor}">${mtText}</span>` : '';
                            const cnBadge = badge(cap.chinese, '🇨🇳 中文', '— 中文', 'rgba(245,158,11,0.1)', 'var(--accent-yellow)', 'rgba(100,116,139,0.05)', 'var(--text-muted)');
                            const visBadge = badge(cap.vision, '👁 视觉', '— 视觉', 'rgba(6,182,212,0.1)', 'var(--accent-cyan)', 'rgba(100,116,139,0.05)', 'var(--text-muted)');
                            const jsonBadge = badge(cap.json_mode, '📋 JSON', '— JSON', 'rgba(16,185,129,0.1)', 'var(--accent-green)', 'rgba(100,116,139,0.05)', 'var(--text-muted)');
                            const streamBadge = badge(cap.streaming, '⚡ 流式', '— 流式', 'rgba(139,92,246,0.1)', 'var(--accent-purple)', 'rgba(100,116,139,0.05)', 'var(--text-muted)');
                            const reasonBadge = badge(cap.reasoning, '🧠 推理', '— 推理', 'rgba(59,130,246,0.1)', 'var(--accent-blue)', 'rgba(100,116,139,0.05)', 'var(--text-muted)');
                            const latencyTag = cap.latency_ms ? `<span style="font-size:0.6rem;color:${cap.latency_ms < 3000 ? 'var(--accent-green)' : cap.latency_ms < 8000 ? 'var(--accent-yellow)' : 'var(--accent-red)'}">${(cap.latency_ms/1000).toFixed(1)}s</span>` : '';
                            const cachedTag = cap.cached ? '<span style="font-size:0.6rem;color:var(--text-muted)">📦</span>' : '<span style="font-size:0.6rem;color:var(--accent-yellow)">🔬</span>';
                            const errTag = cap.error ? `<span style="font-size:0.65rem;color:var(--accent-red)" title="${escapeHtml(cap.error)}">⚠</span>` : '';

                            capBadges = `<div style="display:flex;gap:3px;align-items:center;flex-shrink:0;flex-wrap:wrap">
                                ${cachedTag}${latencyTag}
                                ${availBadge}${tcBadge}${mtBadge}${cnBadge}
                                ${visBadge}${jsonBadge}${streamBadge}${reasonBadge}
                                ${errTag}
                            </div>`;
                        }

                        let actionHtml;
                        if (isActive) {
                            actionHtml = '<span style="font-size:0.75rem;color:var(--accent-green);flex-shrink:0">✓ 已启用</span>';
                        } else if (inCatalog) {
                            actionHtml = `<button class="btn btn-primary btn-sm" onclick="activateFromCatalog('${currentProvider}','${modelId}')">启用</button>`;
                        } else {
                            actionHtml = `<button class="btn btn-sm btn-secondary" onclick="pullModelToCatalog('${currentProvider}','${modelId}')">拉取到目录</button>`;
                        }

                        return `<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:var(--bg-primary);border-radius:6px;margin-bottom:4px;border:1px solid var(--border);gap:8px;flex-wrap:wrap">
                            <span style="font-size:0.85rem;min-width:0;overflow:hidden;text-overflow:ellipsis">${modelId}</span>
                            <div style="display:flex;gap:8px;align-items:center;flex-shrink:0">
                                ${capBadges}
                                ${actionHtml}
                            </div>
                        </div>`;
                    }).join('');

                if (capSummary.tested_now > 0) {
                    toast(`已测试 ${capSummary.tested_now} 个新模型的能力`, 'success');
                } else if (capSummary.all_cached) {
                    toast('所有模型已从缓存加载', 'info');
                }
            } catch (e) {
                toast('拉取失败: ' + e.message, 'error');
            }
        }

        async function pullModelToCatalog(providerId, modelId) {
            const resp = await fetch(API + `/api/catalog/model/add?provider_id=${providerId}&model_id=${modelId}&name=${modelId}&category=通用`, {method:'POST'});
            if (resp.ok) {
                toast(`${modelId} 已拉取到模型目录`, 'success');
                fetchRemoteModels();
            } else {
                const err = await resp.json();
                toast(err.detail || '拉取失败', 'error');
            }
        }

        async function activateFromCatalog(providerId, modelId) {
            const modelsResp = await fetch(API + `/api/catalog/provider/${providerId}/models`);
            const modelsData = await modelsResp.json();
            const enabledModels = (modelsData.models || []).filter(m => m.enabled);
            const maxPri = enabledModels.reduce((max, m) => Math.max(max, m.priority || 0), 0);
            const defaultPri = maxPri + 1;
            const priority = prompt('设置优先级（数字越小越优先）:', String(defaultPri));
            if (priority === null) return;
            const resp = await fetch(API + `/api/catalog/model/activate?provider_id=${providerId}&model_id=${modelId}&priority=${priority}`, {method:'POST'});
            if (resp.ok) {
                toast(`${modelId} 已启用`, 'success');
                loadProviderModels(currentProvider);
                loadProviderCards();
                searchProviderModels();
            } else {
                const err = await resp.json();
                toast(err.detail || '激活失败', 'error');
            }
        }

        async function manualAddModel() {
            const modelId = document.getElementById('manual-model-id').value.trim();
            const priority = document.getElementById('manual-priority').value;
            const rpd = document.getElementById('manual-rpd').value;
            const rpm = document.getElementById('manual-rpm').value;
            if (!modelId) { toast('请输入模型 ID', 'error'); return; }
            await fetch(API + `/api/config/model/add?provider=${currentProvider}&name=${modelId}&priority=${priority}&rpd=${rpd}&rpm=${rpm}`, {method:'POST'});
            document.getElementById('manual-model-id').value = '';
            loadProviderModels(currentProvider);
            loadProviderCards();
            toast(`${modelId} 已添加`, 'success');
        }

        // --- 接入说明 ---
        function showGuide(providerId) {
            const meta = providerMeta[providerId] || {};
            document.getElementById('guide-modal-title').textContent = (meta.name || providerId) + ' - 接入说明';
            document.getElementById('guide-modal-url').innerHTML = meta.url ? `<a href="${meta.url}" target="_blank" style="color:var(--accent-blue);font-size:0.85rem">🔗 ${meta.url}</a>` : '';
            document.getElementById('guide-modal-content').textContent = meta.guide || '暂无接入说明';
            showModal('guide');
        }

        // --- 拖拽排序 ---
        let dragSrcEl = null;

        function initDragAndDrop() {
            const grid = document.getElementById('providers-grid');
            const cards = grid.querySelectorAll('.draggable');
            cards.forEach(card => {
                card.addEventListener('dragstart', handleDragStart);
                card.addEventListener('dragover', handleDragOver);
                card.addEventListener('dragenter', handleDragEnter);
                card.addEventListener('dragleave', handleDragLeave);
                card.addEventListener('drop', handleDrop);
                card.addEventListener('dragend', handleDragEnd);
            });
        }

        function handleDragStart(e) {
            dragSrcEl = this;
            this.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', this.dataset.providerId);
        }

        function handleDragOver(e) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
        }

        function handleDragEnter(e) {
            e.preventDefault();
            this.classList.add('drag-over');
        }

        function handleDragLeave(e) {
            this.classList.remove('drag-over');
        }

        function handleDrop(e) {
            e.stopPropagation();
            e.preventDefault();
            this.classList.remove('drag-over');
            if (dragSrcEl !== this) {
                const grid = document.getElementById('providers-grid');
                const allCards = [...grid.querySelectorAll('.draggable')];
                const srcIdx = allCards.indexOf(dragSrcEl);
                const dstIdx = allCards.indexOf(this);
                if (srcIdx < dstIdx) {
                    this.parentNode.insertBefore(dragSrcEl, this.nextSibling);
                } else {
                    this.parentNode.insertBefore(dragSrcEl, this);
                }
                saveProviderOrder();
            }
        }

        function handleDragEnd(e) {
            this.classList.remove('dragging');
            document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
        }

        async function saveProviderOrder() {
            const grid = document.getElementById('providers-grid');
            const cards = [...grid.querySelectorAll('.draggable')];
            const orderedIds = cards.map(c => c.dataset.providerId);

            // 更新显示的优先级数字
            cards.forEach((card, idx) => {
                const badge = card.querySelector('.priority-badge');
                if (badge) badge.textContent = idx + 1;
            });

            try {
                await fetch(API + '/api/provider/reorder', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(orderedIds),
                });
                toast('厂商优先级已更新', 'success');
            } catch (e) {
                toast('保存排序失败: ' + e.message, 'error');
            }
        }

        // --- 聊天功能（会话管理 + 流式 + Markdown） ---
        let currentSessionId = null;

        async function loadChatModelSelector() {
            try {
                const resp = await fetch(API + '/v1/models');
                const data = await resp.json();
                const select = document.getElementById('chat-model');
                const currentVal = select.value;
                select.innerHTML = '<option value="auto">🤖 自动选择模型</option>';
                data.models.filter(m => m.enabled).forEach(m => {
                    select.innerHTML += `<option value="${m.id}">[${m.provider}] ${m.id}</option>`;
                });
                select.value = currentVal || 'auto';
            } catch(e) {}
        }

        async function loadSessionList() {
            try {
                const resp = await fetch(API + '/api/chat/sessions');
                const data = await resp.json();
                const list = document.getElementById('session-list');
                const sessions = data.sessions || [];
                if (!sessions.length) {
                    list.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:0.8rem">暂无会话</div>';
                    return;
                }
                list.innerHTML = sessions.map(s => {
                    const isActive = s.id === currentSessionId;
                    const bg = isActive ? 'var(--accent-blue)' : 'transparent';
                    const color = isActive ? '#fff' : 'var(--text-primary)';
                    const t = new Date(s.updated_at * 1000).toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
                    return '<div onclick="switchSession(\\''+s.id+'\\')" style="padding:8px 10px;border-radius:8px;margin-bottom:4px;cursor:pointer;background:'+bg+';color:'+color+';transition:all 0.15s">'
                        + '<div style="font-size:0.82rem;font-weight:'+(isActive?'600':'400')+';overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+escapeHtml(s.title)+'</div>'
                        + '<div style="font-size:0.65rem;color:'+(isActive?'rgba(255,255,255,0.7)':'var(--text-muted)')+';margin-top:2px;display:flex;justify-content:space-between">'
                        + '<span>'+s.message_count+' 条</span><span>'+t+'</span></div></div>';
                }).join('');
            } catch(e) {
                document.getElementById('session-list').innerHTML = '<div style="padding:12px;color:var(--accent-red);font-size:0.8rem">加载失败</div>';
            }
        }

        async function createNewSession() {
            const model = document.getElementById('chat-model').value;
            const resp = await fetch(API + '/api/chat/sessions?model=' + encodeURIComponent(model), {method:'POST'});
            const data = await resp.json();
            currentSessionId = data.session.id;
            localStorage.setItem('mp_current_session', currentSessionId);
            await loadSessionList();
            renderSessionMessages([]);
            updateContextInfo(0, 0);
            document.getElementById('chat-title').textContent = data.session.title;
        }

        async function switchSession(sessionId) {
            currentSessionId = sessionId;
            localStorage.setItem('mp_current_session', sessionId);
            await loadSessionList();
            try {
                const resp = await fetch(API + '/api/chat/sessions/' + sessionId);
                if (!resp.ok) { currentSessionId = null; localStorage.removeItem('mp_current_session'); loadSessionList(); return; }
                const data = await resp.json();
                const s = data.session;
                document.getElementById('chat-title').textContent = s.title;
                renderSessionMessages(s.messages || []);
                updateContextInfo(s.tokens_est, (s.messages||[]).length);
            } catch(e) { toast('加载会话失败', 'error'); }
        }

        async function deleteCurrentSession() {
            if (!currentSessionId) return;
            if (!confirm('确认删除当前会话？')) return;
            await fetch(API + '/api/chat/sessions/' + currentSessionId, {method:'DELETE'});
            currentSessionId = null;
            localStorage.removeItem('mp_current_session');
            await loadSessionList();
            renderSessionMessages([]);
            document.getElementById('chat-title').textContent = '新对话';
            updateContextInfo(0, 0);
        }

        async function renameCurrentSession() {
            if (!currentSessionId) return;
            const title = prompt('请输入新的会话标题:', document.getElementById('chat-title').textContent);
            if (!title) return;
            await fetch(API + '/api/chat/sessions/' + currentSessionId + '/title?title=' + encodeURIComponent(title), {method:'PUT'});
            document.getElementById('chat-title').textContent = title;
            loadSessionList();
        }

        function renderSessionMessages(messages) {
            const container = document.getElementById('chat-messages');
            container.innerHTML = '';
            if (!messages.length) {
                const empty = document.createElement('div');
                empty.style.cssText = 'text-align:center;color:var(--text-muted);padding:40px 0';
                empty.innerHTML = '<p>开始输入消息，对话历史会自动保存</p><p style="font-size:0.8rem;margin-top:8px">刷新页面不丢失 · 支持多会话管理 · 流式输出</p>';
                container.appendChild(empty);
                return;
            }
            messages.forEach(m => appendMessage(m.role, m.content, m.model, false));
        }

        function updateContextInfo(tokensEst, msgCount) {
            const el = document.getElementById('chat-context-info');
            if (!tokensEst && !msgCount) { el.textContent = ''; return; }
            const pct = Math.min(100, Math.round(tokensEst / 320));
            const color = pct > 80 ? 'var(--accent-red)' : pct > 50 ? 'var(--accent-yellow)' : 'var(--accent-green)';
            el.innerHTML = '<span style="color:'+color+'">~'+tokensEst+'</span> tokens · '+msgCount+' 条';
        }

        // --- Markdown 简易渲染 ---
        function renderMarkdown(text) {
            if (!text) return '';
            let html = escapeHtml(text);
            // 代码块 ```lang ... ```
            html = html.replace(/```(\\w*)\\n([\\s\\S]*?)```/g, function(_, lang, code) {
                return '<pre style="background:var(--bg-tertiary);padding:12px;border-radius:8px;overflow-x:auto;margin:8px 0;position:relative;font-size:0.82rem;line-height:1.5">'
                    + (lang ? '<div style="position:absolute;top:4px;right:8px;font-size:0.65rem;color:var(--text-muted)">'+lang+'</div>' : '')
                    + '<code>'+code+'</code></pre>';
            });
            // 行内代码 `code`
            html = html.replace(/`([^`]+)`/g, '<code style="background:var(--bg-tertiary);padding:1px 5px;border-radius:4px;font-size:0.85em">$1</code>');
            // 加粗 **text**
            html = html.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
            // 斜体 *text*
            html = html.replace(/\\*(.+?)\\*/g, '<em>$1</em>');
            // 标题 ### text
            html = html.replace(/^### (.+)$/gm, '<div style="font-size:1rem;font-weight:700;margin:8px 0 4px">$1</div>');
            html = html.replace(/^## (.+)$/gm, '<div style="font-size:1.1rem;font-weight:700;margin:10px 0 4px">$1</div>');
            // 无序列表
            html = html.replace(/^- (.+)$/gm, '<div style="padding-left:16px">• $1</div>');
            html = html.replace(/^\\* (.+)$/gm, '<div style="padding-left:16px">• $1</div>');
            // 有序列表
            html = html.replace(/^(\\d+)\\. (.+)$/gm, '<div style="padding-left:16px">$1. $2</div>');
            // 普通换行
            html = html.replace(/\\n/g, '<br>');
            return html;
        }

        function appendMessage(role, content, model, withActions) {
            const container = document.getElementById('chat-messages');
            const empty = container.querySelector('[style*="text-align:center"]');
            if (empty) empty.remove();

            const isUser = role === 'user';
            const bgColor = isUser ? 'var(--accent-blue)' : 'var(--bg-primary)';
            const textColor = isUser ? '#fff' : 'var(--text-primary)';
            const align = isUser ? 'flex-end' : 'flex-start';

            const wrapper = document.createElement('div');
            wrapper.style.cssText = 'display:flex;justify-content:'+align;

            const outerDiv = document.createElement('div');
            outerDiv.style.cssText = 'max-width:80%;display:flex;flex-direction:column;gap:4px;align-items:'+align;

            const bubble = document.createElement('div');
            bubble.style.cssText = 'padding:12px 16px;border-radius:12px;background:'+bgColor+';color:'+textColor+';border:1px solid var(--border);word-break:break-word;line-height:1.6';
            bubble.className = 'msg-bubble';

            if (isUser) {
                bubble.innerHTML = escapeHtml(content || '').replace(/\\n/g, '<br>');
            } else {
                bubble.innerHTML = renderMarkdown(content || '');
            }

            outerDiv.appendChild(bubble);

            // 操作栏
            if (!isUser && withActions !== false) {
                const actions = document.createElement('div');
                actions.style.cssText = 'display:flex;gap:6px;opacity:0;transition:opacity 0.15s';
                outerDiv.onmouseenter = () => actions.style.opacity = '1';
                outerDiv.onmouseleave = () => actions.style.opacity = '0';

                const copyBtn = document.createElement('button');
                copyBtn.className = 'btn btn-ghost btn-sm';
                copyBtn.style.cssText = 'font-size:0.65rem;padding:2px 6px';
                copyBtn.textContent = '📋 复制';
                copyBtn.onclick = () => {
                    navigator.clipboard.writeText(content || '').then(() => toast('已复制到剪贴板', 'success'));
                };
                actions.appendChild(copyBtn);

                if (model) {
                    const modelSpan = document.createElement('span');
                    modelSpan.style.cssText = 'font-size:0.65rem;color:var(--text-muted);align-self:center';
                    modelSpan.textContent = model;
                    actions.appendChild(modelSpan);
                }
                outerDiv.appendChild(actions);
            }

            wrapper.appendChild(outerDiv);
            container.appendChild(wrapper);
            container.scrollTop = container.scrollHeight;
            return bubble;
        }

        function escapeHtml(text) {
            if (!text) return '';
            return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        }

        // --- 流式发送 ---
        async function sendChat() {
            if (!currentSessionId) {
                await createNewSession();
            }
            const input = document.getElementById('chat-input');
            const msg = input.value.trim();
            if (!msg) return;

            const model = document.getElementById('chat-model').value;
            input.value = '';

            appendMessage('user', msg);

            const btn = document.getElementById('chat-send-btn');
            btn.disabled = true;
            btn.textContent = '思考中...';

            // 创建占位气泡
            const bubble = appendMessage('assistant', '', null, false);
            let fullText = '';
            let usedModel = '';

            try {
                const resp = await fetch(API + '/api/chat/sessions/' + currentSessionId + '/stream', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        model: model,
                        messages: [{role: 'user', content: msg}],
                        temperature: 0.7,
                    }),
                });

                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    bubble.innerHTML = '❌ ' + escapeHtml(err.detail || '请求失败');
                    return;
                }

                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const {value, done} = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, {stream: true});

                    const lines = buffer.split('\\n');
                    buffer = lines.pop() || '';

                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;
                        const payload = line.slice(6).trim();
                        if (payload === '[DONE]') continue;

                        try {
                            const chunk = JSON.parse(payload);
                            if (chunk.meta) {
                                usedModel = chunk.meta.model || '';
                                if (chunk.meta.title) {
                                    document.getElementById('chat-title').textContent = chunk.meta.title;
                                    loadSessionList();
                                }
                                continue;
                            }
                            if (chunk.session_info) {
                                updateContextInfo(chunk.session_info.tokens_est, chunk.session_info.total_messages);
                            }
                            const delta = chunk.choices && chunk.choices[0] && chunk.choices[0].delta;
                            if (delta && delta.content) {
                                fullText += delta.content;
                                bubble.innerHTML = renderMarkdown(fullText);
                                const container = document.getElementById('chat-messages');
                                container.scrollTop = container.scrollHeight;
                            }
                        } catch(pe) {}
                    }
                }

                // 渲染操作栏
                const outerDiv = bubble.parentElement;
                if (outerDiv) {
                    const actions = document.createElement('div');
                    actions.style.cssText = 'display:flex;gap:6px;opacity:0;transition:opacity 0.15s';
                    outerDiv.onmouseenter = () => actions.style.opacity = '1';
                    outerDiv.onmouseleave = () => actions.style.opacity = '0';
                    const copyBtn = document.createElement('button');
                    copyBtn.className = 'btn btn-ghost btn-sm';
                    copyBtn.style.cssText = 'font-size:0.65rem;padding:2px 6px';
                    copyBtn.textContent = '📋 复制';
                    copyBtn.onclick = () => navigator.clipboard.writeText(fullText).then(() => toast('已复制', 'success'));
                    actions.appendChild(copyBtn);
                    if (usedModel) {
                        const ms = document.createElement('span');
                        ms.style.cssText = 'font-size:0.65rem;color:var(--text-muted);align-self:center';
                        ms.textContent = usedModel;
                        actions.appendChild(ms);
                    }
                    outerDiv.appendChild(actions);
                }
            } catch (e) {
                bubble.innerHTML = '❌ ' + escapeHtml(e.message);
            } finally {
                btn.disabled = false;
                btn.textContent = '发送';
            }
        }

        // --- 移动端侧栏切换 ---
        function toggleChatSidebar() {
            document.getElementById('chat-sidebar').classList.toggle('show');
            document.getElementById('chat-sidebar-overlay').classList.toggle('show');
        }

        // --- 初始化 ---
        loadUsage();
        loadProviderCards();
        loadConfig();
        loadChatModelSelector();
        loadSessionList().then(() => {
            const savedId = localStorage.getItem('mp_current_session');
            if (savedId) switchSession(savedId).catch(() => {});
        });
        setInterval(loadUsage, 30000);

        // 从 URL hash 恢复 tab 状态
        (function restoreTab() {
            const hash = location.hash.replace('#', '');
            if (hash) {
                const tabs = document.querySelectorAll('.tab');
                for (const tab of tabs) {
                    const onclick = tab.getAttribute('onclick') || '';
                    if (onclick.includes("'" + hash + "'")) {
                        switchTab(hash, tab);
                        break;
                    }
                }
            }
        })();
    </script>
</body>
</html>
"""
