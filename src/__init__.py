# Created by model-proxy on 2026/05/22
# Copyright © 2026


class LogTag:
    """统一日志前缀常量，新增代码应引用这些常量而非硬编码字符串"""
    API = "[API]"
    INFER = "[推理]"
    STREAM = "[流式]"
    ROUTE = "[路由]"
    SESSION = "[会话]"
    COMPACT = "[Compact]"
    CONTEXT = "[Context]"
    MEMORY = "[Memory]"
    SLOW = "[慢请求]"
    BREAKER = "[熔断]"
    RATE_LIMIT = "[限流]"
    COST = "[成本]"
