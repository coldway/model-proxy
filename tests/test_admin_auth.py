# Created by model-proxy on 2026/05/14
# Copyright © 2026

"""管理面板认证中间件测试"""

import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app_with_token(tmp_path, monkeypatch):
    """创建启用 admin_token 的 app 实例"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "conf").mkdir()
    (tmp_path / "data").mkdir()

    import shutil
    import importlib
    src = os.path.join(os.path.dirname(__file__), "..", "conf")
    for f in ("config.yaml.example", "providers_catalog.yaml"):
        src_file = os.path.join(src, f)
        if os.path.exists(src_file):
            dst = "config.yaml" if f.endswith(".example") else f
            shutil.copy(src_file, tmp_path / "conf" / dst)

    config_path = tmp_path / "conf" / "config.yaml"
    content = config_path.read_text()
    content += "\n  admin_token: test-secret-token\n"
    config_path.write_text(content)

    import main as main_mod
    importlib.reload(main_mod)
    return main_mod.app


class TestAdminAuth:
    @pytest.mark.asyncio
    async def test_ui_accessible_without_token(self, app_with_token):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/ui")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_root_accessible_without_token(self, app_with_token):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_api_requires_token(self, app_with_token):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/models")
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_api_with_valid_token(self, app_with_token):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/models",
                headers={"Authorization": "Bearer test-secret-token"},
            )
            assert resp.status_code != 401
