# model-proxy 优化 Backlog

> 扫描日期：2026-05-17 | 状态：P0 = 阻塞级 / P1 = 重要 / P2 = 改善

## 全部已完成 ✅

### P0（阻塞级）

| 编号 | 名称 | 涉及文件 | 状态 |
|------|------|---------|------|
| MP-01 | 流式 SSE 保留完整 delta dict | `openai_compat.py`, `github.py`, `groq.py`, `google.py`, `huggingface.py`, `base.py`, `streaming.py`, `dispatcher.py` | ✅ 已完成 |
| MP-02 | /v1/* API Token 认证 | `main.py`, `schemas.py` | ✅ 已完成 |

### P1（重要）

| 编号 | 名称 | 涉及文件 | 状态 |
|------|------|---------|------|
| MP-03 | 流式配额记账时点修正 | `dispatcher.py` | ✅ 已完成 |
| MP-04 | per-model 首包超时 | `dispatcher.py` | ✅ 已完成 |
| MP-05 | RateLimiter 原子操作 | `rate_limiter.py`, `dispatcher.py` | ✅ 已完成 |
| MP-06 | /health + /ready 探针 | `routes.py`, `main.py` | ✅ 已完成 |
| MP-07 | RequestHistory 容量可配置 | `schemas.py`, `history.py`, `routes.py` | ✅ 已完成 |
| MP-08 | 流式异常不存残缺回复 | `routes.py` | ✅ 已完成 |
| MP-09 | SSE 错误帧 OpenAI 规范化 | `streaming.py` | ✅ 已完成 |

### P2（改善）

| 编号 | 名称 | 涉及文件 | 状态 |
|------|------|---------|------|
| MP-10 | 路由缓存哈希含多模态/tools | `dispatcher.py` | ✅ 已完成 |
| MP-11 | CORS 中间件可配置 | `main.py`, `schemas.py` | ✅ 已完成 |
| MP-12 | catalog/capabilities 合并一致 | `capability_tester.py`, `catalog.py`, `dispatcher.py` | ✅ 已完成 |
| MP-13 | routes/dispatcher region 分段 | `routes.py`, `dispatcher.py` | ✅ 已完成 |
| MP-14 | 管理 UI 注释分区 | `ui.html` | ✅ 已完成 |
| MP-15 | SessionManager 并发锁 | `session.py` | ✅ 已完成 |
| MP-16 | DEBUG 请求体脱敏 | `dispatcher.py` | ✅ 已完成 |
| MP-17 | 熔断粒度到模型级 | `circuit_breaker.py`, `dispatcher.py` | ✅ 已完成 |
| MP-18 | Payload 估算安全余量 | `payload_tracker.py` | ✅ 已完成 |
