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
    content = config_path.read_text(encoding="utf-8")
    content += "\n  admin_token: test-secret-token\n"
    config_path.write_text(content, encoding="utf-8")

    import main as main_mod
    importlib.reload(main_mod)
    return main_mod.app


class TestAdminAuth:
    @pytest.mark.asyncio
    async def test_ui_accessible_without_token(self, app_with_token):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/mp/ui")
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
            resp = await client.get("/mp/api/config")
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_api_with_valid_token(self, app_with_token):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/mp/api/config",
                headers={"Authorization": "Bearer test-secret-token"},
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_v1_also_protected_when_only_admin_token(self, app_with_token):
        """只配 admin_token 时，/v1/ 路径也应受保护（互兜底）"""
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            assert resp.status_code == 401

            resp = await client.get(
                "/v1/models",
                headers={"Authorization": "Bearer test-secret-token"},
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_bearer_case_insensitive(self, app_with_token):
        """Bearer 前缀大小写不敏感"""
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/mp/api/config",
                headers={"Authorization": "bearer test-secret-token"},
            )
            assert resp.status_code == 200
            resp = await client.get(
                "/mp/api/config",
                headers={"Authorization": "BEARER test-secret-token"},
            )
            assert resp.status_code == 200
