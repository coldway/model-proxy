# Created by model-proxy on 2026/05/20
# Copyright © 2026

"""Ollama Provider 单元测试 — 使用 mock 避免依赖真实 Ollama 服务"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.schemas import ChatCompletionRequest, ChatMessage
from src.providers.ollama import OllamaProvider, _infer_tool_calling


# ---------------------------------------------------------------------------
# _infer_tool_calling 纯函数测试
# ---------------------------------------------------------------------------

class TestInferToolCalling:
    def test_llama31_supported(self):
        assert _infer_tool_calling("llama3.1:8b", "") is True

    def test_llama32_supported(self):
        assert _infer_tool_calling("llama3.2:3b", "") is True

    def test_llama4_supported(self):
        assert _infer_tool_calling("llama4-scout:latest", "") is True

    def test_qwen25_supported(self):
        assert _infer_tool_calling("qwen2.5:14b", "") is True

    def test_qwen3_supported(self):
        assert _infer_tool_calling("qwen3:8b", "") is True

    def test_mistral_supported(self):
        assert _infer_tool_calling("mistral:latest", "") is True

    def test_command_r_supported(self):
        assert _infer_tool_calling("command-r-plus:latest", "") is True

    def test_phi4_supported(self):
        assert _infer_tool_calling("phi4:latest", "") is True

    def test_family_llama(self):
        assert _infer_tool_calling("some-model:latest", "llama") is True

    def test_family_qwen(self):
        assert _infer_tool_calling("custom-model:latest", "qwen2") is True

    def test_unknown_model(self):
        assert _infer_tool_calling("my-custom-finetune:latest", "") is False

    def test_stable_diffusion_not_supported(self):
        assert _infer_tool_calling("stable-diffusion:latest", "") is False


# ---------------------------------------------------------------------------
# OllamaProvider 构造函数测试
# ---------------------------------------------------------------------------

class TestOllamaInit:
    def test_default_base_url(self):
        p = OllamaProvider()
        assert p._base_url == "http://localhost:11434"

    def test_custom_base_url_via_api_key(self):
        p = OllamaProvider("http://192.168.1.100:11434")
        assert p._base_url == "http://192.168.1.100:11434"

    def test_trailing_slash_stripped(self):
        p = OllamaProvider("http://localhost:11434/")
        assert p._base_url == "http://localhost:11434"

    def test_non_url_api_key_uses_default(self):
        p = OllamaProvider("not-a-url")
        assert p._base_url == "http://localhost:11434"

    def test_empty_string_uses_default(self):
        p = OllamaProvider("")
        assert p._base_url == "http://localhost:11434"


# ---------------------------------------------------------------------------
# list_models / health_check / chat_completion (mocked)
# ---------------------------------------------------------------------------

class TestOllamaListModels:
    @pytest.mark.asyncio
    async def test_list_models_success(self):
        p = OllamaProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3.1:8b"},
                {"name": "qwen2.5:14b"},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        p._client = AsyncMock()
        p._client.get = AsyncMock(return_value=mock_response)

        models = await p.list_models()
        assert models == ["llama3.1:8b", "qwen2.5:14b"]

    @pytest.mark.asyncio
    async def test_list_models_empty(self):
        p = OllamaProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}
        mock_response.raise_for_status = MagicMock()

        p._client = AsyncMock()
        p._client.get = AsyncMock(return_value=mock_response)

        models = await p.list_models()
        assert models == []

    @pytest.mark.asyncio
    async def test_list_models_connection_error(self):
        p = OllamaProvider()
        p._client = AsyncMock()
        p._client.get = AsyncMock(side_effect=Exception("Connection refused"))

        models = await p.list_models()
        assert models == []


class TestOllamaHealthCheck:
    @pytest.mark.asyncio
    async def test_health_ok(self):
        p = OllamaProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200

        p._client = AsyncMock()
        p._client.get = AsyncMock(return_value=mock_response)

        assert await p.health_check() is True

    @pytest.mark.asyncio
    async def test_health_fail(self):
        p = OllamaProvider()
        p._client = AsyncMock()
        p._client.get = AsyncMock(side_effect=Exception("Connection refused"))

        assert await p.health_check() is False


class TestOllamaChatCompletion:
    @pytest.mark.asyncio
    async def test_non_streaming_success(self):
        p = OllamaProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "chatcmpl-123",
            "created": 1716220000,
            "model": "llama3.1:8b",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "你好！"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        mock_response.raise_for_status = MagicMock()

        p._client = AsyncMock()
        p._client.post = AsyncMock(return_value=mock_response)

        request = ChatCompletionRequest(
            model="llama3.1:8b",
            messages=[ChatMessage(role="user", content="你好")],
        )
        result = await p.chat_completion("llama3.1:8b", request)

        assert result.choices[0].message.content == "你好！"
        assert result.usage.total_tokens == 15


# ---------------------------------------------------------------------------
# discover_and_register (catalog mock)
# ---------------------------------------------------------------------------

class TestOllamaDiscovery:
    @pytest.mark.asyncio
    async def test_discover_registers_new_models(self):
        p = OllamaProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {
                    "name": "llama3.1:8b",
                    "size": 4_500_000_000,
                    "details": {
                        "family": "llama",
                        "parameter_size": "8B",
                        "quantization_level": "Q4_0",
                    },
                },
                {
                    "name": "qwen2.5:14b",
                    "size": 8_000_000_000,
                    "details": {
                        "family": "qwen2",
                        "parameter_size": "14B",
                        "quantization_level": "Q4_K_M",
                    },
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()

        p._client = AsyncMock()
        p._client.get = AsyncMock(return_value=mock_response)

        catalog = MagicMock()
        catalog.get_models.return_value = []
        catalog.add_model.return_value = True

        added = await p.discover_and_register(catalog)

        assert len(added) == 2
        assert "llama3.1:8b" in added
        assert "qwen2.5:14b" in added
        assert catalog.add_model.call_count == 2

        first_call_args = catalog.add_model.call_args_list[0]
        model_data = first_call_args[0][1]
        assert model_data["id"] == "llama3.1:8b"
        assert model_data["tool_calling"] is True
        assert model_data["category"] == "本地"

    @pytest.mark.asyncio
    async def test_discover_skips_existing(self):
        p = OllamaProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [{"name": "llama3.1:8b", "size": 0, "details": {}}]
        }
        mock_response.raise_for_status = MagicMock()

        p._client = AsyncMock()
        p._client.get = AsyncMock(return_value=mock_response)

        catalog = MagicMock()
        catalog.get_models.return_value = [{"id": "llama3.1:8b"}]

        added = await p.discover_and_register(catalog)
        assert added == []
        catalog.add_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_discover_handles_ollama_offline(self):
        p = OllamaProvider()
        p._client = AsyncMock()
        p._client.get = AsyncMock(side_effect=Exception("Connection refused"))

        catalog = MagicMock()
        added = await p.discover_and_register(catalog)
        assert added == []
