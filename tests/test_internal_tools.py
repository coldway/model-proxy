# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""internal_tools 单元测试 — add_model / list_provider_models"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.internal_tools import execute_tool, get_tool_definitions

_PATCH_DEPS = "src.api.routes._deps"


class TestToolRegistration:
    def test_add_model_registered(self):
        names = [t["function"]["name"] for t in get_tool_definitions()]
        assert "add_model" in names

    def test_list_provider_models_registered(self):
        names = [t["function"]["name"] for t in get_tool_definitions()]
        assert "list_provider_models" in names

    def test_total_tool_count(self):
        assert len(get_tool_definitions()) == 5


class TestAddModelTool:
    @pytest.mark.asyncio
    async def test_missing_args(self):
        result = await execute_tool("add_model", json.dumps({}))
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_missing_provider(self):
        result = await execute_tool("add_model", json.dumps({"model": "test"}))
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_unregistered_provider(self):
        mock_deps = MagicMock()
        mock_deps.dispatcher = MagicMock()
        mock_deps.dispatcher.has_provider.return_value = False
        mock_deps.dispatcher.get_all_providers.return_value = {"ollama": None, "groq": None}
        mock_deps.catalog = MagicMock()

        with patch(_PATCH_DEPS, mock_deps):
            result = await execute_tool("add_model", json.dumps({"provider": "fake", "model": "test"}))
            data = json.loads(result)
            assert "error" in data
            assert "available_providers" in data

    @pytest.mark.asyncio
    async def test_remote_model_not_supported(self):
        mock_deps = MagicMock()
        mock_deps.dispatcher = MagicMock()
        mock_deps.dispatcher.has_provider.return_value = True
        mock_prov = AsyncMock()
        mock_prov.list_models = AsyncMock(return_value=["model-a", "model-b", "model-c"])
        mock_deps.dispatcher.get_provider.return_value = mock_prov
        mock_deps.catalog = MagicMock()

        with patch(_PATCH_DEPS, mock_deps):
            result = await execute_tool("add_model", json.dumps({"provider": "groq", "model": "nonexistent"}))
            data = json.loads(result)
            assert "error" in data
            assert "不支持" in data["error"]

    @pytest.mark.asyncio
    async def test_remote_model_added_successfully(self):
        mock_deps = MagicMock()
        mock_deps.dispatcher = MagicMock()
        mock_deps.dispatcher.has_provider.return_value = True
        mock_prov = AsyncMock()
        mock_prov.list_models = AsyncMock(return_value=["model-a", "model-b"])
        mock_deps.dispatcher.get_provider.return_value = mock_prov
        mock_deps.catalog = MagicMock()
        mock_deps.catalog.get_model.return_value = None
        mock_deps.catalog.add_model.return_value = True
        mock_deps.capability_tester = None

        with patch(_PATCH_DEPS, mock_deps):
            result = await execute_tool("add_model", json.dumps({"provider": "groq", "model": "model-a"}))
            data = json.loads(result)
            assert data["status"] == "ok"
            assert data["action"] == "added_and_tested"
            mock_deps.catalog.add_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_ollama_already_installed(self):
        from src.providers.ollama import OllamaProvider

        mock_deps = MagicMock()
        mock_deps.dispatcher = MagicMock()
        mock_deps.dispatcher.has_provider.return_value = True

        mock_prov = MagicMock(spec=OllamaProvider)
        mock_prov.is_model_installed = AsyncMock(return_value=True)
        mock_prov.discover_and_register = AsyncMock(return_value=[])
        mock_deps.dispatcher.get_provider.return_value = mock_prov
        mock_deps.catalog = MagicMock()
        mock_deps.catalog.get_model.return_value = {"id": "qwen3:0.6b"}

        with patch(_PATCH_DEPS, mock_deps):
            result = await execute_tool("add_model", json.dumps({"provider": "ollama", "model": "qwen3:0.6b"}))
            data = json.loads(result)
            assert data["status"] == "ok"
            assert data["action"] == "already_installed"


class TestListProviderModelsTool:
    @pytest.mark.asyncio
    async def test_missing_provider(self):
        result = await execute_tool("list_provider_models", json.dumps({}))
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_unregistered_provider(self):
        mock_deps = MagicMock()
        mock_deps.dispatcher = MagicMock()
        mock_deps.dispatcher.has_provider.return_value = False
        mock_deps.dispatcher.get_all_providers.return_value = {"ollama": None}

        with patch(_PATCH_DEPS, mock_deps):
            result = await execute_tool("list_provider_models", json.dumps({"provider": "fake"}))
            data = json.loads(result)
            assert "error" in data
            assert "available_providers" in data

    @pytest.mark.asyncio
    async def test_successful_list(self):
        mock_deps = MagicMock()
        mock_deps.dispatcher = MagicMock()
        mock_deps.dispatcher.has_provider.return_value = True
        mock_prov = AsyncMock()
        mock_prov.list_models = AsyncMock(return_value=["m-1", "m-2", "m-3", "m-4"])
        mock_deps.dispatcher.get_provider.return_value = mock_prov
        mock_deps.catalog = MagicMock()
        mock_deps.catalog.get_models.return_value = [
            {"id": "m-1", "enabled": True},
            {"id": "m-3", "enabled": False},
        ]

        with patch(_PATCH_DEPS, mock_deps):
            result = await execute_tool("list_provider_models", json.dumps({"provider": "groq"}))
            data = json.loads(result)
            assert data["total_available"] == 4
            assert "m-2" in data["not_added"]
            assert "m-4" in data["not_added"]
            assert "m-1" in data["added_enabled"]
            assert "m-3" in data["added_disabled"]
            assert data["not_added_count"] == 2

    @pytest.mark.asyncio
    async def test_list_models_failure(self):
        mock_deps = MagicMock()
        mock_deps.dispatcher = MagicMock()
        mock_deps.dispatcher.has_provider.return_value = True
        mock_prov = AsyncMock()
        mock_prov.list_models = AsyncMock(side_effect=Exception("连接超时"))
        mock_deps.dispatcher.get_provider.return_value = mock_prov

        with patch(_PATCH_DEPS, mock_deps):
            result = await execute_tool("list_provider_models", json.dumps({"provider": "groq"}))
            data = json.loads(result)
            assert "error" in data
            assert "拉取" in data["error"]
