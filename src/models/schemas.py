# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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


class AppConfig(BaseModel):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    settings: AppSettings = Field(default_factory=AppSettings)


# --- API 请求/响应模型 ---

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "auto"
    messages: list[ChatMessage]
    temperature: float = 0.7
    max_tokens: int | None = None
    stream: bool = False


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
