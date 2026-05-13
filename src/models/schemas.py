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
    CURSOR = "cursor"


class RateLimit(BaseModel):
    rpd: int = Field(description="每日请求数上限")
    rpm: int = Field(default=0, description="每分钟请求数上限")


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
    route_cache_ttl: int = Field(default=600, description="路由缓存有效期（秒）")
    breaker_threshold: int = Field(default=3, description="连续失败 N 次触发厂商熔断")
    breaker_cooldown: int = Field(default=300, description="熔断冷却时间（秒）")
    max_context_tokens: int = Field(default=8000, description="会话上下文最大 token 数")
    max_sessions: int = Field(default=50, description="最大会话数")
    probe_interval: int = Field(default=5, description="能力探测间隔（秒）")


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


class ChatCompletionRequest(BaseModel):
    model: str = "auto"
    messages: list[ChatMessage] = Field(..., min_length=1)
    temperature: float = 0.7
    max_tokens: int | None = None
    stream: bool = False
    tools: list[ToolDefinition] | None = None
    tool_choice: str | dict | None = None


class Choice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: UsageInfo = Field(default_factory=UsageInfo)


class ModelInfo(BaseModel):
    id: str
    provider: str
    enabled: bool
    priority: int
    rate_limit: RateLimit | None = None
    tool_calling: bool = False


class ModelListResponse(BaseModel):
    models: list[ModelInfo]


class UsageStats(BaseModel):
    provider: str
    model: str
    today_requests: int = 0
    minute_requests: int = 0
    rpd_limit: int = 0
    rpm_limit: int = 0
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
