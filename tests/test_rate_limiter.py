# Created by model-proxy on 2026/05/11
# Copyright © 2026

from src.scheduler.rate_limiter import RateLimiter


class TestRateLimiter:
    def setup_method(self):
        self.limiter = RateLimiter()

    def test_can_request_within_limits(self):
        assert self.limiter.can_request("google", "gemini-2.5-pro", rpd=25, rpm=5)

    def test_rpm_limit_enforced(self):
        for _ in range(5):
            self.limiter.record_request("google", "gemini-2.5-pro")
        assert not self.limiter.can_request("google", "gemini-2.5-pro", rpd=25, rpm=5)

    def test_rpd_limit_enforced(self):
        for _ in range(25):
            self.limiter.record_request("google", "gemini-2.5-pro")
        assert not self.limiter.can_request("google", "gemini-2.5-pro", rpd=25, rpm=100)

    def test_different_models_independent(self):
        for _ in range(25):
            self.limiter.record_request("google", "gemini-2.5-pro")
        assert self.limiter.can_request("google", "gemini-2.5-flash", rpd=500, rpm=10)

    def test_get_usage(self):
        self.limiter.record_request("groq", "llama-3", tokens=100)
        self.limiter.record_request("groq", "llama-3", tokens=200)
        daily, minute, daily_tok, minute_tok = self.limiter.get_usage("groq", "llama-3")
        assert daily == 2
        assert minute == 2
        assert daily_tok == 300
        assert minute_tok == 300

    def test_is_exhausted(self):
        for _ in range(10):
            self.limiter.record_request("test", "model-a")
        assert self.limiter.is_exhausted("test", "model-a", rpd=10)
        assert not self.limiter.is_exhausted("test", "model-a", rpd=100)

    def test_zero_limit_means_unlimited(self):
        for _ in range(1000):
            self.limiter.record_request("test", "unlimited")
        assert self.limiter.can_request("test", "unlimited", rpd=0, rpm=0)

    def test_try_record_request_atomic(self):
        lim = RateLimiter(persist=False)
        for _ in range(4):
            assert lim.try_record_request("x", "m", rpd=100, rpm=5, tokens=0) is True
        assert lim.try_record_request("x", "m", rpd=100, rpm=5, tokens=0) is True
        assert lim.try_record_request("x", "m", rpd=100, rpm=5, tokens=0) is False
        daily, minute, _, _ = lim.get_usage("x", "m")
        assert daily == 5 and minute == 5
