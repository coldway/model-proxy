"""Routes / API 端点测试"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app():
    """创建测试用 FastAPI 应用（不注册任何 Provider）"""
    import os
    os.environ.setdefault("MODEL_PROXY_CONF", "")
    from main import create_app
    return create_app()


@pytest.fixture()
def client(app):
    return TestClient(app)


class TestHealthCheck:
    def test_health_returns_ok(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "providers" in data
        assert "enabled_models" in data


class TestRootRedirect:
    def test_root_redirects_to_ui(self, client: TestClient):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 200
        assert "/ui" in resp.text


class TestStripThinking:
    """测试 _strip_thinking 函数"""

    def test_tag_removal(self):
        from src.api.routes import _strip_thinking
        text = "<think>I should think about this</think>The answer is 42."
        clean, thinking = _strip_thinking(text)
        assert "42" in clean
        assert "should think" in thinking

    def test_thinking_tag_removal(self):
        from src.api.routes import _strip_thinking
        text = "<thinking>Let me reason...</thinking>Result: yes."
        clean, thinking = _strip_thinking(text)
        assert "Result" in clean
        assert "reason" in thinking

    def test_no_thinking(self):
        from src.api.routes import _strip_thinking
        text = "Just a normal reply."
        clean, thinking = _strip_thinking(text)
        assert clean == text
        assert thinking == ""


class TestDedupAnswer:
    def test_quoted_dedup(self):
        from src.api.routes import _dedup_answer
        text = '"我是AI助手。"我是AI助手。'
        result = _dedup_answer(text)
        assert result.count("AI助手") == 1

    def test_no_dedup_needed(self):
        from src.api.routes import _dedup_answer
        text = "Normal unique text."
        result = _dedup_answer(text)
        assert result == text
