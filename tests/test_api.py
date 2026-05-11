# Created by model-proxy on 2026/05/11
# Copyright © 2026

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestAPIEndpoints:
    @pytest.mark.asyncio
    async def test_models_endpoint(self, client):
        resp = await client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert len(data["models"]) >= 4

    @pytest.mark.asyncio
    async def test_usage_endpoint(self, client):
        resp = await client.get("/v1/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert "stats" in data

    @pytest.mark.asyncio
    async def test_config_endpoint(self, client):
        resp = await client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert "settings" in data
        # API Key 不应暴露
        for prov in data["providers"].values():
            assert "api_key" not in prov

    @pytest.mark.asyncio
    async def test_discovery_endpoint(self, client):
        resp = await client.get("/api/discovery")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 3
        assert data[0]["name"] == "Google AI Studio"
        assert data[0]["new_user_only"] is False

    @pytest.mark.asyncio
    async def test_ui_panel_accessible(self, client):
        resp = await client.get("/ui")
        assert resp.status_code == 200
        assert "Model Proxy" in resp.text

    @pytest.mark.asyncio
    async def test_chat_completions_auto_mode(self, client):
        """auto 模式请求，有可用 Provider 时应返回 200 或 503（全部限流）"""
        resp = await client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert resp.status_code in (200, 503)

    @pytest.mark.asyncio
    async def test_root_redirects_to_ui(self, client):
        resp = await client.get("/", follow_redirects=False)
        assert resp.status_code == 200
        assert "/ui" in resp.text
