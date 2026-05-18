"""Dispatcher 单元测试"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.models.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ModelConfig,
    RateLimit,
    UsageInfo,
)
from src.scheduler.dispatcher import (
    AllModelsUnavailable,
    Dispatcher,
    ModelNotFound,
    ProviderCallError,
    RateLimitExceeded,
)
from src.scheduler.rate_limiter import RateLimiter


def _make_response(model: str = "test-model", content: str = "hello") -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-test",
        created=int(time.time()),
        model=model,
        choices=[Choice(message=ChatMessage(role="assistant", content=content))],
        usage=UsageInfo(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _make_request(model: str = "auto", msg: str = "hi") -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content=msg)],
    )


def _make_model_cfg(name: str = "test-model", priority: int = 1, rpd: int = 100) -> ModelConfig:
    return ModelConfig(name=name, priority=priority, rate_limit=RateLimit(rpd=rpd))


@pytest.fixture()
def dispatcher():
    rl = RateLimiter(persist=False)
    return Dispatcher(rl)


class TestDispatchSpecific:
    @pytest.mark.asyncio
    async def test_routes_to_specified_model(self, dispatcher: Dispatcher):
        provider = AsyncMock()
        provider.chat_completion = AsyncMock(return_value=_make_response("gemini-2.0-flash"))
        dispatcher.register_provider("google", provider)

        enabled = [("google", _make_model_cfg("gemini-2.0-flash"))]
        prov, model, result = await dispatcher.dispatch(
            _make_request("gemini-2.0-flash"), enabled,
        )
        assert prov == "google"
        assert model == "gemini-2.0-flash"
        assert result.choices[0].message.content == "hello"

    @pytest.mark.asyncio
    async def test_raises_model_not_found(self, dispatcher: Dispatcher):
        enabled = [("google", _make_model_cfg("gemini-2.0-flash"))]
        with pytest.raises(ModelNotFound):
            await dispatcher.dispatch(_make_request("nonexistent"), enabled)

    @pytest.mark.asyncio
    async def test_raises_rate_limit(self, dispatcher: Dispatcher):
        provider = AsyncMock()
        dispatcher.register_provider("google", provider)

        cfg = _make_model_cfg("gemini-2.0-flash", rpd=1)
        enabled = [("google", cfg)]

        dispatcher._rate_limiter.record_request("google", "gemini-2.0-flash")
        with pytest.raises(RateLimitExceeded):
            await dispatcher.dispatch(_make_request("gemini-2.0-flash"), enabled)


class TestDispatchAuto:
    @pytest.mark.asyncio
    async def test_auto_selects_available(self, dispatcher: Dispatcher):
        provider = AsyncMock()
        provider.chat_completion = AsyncMock(return_value=_make_response("model-a"))
        dispatcher.register_provider("prov_a", provider)

        enabled = [("prov_a", _make_model_cfg("model-a"))]
        prov, model, _ = await dispatcher.dispatch(_make_request(), enabled)
        assert prov == "prov_a"
        assert model == "model-a"

    @pytest.mark.asyncio
    async def test_auto_raises_when_all_exhausted(self, dispatcher: Dispatcher):
        cfg = _make_model_cfg("model-a", rpd=1)
        dispatcher._rate_limiter.record_request("prov_a", "model-a")
        with pytest.raises(AllModelsUnavailable):
            await dispatcher.dispatch(_make_request(), [("prov_a", cfg)])


class TestAutoSwitchOnFailure:
    @pytest.mark.asyncio
    async def test_falls_back_to_next_provider_on_failure(self, dispatcher: Dispatcher):
        fail_provider = AsyncMock()
        fail_provider.chat_completion = AsyncMock(side_effect=RuntimeError("模拟调用失败"))
        ok_provider = AsyncMock()
        ok_provider.chat_completion = AsyncMock(return_value=_make_response("good-model"))

        dispatcher.register_provider("bad", fail_provider)
        dispatcher.register_provider("good", ok_provider)

        enabled = [
            ("bad", _make_model_cfg("bad-model", priority=1)),
            ("good", _make_model_cfg("good-model", priority=2)),
        ]
        prov, model, resp = await dispatcher.dispatch(_make_request(), enabled)
        assert prov == "good"
        assert resp.model == "good-model"
        fail_provider.chat_completion.assert_called_once()
        ok_provider.chat_completion.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_when_all_providers_fail(self, dispatcher: Dispatcher):
        fail_provider = AsyncMock()
        fail_provider.chat_completion = AsyncMock(side_effect=RuntimeError("模拟调用失败"))
        dispatcher.register_provider("bad", fail_provider)

        enabled = [("bad", _make_model_cfg("bad-model"))]
        with pytest.raises(AllModelsUnavailable):
            await dispatcher.dispatch(_make_request(), enabled)


class TestBreakerMechanism:
    def test_breaker_triggers_after_threshold(self, dispatcher: Dispatcher):
        dispatcher._breaker_threshold = 3
        for _ in range(3):
            dispatcher._record_provider_failure("google")
        assert dispatcher.is_provider_broken("google")

    def test_breaker_recovers_after_cooldown(self, dispatcher: Dispatcher):
        dispatcher._breaker_threshold = 1
        dispatcher._breaker_cooldown = 0
        dispatcher._record_provider_failure("google")
        assert not dispatcher.is_provider_broken("google")

    def test_success_clears_failures(self, dispatcher: Dispatcher):
        dispatcher._record_provider_failure("google")
        dispatcher._record_provider_failure("google")
        dispatcher._record_provider_success("google")
        assert "google" not in dispatcher._provider_failures

    def test_clear_breaker(self, dispatcher: Dispatcher):
        dispatcher._breaker_threshold = 1
        dispatcher._record_provider_failure("google")
        dispatcher.clear_breaker("google")
        assert not dispatcher.is_provider_broken("google")

    def test_should_trigger_breaker(self):
        assert Dispatcher._should_trigger_breaker(500)
        assert Dispatcher._should_trigger_breaker(429)
        assert not Dispatcher._should_trigger_breaker(400)
        assert not Dispatcher._should_trigger_breaker(404)
        assert not Dispatcher._should_trigger_breaker(422)


class TestProviderManagement:
    def test_register_and_has(self, dispatcher: Dispatcher):
        dispatcher.register_provider("test", AsyncMock())
        assert dispatcher.has_provider("test")

    def test_unregister(self, dispatcher: Dispatcher):
        dispatcher.register_provider("test", AsyncMock())
        assert dispatcher.unregister_provider("test")
        assert not dispatcher.has_provider("test")

    def test_unregister_nonexistent(self, dispatcher: Dispatcher):
        assert not dispatcher.unregister_provider("nonexistent")


class TestHelpers:
    def test_detect_chinese(self):
        req_cn = _make_request(msg="你好")
        assert Dispatcher._detect_chinese(req_cn)

        req_en = _make_request(msg="hello world")
        assert not Dispatcher._detect_chinese(req_en)

    def test_get_user_hint(self):
        req = _make_request(msg="hello world, this is a test")
        hint = Dispatcher._get_user_hint(req)
        assert "hello world" in hint
