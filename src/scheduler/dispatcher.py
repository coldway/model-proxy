# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.models.schemas import ChatCompletionRequest, ChatCompletionResponse, ModelConfig
from src.scheduler.rate_limiter import RateLimiter

if TYPE_CHECKING:
    from src.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class Dispatcher:
    """模型调度器：根据优先级和可用性选择模型并转发请求"""

    def __init__(self, rate_limiter: RateLimiter):
        self._rate_limiter = rate_limiter
        self._providers: dict[str, "BaseProvider"] = {}

    def register_provider(self, name: str, provider: "BaseProvider") -> None:
        self._providers[name] = provider

    def unregister_provider(self, name: str) -> bool:
        """注销厂商，返回是否成功"""
        return self._providers.pop(name, None) is not None

    def has_provider(self, name: str) -> bool:
        return name in self._providers

    async def dispatch(
        self,
        request: ChatCompletionRequest,
        enabled_models: list[tuple[str, ModelConfig]],
    ) -> ChatCompletionResponse:
        """
        调度请求到可用模型。
        - model="auto" 时按优先级自动选择
        - 指定模型名时尝试直接路由
        - 失败时自动切换到下一优先级模型
        """
        if request.model != "auto":
            return await self._dispatch_specific(request, enabled_models)

        return await self._dispatch_auto(request, enabled_models)

    async def _dispatch_specific(
        self,
        request: ChatCompletionRequest,
        enabled_models: list[tuple[str, ModelConfig]],
    ) -> ChatCompletionResponse:
        """路由到指定模型"""
        for provider_name, model_cfg in enabled_models:
            if model_cfg.name == request.model:
                rpd = model_cfg.rate_limit.rpd if model_cfg.rate_limit else 0
                rpm = model_cfg.rate_limit.rpm if model_cfg.rate_limit else 0

                if not self._rate_limiter.can_request(provider_name, model_cfg.name, rpd, rpm):
                    raise RateLimitExceeded(
                        f"模型 {model_cfg.name} 已达速率限制，请稍后重试或切换模型"
                    )

                return await self._call_provider(provider_name, model_cfg.name, request)

        raise ModelNotFound(f"模型 {request.model} 未找到或未启用")

    async def _dispatch_auto(
        self,
        request: ChatCompletionRequest,
        enabled_models: list[tuple[str, ModelConfig]],
    ) -> ChatCompletionResponse:
        """按优先级自动选择可用模型"""
        errors: list[str] = []

        for provider_name, model_cfg in enabled_models:
            rpd = model_cfg.rate_limit.rpd if model_cfg.rate_limit else 0
            rpm = model_cfg.rate_limit.rpm if model_cfg.rate_limit else 0

            if not self._rate_limiter.can_request(provider_name, model_cfg.name, rpd, rpm):
                errors.append(f"{provider_name}:{model_cfg.name} 速率受限")
                continue

            try:
                return await self._call_provider(provider_name, model_cfg.name, request)
            except ProviderCallError as e:
                errors.append(f"{provider_name}:{model_cfg.name} 调用失败: {e}")
                if self._rate_limiter.is_exhausted(provider_name, model_cfg.name, rpd):
                    logger.warning(f"{provider_name}:{model_cfg.name} 额度可能用尽，切换下一模型")
                continue

        raise AllModelsUnavailable(
            f"所有模型均不可用: {'; '.join(errors)}"
        )

    async def _call_provider(
        self,
        provider_name: str,
        model_name: str,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        """调用具体厂商"""
        provider = self._providers.get(provider_name)
        if not provider:
            raise ProviderCallError(f"厂商 {provider_name} 未注册")

        self._rate_limiter.record_request(provider_name, model_name)
        try:
            return await provider.chat_completion(model_name, request)
        except Exception as e:
            logger.error(f"调用 {provider_name}:{model_name} 异常: {e}")
            raise ProviderCallError(str(e)) from e


class DispatchError(Exception):
    pass


class RateLimitExceeded(DispatchError):
    pass


class ModelNotFound(DispatchError):
    pass


class AllModelsUnavailable(DispatchError):
    pass


class ProviderCallError(DispatchError):
    pass
