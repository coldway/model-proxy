# Created by model-proxy on 2026/05/11
# Copyright © 2026

import time

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
        self.limiter.record_request("groq", "llama-3", )
        self.limiter.record_request("groq", "llama-3")
        daily, minute = self.limiter.get_usage("groq", "llama-3")
        assert daily == 2
        assert minute == 2

    def test_is_exhausted(self):
        for _ in range(10):
            self.limiter.record_request("test", "model-a")
        assert self.limiter.is_exhausted("test", "model-a", rpd=10)
        assert not self.limiter.is_exhausted("test", "model-a", rpd=100)

    def test_zero_limit_means_unlimited(self):
        for _ in range(1000):
            self.limiter.record_request("test", "unlimited")
        assert self.limiter.can_request("test", "unlimited", rpd=0, rpm=0)
