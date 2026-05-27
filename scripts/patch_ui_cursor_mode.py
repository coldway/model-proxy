#!/usr/bin/env python3
# Created by AI on 2026/05/27
# Copyright © 2026

"""自动为 UI 添加 Cursor Mode 选择器的补丁脚本"""

import re
import sys
from pathlib import Path

def patch_ui_file(ui_file_path: Path, backup: bool = True):
    """为 UI HTML 文件添加 Cursor Mode 选择器
    
    Args:
        ui_file_path: UI HTML 文件路径
        backup: 是否备份原文件
    """
    if not ui_file_path.exists():
        print(f"❌ 文件不存在: {ui_file_path}")
        return False
    
    print(f"📖 读取文件: {ui_file_path}")
    content = ui_file_path.read_text(encoding='utf-8')
    
    # 备份
    if backup:
        backup_path = ui_file_path.with_suffix('.html.bak')
        backup_path.write_text(content, encoding='utf-8')
        print(f"💾 已备份到: {backup_path}")
    
    # 1. 添加 CSS 样式(在 </style> 之前)
    css_addition = """
        /* Cursor Mode 选择器样式 */
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
"""
    
    if 'cursor-mode-btn' not in content:
        content = content.replace('</style>', css_addition + '\n    </style>')
        print("✅ 添加 CSS 样式")
    else:
        print("⚠️  CSS 样式已存在,跳过")
    
    # 2. 添加 Mode 选择器 HTML(在聊天输入框之前)
    mode_selector_html = """
                    <!-- Cursor Mode 选择器 -->
                    <div style="padding:12px;border-top:1px solid var(--border);display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:var(--bg-tertiary)">
                        <label style="font-size:0.8rem;font-weight:500;color:var(--text-secondary)">Cursor 模式:</label>
                        <div style="display:flex;gap:6px" id="cursor-mode-group">
                            <button class="btn btn-sm cursor-mode-btn active" data-mode="agent" onclick="selectCursorMode('agent')" style="background:var(--accent-blue);color:#fff">
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
"""
    
    # 查找聊天输入框位置
    input_pattern = r'(<div style="flex-shrink:0;border-top:1px solid var\(--border\);padding-top:16px;display:flex;gap:8px">)'
    
    if 'cursor-mode-group' not in content:
        content = re.sub(input_pattern, mode_selector_html + r'\1', content)
        print("✅ 添加 Mode 选择器 HTML")
    else:
        print("⚠️  Mode 选择器 HTML 已存在,跳过")
    
    # 3. 添加 JavaScript 函数
    js_addition = """
        // Cursor Mode 管理
        let currentCursorMode = 'agent';

        function selectCursorMode(mode) {
            currentCursorMode = mode;
            document.querySelectorAll('.cursor-mode-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            const selectedBtn = document.querySelector(`.cursor-mode-btn[data-mode="${mode}"]`);
            if (selectedBtn) selectedBtn.classList.add('active');
            
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

        // 初始化 Cursor Mode
        window.addEventListener('DOMContentLoaded', () => {
            selectCursorMode('agent');
        });
"""
    
    if 'currentCursorMode' not in content:
        # 在 sendChat 函数之前插入
        content = content.replace('async function sendChat() {', js_addition + '\n        async function sendChat() {')
        print("✅ 添加 JavaScript 函数")
    else:
        print("⚠️  JavaScript 函数已存在,跳过")
    
    # 4. 修改 sendChat 函数,添加 mode、force、sandbox 参数
    # 查找请求体构造部分
    original_body_pattern = r'(body: JSON\.stringify\(\{\s*model: model,\s*messages: \[\{role: [\'"]user[\'"'], content: msg\}\],\s*temperature: 0\.7,)\s*\}\),'
    
    if 'mode: currentCursorMode' not in content:
        replacement_body = r"""\1
                        mode: currentCursorMode,
                        force: document.getElementById('cursor-force')?.checked || false,
                        sandbox: document.getElementById('cursor-sandbox')?.value || undefined,
                    }),"""
        content = re.sub(original_body_pattern, replacement_body, content)
        print("✅ 修改 sendChat 函数请求体")
    else:
        print("⚠️  sendChat 函数已包含 mode 参数,跳过")
    
    # 写入文件
    ui_file_path.write_text(content, encoding='utf-8')
    print(f"💾 已写入: {ui_file_path}")
    
    return True

def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║  UI Cursor Mode 补丁脚本                                       ║
║  自动为 model-proxy UI 添加 Cursor Agent 模式选择器           ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    # 检测项目根目录
    script_dir = Path(__file__).parent.parent
    ui_file = script_dir / 'src' / 'api' / 'static' / 'ui.html'
    
    if not ui_file.exists():
        print(f"❌ UI 文件不存在: {ui_file}")
        print("请在 model-proxy 项目根目录运行此脚本")
        return 1
    
    print(f"📂 项目根目录: {script_dir}")
    print(f"📄 UI 文件: {ui_file}")
    
    confirm = input("\n是否继续应用补丁？(y/n): ")
    if confirm.lower() != 'y':
        print("❌ 已取消")
        return 0
    
    if patch_ui_file(ui_file):
        print("\n🎉 补丁应用成功！")
        print("\n下一步:")
        print("1. 重启 model-proxy 服务: python main.py")
        print("2. 访问 http://127.0.0.1:8000/ui")
        print("3. 切换到 Chat 标签测试 Cursor Mode 选择器")
        return 0
    else:
        print("\n❌ 补丁应用失败")
        return 1

if __name__ == '__main__':
    sys.exit(main())
