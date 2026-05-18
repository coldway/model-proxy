"""RateLimiter 单元测试"""
from __future__ import annotations

import time

import pytest

from src.scheduler.rate_limiter import RateLimiter


@pytest.fixture()
def limiter():
    return RateLimiter(persist=False)


class TestCanRequest:
    def test_allows_when_no_limit(self, limiter: RateLimiter):
        assert limiter.can_request("google", "gemini-2.0-flash", rpd=0, rpm=0)

    def test_blocks_when_rpd_exceeded(self, limiter: RateLimiter):
        for _ in range(5):
            limiter.record_request("google", "gemini-2.0-flash")
        assert not limiter.can_request("google", "gemini-2.0-flash", rpd=5, rpm=0)

    def test_blocks_when_rpm_exceeded(self, limiter: RateLimiter):
        for _ in range(3):
            limiter.record_request("groq", "llama-3.3-70b")
        assert not limiter.can_request("groq", "llama-3.3-70b", rpd=0, rpm=3)

    def test_blocks_when_tpd_exceeded(self, limiter: RateLimiter):
        limiter.record_request("google", "gemini-2.0-flash", tokens=10000)
        assert not limiter.can_request("google", "gemini-2.0-flash", rpd=0, rpm=0, tpd=5000)

    def test_blocks_when_tpm_exceeded(self, limiter: RateLimiter):
        limiter.record_request("google", "gemini-2.0-flash", tokens=8000)
        assert not limiter.can_request("google", "gemini-2.0-flash", rpd=0, rpm=0, tpm=5000)

    def test_allows_after_day_reset(self, limiter: RateLimiter):
        for _ in range(5):
            limiter.record_request("google", "gemini-2.0-flash")
        key = limiter._key("google", "gemini-2.0-flash")
        limiter._usage[key].last_reset_day = "1970-01-01"
        assert limiter.can_request("google", "gemini-2.0-flash", rpd=5, rpm=0)


class TestGetUsage:
    def test_returns_counts(self, limiter: RateLimiter):
        limiter.record_request("google", "gemini-2.0-flash", tokens=100)
        limiter.record_request("google", "gemini-2.0-flash", tokens=200)
        daily, minute, daily_tok, minute_tok = limiter.get_usage("google", "gemini-2.0-flash")
        assert daily == 2
        assert minute == 2
        assert daily_tok == 300
        assert minute_tok == 300


class TestBlacklist:
    def test_mark_and_check(self, limiter: RateLimiter):
        limiter.mark_429("groq", "llama-3.3-70b")
        assert limiter.is_blacklisted("groq", "llama-3.3-70b")
        assert not limiter.can_request("groq", "llama-3.3-70b", rpd=0, rpm=0)

    def test_clear_blacklist(self, limiter: RateLimiter):
        limiter.mark_429("groq", "llama-3.3-70b")
        limiter.clear_blacklist("groq", "llama-3.3-70b")
        assert not limiter.is_blacklisted("groq", "llama-3.3-70b")

    def test_clear_all_blacklist(self, limiter: RateLimiter):
        limiter.mark_429("groq", "m1")
        limiter.mark_429("google", "m2")
        count = limiter.clear_blacklist()
        assert count == 2
        assert not limiter.is_blacklisted("groq", "m1")

    def test_exponential_backoff(self, limiter: RateLimiter):
        limiter.mark_429("groq", "llama-3.3-70b")
        r1 = limiter.get_blacklist_remaining("groq", "llama-3.3-70b")

        limiter._blacklist.clear()
        limiter.mark_429("groq", "llama-3.3-70b")
        r2 = limiter.get_blacklist_remaining("groq", "llama-3.3-70b")
        assert r2 > r1

    def test_clear_429_backoff_resets_consecutive(self, limiter: RateLimiter):
        limiter.mark_429("groq", "llama-3.3-70b")
        limiter.clear_429_backoff("groq", "llama-3.3-70b")
        key = limiter._key("groq", "llama-3.3-70b")
        assert key not in limiter._blacklist_consecutive


class TestRecordTokens:
    def test_adds_to_daily(self, limiter: RateLimiter):
        limiter.record_request("google", "gemini-2.0-flash", tokens=50)
        limiter.record_tokens("google", "gemini-2.0-flash", 150)
        _, _, daily_tok, _ = limiter.get_usage("google", "gemini-2.0-flash")
        assert daily_tok == 200

    def test_ignores_zero(self, limiter: RateLimiter):
        limiter.record_tokens("google", "gemini-2.0-flash", 0)
        _, _, daily_tok, _ = limiter.get_usage("google", "gemini-2.0-flash")
        assert daily_tok == 0
