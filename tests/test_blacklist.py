# Created by model-proxy on 2026/05/14
# Copyright © 2026

"""429 黑名单清除语义、自然日过期、参数组合测试"""

import time

from src.scheduler.rate_limiter import RateLimiter


class TestBlacklistClear:
    def setup_method(self):
        self.limiter = RateLimiter(persist=False)

    def test_clear_specific_model(self):
        self.limiter.mark_429("google", "gemini-pro")
        self.limiter.mark_429("google", "gemini-flash")
        assert self.limiter.clear_blacklist("google", "gemini-pro") == 1
        assert self.limiter.is_blacklisted("google", "gemini-pro") is False
        assert self.limiter.is_blacklisted("google", "gemini-flash") is True

    def test_clear_all_for_provider(self):
        self.limiter.mark_429("google", "gemini-pro")
        self.limiter.mark_429("google", "gemini-flash")
        self.limiter.mark_429("groq", "llama")
        assert self.limiter.clear_blacklist("google") == 2
        assert self.limiter.is_blacklisted("groq", "llama") is True

    def test_clear_all(self):
        self.limiter.mark_429("google", "gemini-pro")
        self.limiter.mark_429("groq", "llama")
        count = self.limiter.clear_blacklist()
        assert count == 2
        assert self.limiter.is_blacklisted("google", "gemini-pro") is False
        assert self.limiter.is_blacklisted("groq", "llama") is False

    def test_clear_with_only_model_is_noop(self):
        """仅传 model 不传 provider 时不做任何操作（避免误删）"""
        self.limiter.mark_429("google", "gemini-pro")
        assert self.limiter.clear_blacklist(model="gemini-pro") == 0
        assert self.limiter.is_blacklisted("google", "gemini-pro") is True

    def test_clear_nonexistent_returns_zero(self):
        assert self.limiter.clear_blacklist("nonexistent", "model") == 0

    def test_blacklisted_blocks_request(self):
        self.limiter.mark_429("google", "gemini-pro")
        assert self.limiter.can_request("google", "gemini-pro", rpd=100, rpm=10) is False

    def test_remaining_seconds(self):
        self.limiter.mark_429("google", "gemini-pro")
        remaining = self.limiter.get_blacklist_remaining("google", "gemini-pro")
        assert remaining > 0

    def test_remaining_zero_for_unblocked(self):
        assert self.limiter.get_blacklist_remaining("google", "gemini-pro") == 0

    def test_get_blacklist_returns_info(self):
        self.limiter.mark_429("google", "gemini-pro")
        bl = self.limiter.get_blacklist()
        assert "google:gemini-pro" in bl
        assert "expire_time" in bl["google:gemini-pro"]
        assert "remaining_seconds" in bl["google:gemini-pro"]
