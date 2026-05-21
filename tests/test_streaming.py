# Created by model-proxy on 2026/05/14
# Copyright © 2026

"""流式 SSE 生成器行为测试"""

import json

import pytest

from src.api.streaming import _stream_generator


async def _make_iter(*chunks):
    for c in chunks:
        yield c


async def _error_iter():
    yield "hello"
    raise RuntimeError("boom")


class TestStreamGenerator:
    @pytest.mark.asyncio
    async def test_normal_stream_ends_with_stop(self):
        chunks = []
        async for line in _stream_generator("test-model", _make_iter("a", "b")):
            chunks.append(line)
        assert len(chunks) == 4
        last_data = json.loads(chunks[-2].removeprefix("data: ").strip())
        assert last_data["choices"][0]["finish_reason"] == "stop"
        assert chunks[-1].strip() == "data: [DONE]"

    @pytest.mark.asyncio
    async def test_error_stream_has_server_error_payload(self):
        chunks = []
        async for line in _stream_generator("test-model", _error_iter()):
            chunks.append(line)
        finish_reasons = []
        error_frames = []
        for c in chunks:
            if c.startswith("data: {"):
                d = json.loads(c.removeprefix("data: ").strip())
                fr = d.get("choices", [{}])[0].get("finish_reason")
                if fr is not None:
                    finish_reasons.append(fr)
                if "error" in d:
                    error_frames.append(d["error"])
        assert "error" in finish_reasons
        assert "stop" not in finish_reasons
        assert len(error_frames) == 1
        assert error_frames[0]["type"] == "server_error"
        assert error_frames[0]["code"] is None
        assert "重试" in error_frames[0]["message"]
        assert chunks[-1].strip() == "data: [DONE]"

    @pytest.mark.asyncio
    async def test_empty_stream(self):
        chunks = []
        async for line in _stream_generator("test-model", _make_iter()):
            chunks.append(line)
        assert len(chunks) == 2
        last_data = json.loads(chunks[0].removeprefix("data: ").strip())
        assert last_data["choices"][0]["finish_reason"] == "stop"
