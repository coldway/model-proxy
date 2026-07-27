# 更新日志

## 主要里程碑

### 2026-07 (当前)
- 新增 Docker 部署支持（Dockerfile + deploy.sh）
- 新增 Caddy 反向代理配置

### 2026-06
- **讯飞星火 (Spark) Provider**: 接入讯飞 MaaS 平台，支持 DeepSeek V4 Pro/Flash、Qwen 3.5/3.6、Spark X2 等模型
- **阿里百炼 (DashScope) Provider**: 通义千问全系列
- **Ollama 本地模型**: 自动发现已安装模型，支持一键下载
- **聊天内置工具**: 自然语言管理模型（添加/搜索/查询）
- **Agent Memory 系统**: L1 会话记忆 + L2 长期记忆

### 2026-05
- **初始发布**: 13 家免费厂商适配
- **智能调度**: 厂商优先级 + 模型优先级 + 滑动窗口限速
- **Web 管理面板**: 使用量监控、模型管理、聊天、API Key 配置
- **流式输出**: 全厂商 SSE 支持
- **Cursor Agent CLI 集成**: plan/ask/agent 三种模式
- **图像/视频生成**: Agnes AI 图像和视频 API
- **请求历史**: JSONL 持久化 + 统计接口

## 技术改进摘要

- 多轮线程安全审计（Memory、Session、Dispatcher、Cache 全面加锁）
- 原子文件写入（防崩溃数据损坏）
- 熔断器优化（半开冷却修正、自动恢复）
- 流式路由策略补全
- Google API Key 从 URL params 移至 Header
