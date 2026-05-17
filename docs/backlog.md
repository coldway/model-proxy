# model-proxy 优化 Backlog

> 扫描日期：2026-05-17 | 状态：P0 = 阻塞级 / P1 = 重要 / P2 = 改善

## P0（阻塞级）

| 编号 | 名称 | 涉及文件 | 描述 |
|------|------|---------|------|
| MP-01 | 流式 SSE 丢失 tool_calls/reasoning delta | `openai_compat.py`, `streaming.py` | 流式仅传 `delta.content`，丢弃 `tool_calls` 等字段 |
| MP-02 | 公网 /v1/* 无认证无限流 | `main.py`, `routes.py` | admin_token 开启时仍允许匿名访问 OpenAI 兼容 API（纯本机可降 P2） |

## P1（重要）

| 编号 | 名称 | 涉及文件 | 描述 |
|------|------|---------|------|
| MP-03 | 流式连接即扣减 RPD/RPM | `dispatcher.py` | 连接失败仍计数，与非流式不对称 |
| MP-04 | per-model timeout 未接入调度 | `dispatcher.py`, `schemas.py` | Schema 有 timeout 字段但调度器固定 60s |
| MP-05 | RateLimiter 无锁竞态 | `rate_limiter.py` | check-then-act 非原子，高并发下计数漂移 |
| MP-06 | 缺 /health + /ready 探针 | `routes.py`, `main.py` | 无标准 K8s 式健康检查端点 |
| MP-07 | RequestHistory 窗口过小 | `history.py`, `routes.py` | 仅 500 条，流式指标不完整 |
| MP-08 | 流式异常落盘残缺回复 | `routes.py` | 中途异常仍 strip_thinking + save |
| MP-09 | SSE 错误帧格式非标准 | `streaming.py` | `finish_reason: "error"` 与 OpenAI 规范不完全一致 |

## P2（改善）

| 编号 | 名称 | 涉及文件 | 描述 |
|------|------|---------|------|
| MP-10 | 路由缓存哈希忽略多模态 | `dispatcher.py` | 大图/长 tools 下缓存不准 |
| MP-11 | 无 CORS 中间件 | `main.py` | 跨域需依赖反向代理 |
| MP-12 | catalog vs capabilities 双轨一致性 | `config/` | 能力缓存与目录可能不同步 |
| MP-13 | routes.py/dispatcher.py 过长 | 多文件 | 重复构建 ModelConfig |
| MP-14 | 管理 UI 单文件过大 | `ui.html` | 无模块化/构建流程 |
| MP-15 | SessionManager 无并发锁 | `session.py` | 多 worker 下写文件冲突 |
| MP-16 | DEBUG 日志含完整请求体 | `dispatcher.py` | 隐私合规风险 |
| MP-17 | 熔断器粒度为厂商非模型 | `circuit_breaker.py` | 单模型抖动导致整厂商熔断 |
| MP-18 | PayloadTracker 413 上限估算偏差 | `payload_tracker.py` | 与真实网关判定可能不一致 |
