# UI 管理面板

内嵌单文件 HTML 实现（`src/api/ui.py`），访问 `/ui` 即可使用。

## 功能

| Tab | 功能 |
|-----|------|
| 使用量 | 各模型 RPD/RPM 用量进度、可用状态（30s 自动刷新） |
| 模型管理 | 厂商卡片网格、拖拽排序优先级、启用/禁用模型、搜索添加模型 |
| 聊天 | 内置对话界面，支持流式输出、选择模型/Cursor 模式 |
| 图像/视频 | 调用 Agnes AI 图像/视频生成 API |
| API Key | 配置各厂商密钥 |

## 技术要点

- 深色主题，响应式，纯 JS 无构建
- Tab 状态通过 `location.hash` 持久化
- 厂商优先级拖拽使用 HTML5 DnD API
- Cursor Agent 模式选择器（agent/plan/ask）仅在选择 Cursor 模型时显示
