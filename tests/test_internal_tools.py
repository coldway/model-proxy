# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""internal_tools 单元测试 — add_model / list_provider_models / search_ollama_library"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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

    def test_search_ollama_library_registered(self):
        names = [t["function"]["name"] for t in get_tool_definitions()]
        assert "search_ollama_library" in names

    def test_total_tool_count(self):
        assert len(get_tool_definitions()) == 6


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


_SAMPLE_HTML = """
<html><body>
<a href="/huihui_ai/gemma-4-abliterated" class="card">
  <span>huihui_ai/gemma-4-abliterated</span>
  <span>vision</span><span>tools</span><span>thinking</span><span>audio</span>
  <span>e2b</span><span>e4b</span><span>26b</span><span>31b</span><span>48b</span>
  <span>151.6K\xa0Pulls</span><span>29\xa0Tags</span>
</a>
<a href="/huihui_ai/granite4.1-abliterated" class="card">
  <span>huihui_ai/granite4.1-abliterated</span>
  <span>tools</span>
  <span>3b</span><span>8b</span><span>30b</span>
  <span>2,456\xa0Pulls</span><span>12\xa0Tags</span>
</a>
<a href="/mannix/llama3.1-8b-abliterated" class="card">
  <span>mannix/llama3.1-8b-abliterated</span>
  <span>8b</span>
  <span>500\xa0Pulls</span>
</a>
</body></html>
"""


class TestSearchOllamaLibrary:
    @pytest.mark.asyncio
    async def test_missing_query(self):
        result = await execute_tool("search_ollama_library", json.dumps({}))
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await execute_tool("search_ollama_library", json.dumps({"query": "  "}))
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = MagicMock()
        mock_resp.text = _SAMPLE_HTML
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_deps = MagicMock()
        mock_deps.dispatcher = MagicMock()
        mock_deps.dispatcher.has_provider.return_value = False

        with patch(_PATCH_DEPS, mock_deps), \
             patch("src.api.internal_tools.httpx.AsyncClient", return_value=mock_client):
            result = await execute_tool("search_ollama_library", json.dumps({"query": "abliterated"}))
            data = json.loads(result)
            assert data["total_found"] == 3
            assert data["suitable_count"] >= 1

            names = [m["name"] for m in data["suitable_models"]]
            assert "huihui_ai/granite4.1-abliterated" in names

    @pytest.mark.asyncio
    async def test_size_parsing(self):
        mock_resp = MagicMock()
        mock_resp.text = _SAMPLE_HTML
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_deps = MagicMock()
        mock_deps.dispatcher = MagicMock()
        mock_deps.dispatcher.has_provider.return_value = False

        with patch(_PATCH_DEPS, mock_deps), \
             patch("src.api.internal_tools.httpx.AsyncClient", return_value=mock_client):
            result = await execute_tool("search_ollama_library",
                                        json.dumps({"query": "abliterated", "vram_gb": 12}))
            data = json.loads(result)
            granite = next(m for m in data["suitable_models"] + data["unsuitable_models"]
                          if m["name"] == "huihui_ai/granite4.1-abliterated")
            size_params = [s["params"] for s in granite["sizes"]]
            assert any("3" in p for p in size_params)
            assert any("8" in p for p in size_params)

    @pytest.mark.asyncio
    async def test_installed_flag(self):
        mock_resp = MagicMock()
        mock_resp.text = _SAMPLE_HTML
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_tags_resp = MagicMock()
        mock_tags_resp.status_code = 200
        mock_tags_resp.json.return_value = {
            "models": [{"name": "mannix/llama3.1-8b-abliterated:latest", "size": 4675906716}]
        }
        mock_ollama_client = MagicMock()
        mock_ollama_client.get = AsyncMock(return_value=mock_tags_resp)

        mock_prov = MagicMock()
        mock_prov._client = mock_ollama_client

        mock_deps = MagicMock()
        mock_deps.dispatcher = MagicMock()
        mock_deps.dispatcher.has_provider.return_value = True
        mock_deps.dispatcher.get_provider.return_value = mock_prov

        with patch(_PATCH_DEPS, mock_deps), \
             patch("src.api.internal_tools.httpx.AsyncClient", return_value=mock_client):
            result = await execute_tool("search_ollama_library",
                                        json.dumps({"query": "abliterated"}))
            data = json.loads(result)
            all_models = data["suitable_models"] + data["unsuitable_models"]
            mannix = next(m for m in all_models
                         if m["name"] == "mannix/llama3.1-8b-abliterated")
            assert mannix["installed"] is True

            granite = next(m for m in all_models
                          if m["name"] == "huihui_ai/granite4.1-abliterated")
            assert granite["installed"] is False

    @pytest.mark.asyncio
    async def test_http_error(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("连接失败"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_deps = MagicMock()

        with patch(_PATCH_DEPS, mock_deps), \
             patch("src.api.internal_tools.httpx.AsyncClient", return_value=mock_client):
            result = await execute_tool("search_ollama_library",
                                        json.dumps({"query": "test"}))
            data = json.loads(result)
            assert "error" in data
            assert "失败" in data["error"]

    @pytest.mark.asyncio
    async def test_url_encoding(self):
        mock_resp = MagicMock()
        mock_resp.text = "<html></html>"
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_deps = MagicMock()
        mock_deps.dispatcher = MagicMock()
        mock_deps.dispatcher.has_provider.return_value = False

        with patch(_PATCH_DEPS, mock_deps), \
             patch("src.api.internal_tools.httpx.AsyncClient", return_value=mock_client):
            result = await execute_tool("search_ollama_library",
                                        json.dumps({"query": "中文 模型"}))
            data = json.loads(result)
            call_url = mock_client.get.call_args[0][0]
            assert "%E4%B8%AD%E6%96%87" in call_url
            assert " " not in call_url.split("?q=")[1]
