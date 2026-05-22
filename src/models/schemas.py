# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ProviderName(str, Enum):
    GOOGLE = "google"
    GROQ = "groq"
    GITHUB = "github"
    OLLAMA = "ollama"
    CURSOR = "cursor"


class RateLimit(BaseModel):
    rpd: int = Field(description="每日请求数上限")
    rpm: int = Field(default=0, description="每分钟请求数上限")
    tpm: int = Field(default=0, description="每分钟 token 数上限")
    tpd: int = Field(default=0, description="每日 token 数上限")


class ModelConfig(BaseModel):
    name: str
    enabled: bool = True
    priority: int = 1
    rate_limit: RateLimit | None = None
    tool_calling: bool = False
    timeout: int = Field(default=60, description="请求超时时间（秒），延迟高的模型可设置更长")


class ProviderConfig(BaseModel):
    api_key: str = ""
    enabled: bool = False
    priority: int = Field(default=99, description="厂商优先级，数字越小越优先")
    models: list[ModelConfig] = Field(default_factory=list)


class AppSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    default_provider: str = "google"
    auto_switch: bool = True
    log_level: str = "info"
    admin_token: str = Field(default="", description="管理面板认证令牌，为空则不启用认证")
    api_token: str = Field(default="", description="OpenAI 兼容 API (/v1/*) 认证令牌，为空则不启用；客户端通过 api_key 传入")
    route_cache_ttl: int = Field(default=600, description="路由缓存有效期（秒）")
    breaker_threshold: int = Field(default=3, description="连续失败 N 次触发厂商熔断")
    breaker_cooldown: int = Field(default=300, description="熔断冷却时间（秒）")
    max_context_tokens: int = Field(default=8000, description="会话上下文最大 token 数")
    max_sessions: int = Field(default=50, description="最大会话数")
    probe_interval: int = Field(default=5, description="能力探测间隔（秒）")
    request_history_max_records: int = Field(
        default=2000,
        description="内存中保留的推理请求历史条数上限（影响 /api/history 与运维统计窗口）",
    )
    cors_origins: str = Field(
        default="",
        description="CORS 允许的 Origin，逗号分隔；空表示不启用跨域（仅同源）；* 表示允许任意来源",
    )
    session_bind_ttl: int = Field(
        default=3600,
        description="会话模型绑定的 TTL（秒），过期后绑定自动失效",
    )
    per_consumer_rpm: int = Field(
        default=0,
        description="单个 API consumer（按 Bearer token 区分）的每分钟请求数上限，0=不限制",
    )


class AppConfig(BaseModel):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    settings: AppSettings = Field(default_factory=AppSettings)


# --- API 请求/响应模型 ---

class FunctionCall(BaseModel):
    """函数调用信息"""
    name: str
    arguments: str


class ToolCall(BaseModel):
    """工具调用（OpenAI tool_calls 格式）"""
    id: str
    type: str = "function"
    function: FunctionCall


class ToolFunction(BaseModel):
    """Tool definition: function schema"""
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    """Tool definition（OpenAI tools 格式）"""
    type: str = "function"
    function: ToolFunction


class ChatMessage(BaseModel):
    role: str
    content: str | list | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, v):
        if isinstance(v, list):
            for part in v:
                if not isinstance(part, dict):
                    raise ValueError(f"content list 中的每个元素必须是 dict，实际为 {type(part).__name__}")
                if "type" not in part:
                    raise ValueError("content list 中的 dict 必须包含 'type' 字段")
                if part["type"] not in ("text", "image_url"):
                    raise ValueError(f"不支持的 content part 类型: {part['type']}")
        return v


class ResponseFormat(BaseModel):
    """结构化输出格式控制（兼容 OpenAI response_format）"""
    type: str = Field(
        default="text",
        description="输出格式：text（默认）、json_object（强制 JSON）、json_schema（严格 schema）",
    )
    json_schema: dict[str, Any] | None = Field(
        default=None,
        description="当 type=json_schema 时，指定 JSON Schema 定义",
    )


class ChatCompletionRequest(BaseModel):
    model: str = "auto"
    messages: list[ChatMessage] = Field(..., min_length=1)
    temperature: float = 0.7
    max_tokens: int | None = None
    stream: bool = False
    tools: list[ToolDefinition] | None = None
    tool_choice: str | dict | None = None
    response_format: ResponseFormat | None = Field(
        default=None,
        description="输出格式控制：{type: 'json_object'} 强制 JSON 输出，{type: 'json_schema', json_schema: {...}} 严格 schema 约束",
    )
    session_id: str | None = Field(
        default=None,
        description="会话标识：首次请求成功后自动绑定模型，后续携带相同 session_id 的请求将路由到同一模型",
    )


class Choice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ProxyInfo(BaseModel):
    """model-proxy 附加路由信息，帮助上游服务追踪实际调用详情"""
    provider: str = Field(description="实际处理请求的厂商 ID")
    trace_id: str = Field(default="", description="请求追踪 ID，用于日志关联")
    latency_ms: float = Field(default=0, description="端到端推理耗时（毫秒）")
    route_strategy: str = Field(default="", description="路由策略：direct / priority / round_robin / fallback")
    session_id: str | None = Field(default=None, description="会话绑定 ID（传入 session_id 时返回，确认绑定关系）")
    bound_model: str | None = Field(default=None, description="当前 session_id 绑定的模型（仅会话绑定时返回）")


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: UsageInfo = Field(default_factory=UsageInfo)
    proxy_info: ProxyInfo | None = Field(default=None, description="model-proxy 路由元数据")


class ModelCapabilities(BaseModel):
    """已探测的模型能力"""
    streaming: bool = False
    reasoning: bool = False
    multi_turn_tc: bool = False
    chinese: bool = False
    vision: bool = False
    json_mode: bool = False
    latency_ms: float = Field(default=99999, description="探测延迟（毫秒）")


class ModelInfo(BaseModel):
    id: str
    provider: str
    enabled: bool
    priority: int
    rate_limit: RateLimit | None = None
    tool_calling: bool = False
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)


class ModelListResponse(BaseModel):
    models: list[ModelInfo]


class UsageStats(BaseModel):
    provider: str
    model: str
    today_requests: int = 0
    minute_requests: int = 0
    today_tokens: int = 0
    minute_tokens: int = 0
    rpd_limit: int = 0
    rpm_limit: int = 0
    tpm_limit: int = 0
    tpd_limit: int = 0
    available: bool = True


class UsageResponse(BaseModel):
    stats: list[UsageStats]


class ProviderDiscovery(BaseModel):
    """可发现的免费模型厂商信息"""
    name: str
    url: str
    description: str
    free_models: list[str]
    integration_guide: str
    new_user_only: bool = False


class ProviderSummary(BaseModel):
    """厂商摘要信息"""
    id: str
    enabled: bool
    priority: int
    model_count: int = Field(description="已启用的模型数量")
    total_models: int = Field(description="目录中全部模型数量")
    has_api_key: bool = Field(description="是否已配置 API Key")


class ProviderListResponse(BaseModel):
    providers: list[ProviderSummary]


class ModelDetail(BaseModel):
    """模型详情（含能力信息）"""
    id: str
    provider: str
    enabled: bool
    priority: int
    tool_calling: bool = False
    rate_limit: RateLimit | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict, description="已检测的能力信息")


class ProviderModelsResponse(BaseModel):
    provider: str
    models: list[ModelDetail]
