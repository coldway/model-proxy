# Created by model-proxy on 2026/05/17
# Copyright © 2026

"""CircuitBreaker 单元测试"""

import time

import pytest

from src.scheduler.circuit_breaker import CircuitBreaker, should_trigger_breaker


class TestShouldTriggerBreaker:
    def test_5xx_triggers(self):
        assert should_trigger_breaker(500) is True
        assert should_trigger_breaker(502) is True
        assert should_trigger_breaker(503) is True

    def test_429_triggers(self):
        assert should_trigger_breaker(429) is True

    def test_401_403_trigger(self):
        assert should_trigger_breaker(401) is True
        assert should_trigger_breaker(403) is True

    def test_client_errors_do_not_trigger(self):
        assert should_trigger_breaker(400) is False
        assert should_trigger_breaker(404) is False
        assert should_trigger_breaker(413) is False
        assert should_trigger_breaker(422) is False


class TestCircuitBreaker:
    def test_initial_state_not_open(self):
        cb = CircuitBreaker(threshold=3, cooldown=60)
        assert cb.is_open("google") is False

    def test_breaker_opens_after_threshold(self):
        cb = CircuitBreaker(threshold=3, cooldown=60)
        cb.record_failure("google")
        cb.record_failure("google")
        assert cb.is_open("google") is False
        cb.record_failure("google")
        assert cb.is_open("google") is True

    def test_success_resets_failures(self):
        cb = CircuitBreaker(threshold=3, cooldown=60)
        cb.record_failure("google")
        cb.record_failure("google")
        cb.record_success("google")
        cb.record_failure("google")
        assert cb.is_open("google") is False

    def test_breaker_recovers_after_cooldown(self):
        cb = CircuitBreaker(threshold=1, cooldown=1)
        cb.record_failure("google")
        assert cb.is_open("google") is True
        time.sleep(1.1)
        assert cb.is_open("google") is False

    def test_clear_specific_provider(self):
        cb = CircuitBreaker(threshold=1, cooldown=300)
        cb.record_failure("google")
        assert cb.is_open("google") is True
        count = cb.clear("google")
        assert count == 1
        assert cb.is_open("google") is False

    def test_clear_all(self):
        cb = CircuitBreaker(threshold=1, cooldown=300)
        cb.record_failure("google")
        cb.record_failure("groq")
        count = cb.clear()
        assert count == 2
        assert cb.is_open("google") is False
        assert cb.is_open("groq") is False

    def test_get_status(self):
        cb = CircuitBreaker(threshold=1, cooldown=300)
        cb.record_failure("google")
        status = cb.get_status()
        assert "google" in status
        assert status["google"]["broken"] is True
        assert status["google"]["remaining_seconds"] > 0

    def test_model_level_independent(self):
        cb = CircuitBreaker(threshold=2, cooldown=60)
        cb.record_failure("google", "m1")
        cb.record_failure("google", "m2")
        assert cb.is_open("google", "m1") is False
        assert cb.is_open("google", "m2") is False
        cb.record_failure("google", "m1")
        assert cb.is_open("google", "m1") is True
        assert cb.is_open("google", "m2") is False

    def test_legacy_provider_blocks_all_models(self):
        cb = CircuitBreaker(threshold=1, cooldown=300)
        cb.record_failure("google")
        assert cb.is_open("google", "any-model") is True

    def test_clear_removes_model_and_legacy_keys(self):
        cb = CircuitBreaker(threshold=1, cooldown=300)
        cb.record_failure("google", "m1")
        n = cb.clear("google")
        assert n >= 1
        assert cb.is_open("google", "m1") is False

    def test_different_providers_independent(self):
        cb = CircuitBreaker(threshold=2, cooldown=60)
        cb.record_failure("google")
        cb.record_failure("google")
        assert cb.is_open("google") is True
        assert cb.is_open("groq") is False

    def test_cooldown_property(self):
        cb = CircuitBreaker(cooldown=120)
        assert cb.cooldown == 120
