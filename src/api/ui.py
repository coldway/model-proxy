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

        /* 响应式 */
        @media (max-width: 768px) {
            .container { padding: 12px; }
            .tabs { overflow-x: auto; flex-wrap: nowrap; -webkit-overflow-scrolling: touch; }
            .stats-grid, .discovery-grid, .config-grid { grid-template-columns: 1fr; }
            header { flex-direction: column; gap: 10px; align-items: flex-start; }
            .modal { width: 96%; padding: 18px; }
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

        <!-- 问答聊天 -->
        <div id="panel-chat" class="panel">
            <div class="card" style="display:flex;flex-direction:column;height:calc(100vh - 220px);min-height:400px">
                <div class="card-header" style="flex-shrink:0">
                    <h3>问答聊天</h3>
                    <div style="display:flex;gap:8px;align-items:center">
                        <select id="chat-model" style="width:auto;min-width:180px">
                            <option value="auto">🤖 自动选择模型</option>
                        </select>
                        <button class="btn btn-ghost btn-sm" onclick="clearChat()">清空</button>
                    </div>
                </div>
                <div id="chat-messages" style="flex:1;overflow-y:auto;padding:16px 0;display:flex;flex-direction:column;gap:12px">
                    <div style="text-align:center;color:var(--text-muted);padding:40px 0">
                        <p>选择模型或使用自动模式，开始对话</p>
                        <p style="font-size:0.8rem;margin-top:8px">自动模式会按厂商优先级 → 模型优先级选择可用模型</p>
                    </div>
                </div>
                <div style="flex-shrink:0;border-top:1px solid var(--border);padding-top:16px;display:flex;gap:8px">
                    <input id="chat-input" placeholder="输入消息..." style="flex:1" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendChat()}">
                    <button class="btn btn-primary" onclick="sendChat()" id="chat-send-btn">发送</button>
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
                <div id="providers-grid" class="stats-grid" style="grid-template-columns: repeat(auto-fill, minmax(270px, 1fr))"
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

                grid.innerHTML = data.stats.map(s => {
                    const pct = s.rpd_limit > 0 ? (s.today_requests / s.rpd_limit * 100) : 0;
                    const color = pct > 80 ? 'red' : pct > 50 ? 'yellow' : 'green';
                    const statusClass = s.available ? 'available' : 'unavailable';
                    const statusText = s.available ? '● 可用' : '● 不可用';
                    return `<div class="stat-card">
                        <div class="model-header">
                            <span class="model-name">${s.model}</span>
                            <span class="provider-tag">${s.provider}</span>
                        </div>
                        <span class="availability ${statusClass}">${statusText}</span>
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

        async function loadProviderModels(providerId) {
            const resp = await fetch(API + `/api/catalog/provider/${providerId}/models`);
            const data = await resp.json();
            const allModels = data.models || [];
            const container = document.getElementById('provider-models-list');

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
                return `<div class="model-item" draggable="${isEnabled}" data-model-id="${m.id}" style="display:flex;align-items:center;justify-content:space-between;padding:12px;background:var(--bg-primary);border-radius:8px;margin-bottom:8px;border:1px solid ${borderColor};opacity:${opacity};${isEnabled ? 'cursor:grab' : ''}">
                    <div style="display:flex;align-items:center;gap:12px">
                        <span class="priority-badge">${isEnabled ? (m.priority || 99) : '-'}</span>
                        <div>
                            <div style="font-weight:600;font-size:0.9rem">${m.name || m.id}</div>
                            <div style="font-size:0.75rem;color:var(--text-muted)">RPD: ${m.default_rpd || '∞'} | RPM: ${m.default_rpm || '∞'}</div>
                        </div>
                    </div>
                    <div style="display:flex;align-items:center;gap:8px">
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
            container.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-muted)">正在拉取厂商远程模型列表...</div>';
            try {
                const resp = await fetch(API + `/api/provider/${currentProvider}/models`);
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    container.innerHTML = '';
                    toast(err.detail || '拉取失败：厂商未注册或 API Key 未配置', 'error');
                    return;
                }
                const data = await resp.json();
                const container = document.getElementById('provider-search-results');

                const catalogResp = await fetch(API + `/api/catalog/provider/${currentProvider}/models`);
                const catalogData = await catalogResp.json();
                const catalogMap = {};
                (catalogData.models || []).forEach(m => { catalogMap[m.id] = m; });

                const remoteModels = data.available_models || [];
                if (remoteModels.length === 0) {
                    container.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-muted)">厂商未返回模型列表</div>';
                    return;
                }

                container.innerHTML = `<div style="margin-bottom:8px;font-size:0.8rem;color:var(--text-secondary)">厂商共 ${remoteModels.length} 个模型：</div>` +
                    remoteModels.map(modelId => {
                        const inCatalog = catalogMap[modelId];
                        const isActive = inCatalog && inCatalog.enabled;
                        let actionHtml;
                        if (isActive) {
                            actionHtml = '<span style="font-size:0.75rem;color:var(--accent-green)">✓ 已启用</span>';
                        } else if (inCatalog) {
                            actionHtml = `<button class="btn btn-primary btn-sm" onclick="activateFromCatalog('${currentProvider}','${modelId}')">启用</button>`;
                        } else {
                            actionHtml = `<button class="btn btn-sm btn-secondary" onclick="pullModelToCatalog('${currentProvider}','${modelId}')">拉取到目录</button>`;
                        }
                        return `<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:var(--bg-primary);border-radius:6px;margin-bottom:4px;border:1px solid var(--border)">
                            <span style="font-size:0.85rem">${modelId}</span>
                            ${actionHtml}
                        </div>`;
                    }).join('');
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

        // --- 聊天功能 ---
        let chatHistory = [];

        async function loadChatModelSelector() {
            const resp = await fetch(API + '/v1/models');
            const data = await resp.json();
            const select = document.getElementById('chat-model');
            const currentVal = select.value;
            select.innerHTML = '<option value="auto">🤖 自动选择模型</option>';
            data.models.filter(m => m.enabled).forEach(m => {
                select.innerHTML += `<option value="${m.id}">[${m.provider}] ${m.id}</option>`;
            });
            select.value = currentVal || 'auto';
        }

        function clearChat() {
            chatHistory = [];
            document.getElementById('chat-messages').innerHTML = `
                <div style="text-align:center;color:var(--text-muted);padding:40px 0">
                    <p>选择模型或使用自动模式，开始对话</p>
                    <p style="font-size:0.8rem;margin-top:8px">自动模式会按厂商优先级 → 模型优先级选择可用模型</p>
                </div>`;
        }

        function appendMessage(role, content, model) {
            const container = document.getElementById('chat-messages');
            // 移除空状态提示
            const empty = container.querySelector('[style*="text-align:center"]');
            if (empty) empty.remove();

            const isUser = role === 'user';
            const bgColor = isUser ? 'var(--accent-blue)' : 'var(--bg-primary)';
            const textColor = isUser ? '#fff' : 'var(--text-primary)';
            const align = isUser ? 'flex-end' : 'flex-start';
            const modelTag = !isUser && model ? `<div style="font-size:0.7rem;color:var(--text-muted);margin-top:4px">${model}</div>` : '';

            container.innerHTML += `
                <div style="display:flex;justify-content:${align}">
                    <div style="max-width:80%;padding:12px 16px;border-radius:12px;background:${bgColor};color:${textColor};border:1px solid var(--border);white-space:pre-wrap;word-break:break-word">
                        ${escapeHtml(content)}${modelTag}
                    </div>
                </div>`;
            container.scrollTop = container.scrollHeight;
        }

        function escapeHtml(text) {
            return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        }

        async function sendChat() {
            const input = document.getElementById('chat-input');
            const msg = input.value.trim();
            if (!msg) return;

            const model = document.getElementById('chat-model').value;
            input.value = '';

            chatHistory.push({role: 'user', content: msg});
            appendMessage('user', msg);

            // 显示加载中
            const btn = document.getElementById('chat-send-btn');
            btn.disabled = true;
            btn.textContent = '思考中...';

            try {
                const resp = await fetch(API + '/v1/chat/completions', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        model: model,
                        messages: chatHistory,
                        temperature: 0.7,
                    }),
                });

                if (!resp.ok) {
                    const err = await resp.json();
                    appendMessage('assistant', `❌ 错误: ${err.detail || '请求失败'}`, null);
                    return;
                }

                const data = await resp.json();
                const reply = data.choices[0].message.content;
                const usedModel = data.model;
                chatHistory.push({role: 'assistant', content: reply});
                appendMessage('assistant', reply, usedModel);
            } catch (e) {
                appendMessage('assistant', `❌ 网络错误: ${e.message}`, null);
            } finally {
                btn.disabled = false;
                btn.textContent = '发送';
            }
        }

        // --- 初始化 ---
        loadUsage();
        loadProviderCards();
        loadConfig();
        loadChatModelSelector();
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
