# Created by model-proxy on 2026/05/11
# Copyright © 2026

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
from src.providers.base import BaseProvider
from src.scheduler.dispatcher import (
    AllModelsUnavailable,
    Dispatcher,
    ModelNotFound,
    RateLimitExceeded,
)
from src.scheduler.rate_limiter import RateLimiter


class MockProvider(BaseProvider):
    """用于测试的模拟 Provider"""

    def __init__(self, should_fail: bool = False):
        super().__init__("")
        self.should_fail = should_fail
        self.call_count = 0

    async def chat_completion(self, model, request):
        self.call_count += 1
        if self.should_fail:
            raise RuntimeError("模拟调用失败")
        return ChatCompletionResponse(
            id="test-id",
            created=0,
            model=model,
            choices=[Choice(message=ChatMessage(role="assistant", content="mock response"))],
            usage=UsageInfo(),
        )

    async def list_models(self):
        return ["mock-model"]

    async def health_check(self):
        return True


@pytest.fixture
def setup_dispatcher():
    rate_limiter = RateLimiter(persist=False)
    dispatcher = Dispatcher(rate_limiter)
    mock_provider = MockProvider()
    dispatcher.register_provider("mock", mock_provider)
    return dispatcher, rate_limiter, mock_provider


class TestDispatcher:
    @pytest.mark.asyncio
    async def test_auto_dispatch_selects_first_available(self, setup_dispatcher):
        dispatcher, _, mock_provider = setup_dispatcher
        models = [
            ("mock", ModelConfig(name="model-a", priority=1, rate_limit=RateLimit(rpd=100, rpm=10))),
            ("mock", ModelConfig(name="model-b", priority=2, rate_limit=RateLimit(rpd=100, rpm=10))),
        ]
        request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")])
        prov, model, resp = await dispatcher.dispatch(request, models)
        assert resp.model == "model-a"
        assert prov == "mock"
        assert model == "model-a"

    @pytest.mark.asyncio
    async def test_specific_model_dispatch(self, setup_dispatcher):
        dispatcher, _, _ = setup_dispatcher
        models = [
            ("mock", ModelConfig(name="model-a", priority=1, rate_limit=RateLimit(rpd=100, rpm=10))),
            ("mock", ModelConfig(name="model-b", priority=2, rate_limit=RateLimit(rpd=100, rpm=10))),
        ]
        request = ChatCompletionRequest(
            model="model-b",
            messages=[ChatMessage(role="user", content="hi")],
        )
        prov, model, resp = await dispatcher.dispatch(request, models)
        assert resp.model == "model-b"
        assert prov == "mock"

    @pytest.mark.asyncio
    async def test_model_not_found(self, setup_dispatcher):
        dispatcher, _, _ = setup_dispatcher
        models = [
            ("mock", ModelConfig(name="model-a", priority=1)),
        ]
        request = ChatCompletionRequest(
            model="nonexistent",
            messages=[ChatMessage(role="user", content="hi")],
        )
        with pytest.raises(ModelNotFound):
            await dispatcher.dispatch(request, models)

    @pytest.mark.asyncio
    async def test_auto_switch_on_failure(self):
        rate_limiter = RateLimiter(persist=False)
        dispatcher = Dispatcher(rate_limiter)

        fail_provider = MockProvider(should_fail=True)
        ok_provider = MockProvider(should_fail=False)
        dispatcher.register_provider("bad", fail_provider)
        dispatcher.register_provider("good", ok_provider)

        models = [
            ("bad", ModelConfig(name="bad-model", priority=1, rate_limit=RateLimit(rpd=100, rpm=10))),
            ("good", ModelConfig(name="good-model", priority=2, rate_limit=RateLimit(rpd=100, rpm=10))),
        ]
        request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")])
        prov, model, resp = await dispatcher.dispatch(request, models)
        assert resp.model == "good-model"
        assert prov == "good"
        assert fail_provider.call_count == 1
        assert ok_provider.call_count == 1

    @pytest.mark.asyncio
    async def test_all_models_unavailable(self):
        rate_limiter = RateLimiter(persist=False)
        dispatcher = Dispatcher(rate_limiter)
        fail_provider = MockProvider(should_fail=True)
        dispatcher.register_provider("bad", fail_provider)

        models = [
            ("bad", ModelConfig(name="m1", priority=1, rate_limit=RateLimit(rpd=100, rpm=10))),
        ]
        request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")])
        with pytest.raises(AllModelsUnavailable):
            await dispatcher.dispatch(request, models)

    @pytest.mark.asyncio
    async def test_rate_limited_model_skipped(self, setup_dispatcher):
        dispatcher, rate_limiter, _ = setup_dispatcher
        # 耗尽 model-a 的 RPM
        for _ in range(5):
            rate_limiter.record_request("mock", "model-a")

        models = [
            ("mock", ModelConfig(name="model-a", priority=1, rate_limit=RateLimit(rpd=100, rpm=5))),
            ("mock", ModelConfig(name="model-b", priority=2, rate_limit=RateLimit(rpd=100, rpm=10))),
        ]
        request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")])
        prov, model, resp = await dispatcher.dispatch(request, models)
        assert resp.model == "model-b"
